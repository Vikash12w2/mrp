"""B1 per-group regions, dependency scheduling and bounded region expansion."""
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from math import lcm
import multiprocessing as mp
import time
from ..planner import GroupPlan, build_ext, levels
from ..twoshot import Options, Result
from ..runtime import verify
from .geometry import group_box, overlaps, expand_box
from .twoshot import solve_isolated


def solve_job(job):
    sub, opts, horizons = job
    start = time.monotonic()
    result = Result(False, status='NO_PLAN', note='no valid alignment horizon')
    for horizon in horizons:
        result = solve_isolated(replace(sub, T=horizon), opts)
        if result.ok or result.status == 'UNKNOWN':
            break
    return result, time.monotonic()-start


def run_wavefront(inst, groups, anchors, P, bus, parallel=True):
    workers = {w.id: w for w in inst.workers}
    boxes = {r: group_box(inst, anchors[r], [workers[w] for w in sorted(groups[r])])
             for r in sorted(anchors)}
    full = (0, inst.xmax, 0, inst.ymax)
    region_history = []
    cumulative = {r: 0.0 for r in anchors}
    # Local box, expanded box, then full map only if necessary. Every expansion
    # rebuilds dependencies and all plans; earlier reservations cannot be reused.
    for expansion in range(3):
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
                    mode = P.group_mode if P.group_mode != 'auto' else (
                        'one_shot' if len(groups[r]) <= P.one_shot_max_workers else 'two_shot')
                    horizons = ([align or P.one_shot_H or inst.T] if mode == 'one_shot' else
                                [inst.T] if not align else
                                [t for t in (min(inst.T, align)-k for k in range(P.K_T)) if t >= 4])
                    opts = Options(box=boxes[r], ext=ext, align_M=align, mode=mode,
                        collisions=P.collisions, exclusive_recharge=P.exclusive_recharge,
                        seed=P.seed, rlimit=P.rlimit, phase2_optimize_cost=P.phase2_optimize_cost)
                    jobs.append((inst.sub([workers[w] for w in sorted(groups[r])]), opts, horizons))
                    active.append(r)
                outputs = list(pool.map(solve_job, jobs)) if pool and len(jobs)>1 else [solve_job(j) for j in jobs]
                for r, (result, elapsed) in zip(active, outputs):
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
        region_history.append(dict(expansion=expansion, boxes=dict(boxes), valid=valid,
            statuses={r: g.res.status if g.res else 'IDLE' for r, g in plans.items()}))
        for r, g in plans.items():
            g.wall = cumulative[r]
            g.region_history = list(region_history)
        if valid or all(box == full or not groups[r] for r, box in boxes.items()):
            return plans, order, nbrs
        boxes = {r: ((expand_box(inst, b) if expansion == 0 else full) if groups[r] else b)
                 for r, b in boxes.items()}
    return plans, order, nbrs
