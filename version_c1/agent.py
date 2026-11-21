"""C1 recharger agents. Each instance owns only its local state."""
from collections import Counter
from dataclasses import dataclass
from ..version_b1.geometry import distances, neighbors
from ..version_b2.controller import make_bid
from ..version_b2.simulation import next_move


@dataclass(frozen=True)
class LocalGrid:
    xmax: int
    ymax: int
    prims: tuple
    visible_free: frozenset

    def free(self, cell):
        return cell in self.visible_free


class Recharger:
    def __init__(self, rid, params):
        self.id = rid
        self.params = params
        self.members = set()
        self.lock = None
        self.visits = Counter()

    def offers(self, grid, pos, inbox):
        travel = distances(grid, [pos])
        offers = {}
        self.members.intersection_update(inbox)
        if self.lock and (self.lock.worker not in inbox or
                          inbox[self.lock.worker][1].charge == inbox[self.lock.worker][1].capacity):
            self.lock = None
        for wid, (spec, state) in inbox.items():
            bid = make_bid(grid, self.params, self.id, spec, state, travel)
            if bid:
                # Preserve a service lock. Preserve other membership unless a
                # service completes and the owner opens that coalition to bids.
                offers[wid] = bid
        return offers

    def select(self, offers, replies, inbox):
        self.members.clear()
        candidates = []
        for wid, bid in offers.items():
            if min(replies[wid])[2] == self.id:
                self.members.add(wid)
                if inbox[wid][1].charge < inbox[wid][1].capacity:
                    candidates.append(bid)
        if self.lock and self.lock.worker not in self.members:
            self.lock = None
        if not self.lock and candidates:
            self.lock = min(candidates)
        return offers[self.lock.worker] if self.lock else None

    def move(self, grid, pos, bid, charging, occupied, desired):
        self.visits[pos] += 1
        if charging:
            return pos, {pos}
        if bid:
            dest, sweep = next_move(grid, pos, bid.meeting_cell, occupied)
            if dest != pos or pos not in desired:
                return dest, sweep
        elif self.members and pos not in desired:
            return pos, {pos}
        options = [(self.visits[c], c, sweep) for c, sweep in neighbors(grid, pos)
                   if not sweep & occupied and c not in desired]
        if options:
            _, dest, sweep = min(options, key=lambda item: item[:2])
            return dest, sweep
        return pos, {pos}
