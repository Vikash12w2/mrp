class Negotiator:
    def __init__(self, agent, members, bus):
        self.agent, self.members, self.bus = agent, set(members), bus
        self.views, self.offers, self.votes = {}, {}, {}
        self.choice = None

    @property
    def rid(self):
        return self.agent.id

    def snapshot(self):
        a = self.agent
        return dict(members=tuple(sorted(self.members)), scores=dict(a.cand),
                    util=dict(a.util), cap_n=a.P.cap_n, cap_util=a.P.cap_util)

    def publish(self, epoch):
        self.epoch = epoch
        self.views, self.offers, self.votes, self.choice = {}, {}, {}, None
        for n in self.agent.neighbors:
            self.bus.send(self.rid, n, "group_state", (epoch, self.snapshot()))

    def receive(self, kind):
        return [m for m in self.bus.drain(self.rid, kind)
                if m.src in self.agent.neighbors and m.payload[0] == self.epoch]

    def read_states(self):
        self.views = {m.src: m.payload[1] for m in self.receive("group_state")}

    @property
    def complete(self):
        return all(n in self.views for n in self.agent.neighbors)

    @staticmethod
    def fits(state, members):
        return (all(w in state["scores"] for w in members)
                and len(members) <= state["cap_n"]
                and sum(state["util"][w] for w in members) <= state["cap_util"] + 1e-12)

    def missing(self):
        owned = self.members | {w for s in self.views.values() for w in s["members"]}
        return set(self.agent.cand) - owned

    def claim(self):
        if not self.complete:
            return
        own = self.snapshot()
        for w in sorted(self.missing(), key=lambda w: (self.agent.cand[w], w)):
            if not self.fits(own, self.members | {w}):
                continue
            bids = [(s["scores"][w], n) for n, s in self.views.items()
                    if w in s["scores"] and self.fits(s, set(s["members"]) | {w})]
            if any(b < (self.agent.cand[w], self.rid) for b in bids):
                continue
            # One claim per round keeps the capacity advertised in this round valid.
            self.members.add(w)
            break

    def propose(self, mode):
        if not self.complete or not self.members.issubset(self.agent.cand):
            if mode == "rebalance":
                self.rebalance_offers = {}
                for n in self.agent.neighbors:
                    self.bus.send(self.rid, n, "group_bid", (self.epoch, {}))
            return
        own = self.snapshot()
        trials = []
        for n, remote in sorted(self.views.items()):
            other = set(remote["members"])
            if not other.issubset(remote["scores"]):
                continue
            if mode == "rebalance":
                for w in sorted(self.missing()):
                    for moved in sorted(self.members):
                        trials.append((n, (self.members - {moved}) | {w}, other | {moved}))
                        # A swap can free utilisation even when the peer has no
                        # spare worker-count slot for a simple offload.
                        for received in sorted(other):
                            trials.append((n, (self.members - {moved}) | {received, w},
                                           (other - {received}) | {moved}))
            else:
                for w in sorted(self.members):
                    trials.append((n, self.members - {w}, other | {w}))
                for w in sorted(other):
                    trials.append((n, self.members | {w}, other - {w}))
                for w in sorted(self.members):
                    for v in sorted(other):
                        trials.append((n, (self.members - {w}) | {v}, (other - {v}) | {w}))
        offers = []
        for n, left, right in trials:
            remote = self.views[n]
            if left & right or not self.fits(own, left) or not self.fits(remote, right):
                continue
            delta = (sum(own["scores"][w] for w in left) + sum(remote["scores"][w] for w in right)
                     - sum(own["scores"][w] for w in self.members)
                     - sum(remote["scores"][w] for w in remote["members"]))
            if mode == "swap" and delta >= -self.agent.P.eps:
                continue
            key = (delta, self.rid, n, tuple(sorted(left)), tuple(sorted(right)))
            offers.append((key, dict(src=self.rid, dst=n, before=own["members"],
                                     peer_before=remote["members"], after=tuple(sorted(left)),
                                     peer_after=tuple(sorted(right)))))
        if mode == "rebalance":
            # Only feasible local proposals bid. A full, incapable contender
            # must not suppress another recharger that can make room.
            self.rebalance_offers = {}
            for key, offer in sorted(offers, key=lambda x: x[0]):
                added = set(offer["after"]) - self.members - set(self.views[offer["dst"]]["members"])
                for w in added:
                    self.rebalance_offers.setdefault(w, (key, offer))
            bids = {w: item[0] for w, item in self.rebalance_offers.items()}
            for n in self.agent.neighbors:
                self.bus.send(self.rid, n, "group_bid", (self.epoch, bids))
        elif offers:
            key, offer = min(offers, key=lambda x: x[0])
            self.send_offer(key, offer)

    def select_rebalance(self):
        bids = {m.src: m.payload[1] for m in self.receive("group_bid")}
        if not self.complete or any(n not in bids for n in self.agent.neighbors):
            return
        eligible = [(key, offer) for w, (key, offer) in self.rebalance_offers.items()
                    if not any(w in other and other[w] < key for other in bids.values())]
        if eligible:
            self.send_offer(*min(eligible, key=lambda x: x[0]))

    def send_offer(self, key, offer):
        offer["key"] = key
        self.offers[key] = offer
        self.bus.send(self.rid, offer["dst"], "group_offer", (self.epoch, offer))

    def vote(self):
        for m in self.receive("group_offer"):
            offer = m.payload[1]
            if (self.complete and offer["src"] == m.src and offer["dst"] == self.rid
                    and offer["peer_before"] == tuple(sorted(self.members))
                    and offer["before"] == self.views[m.src]["members"]
                    and self.fits(self.snapshot(), set(offer["peer_after"]))):
                self.offers[offer["key"]] = offer
        if not self.offers:
            return
        self.choice = min(self.offers)
        offer = self.offers[self.choice]
        peer = offer["dst"] if offer["src"] == self.rid else offer["src"]
        self.bus.send(self.rid, peer, "group_vote", (self.epoch, self.choice))

    def commit(self):
        self.votes = {m.src: m.payload[1] for m in self.receive("group_vote")}
        if self.choice is None:
            return
        offer = self.offers[self.choice]
        if offer["src"] != self.rid or self.votes.get(offer["dst"]) != self.choice:
            return
        if self.bus.send(self.rid, offer["dst"], "group_commit", (self.epoch, offer)):
            self.members = set(offer["after"])

    def finish(self):
        for m in self.receive("group_commit"):
            offer = m.payload[1]
            if (self.choice == offer["key"] and offer["src"] == m.src
                    and offer["dst"] == self.rid
                    and offer["peer_before"] == tuple(sorted(self.members))):
                self.members = set(offer["peer_after"])


def rounds(actors, bus, count, mode):
    """Fixed simulator rounds, with no global best/ownership/convergence decision."""
    for _ in range(max(0, count)):
        bus._group_epoch = getattr(bus, "_group_epoch", 0) + 1
        epoch = bus._group_epoch
        for a in actors:
            a.publish(epoch)
        for a in actors:
            a.read_states()
        if mode == "claim":
            for a in actors:
                a.claim()
        else:
            for a in actors:
                a.propose(mode)
            if mode == "rebalance":
                for a in actors:
                    a.select_rebalance()
            for a in actors:
                a.vote()
            for a in actors:
                a.commit()
            for a in actors:
                a.finish()
