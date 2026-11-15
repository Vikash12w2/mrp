from __future__ import annotations
from collections import deque
from dataclasses import dataclass
from .bus import Bus
from .model import Instance, OFFS8, WorkerSpec
from .params import Params

INF = float("inf")


def box_of(inst: Instance, anchor, R):
    return (max(0, anchor[0] - R), min(inst.xmax, anchor[0] + R), max(0, anchor[1] - R), min(inst.ymax, anchor[1] + R))


def step_dist(inst: Instance, src, box):
    x0, x1, y0, y1 = box
    ok = lambda c: x0 <= c[0] <= x1 and y0 <= c[1] <= y1 and inst.free(c)
    dist, q = {src: 0}, deque([src])
    while q:
        c = q.popleft()
        for p in inst.prims:
            if p.dx == 0 and p.dy == 0:
                continue
            n = (c[0] + p.dx, c[1] + p.dy)
            if n in dist or not all(ok((c[0] + sx, c[1] + sy)) for sx, sy in p.swath):
                continue
            dist[n] = dist[c] + 1
            q.append(n)
    return dist


def t_empty_of(w: WorkerSpec, charge=None) -> int:
    e, k, L = w.fch if charge is None else charge, 0, len(w.loop)
    while e >= w.req[k % L]:
        e -= w.req[k % L]
        k += 1
        if k > 10_000:
            break
    return k


class Agent:
    def __init__(self, rid, anchor, inst: Instance, P: Params):
        self.id, self.anchor, self.inst, self.P = rid, anchor, inst.sub([], 1, inst.T), P
        self.box = box_of(inst, anchor, P.R_s)
        self.dist = step_dist(inst, anchor, self.box)
        self.heard, self.cand, self.util = {}, {}, {}
        self.neighbors, self.neigh_cand, self.view, self.claims = [], {}, {}, {}

    def in_region(self, c):
        x0, x1, y0, y1 = self.box
        return x0 <= c[0] <= x1 and y0 <= c[1] <= y1

    def reach(self, cell):
        return min((self.dist[(cell[0] + ox, cell[1] + oy)] for ox, oy in OFFS8
                    if (cell[0] + ox, cell[1] + oy) in self.dist), default=None)

    def score(self, w: WorkerSpec, charge=None) -> float:
        te, L, P = t_empty_of(w, charge), len(w.loop), self.P
        t_arr = None
        for k in range(te + 1):
            d = self.reach(w.loop[k % L])
            if d is not None and d <= k:
                t_arr = d
                break
        if t_arr is None:
            d = self.reach(w.loop[te % L])
            if d is None:
                return INF
            t_arr = d
        return P.u1 * max(0, t_arr - te) + P.u2 * t_arr + P.u3 * max(0, te - t_arr)

    def util_share(self, w: WorkerSpec) -> float:
        ds = [d for d in (self.reach(c) for c in w.loop) if d is not None]
        if not ds:
            return INF
        cbar = sum(w.req) / len(w.req)
        return cbar / self.inst.delta_max + (cbar / w.fch) * (sum(ds) / len(ds))

    def hear(self, w: WorkerSpec):
        self.heard[w.id] = w
        self.inst = self.inst.sub(list(self.heard.values()), 1, self.inst.T)
        if all(self.in_region(c) for c in w.loop):
            s, u = self.score(w), self.util_share(w)
            if s < INF and u < INF:
                self.cand[w.id], self.util[w.id] = s, u

    def fits(self, members, extra=None, drop=None) -> bool:
        ids = set(members)
        if extra is not None:
            ids.add(extra)
        if drop is not None:
            ids.discard(drop)
        return ids.issubset(self.cand) and (self.P.cap_n is None or len(ids) <= self.P.cap_n)

    def select_claims(self) -> dict:
        lost = {w for nb, cl in self.view.items() for w, s in cl.items()
                if w in self.cand and (s, nb) < (self.cand[w], self.id)}
        claims, ids = {}, []
        for w in sorted(self.cand, key=lambda w: (self.cand[w], w)):
            if w in lost or not self.fits(ids, extra=w):
                continue
            claims[w] = self.cand[w]
            ids.append(w)
        return claims


@dataclass
class GroupingResult:
    groups: dict
    unclaimed: dict
    agents: dict


def _local_grouping(agents, bus, P):
    from .negotiation import Negotiator, assign
    actors = [Negotiator(a, (), bus) for a in agents.values()]
    assign(actors, bus, P)
    # Output aggregation only: these dictionaries never select assignments.
    for actor in actors:
        actor.agent.claims = {w: actor.agent.cand[w] for w in actor.members}
    return {actor.rid: set(actor.members) for actor in actors}


def summarize_unclaimed(inst, groups, agents):
    owned = {w for members in groups.values() for w in members}
    result = {}
    for w in inst.workers:
        if w.id in owned:
            continue
        hearing = [a for a in agents.values() if w.id in a.heard]
        eligible = [a for a in hearing if w.id in a.cand]
        if not hearing:
            reason = "outside_all_regions"
        elif not any(all(a.in_region(c) for c in w.loop) for a in hearing):
            reason = "loop_not_inside_any_region"
        elif not eligible:
            reason = "unreachable_in_region"
        elif any(a.fits(groups[a.id], extra=w.id) for a in eligible):
            reason = "negotiation_incomplete"
        else:
            reason = "capacity"
        result[w.id] = reason
    return result


def run_grouping(inst: Instance, rechargers: dict, P: Params, bus: Bus) -> GroupingResult:
    agents = {rid: Agent(rid, a, inst, P) for rid, a in rechargers.items()}
    for a in agents.values():
        bus.broadcast(a.id, "beacon", {"anchor": a.anchor})
    for a in agents.values():
        a.neighbors = sorted({m.src for m in bus.drain(a.id, "beacon")})
    for w in sorted(inst.workers, key=lambda w: w.id):          # passive worker broadcasts
        for a in agents.values():
            if any(a.in_region(c) for c in w.loop):
                bus.stats["worker_broadcast"] += 1
                a.hear(w)
    for a in agents.values():
        for nb in a.neighbors:
            bus.send(a.id, nb, "candidates", dict(a.cand))
    for a in agents.values():
        for m in bus.drain(a.id, "candidates"):
            a.neigh_cand[m.src] = m.payload
    groups = _local_grouping(agents, bus, P)
    unclaimed = summarize_unclaimed(inst, groups, agents)
    return GroupingResult({rid: sorted(g) for rid, g in groups.items()}, unclaimed, agents)
