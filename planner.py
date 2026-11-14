from __future__ import annotations
import time
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Optional
from .bus import Bus
from .grouping import box_of
from .model import Instance, cheb
from .params import Params
from .twoshot import Ext, Options, Result, solve_isolated, wrap


@dataclass
class GroupPlan:
    rid: int
    workers: list
    anchor: tuple
    res: Optional[Result] = None        # None / not ok -> idle (no workers) or no plan
    fallback: bool = False
    notes: list = field(default_factory=list)
    wall: float = 0.0

    @property
    def live(self) -> bool:
        return bool(self.res and self.res.ok)


def overlap_neighbors(anchors: dict, R_s: int) -> dict:
    return {r: sorted(o for o in anchors if o != r and cheb(anchors[r], anchors[o]) <= 2 * R_s) for r in anchors}


def levels(nbrs: dict) -> dict:
    lvl = {}
    for r in sorted(nbrs):
        hi = [h for h in nbrs[r] if h < r]
        lvl[r] = 0 if not hi else 1 + max(lvl[h] for h in hi)
    return lvl


def build_ext(hi_plans: list, box: tuple):
    live = [g for g in hi_plans if g is not None and g.live]
    parked = [g for g in hi_plans if g is not None and not g.live and not g.workers]
    periods = {g.res.M - 1 for g in live}
    if len(periods) > 1:
        return None, None, f"neighbour periods differ {sorted(p + 1 for p in periods)}"
    if not live and not parked:
        return None, None, None
    per = periods.pop() if periods else 1
    step_all = [None] + [set() for _ in range(per)]
    time_rech = [None] + [set() for _ in range(per)]
    for g in parked:
        for t in range(1, per + 1):
            step_all[t].add(g.anchor)
            time_rech[t].add(g.anchor)
    for g in live:
        r = g.res
        for pos in r.wpos.values():
            for t in range(1, per + 1):
                step_all[t].update((pos[t], pos[t + 1]))
        for sweeps in r.wsweep.values():
            for t in range(1, per + 1):
                step_all[t].update(sweeps[t])
        for rr in r.rpos:
            for t in range(1, per + 1):
                sw = r.rsweep[rr][t]
                step_all[t] |= sw
                time_rech[t] |= {r.rpos[rr][t]} | sw | r.rsweep[rr][wrap(t - 1, per)]
    x0, x1, y0, y1 = box
    keep = lambda s: {c for c in s if x0 <= c[0] <= x1 and y0 <= c[1] <= y1}
    ext = Ext(per, [None] + [keep(s) for s in step_all[1:]], [None] + [keep(s) for s in time_rech[1:]])
    return ext, (per + 1 if live else None), None


def build_job(rid, group, inst: Instance, anchors, plans, nbrs, P: Params):
    box = box_of(inst, anchors[rid], P.R_s)
    hi = [h for h in nbrs[rid] if h < rid]
    ext, align, reason = build_ext([plans.get(h) for h in hi], box)
    for h in hi:
        if plans.get(h) is None:
            reason = reason or f"neighbour {h} plan unavailable"
        elif not plans[h].live and plans[h].workers:
            reason = reason or f"neighbour {h} published no plan"
    if reason:
        ext, align = None, None
    mode = P.group_mode if P.group_mode != "auto" else ("one_shot" if len(group) <= P.one_shot_max_workers else "two_shot")
    if mode == "one_shot":                       # horizon H is the hypercycle itself; aligned -> H = neighbours' T'
        T_list = [align] if align else [P.one_shot_H or inst.T]
    else:
        T_list = [inst.T] if not align else [t for t in (min(inst.T, align) - k for k in range(P.K_T)) if t >= 4]
    opts = Options(mode=mode, collisions=P.collisions, exclusive_recharge=P.exclusive_recharge, box=box, ext=ext,
                   align_M=align, seed=P.seed, rlimit=P.rlimit,
                   phase2_optimize_cost=P.phase2_optimize_cost)
    return (inst.sub([w for w in inst.workers if w.id in group], 1, inst.T), opts, reason, T_list)


def solve_group_job(job):
    sub, opts, reason, T_list = job
    t0 = time.time()
    res = None
    for T in T_list:
        res = solve_isolated(sub.sub(sub.workers, 1, T), opts)
        if res.status == "UNKNOWN":
            return res, False, res.note, time.time() - t0
        if res.ok:
            break
    if not res.ok and opts.ext is not None:               # aligned/constrained planning infeasible
        res = solve_isolated(sub.sub(sub.workers, 1, T_list[0]), replace(opts, ext=None, align_M=None))
        if res.status == "UNKNOWN":
            return res, False, res.note, time.time() - t0
        reason = reason or "aligned planning infeasible"
    return res, bool(reason and res.ok), reason, time.time() - t0


def run_wavefront(inst: Instance, groups: dict, anchors: dict, P: Params, bus: Bus, parallel=False,
                  keep: Optional[dict] = None, resolve: Optional[set] = None):
    nbrs = overlap_neighbors(anchors, P.R_s)
    lvl = levels(nbrs)
    plans, order = ({r: g for r, g in keep.items() if r in anchors} if keep else {}), []
    resolved = set(lvl) if resolve is None else set(resolve)
    published = set()

    def publish(r):
        g = plans[r]
        kind = "plan" if (g.live or not g.workers) else "no_plan"
        for lo in (x for x in nbrs[r] if x > r):
            bus.send(r, lo, kind, g)
        published.add(r)

    # Replanned runs may keep earlier plans. Send those through the same local
    # message path used by newly solved groups before lower-priority agents read them.
    for r in sorted(plans):
        if r not in resolved:
            publish(r)

    def receive_for(r):
        known = {}
        msgs = bus.drain(r, "plan") + bus.drain(r, "no_plan")
        for msg in sorted(msgs, key=lambda m: (m.src, m.seq)):
            if msg.src in nbrs[r] and msg.src < r:
                known[msg.src] = msg.payload
        return known

    import multiprocessing as mp
    pool = ProcessPoolExecutor(mp_context=mp.get_context("spawn")) if parallel else None
    try:
        for L in sorted(set(lvl.values())):
            rids = [r for r in sorted(lvl) if lvl[r] == L]
            rids = [r for r in rids if resolve is None or r in resolve]
            work = [r for r in rids if groups[r]]
            known = {r: receive_for(r) for r in work}
            jobs = [build_job(r, groups[r], inst, anchors, known[r], nbrs, P) for r in work]
            out = list(pool.map(solve_group_job, jobs)) if pool else [solve_group_job(j) for j in jobs]
            done = dict(zip(work, out))
            for r in rids:
                g = GroupPlan(r, list(groups[r]), anchors[r])
                if r in done:
                    g.res, g.fallback, reason, g.wall = done[r]
                    if reason:
                        g.notes.append(f"runtime collision handling: {reason}")
                    if not g.res.ok:
                        g.notes.append(g.res.note)
                plans[r] = g
                order.append((L, r))
                if r not in published:
                    publish(r)
    finally:
        if pool:
            pool.shutdown()
    for r in sorted(anchors):
        for nb in nbrs[r]:
            bus.send(r, nb, "ready", {"rid": r})
    return plans, order, nbrs


def _eff(plans_subset) -> float:
    live = [g for g in plans_subset if g.live]
    den = sum(g.res.nW * g.res.M for g in live)
    wait = sum(g.res.p1_wait + (g.res.M - g.res.T) * g.res.nW for g in live)
    return 100.0 * (den - wait) / den if den else 100.0


def _plan_for(rid, group, inst, anchors, known, nbrs, P):
    if not group:                                           # idle recharger: parked at its anchor
        return GroupPlan(rid, [], anchors[rid])
    res, fb, reason, wall = solve_group_job(build_job(rid, group, inst, anchors, known, nbrs, P))
    if not res.ok:
        return None
    return GroupPlan(rid, sorted(group), anchors[rid], res, fb,
                     [f"runtime collision handling: {reason}"] if reason else [], wall)


def refine_partition(inst: Instance, groups: dict, plans: dict, anchors: dict, agents: dict, nbrs: dict, P: Params,
                     bus: Bus):
    from .refinement import Refiner, refine
    actors = [Refiner(a, plans[a.id], bus) for a in agents.values()]
    refine(actors, bus, P.K_refine)
    for actor in actors:
        groups[actor.rid] = set(actor.members)
        plans[actor.rid] = actor.plan
        actor.agent.claims = {w: actor.agent.cand[w] for w in actor.members}
    return sum(a.accepted for a in actors)
