from __future__ import annotations
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import dataclass
from itertools import count
from .model import cheb

ACK_KINDS = {"candidates", "claim", "rebalance", "swap", "plan", "no_plan", "ready",
             "group_state", "group_bid", "group_offer", "group_vote", "group_commit",
             "refine_offer", "refine_accept", "refine_commit", "replan_notice"}
PERIODIC_KINDS = {"beacon", "setup_path", "setup_ready", "setup_position"}


@dataclass
class Msg:
    src: int
    dst: int
    kind: str
    payload: object
    seq: int


class Bus:
    def __init__(self, anchors: dict, R_c: int, retries: int = 3, loss=None):
        self.anchors, self.R_c, self.retries, self.loss = anchors, R_c, retries, loss
        self.inbox = defaultdict(list)
        self.stats = Counter()
        self.unreachable: list = []
        self._seq = count(1)

    def in_range(self, a: int, b: int) -> bool:
        return a != b and cheb(self.anchors[a], self.anchors[b]) <= self.R_c

    def send(self, src: int, dst: int, kind: str, payload) -> bool:
        if not self.in_range(src, dst):
            self.unreachable.append((src, dst, kind))
            return False
        seq = next(self._seq)
        attempts = 1 + (self.retries if kind in ACK_KINDS else 0)
        for k in range(attempts):
            if self.loss is None or not self.loss(src, dst, kind, seq, k):
                self.inbox[dst].append(Msg(src, dst, kind, deepcopy(payload), seq))
                self.stats[kind] += 1
                if kind in ACK_KINDS:
                    self.stats["ack"] += 1
                return True
            self.stats["retry"] += 1
        if kind not in PERIODIC_KINDS:
            self.unreachable.append((src, dst, kind))
        else:
            self.stats["periodic_drop"] += 1
        return False

    def broadcast(self, src: int, kind: str, payload) -> None:
        for dst in sorted(self.anchors):
            if self.in_range(src, dst):
                self.send(src, dst, kind, payload)

    def drain(self, rid: int, kind: str) -> list:
        keep, out = [], []
        for m in self.inbox[rid]:
            (out if m.kind == kind else keep).append(m)
        self.inbox[rid] = keep
        return sorted(out, key=lambda m: (m.src, m.seq))
