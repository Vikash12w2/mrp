from __future__ import annotations
from collections import deque
from itertools import combinations
from math import lcm
from .model import Instance, worker_edge_cells
from .params import default_Rs
from .twoshot import wrap


def verify(plans: dict, cap: int = 3000) -> list:
    live = [g for g in plans.values() if g and g.live]
    parked = [g for g in plans.values() if g and not g.live]
    periods = [g.res.M - 1 for g in live]
    H = min(lcm(*periods), cap) if periods else 1
    out = []
    for t in range(1, H + 1):
        rob = []
        for g in live:
            tt = wrap(t, g.res.M - 1)
            for wid, pos in g.res.wpos.items():
                cells = g.res.wsweep[wid][tt] if wid in g.res.wsweep else {pos[tt], pos[tt + 1]}
                rob.append((("W", wid), g.rid, "W", cells))
            for r in g.res.rpos:
                rob.append((("R", g.rid, r), g.rid, "R", g.res.rsweep[r][tt]))
        for g in parked:
            rob.append((("R", g.rid, 1), g.rid, "R", {g.anchor}))
        for a, b in combinations(rob, 2):
            if a[2] == "W" and b[2] == "W":
                continue
            if a[3] & b[3]:
                out.append((t, a[0], b[0], "same_group" if a[1] == b[1] else "cross_group"))
    return out


class Arbiter:
    def __init__(self, inst: Instance, priority: dict, neighbors: dict | None = None):
        self.inst, self.priority, self.neighbors = inst, priority, neighbors
        self.step4 = [p for p in inst.prims if abs(p.dx) + abs(p.dy) == 1]
        self.by_d = {(p.dx, p.dy): p for p in inst.prims}

    def cells(self, c, dst):
        p = self.by_d.get((dst[0] - c[0], dst[1] - c[1]))
        return {(c[0] + sx, c[1] + sy) for sx, sy in p.swath} if p else {c, dst}

    def first_step(self, src, goal, blocked):
        if src == goal:
            return src
        prev, q = {src: None}, deque([src])
        while q:
            c = q.popleft()
            for p in self.inst.prims:
                n = (c[0] + p.dx, c[1] + p.dy)
                if (p.dx or p.dy) and n not in prev:
                    cs = {(c[0] + sx, c[1] + sy) for sx, sy in p.swath}
                    if all(self.inst.free(x) for x in cs) and not (cs & blocked):
                        prev[n] = c
                        if n == goal:
                            while prev[n] != src:
                                n = prev[n]
                            return n
                        q.append(n)
        return None

    def resolve(self, cur, want, goal, static=frozenset(), future=None):
        future = future or {}
        committed, out = {}, {}
        for r in sorted(cur, key=lambda r: self.priority[r]):
            c, w = cur[r], want[r]
            if isinstance(static, dict):
                reserved = set(static.get(r, ()))
            else:
                reserved = set(static)
            for h, cells in committed.items():
                local = self.neighbors is None or h in self.neighbors.get(r, ())
                if local and self.priority[h] < self.priority[r]:
                    reserved |= cells

            f = future.get(r, w)
            intended = self.cells(c, w) | self.cells(w, f)
            if not (intended & reserved):
                cell, act = w, "go"
            elif not (({c} | self.cells(c, w)) & reserved):
                cell, act = c, "wait"
            else:
                side = next(((c[0] + p.dx, c[1] + p.dy) for p in sorted(self.step4, key=lambda p: (p.dx, p.dy))
                             if self.inst.free((c[0] + p.dx, c[1] + p.dy))
                             and not (({(c[0] + sx, c[1] + sy) for sx, sy in p.swath}
                                       | {(c[0] + p.dx, c[1] + p.dy)}) & reserved)), None)
                if side:
                    cell, act = side, "step_aside"
                else:
                    n = self.first_step(c, goal[r], frozenset(reserved))
                    cell, act = (n, "replan") if n and n != c else (c, "wait")
            # Reserve this robot's accepted swept cells for both upcoming steps.
            if cell == w:
                nxt = f
                footprint = self.cells(c, cell) | self.cells(cell, nxt)
            elif cell == c:
                footprint = {c} | self.cells(c, w)
            else:
                footprint = self.cells(c, cell) | {cell}
            committed[r] = footprint
            out[r] = (cell, act)
        return out


def simulate(plans: dict, inst: Instance, steps: int, R_s: int | None = None, R_c: int | None = None):
    R = {g.rid: g for g in plans.values() if g and g.live}
    anchors = {g.rid: g.anchor for g in plans.values() if g}
    R_s = default_Rs(inst.xmax + 1) if R_s is None else R_s
    R_c = 2 * R_s if R_c is None else R_c
    neighbors = {r: [o for o in anchors if o != r and
                     max(abs(anchors[r][0] - anchors[o][0]), abs(anchors[r][1] - anchors[o][1])) <= R_c]
                 for r in anchors}
    arb = Arbiter(inst, {g.rid: (g.rid,) for g in plans.values() if g}, neighbors)
    path = {r: g.res.rpos[1] for r, g in R.items()}
    per = {r: g.res.M - 1 for r, g in R.items()}
    cur = {g.rid: (g.res.rpos[1][1] if g.live else g.anchor) for g in plans.values() if g}
    k = {r: 1 for r in R}
    counts = {"go": 0, "wait": 0, "step_aside": 0, "replan": 0}
    delay = {r: 0 for r in R}
    for t in range(1, steps + 1):
        want, goal, future, static = {}, {}, {}, {}
        for rid in cur:
            static[rid] = set()
            if rid in R:
                p, n = path[rid], per[rid]
                on = cur[rid] == p[k[rid]]
                goal[rid] = p[wrap(k[rid] + 1, n)] if on else p[k[rid]]
                want[rid] = goal[rid] if on else (arb.first_step(cur[rid], goal[rid], frozenset()) or cur[rid])
                future[rid] = (p[wrap(k[rid] + 2, n)] if on
                               else (arb.first_step(want[rid], goal[rid], frozenset()) or want[rid]))
            else:
                want[rid] = goal[rid] = cur[rid]
                future[rid] = cur[rid]
        for rid, g in R.items():
            tt = wrap(t, g.res.M - 1)
            for w in inst.workers:
                sensed = all(max(abs(c[0] - anchors[rid][0]), abs(c[1] - anchors[rid][1])) <= R_s
                             for c in w.loop)
                if sensed:
                    own = g.res.wpos.get(w.id)
                    if own is not None:
                        per_w = g.res.M - 1
                        idx = [wrap(t + j, per_w) for j in range(3)]
                        static[rid].update(own[i] for i in idx)
                        for j in range(2):
                            if w.id in g.res.wsweep:
                                static[rid].update(g.res.wsweep[w.id][wrap(t + j, per_w)])
                    else:
                        idx0, idx1, idx2 = (t - 1) % len(w.loop), t % len(w.loop), (t + 1) % len(w.loop)
                        static[rid].update((w.loop[idx0], w.loop[idx1], w.loop[idx2]))
                        static[rid].update(worker_edge_cells(inst, w, idx0))
                        static[rid].update(worker_edge_cells(inst, w, idx1))
        res = arb.resolve(cur, want, goal, static, future)
        for rid, (cell, act) in res.items():
            counts[act] += 1
            if rid in R:
                p, n = path[rid], per[rid]
                if cur[rid] == p[k[rid]] and cell == p[wrap(k[rid] + 1, n)]:
                    k[rid] = wrap(k[rid] + 1, n)
                elif act != "go":
                    delay[rid] += 1             # would be broadcast as a DelayUpdate
            cur[rid] = cell
    return counts, delay
