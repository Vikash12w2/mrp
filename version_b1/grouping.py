from dataclasses import dataclass, replace
from fractions import Fraction
import time
import z3
from ..grouping import Agent, GroupingResult, summarize_unclaimed
from ..params import Params


@dataclass
class CentralGrouping:
    grouping: GroupingResult
    params: Params
    status: str
    reason: str
    seconds: float
    objectives: dict
    scope: str


def group_workers(inst, anchors, params=None, timeout_ms=0, *, excluded=(), min_assigned=0):
    started = time.monotonic()
    p = params or Params()
    if timeout_ms < 0 or inst.delta_max <= 0 or inst.T < 4:
        raise ValueError('require timeout_ms >= 0, delta_max > 0 and T >= 4')
    if len(anchors) != inst.n_rech:
        raise ValueError('anchor count must equal instance recharger count')
    if len(set(anchors.values())) != len(anchors) or any(not inst.free(c) for c in anchors.values()):
        raise ValueError('anchors must be distinct free map cells')
    ids = [w.id for w in inst.workers]
    if len(set(ids)) != len(ids):
        raise ValueError('worker IDs must be unique')
    for w in inst.workers:
        if (not w.loop or len(w.req) != len(w.loop) or w.fch <= 0
                or any(e < 0 for e in w.req) or any(not inst.free(c) for c in w.loop)):
            raise ValueError(f'invalid loop, energy or capacity for worker {w.id}')
    # B1 deliberately has no cap, including explicit Version A count limits.
    p = replace(p, cap_n=None, K_refine=0,
                R_s=max(inst.xmax, inst.ymax, 1))
    if p.R_s < 0:
        raise ValueError('R_s must be nonnegative')
    agents = {r: Agent(r, anchors[r], inst, p) for r in sorted(anchors)}
    for a in agents.values():
        for w in inst.workers:
            if any(a.in_region(c) for c in w.loop):
                a.hear(w)
    opt = z3.Optimize()
    opt.set(priority='lex')
    if timeout_ms:
        opt.set(timeout=timeout_ms)
    x = {(r, w): z3.Bool(f'assign_{r}_{w}') for r, a in agents.items() for w in sorted(a.cand)}
    for w in ids:
        opt.add(z3.Sum([z3.IntVal(0)] + [z3.If(v, 1, 0) for (r, wid), v in x.items() if wid == w]) <= 1)
    assigned = z3.Sum([z3.IntVal(0)] + [z3.If(v, 1, 0) for v in x.values()])
    opt.add(assigned >= min_assigned)
    # Exclude only the exact previously tried partition. A failed bounded SMT
    # attempt does not prove that its worker subset is inherently infeasible.
    for groups in excluded:
        opt.add(z3.Or([z3.Not(v) if w in groups.get(r, ()) else v
                       for (r, w), v in x.items()]))
    counts = {r: z3.Sum([z3.IntVal(0)] + [z3.If(x[r, w], 1, 0) for w in a.cand]) for r, a in agents.items()}
    # Exact linear encoding of integer count squared, avoiding nonlinear SMT.
    balance = z3.Sum([z3.IntVal(0)] + [z3.If(counts[r] >= k, 2*k-1, 0)
                      for r, a in agents.items() for k in range(1, len(a.cand)+1)])
    demand = {w.id: Fraction(sum(w.req), len(w.req) * inst.delta_max) for w in inst.workers}
    real = lambda q: z3.RealVal(f'{q.numerator}/{q.denominator}')
    loads = {r: z3.Sum([z3.IntVal(0)] + [z3.If(x[r, w], real(demand[w]), 0) for w in a.cand]) for r, a in agents.items()}
    imbalance = z3.Sum([z3.IntVal(0)] + [z3.If(loads[r] >= loads[s], loads[r]-loads[s], loads[s]-loads[r])
                        for r in agents for s in agents if r < s])
    distances = {}
    by_id = {w.id: w for w in inst.workers}
    for r, a in agents.items():
        for w in a.cand:
            ds = [a.reach(c) for c in by_id[w].loop]
            reachable = [d for d in ds if d is not None]
            distances[r, w] = Fraction(sum(reachable), len(reachable))
    travel = z3.Sum([z3.IntVal(0)] + [z3.If(x[pair], real(d), 0) for pair, d in distances.items()])
    opt.maximize(assigned)
    opt.minimize(balance)
    opt.minimize(imbalance)
    opt.minimize(travel)
    # Stable individual ownership after all substantive objectives are fixed.
    rank = {r: i+1 for i, r in enumerate(agents)}
    for w in sorted(ids):
        opt.minimize(z3.Sum([z3.IntVal(0)] + [z3.If(v, rank[r], 0) for (r, wid), v in x.items() if wid == w]))
    outcome = opt.check()
    groups = {r: [] for r in agents}
    objectives = {}
    if outcome == z3.sat:
        model = opt.model()
        for (r, w), variable in x.items():
            if z3.is_true(model.eval(variable, model_completion=True)):
                groups[r].append(w)
        status, reason = 'OPTIMAL', ''
        objectives = dict(assigned=sum(map(len, groups.values())),
                          count_square=sum(len(v)**2 for v in groups.values()),
                          demand_loads={r: float(sum((demand[w] for w in members), Fraction()))
                                        for r, members in groups.items()},
                          distance=float(sum((distances[r, w] for r, members in groups.items() for w in members), Fraction())))
    else:
        status = 'UNKNOWN' if outcome == z3.unknown else 'UNSAT'
        reason = opt.reason_unknown() if outcome == z3.unknown else 'assignment constraints unsatisfiable'
    missing = summarize_unclaimed(inst, groups, agents)
    for w, why in list(missing.items()):
        if why == 'negotiation_incomplete':
            missing[w] = 'grouping_unknown' if status == 'UNKNOWN' else 'not_assigned'
    for r, a in agents.items():
        a.claims = {w: a.cand[w] for w in groups[r]}
    return CentralGrouping(GroupingResult(groups, missing, agents), p, status, reason,
                           time.monotonic()-started, objectives, 'global')
