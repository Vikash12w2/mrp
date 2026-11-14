from .negotiation import Negotiator

class Refiner(Negotiator):
    def __init__(self, agent, plan, bus):
        super().__init__(agent, plan.workers, bus)
        self.plan = plan
        self.tried = set()
        self.accepted = 0

    def snapshot(self):
        state = super().snapshot()
        state.update(plan=self.plan, neighbors=tuple(self.agent.neighbors))
        return state

    def publish(self, epoch):
        super().publish(epoch)
        self.pending_out = self.pending_in = None

    def solve_local(self, members, replacement=None):
        from .planner import _plan_for
        known = {n: s["plan"] for n, s in self.views.items() if n < self.rid}
        if replacement:
            known[replacement.rid] = replacement
        result = _plan_for(self.rid, members, self.agent.inst,
                           {self.rid: self.agent.anchor}, known,
                           {self.rid: self.agent.neighbors}, self.agent.P)
        # A refinement cannot discard the neighbors' collision reservations.
        return result if result is not None and not result.fallback else None

    def offer(self):
        if not self.complete or (self.members and not self.plan.live):
            return
        own = self.snapshot()
        for peer, state in sorted(self.views.items()):
            if peer <= self.rid or (state["members"] and not state["plan"].live):
                continue
            if (any(n > self.rid and n != peer for n in self.agent.neighbors)
                    or any(n > peer for n in state["neighbors"])):
                continue
            other = set(state["members"])
            trials = [(self.members - {w}, other | {w}) for w in sorted(self.members)]
            trials += [(self.members | {w}, other - {w}) for w in sorted(other)]
            trials += [((self.members - {w}) | {v}, (other - {v}) | {w})
                       for w in sorted(self.members) for v in sorted(other)]
            for left, right in trials:
                key = (peer, tuple(sorted(self.members)), tuple(sorted(other)),
                       tuple(sorted(left)), tuple(sorted(right)))
                if key in self.tried or not self.fits(own, left) or not self.fits(state, right):
                    continue
                self.tried.add(key)
                trial = self.solve_local(left)
                if trial is not None:
                    proposal = dict(key=key, plan=trial, members=tuple(sorted(right)))
                    if self.bus.send(self.rid, peer, "refine_offer", (self.epoch, proposal)):
                        self.pending_out = proposal
                # At most one own SMT attempt in a local round.
                return

    def respond(self):
        from .planner import _eff
        for m in self.receive("refine_offer"):
            if self.pending_out or self.pending_in or not self.complete or m.src >= self.rid:
                continue
            proposal = m.payload[1]
            key = proposal["key"]
            source = self.views[m.src]
            left, right = set(proposal["plan"].workers), set(proposal["members"])
            if (key[0] != self.rid or key[1] != source["members"]
                    or key[2] != tuple(sorted(self.members))
                    or left & right or left | right != set(source["members"]) | self.members
                    or not self.fits(self.snapshot(), right)
                    or any(n > self.rid for n in self.agent.neighbors)
                    or any(n > m.src and n != self.rid for n in source["neighbors"])):
                continue
            trial = self.solve_local(right, proposal["plan"])
            if trial is None:
                continue
            gain = _eff([proposal["plan"], trial]) - _eff([source["plan"], self.plan])
            if gain < self.agent.P.refine_min_gain:
                continue
            if self.bus.send(self.rid, m.src, "refine_accept", (self.epoch, key)):
                self.pending_in = (m.src, key, trial)

    def commit_refinement(self):
        for m in self.receive("refine_accept"):
            if not self.pending_out:
                continue
            key = self.pending_out["key"]
            if m.src == key[0] and m.payload[1] == key:
                if self.bus.send(self.rid, m.src, "refine_commit", (self.epoch, key)):
                    self.plan = self.pending_out["plan"]
                    self.members = set(self.plan.workers)
                    self.accepted += 1
                    self.pending_out = None

    def finish_refinement(self):
        for m in self.receive("refine_commit"):
            if self.pending_in and (m.src, m.payload[1]) == self.pending_in[:2]:
                self.plan = self.pending_in[2]
                self.members = set(self.plan.workers)
                self.pending_in = None


def refine(actors, bus, count):
    """Schedule local handlers; only the endpoints decide whether a trial commits."""
    for _ in range(max(0, count)):
        bus._group_epoch = getattr(bus, "_group_epoch", 0) + 1
        for actor in actors:
            actor.publish(bus._group_epoch)
        for actor in actors:
            actor.read_states()
        for actor in actors:
            actor.offer()
        for actor in actors:
            actor.respond()
        for actor in actors:
            actor.commit_refinement()
        for actor in actors:
            actor.finish_refinement()
