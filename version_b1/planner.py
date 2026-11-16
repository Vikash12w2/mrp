from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from math import lcm
import multiprocessing as mp
import time
import pickle
from copy import deepcopy
from ..bus import Bus
from ..planner import GroupPlan, build_ext, levels
from ..twoshot import Options, Result
from ..runtime import verify
from .geometry import group_box, overlaps
from .twoshot import solve_isolated


def solve_job(job):
    sub, opts, horizons = job
    start = time.monotonic()
    result = Result(False, status='NO_PLAN', note='no valid alignment horizon')
    for horizon in horizons[:2]:
        remaining = 60 - (time.monotonic()-start)
        if remaining <= 0:
            break
        result = solve_isolated(replace(sub, T=horizon), opts, timeout_s=remaining)
        if result.ok or result.status == 'UNKNOWN':
            break
    return result, time.monotonic()-start


def _pass(inst, groups, anchors, P, bus, parallel=True, *, forced=None, cache=None):
    forced = forced or {}
    cache = cache if cache is not None else {}
    workers = {w.id: w for w in inst.workers}
    boxes = {r: group_box(inst, anchors[r], [workers[w] for w in sorted(groups[r])])
             for r in sorted(anchors)}
    region_history = []
    cumulative = {r: 0.0 for r in anchors}
    # Failure returns to the central regrouping loop; never retry this partition
    # in larger regions before considering a different assignment.
    nbrs = {r: [s for s in boxes if s != r and overlaps(boxes[r], boxes[s])] for r in boxes}
    lvl = levels(nbrs)
    plans, order = {}, []
    pool = ProcessPoolExecutor(mp_context=mp.get_context('spawn')) if parallel else None
    try:
        for level in sorted(set(lvl.values())):
            batch = [r for r in sorted(boxes) if lvl[r] == level]
            jobs, active = [], []
            for r in batch:
                g = GroupPlan(r, sorted(groups[r]), anchors[r])
                g.box = boxes[r]
                plans[r] = g
                order.append((level, r))
                messages = bus.drain(r, 'plan') + bus.drain(r, 'no_plan')
                known = {m.src: m.payload for m in messages if m.src in nbrs[r] and m.src < r}
                if not groups[r]:
                    continue
                higher_ids = [h for h in nbrs[r] if h < r]
                higher = [known[h] for h in higher_ids if h in known]
                ext, align, reason = build_ext(higher, boxes[r])
                if any(h not in known for h in higher_ids):
                    reason = 'higher-priority plan message unavailable'
                if any(h.workers and not h.live for h in higher):
                    reason = 'higher-priority group has no valid plan'
                if reason:
                    g.res = Result(False, status='DEPENDENCY_BLOCKED', note=reason)
                    g.notes.append(reason)
                    continue
                if r in forced:
                    if align is not None and align != forced[r]:
                        g.res = Result(False, status='DEPENDENCY_BLOCKED', note='cycle mismatch')
                        continue
                    align = forced[r]
                mode = P.group_mode if P.group_mode != 'auto' else (
                    'one_shot' if len(groups[r]) <= P.one_shot_max_workers else 'two_shot')
                horizons = ([align or P.one_shot_H or inst.T] if mode == 'one_shot' else
                            [inst.T] if not align else
                            [t for t in (min(inst.T, align)-k for k in range(min(P.K_T, 2))) if t >= 4])
                opts = Options(box=boxes[r], ext=ext, align_M=align, mode=mode,
                    collisions=P.collisions, exclusive_recharge=P.exclusive_recharge,
                    seed=P.seed, rlimit=P.rlimit, phase2_optimize_cost=P.phase2_optimize_cost)
                job = (inst.sub([workers[w] for w in sorted(groups[r])]), opts, horizons)
                # Exact inputs include map, energy model, region, period and all
                # upstream swept occupancy. Never reuse based on membership alone.
                key = pickle.dumps(job)
                if key in cache:
                    g.res = deepcopy(cache[key])
                    g.notes.append('reused plan with identical planning constraints')
                else:
                    jobs.append(job)
                    active.append((r, key))
            outputs = list(pool.map(solve_job, jobs)) if pool and len(jobs)>1 else [solve_job(j) for j in jobs]
            for (r, key), (result, elapsed) in zip(active, outputs):
                if result.ok:
                    cache[key] = deepcopy(result)
                plans[r].res = result
                cumulative[r] += elapsed
                if not result.ok:
                    plans[r].notes.append(result.note)
            for r in batch:
                g = plans[r]
                for peer in nbrs[r]:
                    if peer > r:
                        bus.send(r, peer, 'plan' if g.live or not g.workers else 'no_plan', g)
    finally:
        if pool:
            pool.shutdown()
    periods = [g.res.M-1 for g in plans.values() if g.live]
    valid = (all(not g.workers or g.live for g in plans.values()) and not verify(plans)
             and (lcm(*periods) if periods else 1) <= 3000)
    region_history.append(dict(expansion=0, boxes=dict(boxes), valid=valid,
        statuses={r: g.res.status if g.res else 'IDLE' for r, g in plans.items()}))
    for r, g in plans.items():
        g.wall = cumulative[r]
        g.region_history = list(region_history)
    return plans, order, nbrs


def run_wavefront(inst, groups, anchors, P, bus, parallel=True, *, cache=None):
    """Try the initial schedule and at most two longer cycles per partition.

    Only connected overlap components containing a failure are retimed. Every
    changed schedule is solved again and the entire joint schedule is checked.
    """
    cache = cache if cache is not None else {}
    history, totals, forced = [], {r: 0.0 for r in anchors}, {}
    for attempt in range(3):
        current_bus = bus if attempt == 0 else Bus(anchors, P.R_c, P.retries)
        plans, order, neighbors = _pass(inst, groups, anchors, P, current_bus,
                                        parallel, forced=forced, cache=cache)
        if attempt:
            bus.stats.update(current_bus.stats)
        entry = dict(plans[next(iter(plans))].region_history[0]) if plans else dict(valid=True)
        entry.update(cycle_attempt=attempt, forced_cycles=dict(forced))
        history.append(entry)
        for r, g in plans.items():
            totals[r] += g.wall
        if entry['valid']:
            break
        failed = {r for r, g in plans.items() if g.workers and not g.live}
        # Without a successful upstream cycle, extension has no evidence to use;
        # return promptly for regrouping (including solver-budget exhaustion).
        affected = set(failed)
        frontier = list(failed)
        while frontier:
            for n in neighbors[frontier.pop()]:
                if n not in affected:
                    affected.add(n)
                    frontier.append(n)
        live = [g.res.M for r, g in plans.items() if r in affected and g.live]
        if not failed or not live or any(
                plans[r].res and plans[r].res.status == 'UNKNOWN' for r in failed):
            break
        target = max(live + [forced.get(r, 0) for r in affected]) + 4
        forced.update({r: target for r in affected})
    for r, g in plans.items():
        g.wall = totals[r]
        g.region_history = history
    return plans, order, neighbors
