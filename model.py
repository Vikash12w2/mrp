from __future__ import annotations
import os
import re
import json
from dataclasses import dataclass, replace
from typing import Optional

Cell = tuple[int, int]
OFFS8 = [(1, 0), (1, 1), (0, 1), (-1, 1), (-1, 0), (-1, -1), (0, -1), (1, -1)]


def cheb(a: Cell, b: Cell) -> int:
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


@dataclass(frozen=True)
class Prim:
    id: int
    dx: int
    dy: int
    cost: float
    swath: tuple          # cells (relative) swept, includes (0,0) and (dx,dy)


@dataclass(frozen=True)
class WorkerSpec:
    id: int               # 1-based, as in the repo
    fch: int              # full charge
    loop: tuple           # waypoints; one waypoint per time step
    req: tuple            # energy needed per step (indexed like the repo, see twoshot.cost_by)
    swaths: tuple = ()    # optional absolute swept cells for each loop edge


def worker_edge_cells(inst, worker, edge):
    a, b = worker.loop[edge], worker.loop[(edge + 1) % len(worker.loop)]
    if worker.swaths:
        return set(worker.swaths[edge]) | {a, b}
    if inst.worker_motion == "waypoints":
        return {a, b}
    dx, dy = b[0] - a[0], b[1] - a[1]
    matching = [p for p in inst.prims if (p.dx, p.dy) == (dx, dy)]
    if matching:
        return {(a[0] + x, a[1] + y) for p in matching for x, y in p.swath} | {a, b}
    cells = set()
    for x in range(min(a[0], b[0]), max(a[0], b[0]) + 1):
        for y in range(min(a[1], b[1]), max(a[1], b[1]) + 1):
            lo, hi = 0.0, 1.0
            for origin, delta, center in ((a[0], dx, x), (a[1], dy, y)):
                if delta == 0:
                    if not center - 0.5 <= origin <= center + 0.5:
                        hi = -1.0
                        break
                else:
                    ts = ((center - 0.5 - origin) / delta, (center + 0.5 - origin) / delta)
                    lo, hi = max(lo, min(ts)), min(hi, max(ts))
            if lo <= hi + 1e-12:
                cells.add((x, y))
    return cells | {a, b}


def worker_transition_cells(inst, worker, start, end):
    if start == end:
        return {start}
    edges = [i for i, c in enumerate(worker.loop)
             if c == start and worker.loop[(i + 1) % len(worker.loop)] == end]
    if not edges:
        raise ValueError(f"worker {worker.id}: transition is not a loop edge: {start} -> {end}")
    return set().union(*(worker_edge_cells(inst, worker, i) for i in edges))


@dataclass(frozen=True)
class Instance:
    xmax: int
    ymax: int
    obstacles: frozenset
    prims: tuple
    workers: tuple
    T: int                # hypercycle (phase-1 length)
    delta_max: int
    n_rech: int
    name: str = ""
    worker_motion: str = "waypoints"  # legacy abstract edges; "straight" is an explicit assumption

    def free(self, c: Cell) -> bool:
        return 0 <= c[0] <= self.xmax and 0 <= c[1] <= self.ymax and c not in self.obstacles

    def sub(self, workers, n_rech=1, T=None) -> "Instance":
        return replace(self, workers=tuple(workers), n_rech=n_rech, T=self.T if T is None else T)


def _ints(path: str) -> list:
    return [l.strip() for l in open(path).read().replace("\r", "").split("\n")]


def load_primitives(path: str) -> tuple:
    prims = []
    for blk in open(path).read().replace("\r", "").strip().split("\n\n"):
        d = {}
        for line in blk.strip().split("\n"):
            if ":" in line:
                k, v = line.split(":", 1)
                d[k.strip()] = v.strip()
        pf = [int(v) for v in re.findall(r"-?\d+", d["pos_f"])]
        sw = tuple((int(a), int(b)) for a, b in re.findall(r"\[\s*(-?\d+)\s*,\s*(-?\d+)\s*\]", d["swath"]))
        prims.append(Prim(int(d["ID"]), pf[0], pf[1], float(d["cost"]), sw))
    return tuple(sorted(prims, key=lambda p: p.id))


def load_instance(d: str, n_workers: Optional[int] = None, n_rech: Optional[int] = None,
                  T: Optional[int] = None) -> Instance:
    ws = [int(x) for x in _ints(os.path.join(d, "workspace.txt")) if x]
    xmax, ymax, nw, hlen, rech, nr = ws[:6]
    lines = _ints(os.path.join(d, "obstacle.txt"))
    obs = frozenset(tuple(map(int, l.split())) for l in lines[1:] if l and re.match(r"^-?\d+\s+-?\d+$", l))
    nw = n_workers or nw
    workers = []
    for i in range(1, nw + 1):
        L = [l for l in _ints(os.path.join(d, f"rob{i}_traj.txt")) if l]
        fch, n = int(L[0]), int(L[1])
        loop = tuple(tuple(map(int, L[2 + k].split())) for k in range(n))
        req = tuple(int(L[2 + n + k]) for k in range(n))
        workers.append(WorkerSpec(i, fch, loop, req))
    sidecar = os.path.join(d, "worker_swaths.json")
    if os.path.exists(sidecar):
        with open(sidecar) as f:
            supplied = json.load(f)
        for i, w in enumerate(workers):
            if str(w.id) in supplied:
                edges = supplied[str(w.id)]
                if len(edges) != len(w.loop):
                    raise ValueError(f"worker {w.id}: expected one swath for each loop edge")
                swaths = tuple(tuple(tuple(c) for c in edge) for edge in edges)
                for k, cells in enumerate(swaths):
                    if w.loop[k] not in cells or w.loop[(k + 1) % len(w.loop)] not in cells:
                        raise ValueError(f"worker {w.id}: edge {k} swath must include both endpoints")
                    if any(len(c) != 2 or not (0 <= c[0] <= xmax and 0 <= c[1] <= ymax) or c in obs for c in cells):
                        raise ValueError(f"worker {w.id}: edge {k} swath contains a blocked or invalid cell")
                workers[i] = replace(w, swaths=swaths)
    return Instance(xmax, ymax, obs, load_primitives(os.path.join(d, "2d_template.txt")), tuple(workers),
                    T or hlen, rech, n_rech or nr, os.path.basename(os.path.normpath(d)))
