"""B1 phase-1 variant. Constraints copied from the shared two-shot core.

Only tie-breaking differs. Phase 2 and result construction remain shared-compatible.
Keep this phase1 copy in sync when original encoding constraints are corrected.
"""
from __future__ import annotations
import math
import os
import time
from typing import Optional
import z3
from ..model import Instance, OFFS8, worker_edge_cells
from ..twoshot import (Options, Result, SolverUnknown, _cfg, _obstacle_fn,
    _recharger_block, _arr, _blocked, _wait_prim, _add_sweeps_disjoint,
    _checked_sat, wrap, _halts_and_instances, phase2, _one_shot_result)
from .geometry import charging_cells, distances

def completion_objectives(s, inst, o, rx, ry, rp, ch, T):
    """Heuristic obstacle-aware completion bounds, subordinate to waiting.

    Independent shortest routes ignore inter-worker ordering and dynamic
    collisions; the actual phase-2 solve still checks the complete schedule.
    """
    width = inst.xmax+1
    unreachable = (inst.xmax+1)*(inst.ymax+1)*2
    def table(mapping):
        a = z3.K(z3.IntSort(), unreachable)
        for (x, y), value in sorted(mapping.items()):
            a = z3.Store(a, y*width+x, value)
        return a
    estimates, deficits = [], []
    for i, w in enumerate(inst.workers, 1):
        cells = charging_cells(inst, w.loop[0])
        to_worker = table(distances(inst, cells, reverse=True, box=o.box))
        from_worker = table(distances(inst, cells, box=o.box))
        deficit = (w.fch-ch[i][T]+inst.delta_max-1)/inst.delta_max
        deficits.append(deficit)
        # B1 local jobs have one recharger. Keep the helper valid for multiple
        # rechargers by taking the cheapest individual completion estimate.
        routes = [z3.Select(to_worker, ry[r][T]*width+rx[r][T]) + deficit +
                  z3.Select(from_worker, ry[r][1]*width+rx[r][1]) for r in range(1, inst.n_rech+1)]
        best = routes[0]
        for cost in routes[1:]:
            best = z3.If(cost < best, cost, best)
        estimates.append(best)
    if estimates:
        worst = estimates[0]
        for cost in estimates[1:]:
            worst = z3.If(cost > worst, cost, worst)
        s.minimize(worst)
        s.minimize(z3.Sum(estimates))
        s.minimize(z3.Sum(deficits))
    s.minimize(z3.Sum([z3.If(rp[r][t] == k, z3.RealVal(str(pr.cost)), 0)
                      for r in range(1, inst.n_rech+1) for t in range(1, T)
                      for k, pr in enumerate(inst.prims, 1)]))

def phase1(inst: Instance, o: Options, fixed: Optional[dict] = None):
    s = z3.Optimize()
    _cfg(s, o)
    obstacle = _obstacle_fn(inst, s)
    T, R, W = inst.T, inst.n_rech, inst.workers
    nW, wk = len(W), _wait_prim(inst)
    rx, ry, rp = _recharger_block(inst, s, obstacle, o, T, "a")
    if o.p_starts:
        for r in range(1, R + 1):
            s.add(z3.Or([z3.And(rx[r][1] == c[0], ry[r][1] == c[1]) for c in o.p_starts]))
    rt = _arr(inst, o.ext.time_rech) if o.ext else None
    wt, wp, wx, wy, ch, ra = {}, {}, {}, {}, {}, {}
    AM = {}
    wait_terms = []
    for i, w in enumerate(W, 1):
        L = len(w.loop)
        wt[i] = [None] + [z3.Int(f"wt{i}_{t}") for t in range(1, T + 1)]
        wp[i] = [None] + [z3.Int(f"wp{i}_{t}") for t in range(1, T)]
        wx[i] = [None] + [z3.Int(f"wx{i}_{t}") for t in range(1, T + 1)]
        wy[i] = [None] + [z3.Int(f"wy{i}_{t}") for t in range(1, T + 1)]
        ch[i] = [None] + [z3.Int(f"ch{i}_{t}") for t in range(1, T + 1)]
        ra[i] = [None] + [z3.Int(f"ra{i}_{t}") for t in range(1, T)]
        s.add(wt[i][1] == 1, ch[i][1] == w.fch, wx[i][1] == wx[i][T], wy[i][1] == wy[i][T])
        for t in range(1, T + 1):
            s.add(wt[i][t] >= 1, wt[i][t] <= L, ch[i][t] >= 0, ch[i][t] <= w.fch,
                  wx[i][t] >= 0, wx[i][t] <= inst.xmax, wy[i][t] >= 0, wy[i][t] <= inst.ymax)
            for k, (x, y) in enumerate(w.loop, 1):
                s.add(z3.Implies(wt[i][t] == k, z3.And(wx[i][t] == x, wy[i][t] == y)))
            if rt:
                s.add(z3.Not(_blocked(inst, rt[0], rt[1], wrap(t, o.ext.period), wx[i][t], wy[i][t])))
        for t in range(1, T):
            amt = AM[(i, t)] = z3.Int(f"amt{i}_{t}")
            s.add(wp[i][t] >= 0, wp[i][t] <= 2, amt > 0, amt <= inst.delta_max, ra[i][t] >= 1, ra[i][t] <= R)
            if o.cost_by == "time":
                cost = w.req[(t - 1) % L]
            else:
                cost = z3.Sum([z3.If(wt[i][t] == k, w.req[k - 1], 0) for k in range(1, L + 1)])
            nxt = z3.If(wt[i][t] < L, wt[i][t] + 1, 1)
            s.add(z3.Implies(wp[i][t] == 1, z3.And(wt[i][t + 1] == nxt, ch[i][t + 1] == ch[i][t] - cost)))
            s.add(z3.Implies(wp[i][t] == 0, z3.And(wt[i][t + 1] == wt[i][t], ch[i][t + 1] == ch[i][t])))
            place = [z3.And(rp[r][t] == wk, ra[i][t] == r, rx[r][t] == wx[i][t] + ox, ry[r][t] == wy[i][t] + oy)
                     for r in range(1, R + 1) for ox, oy in OFFS8]
            s.add(z3.Implies(wp[i][t] == 2, z3.And(wt[i][t + 1] == wt[i][t], ch[i][t] < w.fch,
                                                   ch[i][t + 1] == ch[i][t] + amt, z3.Or(place))))
            if o.wait_vars:
                wc, rcn = z3.Int(f"wc{i}_{t}"), z3.Int(f"rc{i}_{t}")
                s.add(wc >= 0, wc <= 1, rcn >= 0, rcn <= 1)
                s.add(z3.Implies(wp[i][t] == 0, z3.And(wc == 1, rcn == 0)),
                      z3.Implies(wp[i][t] == 2, z3.And(wc == 1, rcn == 1)),
                      z3.Implies(wp[i][t] == 1, z3.And(wc == 0, rcn == 0)))
                wait_terms.append(wc)
            else:
                wait_terms.append(z3.If(wp[i][t] == 1, 0, 1))
    if o.ext:
        for i, w in enumerate(W, 1):
            for edge in range(len(w.loop)):
                cells = worker_edge_cells(inst, w, edge)
                for t in range(1, T):
                    if cells & o.ext.time_rech[wrap(t, o.ext.period)]:
                        s.add(z3.Not(z3.And(wp[i][t] == 1, wt[i][t] == edge + 1)))
    if o.exclusive_recharge:
        for t in range(1, T):
            for a in range(1, nW + 1):
                for b in range(a + 1, nW + 1):
                    s.add(z3.Not(z3.And(wp[a][t] == 2, wp[b][t] == 2, ra[a][t] == ra[b][t])))
    if o.collisions:
        # A recharger's complete swept footprint must avoid both endpoint cells of
        # every worker transition (the input model does not encode worker sub-cell paths).
        for r in range(1, R + 1):
            for i in range(1, nW + 1):
                for t in range(1, T):
                    for k, pr in enumerate(inst.prims, 1):
                        for sx, sy in pr.swath:
                            for endpoint_t in (t, t + 1):
                                s.add(z3.Implies(rp[r][t] == k,
                                                 z3.Or(wx[i][endpoint_t] != rx[r][t] + sx,
                                                       wy[i][endpoint_t] != ry[r][t] + sy)))
        for i, w in enumerate(W, 1):
            for edge in range(len(w.loop)):
                cells = worker_edge_cells(inst, w, edge)
                for r in range(1, R + 1):
                    for t in range(1, T):
                        for k, pr in enumerate(inst.prims, 1):
                            clear = [z3.Or(rx[r][t] + sx != x, ry[r][t] + sy != y)
                                     for sx, sy in pr.swath for x, y in cells]
                            s.add(z3.Implies(z3.And(wp[i][t] == 1, wt[i][t] == edge + 1,
                                                  rp[r][t] == k), z3.And(clear)))
        for r1 in range(1, R + 1):
            for r2 in range(r1 + 1, R + 1):
                for t in range(1, T):
                    _add_sweeps_disjoint(s, rx, ry, rp, r1, r2, t, inst.prims)
    if fixed:                                   # cross-check hook: pin variables using the repo's names
        named = {}
        for i in wt:
            for t in range(1, T + 1):
                named.update({f"wtraj_{i}_{t}": wt[i][t], f"wx_{i}_{t}": wx[i][t], f"wy_{i}_{t}": wy[i][t], f"ch_{i}_{t}": ch[i][t]})
            for t in range(1, T):
                named.update({f"wprim_{i}_{t}": wp[i][t], f"rechassigned_{i}_{t}": ra[i][t], f"rech_{i}_{t}": AM[(i, t)]})
        for r in range(1, R + 1):
            for t in range(1, T + 1):
                named.update({f"rx_{r}_{t}": rx[r][t], f"ry_{r}_{t}": ry[r][t]})
            for t in range(1, T):
                named[f"rprim_{r}_{t}"] = rp[r][t]
        for n, v in fixed.items():
            if n in named:
                s.add(named[n] == v)
    if o.mode == "one_shot":                    # repo one-shot matching: same charge, same recharger location
        for i, w in enumerate(W, 1):
            s.add(ch[i][T] == ch[i][1])
        for r in range(1, R + 1):
            s.add(rx[r][T] == rx[r][1], ry[r][T] == ry[r][1])
    total = z3.Sum(wait_terms) if wait_terms else z3.IntVal(0)
    if o.wait_vars:
        tv = z3.Int("total_wait")
        s.add(tv == total)
        total = tv
    s.minimize(total)
    if o.mode != "one_shot":
        completion_objectives(s, inst, o, rx, ry, rp, ch, T)
    if not _checked_sat(s):
        return None
    m = s.model()
    ev = lambda e: m.eval(e, model_completion=True).as_long()
    p1 = dict(T=T, wp={i: [ev(wp[i][t]) for t in range(1, T)] for i in wp},
              wx={i: [ev(wx[i][t]) for t in range(1, T + 1)] for i in wx},
              wy={i: [ev(wy[i][t]) for t in range(1, T + 1)] for i in wy},
              ch={i: [ev(ch[i][t]) for t in range(1, T + 1)] for i in ch},
              ra={i: [ev(ra[i][t]) for t in range(1, T)] for i in ra},
              rx={r: [ev(rx[r][t]) for t in range(1, T + 1)] for r in range(1, R + 1)},
              ry={r: [ev(ry[r][t]) for t in range(1, T + 1)] for r in range(1, R + 1)},
              wait=ev(total),
              wt={i: [ev(wt[i][t]) for t in range(1, T + 1)] for i in wt},
              rp={r: [ev(rp[r][t]) for t in range(1, T)] for r in range(1, R + 1)},
              amt={i: [ev(AM[(i, t)]) for t in range(1, T)] for i in wt})
    return p1

def solve(inst: Instance, o: Optional[Options] = None) -> Result:
    o = o or Options()
    if o.mode == "one_shot" and o.align_M:       # forced hypercycle: the one-shot horizon IS T'
        from dataclasses import replace as _rep
        inst = _rep(inst, T=o.align_M)
    t0 = time.time()
    def progress(message):
        if os.environ.get("MRP_PROGRESS") == "1":
            print(f"[solver pid={os.getpid()} workers={len(inst.workers)} rechargers={inst.n_rech}] {message}", flush=True)
    progress(f"phase 1 started; T={inst.T}; mode={o.mode}")
    try:
        p1 = phase1(inst, o)
    except SolverUnknown as exc:
        return Result(False, T=inst.T, status="UNKNOWN", t_phase1=time.time() - t0,
                      note=f"phase 1 inconclusive: {exc}")
    t1 = time.time()
    progress(f"phase 1 finished in {t1 - t0:.1f}s")
    if p1 is None:
        return Result(False, T=inst.T, status="UNSAT", t_phase1=t1 - t0,
                      note="phase 1 UNSAT at the configured horizon and constraints")
    if o.mode == "one_shot":
        return _one_shot_result(inst, p1, t1 - t0)
    halts, instances = _halts_and_instances(inst, p1)
    start = max(h[0] for h in halts.values()) + max(h[2] for h in halts.values())
    Ms = [o.align_M] if o.align_M else range(max(start, inst.T), inst.T + o.max_extra + 1)
    out = None
    for M in Ms:
        if M < start:
            continue
        progress(f"phase 2 started; T'={M}; cost optimization={o.phase2_optimize_cost}")
        try:
            out = phase2(inst, o, p1, halts, instances, M)
        except SolverUnknown as exc:
            # Skipping an inconclusive smaller horizon would invalidate a claim
            # that the eventual larger horizon is the smallest feasible one.
            return Result(False, T=inst.T, M=M, status="UNKNOWN",
                          t_phase1=t1 - t0, t_phase2=time.time() - t1,
                          note=f"phase 2 inconclusive at T'={M}: {exc}")
        if out:
            progress(f"phase 2 feasible at T'={M}")
            break
        progress(f"phase 2 infeasible at T'={M} for the selected phase-1 schedule")
    t2 = time.time()
    if out is None:
        status = "UNSAT" if o.align_M is not None else "SEARCH_EXHAUSTED"
        note = (f"phase 2 UNSAT at fixed T'={o.align_M} for the selected phase-1 schedule"
                if o.align_M is not None else
                f"no phase-2 plan through T'={inst.T + o.max_extra} for the selected phase-1 schedule")
        return Result(False, T=inst.T, status=status, t_phase1=t1 - t0,
                      t_phase2=t2 - t1, note=note)
    rpos, rprim, last = out
    nW, T = len(inst.workers), inst.T
    wpos = {}
    for i, w in enumerate(inst.workers, 1):
        wpos[w.id] = [None] + [(p1["wx"][i][t - 1], p1["wy"][i][t - 1]) if t <= T else w.loop[0] for t in range(1, M + 1)]
    rsweep = {r: [None] + [{(rpos[r][t][0] + sx, rpos[r][t][1] + sy) for sx, sy in inst.prims[rprim[r][t] - 1].swath}
                            for t in range(1, M)] for r in rpos}
    lastrech = sum(h[2] for h in halts.values())
    hard_wait = p1["wait"] + (M - T) * nW
    res = Result(True, T=T, M=M, nW=nW, wpos=wpos, rpos=rpos, rsweep=rsweep,
                 instances=[(inst.workers[i - 1].id, t, r, p) for i, t, r, p in instances],
                 halts=halts, last=last, p1_wait=p1["wait"], t_phase1=t1 - t0, t_phase2=t2 - t1)
    res.hard_eff = 100.0 * (nW * M - hard_wait) / (nW * M)
    res.primitive_cost = sum(inst.prims[k - 1].cost for steps in rprim.values() for k in steps[1:])
    res.wsweep = {w.id: [None] + [(worker_edge_cells(inst, w, p1["wt"][i][t - 1] - 1)
                                  if t < T and p1["wp"][i][t - 1] == 1 else {wpos[w.id][t]})
                                 for t in range(1, M)] for i, w in enumerate(inst.workers, 1)}
    res.soft_eff = 100.0 * (nW * M - (hard_wait - len(instances) - lastrech)) / (nW * M)
    return res

def solve_isolated(inst: Instance, o: Optional[Options] = None) -> Result:
    """Solve in a fresh process so Z3's global state (and thus tie-breaking among equally optimal
    models) cannot depend on what was solved earlier -> reproducible results."""
    import multiprocessing as mp
    with mp.get_context("spawn").Pool(1, maxtasksperchild=1) as pool:
        return pool.apply(solve, (inst, o or Options()))
