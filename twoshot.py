from __future__ import annotations
import math
import os
import time
from dataclasses import dataclass, field
from typing import Optional
import z3
from .model import Instance, OFFS8, worker_edge_cells, worker_transition_cells


@dataclass
class Ext:
    """Higher-priority occupancy, wrapped time index 1..period (period = T'-1)."""
    period: int
    step_all: list        # [t] cells our recharger's sweep must avoid during step t
    time_rech: list       # [t] cells our workers must avoid at time t


@dataclass
class Options:
    collisions: bool = True              # swept-cell checks (the reference repo leaves collision calls commented out)
    exclusive_recharge: bool = False
    cost_by: str = "time"
    box: Optional[tuple] = None          # (x0, x1, y0, y1) recharger region
    p_starts: Optional[tuple] = None     # allowed recharger start cells in phase 1
    ext: Optional[Ext] = None
    align_M: Optional[int] = None        # force T' (= M) to this value
    max_extra: int = 50                  # M searched up to T + max_extra
    seed: int = 0
    rlimit: int = 0
    mode: str = "two_shot"              # "one_shot": the repo's monolithic variant (phase 1 over a fixed hypercycle + matching)
    phase2_optimize_cost: bool = False    # optional paper variant: minimize total recharger primitive cost Y
    wait_vars: bool = True               # repo style: waitcount/rechcount Int vars, total_wait = sum
    move_vars: bool = True               # repo style: displacement helper vars rx_f/ry_f


@dataclass
class Result:
    ok: bool
    T: int = 0
    M: int = 0
    nW: int = 0
    wpos: dict = field(default_factory=dict)      # wid -> [None, p1..pM]
    rpos: dict = field(default_factory=dict)      # rid -> [None, p1..pM]
    rsweep: dict = field(default_factory=dict)    # rid -> [None, cells swept during step 1..M-1]
    instances: list = field(default_factory=list) # (wid, t, rid, rpos)
    halts: dict = field(default_factory=dict)     # wid -> (timept, charge, duration)
    last: list = field(default_factory=list)      # (wid, start_t, duration)
    p1_wait: int = 0
    hard_eff: float = 0.0
    soft_eff: float = 0.0
    t_phase1: float = 0.0
    t_phase2: float = 0.0
    note: str = ""
    wsweep: dict = field(default_factory=dict)
    status: str = ""
    primitive_cost: float = 0.0

    def __post_init__(self):
        if not self.status:
            self.status = "SAT" if self.ok else "NO_PLAN"


class SolverUnknown(RuntimeError):
    """The solver did not establish feasibility or infeasibility."""


def _checked_sat(s):
    outcome = s.check()
    if outcome == z3.unknown:
        raise SolverUnknown(s.reason_unknown())
    return outcome == z3.sat


def wrap(t: int, period: int) -> int:
    return ((t - 1) % period) + 1


def _cfg(s, o: Options):
    z3.set_param("smt.random_seed", o.seed)
    z3.set_param("sat.random_seed", o.seed)
    if o.rlimit:
        try:
            s.set("rlimit", o.rlimit)
        except Exception:
            pass


def _arr(inst: Instance, sets: list):
    """Z3 array: index (t, x, y) -> True if blocked."""
    K = (inst.xmax + 1) * (inst.ymax + 1)
    a = z3.K(z3.IntSort(), False)
    for t in range(1, len(sets)):
        for (x, y) in sets[t]:
            if 0 <= x <= inst.xmax and 0 <= y <= inst.ymax:
                a = z3.Store(a, t * K + x * (inst.ymax + 1) + y, True)
    return a, K


def _blocked(inst, arr, K, t, x, y):
    return z3.Select(arr, t * K + x * (inst.ymax + 1) + y)


def _obstacle_fn(inst, s):
    f = z3.Function("obstacle", z3.IntSort(), z3.IntSort(), z3.BoolSort())
    for x in range(inst.xmax + 1):
        for y in range(inst.ymax + 1):
            s.add(f(x, y) == ((x, y) in inst.obstacles))
    return f


def _bounds(inst, o):
    return o.box if o.box else (0, inst.xmax, 0, inst.ymax)


def _swath_conflict_offsets(a, b):
    """Relative origin offsets for which two primitive swaths share a cell."""
    return {(ax - bx, ay - by) for ax, ay in a.swath for bx, by in b.swath}


def _add_sweeps_disjoint(s, rx, ry, rp, r1, r2, t, prims):
    """Forbid two selected primitive sweeps from sharing any grid cell."""
    for k1, p1 in enumerate(prims, 1):
        for k2, p2 in enumerate(prims, 1):
            offsets = _swath_conflict_offsets(p1, p2)
            outside = [z3.Or(rx[r2][t] - rx[r1][t] != dx,
                             ry[r2][t] - ry[r1][t] != dy)
                       for dx, dy in offsets]
            s.add(z3.Implies(z3.And(rp[r1][t] == k1, rp[r2][t] == k2), z3.And(outside)))


def _recharger_block(inst, s, obstacle, o, M, tag):
    R, P = inst.n_rech, len(inst.prims)
    x0, x1, y0, y1 = _bounds(inst, o)
    rx = [None] + [[None] + [z3.Int(f"rx{tag}_{r}_{t}") for t in range(1, M + 1)] for r in range(1, R + 1)]
    ry = [None] + [[None] + [z3.Int(f"ry{tag}_{r}_{t}") for t in range(1, M + 1)] for r in range(1, R + 1)]
    rp = [None] + [[None] + [z3.Int(f"rp{tag}_{r}_{t}") for t in range(1, M)] for r in range(1, R + 1)]
    ext = _arr(inst, o.ext.step_all) if o.ext else None
    for r in range(1, R + 1):
        for t in range(1, M + 1):
            s.add(rx[r][t] >= x0, rx[r][t] <= x1, ry[r][t] >= y0, ry[r][t] <= y1)
        for t in range(1, M):
            s.add(rp[r][t] >= 1, rp[r][t] <= P)
            if o.move_vars:
                fx, fy = z3.Int(f"rxf{tag}_{r}_{t}"), z3.Int(f"ryf{tag}_{r}_{t}")
                s.add(rx[r][t + 1] == rx[r][t] + fx, ry[r][t + 1] == ry[r][t] + fy)
            for k, pr in enumerate(inst.prims, 1):
                c = [fx == pr.dx, fy == pr.dy] if o.move_vars else [rx[r][t + 1] == rx[r][t] + pr.dx, ry[r][t + 1] == ry[r][t] + pr.dy]
                for sx, sy in pr.swath:
                    x, y = rx[r][t] + sx, ry[r][t] + sy
                    c += [x >= 0, x <= inst.xmax, y >= 0, y <= inst.ymax,
                          x >= x0, x <= x1, y >= y0, y <= y1,
                          z3.Not(obstacle(x, y))]
                if ext:
                    c += [z3.Not(_blocked(inst, ext[0], ext[1], wrap(t, o.ext.period), rx[r][t] + sx, ry[r][t] + sy))
                          for sx, sy in pr.swath]
                s.add(z3.Implies(rp[r][t] == k, z3.And(c)))
    return rx, ry, rp


def _wait_prim(inst) -> int:
    return next(k for k, p in enumerate(inst.prims, 1) if p.dx == 0 and p.dy == 0)


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


def _halts_and_instances(inst, p1):
    halts, instances = {}, []
    for i, w in enumerate(inst.workers, 1):
        c = p1["ch"][i]
        t_h = 1
        for t in range(2, len(c) + 1):                    # last time the charge value changed
            if c[t - 1] != c[t - 2]:
                t_h = t
        charge = c[t_h - 1]
        halts[w.id] = (t_h, charge, math.ceil((w.fch - charge) / inst.delta_max))
        for t in range(1, inst.T):
            if p1["wp"][i][t - 1] == 2:
                r = p1["ra"][i][t - 1]
                instances.append((i, t, r, (p1["rx"][r][t - 1], p1["ry"][r][t - 1])))
    return halts, instances


def phase2(inst: Instance, o: Options, p1, halts, instances, M: int):
    s = z3.Optimize()
    _cfg(s, o)
    obstacle = _obstacle_fn(inst, s)
    R, W = inst.n_rech, inst.workers
    rx, ry, rp = _recharger_block(inst, s, obstacle, o, M, "b")
    for (i, t, r, pos) in instances:                       # intermediate recharging from shot 1
        s.add(rx[r][t] == pos[0], ry[r][t] == pos[1], rx[r][t + 1] == pos[0], ry[r][t + 1] == pos[1])
    for r in range(1, R + 1):
        # Phase 1 chooses the initial recharger location; phase 2 must preserve it.
        s.add(rx[r][1] == p1["rx"][r][0], ry[r][1] == p1["ry"][r][0])
        s.add(rx[r][M] == rx[r][1], ry[r][M] == ry[r][1])
        if o.p_starts:
            s.add(z3.Or([z3.And(rx[r][1] == c[0], ry[r][1] == c[1]) for c in o.p_starts]))
    T = p1["T"]
    if o.collisions:
        # Phase 2 fixes workers to their phase-1 path through T, then at their
        # loop start. Check each recharger sweep against both endpoint cells.
        for r in range(1, R + 1):
            for i in range(1, len(W) + 1):
                for t in range(1, M):
                    x0w, y0w = p1["wx"][i][min(t, T) - 1], p1["wy"][i][min(t, T) - 1]
                    x1w, y1w = p1["wx"][i][min(t + 1, T) - 1], p1["wy"][i][min(t + 1, T) - 1]
                    wcells = (worker_edge_cells(inst, W[i - 1], p1["wt"][i][t - 1] - 1)
                              if t < T and p1["wp"][i][t - 1] == 1 else {(x0w, y0w)})
                    for k, pr in enumerate(inst.prims, 1):
                        for sx, sy in pr.swath:
                            for xw, yw in wcells:
                                s.add(z3.Implies(rp[r][t] == k,
                                                 z3.Or(xw != rx[r][t] + sx,
                                                       yw != ry[r][t] + sy)))
        for r1 in range(1, R + 1):
            for r2 in range(r1 + 1, R + 1):
                for t in range(1, M):
                    _add_sweeps_disjoint(s, rx, ry, rp, r1, r2, t, inst.prims)
    flags = {}
    for i, w in enumerate(W, 1):
        t_h, _, d = halts[w.id]
        dur = d + 1
        if o.ext:                                          # final resting cell vs higher-priority rechargers
            for t in range(t_h, M + 1):
                if w.loop[0] in o.ext.time_rech[wrap(t, o.ext.period)]:
                    return None
        starts = list(range(t_h, M - dur + 2))
        if not starts:
            return None
        for st in starts:
            flags[(i, st)] = z3.Bool(f"lr{i}_{st}")
            opts = []
            for r in range(1, R + 1):
                for ox, oy in OFFS8:
                    c = (w.loop[0][0] + ox, w.loop[0][1] + oy)
                    if c in inst.obstacles:
                        continue
                    opts.append(z3.And([z3.And(rx[r][st + k] == c[0], ry[r][st + k] == c[1]) for k in range(dur)]))
            s.add(z3.Implies(flags[(i, st)], z3.Or(opts) if opts else z3.BoolVal(False)))
        s.add(z3.Sum([z3.If(flags[(i, st)], 1, 0) for st in starts]) == 1)
    if o.phase2_optimize_cost:
        # Optional paper variant: for the first feasible M, minimize total
        # recharger primitive cost Y. The outer loop separately minimizes T'.
        path_cost = z3.Sum([
            z3.If(rp[r][t] == k, z3.RealVal(str(pr.cost)), z3.RealVal(0))
            for r in range(1, R + 1)
            for t in range(1, M)
            for k, pr in enumerate(inst.prims, 1)
        ])
        s.minimize(path_cost)
    if not _checked_sat(s):
        return None
    m = s.model()
    ev = lambda e: m.eval(e, model_completion=True).as_long()
    rpos = {r: [None] + [(ev(rx[r][t]), ev(ry[r][t])) for t in range(1, M + 1)] for r in range(1, R + 1)}
    rprim = {r: [None] + [ev(rp[r][t]) for t in range(1, M)] for r in range(1, R + 1)}
    last = [(W[i - 1].id, st, halts[W[i - 1].id][2]) for (i, st), f in flags.items()
            if z3.is_true(m.eval(f, model_completion=True))]
    return rpos, rprim, last


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


def _one_shot_result(inst: Instance, p1, t_phase1: float) -> Result:
    """Result of the one-shot mode: M = T = H, no phase 2. Recharge steps are listed in `instances`."""
    T, W = inst.T, inst.workers
    nW = len(W)
    wpos = {w.id: [None] + [(p1["wx"][i][t], p1["wy"][i][t]) for t in range(T)] for i, w in enumerate(W, 1)}
    rpos = {r: [None] + [(p1["rx"][r][t], p1["ry"][r][t]) for t in range(T)] for r in p1["rx"]}
    rsweep = {r: [None] + [{(rpos[r][t][0] + sx, rpos[r][t][1] + sy) for sx, sy in inst.prims[p1["rp"][r][t - 1] - 1].swath}
                            for t in range(1, T)] for r in rpos}
    inst_list = [(w.id, t, p1["ra"][i][t - 1], rpos[p1["ra"][i][t - 1]][t])
                 for i, w in enumerate(W, 1) for t in range(1, T) if p1["wp"][i][t - 1] == 2]
    res = Result(True, T=T, M=T, nW=nW, wpos=wpos, rpos=rpos, rsweep=rsweep, instances=inst_list,
                 p1_wait=p1["wait"], t_phase1=t_phase1, note="one-shot")
    res.hard_eff = 100.0 * (nW * T - p1["wait"]) / (nW * T)
    res.primitive_cost = sum(inst.prims[k - 1].cost for steps in p1["rp"].values() for k in steps)
    res.wsweep = {w.id: [None] + [(worker_edge_cells(inst, w, p1["wt"][i][t - 1] - 1)
                                  if p1["wp"][i][t - 1] == 1 else {wpos[w.id][t]})
                                 for t in range(1, T)] for i, w in enumerate(W, 1)}
    res.soft_eff = 100.0 * (nW * T - (p1["wait"] - len(inst_list))) / (nW * T)
    return res


def solve_isolated(inst: Instance, o: Optional[Options] = None) -> Result:
    """Solve in a fresh process so Z3's global state (and thus tie-breaking among equally optimal
    models) cannot depend on what was solved earlier -> reproducible results."""
    import multiprocessing as mp
    with mp.get_context("spawn").Pool(1, maxtasksperchild=1) as pool:
        return pool.apply(solve, (inst, o or Options()))
