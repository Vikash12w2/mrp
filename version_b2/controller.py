"""Tick-driven recharger decisions over a shared, synchronous bid exchange."""
from dataclasses import dataclass

from ..model import Cell
from ..params import Params
from ..version_b1.geometry import charging_cells, distances, group_box


@dataclass(frozen=True)
class WorkerBroadcast:
    tick: int
    worker_id: int
    position: Cell
    loop_index: int
    charge: int
    capacity: int
    next_step_cost: int


@dataclass(frozen=True, order=True)
class Bid:
    score: float
    recharger: int
    worker: int
    meeting_cell: Cell


def steps_remaining(worker, state):
    """Number of affordable edges starting at the reported loop phase."""
    cycle_cost = sum(worker.req)
    if cycle_cost == 0:
        return None
    cycles, energy = divmod(state.charge, cycle_cost)
    steps = cycles * len(worker.loop)
    while energy >= worker.req[(state.loop_index + steps) % len(worker.loop)]:
        energy -= worker.req[(state.loop_index + steps) % len(worker.loop)]
        steps += 1
    return steps


def make_bid(inst, params, rid, worker, state, travel):
    empty = steps_remaining(worker, state)
    if empty is None:
        return None
    # Each loop phase recurs. Compute its earliest reachable visit directly.
    length = len(worker.loop)
    options = []
    for offset in range(min(empty + 1, length)):
        cell = worker.loop[(state.loop_index + offset) % length]
        for dest in charging_cells(inst, cell):
            if dest not in travel:
                continue
            distance = travel[dest]
            cycles = max(0, (distance - offset + length - 1) // length)
            arrival = offset + cycles * length
            if arrival <= empty:
                options.append((arrival, distance, dest))
            elif offset == empty % length:
                options.append((max(distance, empty), distance, dest))
    if not options:
        return None
    arrival, distance, dest = min(options)
    score = (params.u1 * max(0, arrival - empty) + params.u2 * distance
             + params.u3 * max(0, empty - arrival))
    return Bid(score, rid, worker.id, dest)


class RechargerController:
    def __init__(self, inst, anchors, groups, params=None):
        self.inst = inst
        self.params = params or Params()
        self.workers = {w.id: w for w in inst.workers}
        self.owner = {w: r for r, members in groups.items() for w in members}
        self.boxes = {r: group_box(inst, anchors[r], [self.workers[w] for w in members])
                      for r, members in groups.items()}
        self.locks = {}
        self.last_seen = {}
        self.tick = -1

    def decide(self, tick, positions, broadcasts):
        if tick <= self.tick:
            raise ValueError('decision ticks must increase')
        live = {}
        for state in broadcasts:
            if state.tick != tick:
                continue
            w = self.workers[state.worker_id]
            if (state.worker_id in live or not 0 <= state.loop_index < len(w.loop)
                    or state.position != w.loop[state.loop_index] or state.capacity != w.fch
                    or not 0 <= state.charge <= w.fch or state.next_step_cost != w.req[state.loop_index]):
                raise ValueError(f'inconsistent broadcast for worker {w.id}')
            live[w.id] = state
        self.tick = tick
        self.last_seen.update(dict.fromkeys(live, tick))
        timeout = self.params.hb_period * self.params.k_missed
        for r, lock in list(self.locks.items()):
            state = live.get(lock.worker)
            if (r not in positions or tick - self.last_seen[lock.worker] >= timeout
                    or (state and state.charge == state.capacity)):
                del self.locks[r]
        locked = {lock.worker for lock in self.locks.values()}
        offers = []
        targets = {}
        for r, position in sorted(positions.items()):
            travel = distances(self.inst, [position])
            x0, x1, y0, y1 = self.boxes[r]
            for wid, state in live.items():
                if wid not in self.owner or state.charge == state.capacity:
                    continue
                if r in self.locks:
                    if self.locks[r].worker != wid:
                        continue
                elif wid in locked or (self.owner[wid] != r and not
                        (x0 <= state.position[0] <= x1 and y0 <= state.position[1] <= y1)):
                    continue
                bid = make_bid(self.inst, self.params, r, self.workers[wid], state, travel)
                if bid and r in self.locks:
                    targets[r] = bid
                elif bid:
                    offers.append(bid)
        for bid in sorted(offers):
            if bid.recharger not in self.locks and bid.worker not in locked:
                self.locks[bid.recharger] = targets[bid.recharger] = bid
                locked.add(bid.worker)
        return targets
