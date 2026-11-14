from __future__ import annotations
from .model import Instance, cheb


def pick_anchors(inst: Instance, k: int, R_s: int | None = None) -> dict:
    if R_s is not None:
        return _coverage_anchors(inst, k, R_s)
    loops = {c for w in inst.workers for c in w.loop}
    cand = sorted((x, y) for x in range(inst.xmax + 1) for y in range(inst.ymax + 1)
                  if inst.free((x, y)) and (x, y) not in loops)
    cents = [(sum(c[0] for c in w.loop) / len(w.loop), sum(c[1] for c in w.loop) / len(w.loop)) for w in inst.workers]
    d = lambda a, b: max(abs(a[0] - b[0]), abs(a[1] - b[1]))
    near = lambda p: min(cand, key=lambda c: (d(c, p), c))
    meds = [near(cents[0])]
    while len(meds) < k:                                   # farthest-point init
        far = max(cents, key=lambda p: (min(d(p, m) for m in meds), p))
        meds.append(near(far))
    for _ in range(10):
        cl = {i: [] for i in range(k)}
        for p in cents:
            cl[min(range(k), key=lambda i: (d(p, meds[i]), i))].append(p)
        new = [near((sum(p[0] for p in cl[i]) / len(cl[i]), sum(p[1] for p in cl[i]) / len(cl[i])))
               if cl[i] else meds[i] for i in range(k)]
        if new == meds:
            break
        meds = new
    return {i + 1: m for i, m in enumerate(sorted(meds))}


def _coverage_anchors(inst: Instance, k: int, R_s: int) -> dict:
    import itertools
    loops = {c for w in inst.workers for c in w.loop}
    cov = {}
    for x in range(inst.xmax + 1):
        for y in range(inst.ymax + 1):
            if inst.free((x, y)) and (x, y) not in loops:
                s = frozenset(w.id for w in inst.workers if all(abs(c[0] - x) <= R_s and abs(c[1] - y) <= R_s for c in w.loop))
                if s:
                    cov[(x, y)] = s
    cells = sorted(cov)
    # keep one cell per distinct coverage set that is closest to the centre of its workers (cuts the search)
    by_set = {}
    for c in cells:
        by_set.setdefault(cov[c], []).append(c)
    reps = {s: min(cs, key=lambda c: (sum(max(abs(c[0] - p[0]), abs(c[1] - p[1])) for w in inst.workers if w.id in s for p in [w.loop[0]]), c))
            for s, cs in by_set.items()}
    sets = sorted(reps, key=lambda s: (-len(s), sorted(s)))
    best = None
    for combo in itertools.combinations(sets, min(k, len(sets))):
        covered = set().union(*combo)
        # assign each covered worker to the first set that covers it (ID order) -> group sizes
        load = {}
        for w in sorted(covered):
            i = next(i for i, s in enumerate(combo) if w in s)
            load[i] = load.get(i, 0) + 1
        key = (-len(covered), max(load.values()) if load else 0, tuple(reps[s] for s in combo))
        if best is None or key < best[0]:
            best = (key, combo)
    anchors = sorted(reps[s] for s in best[1])
    spare = [c for c in cells if c not in anchors]
    while len(anchors) < k and spare:                      # more rechargers than needed: spread extras
        anchors.append(max(spare, key=lambda c: (min(max(abs(c[0] - a[0]), abs(c[1] - a[1])) for a in anchors), c)))
    return {i + 1: a for i, a in enumerate(sorted(anchors))}
