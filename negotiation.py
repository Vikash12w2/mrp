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
                    util=dict(a.util), cap_n=a.P.cap_n)

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
                and (state["cap_n"] is None or len(members) <= state["cap_n"]))

    @staticmethod
    def bid(state, worker, rid):
        members = state["members"]
        return (len(members), sum(state["util"][w] for w in members) + state["util"][worker],
                state["scores"][worker], rid)

    @staticmethod
    def balance_cost(left_state, left, right_state, right):
        # Lexicographic objective: balance worker counts first, then estimated
        # charging/travel workload. No utilization threshold rejects a worker.
        lu = sum(left_state["util"][w] for w in left)
        ru = sum(right_state["util"][w] for w in right)
        return (len(left) ** 2 + len(right) ** 2, lu ** 2 + ru ** 2)

    @staticmethod
    def compare_cost(after, before, eps):
        for new, old in zip(after, before):
            if new < old - eps:
                return -1
            if new > old + eps:
                return 1
        return 0

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
            bids = [self.bid(s, w, n) for n, s in self.views.items()
                    if w in s["scores"] and self.fits(s, set(s["members"]) | {w})]
            if any(b < self.bid(own, w, self.rid) for b in bids):
                continue
            # One claim per round keeps advertised loads consistent across bidders.
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
                        # An exchange can make room under an explicitly requested
                        # worker-count limit when a simple offload cannot.
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
            before = self.balance_cost(own, self.members, remote, remote["members"])
            after = self.balance_cost(own, left, remote, right)
            change = self.compare_cost(after, before, self.agent.P.eps)
            if mode == "swap" and (change > 0 or (change == 0 and delta >= -self.agent.P.eps)):
                continue
            key = (after[0] - before[0], after[1] - before[1], delta,
                   self.rid, n, tuple(sorted(left)), tuple(sorted(right)))
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
    """Schedule bounded local decisions; stop idle, reliable rounds early."""
    for _ in range(max(0, count)):
        previous = [set(a.members) for a in actors]
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
        # This only shortens idle simulator rounds; ownership is decided above
        # by local messages. Do not stop early under injected message loss.
        if (mode in ("claim", "swap") and bus.loss is None and all(a.complete for a in actors)
                and all(a.members == old for a, old in zip(actors, previous))):
            break


def assign(actors, bus, P):
    """Schedule auctions and overlap balancing, also used after failures."""
    size = max((len(a.agent.cand) for a in actors), default=0)
    # One claim per round must not impose an accidental 20-worker ceiling.
    rounds(actors, bus, max(1, P.K_round, sum(len(a.agent.cand) for a in actors)), "claim")
    for _ in range(max(0, P.K_unclaimed)):
        rounds(actors, bus, 1, "rebalance")
        rounds(actors, bus, 1, "claim")
    if P.K_swap > 0:
        rounds(actors, bus, max(P.K_swap, size * size), "swap")
