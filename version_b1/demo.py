from ..model import Instance, Prim, WorkerSpec


def demo():
    moves = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
    prims = tuple(Prim(i, x, y, 1, tuple(sorted({(0, 0), (x, y)})))
                  for i, (x, y) in enumerate(moves))
    workers = (WorkerSpec(1, 8, ((1, 1), (2, 1), (2, 2), (1, 2)), (1,)*4),
               WorkerSpec(2, 8, ((5, 1), (6, 1), (6, 2), (5, 2)), (1,)*4))
    return Instance(7, 4, frozenset(), prims, workers, 8, 2, 2, 'B1 demo'), {1: (0, 0), 2: (7, 0)}
