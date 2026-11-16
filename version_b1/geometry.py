"""Obstacle-aware geometry shared by B1 assignment and local planning."""
from collections import deque
from fractions import Fraction
from ..model import OFFS8, worker_edge_cells


def neighbors(inst, cell, reverse=False, box=None):
    for p in inst.prims:
        if not (p.dx or p.dy):
            continue
        start = (cell[0]-p.dx, cell[1]-p.dy) if reverse else cell
        end = cell if reverse else (cell[0]+p.dx, cell[1]+p.dy)
        swept = {(start[0]+x, start[1]+y) for x, y in p.swath} | {start, end}
        if all(inst.free(c) and (box is None or
               box[0] <= c[0] <= box[1] and box[2] <= c[1] <= box[3]) for c in swept):
            yield (start if reverse else end), swept


def distances(inst, sources, reverse=False, box=None):
    dist = {c: 0 for c in sources if inst.free(c) and (box is None or
            box[0] <= c[0] <= box[1] and box[2] <= c[1] <= box[3])}
    queue = deque(sorted(dist))
    while queue:
        c = queue.popleft()
        for nxt, _ in neighbors(inst, c, reverse, box):
            if nxt not in dist:
                dist[nxt] = dist[c]+1
                queue.append(nxt)
    return dist


def charging_cells(inst, cell):
    return {(cell[0]+x, cell[1]+y) for x, y in OFFS8
            if inst.free((cell[0]+x, cell[1]+y))}


def loop_distance(inst, left, right):
    """Symmetric mean nearest-loop distance over all charging neighborhoods."""
    means = []
    for source, target in ((left, right), (right, left)):
        target_cells = set().union(*(charging_cells(inst, c) for c in target.loop))
        dist = distances(inst, target_cells, reverse=True)
        ds = [min((dist[c] for c in charging_cells(inst, cell) if c in dist), default=None)
              for cell in source.loop]
        ds = [d for d in ds if d is not None]
        if not ds:
            return None
        means.append(Fraction(sum(ds), len(ds)))
    return sum(means)/2


def group_box(inst, anchor, workers, padding=1):
    # Include full worker sweeps, plus shortest primitive paths from the anchor
    # to every reachable charging location. Detours are included before bounding.
    cells = {anchor}
    targets = set()
    for w in workers:
        for edge, cell in enumerate(w.loop):
            cells.update(worker_edge_cells(inst, w, edge))
            targets.update(charging_cells(inst, cell))
    parents, queue = {anchor: None}, deque([anchor])
    while queue:
        c = queue.popleft()
        for nxt, swept in neighbors(inst, c):
            if nxt not in parents:
                parents[nxt] = (c, swept)
                queue.append(nxt)
    traced = set()
    for target in sorted(targets & parents.keys()):
        c = target
        cells.add(c)
        while parents[c] is not None and c not in traced:
            traced.add(c)
            c, swept = parents[c]
            cells.update(swept)
    pad = padding if workers else 0
    return (max(0, min(c[0] for c in cells)-pad), min(inst.xmax, max(c[0] for c in cells)+pad),
            max(0, min(c[1] for c in cells)-pad), min(inst.ymax, max(c[1] for c in cells)+pad))


def overlaps(a, b):
    return a[0] <= b[1] and b[0] <= a[1] and a[2] <= b[3] and b[2] <= a[3]


def expand_box(inst, box):
    dx, dy = max(1, (box[1]-box[0]+1)//2), max(1, (box[3]-box[2]+1)//2)
    return max(0, box[0]-dx), min(inst.xmax, box[1]+dx), max(0, box[2]-dy), min(inst.ymax, box[3]+dy)
