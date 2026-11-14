import unittest
from types import SimpleNamespace

from mrp.bus import Bus
from mrp.negotiation import Negotiator, rounds
from mrp.params import Params


class NegotiationTests(unittest.TestCase):
    def actors(self, scores, members=None, anchors=None, cap=1, loss=None):
        anchors = anchors or {r: (r, 0) for r in scores}
        bus = Bus(anchors, 2, loss=loss)
        actors = []
        for rid, cand in scores.items():
            a = SimpleNamespace(id=rid, cand=cand, util={w: 0.1 for w in cand},
                                P=Params(cap_n=cap),
                                neighbors=[n for n in scores if bus.in_range(rid, n)])
            actors.append(Negotiator(a, (members or {}).get(rid, ()), bus))
        return actors, bus

    def check(self, actors):
        owned = set()
        for a in actors:
            self.assertFalse(owned & a.members)
            self.assertTrue(a.fits(a.snapshot(), a.members))
            owned |= a.members
        return {a.rid: a.members for a in actors}

    def test_capacity_aware_local_bids_and_id_tie(self):
        actors, bus = self.actors({1: {1: 1, 2: 1}, 2: {1: 1, 2: 2}})
        rounds(actors, bus, 3, "claim")
        self.assertEqual(self.check(actors), {1: {1}, 2: {2}})

    def test_neighbor_offload_frees_capacity_for_unclaimed_worker(self):
        actors, bus = self.actors({1: {1: 1, 2: 2}, 2: {1: 3}}, {1: {1}})
        rounds(actors, bus, 1, "rebalance")
        self.assertEqual(self.check(actors), {1: {2}, 2: {1}})

    def test_pair_swap_improves_local_score(self):
        actors, bus = self.actors({1: {1: 5, 2: 1}, 2: {1: 1, 2: 5}}, {1: {1}, 2: {2}})
        rounds(actors, bus, 1, "swap")
        self.assertEqual(self.check(actors), {1: {2}, 2: {1}})

    def test_missing_snapshot_does_not_allow_duplicate_claim(self):
        actors, bus = self.actors({1: {1: 1}, 2: {1: 2}},
                                 loss=lambda s, d, k, q, a: k == "group_state" and d == 2)
        rounds(actors, bus, 2, "claim")
        self.assertEqual(self.check(actors), {1: {1}, 2: set()})

    def test_lost_offer_vote_or_commit_preserves_ownership(self):
        for kind in ("group_offer", "group_vote", "group_commit"):
            with self.subTest(kind=kind):
                actors, bus = self.actors({1: {1: 1, 2: 2}, 2: {1: 3}}, {1: {1}},
                                         loss=lambda s, d, k, q, a: k == kind)
                rounds(actors, bus, 1, "rebalance")
                self.assertEqual(self.check(actors), {1: {1}, 2: set()})
                bus.loss = None
                rounds(actors, bus, 1, "rebalance")
                self.assertEqual(self.check(actors), {1: {2}, 2: {1}})

    def test_disconnected_component_cannot_change_local_decisions(self):
        actors, bus = self.actors({1: {1: 2}, 2: {1: 3}, 3: {2: 0}},
                                 anchors={1: (0, 0), 2: (1, 0), 3: (20, 0)})
        rounds(actors, bus, 2, "claim")
        self.assertEqual(self.check(actors), {1: {1}, 2: set(), 3: {2}})
        self.assertNotIn(3, actors[0].views)
        self.assertFalse(bus.unreachable)

    def test_received_snapshot_is_not_live_peer_state(self):
        actors, bus = self.actors({1: {1: 1}, 2: {1: 2}})
        for a in actors:
            a.publish(1)
        actors[1].agent.cand[1] = -100
        for a in actors:
            a.read_states()
            a.claim()
        self.assertEqual(actors[0].views[2]["scores"][1], 2)

    def test_competing_pair_proposals_do_not_double_book_capacity(self):
        actors, bus = self.actors({1: {1: 1, 3: 2}, 2: {2: 1, 4: 2}, 3: {1: 3, 2: 3}},
                                 {1: {1}, 2: {2}})
        rounds(actors, bus, 3, "rebalance")
        groups = self.check(actors)
        self.assertEqual(len(groups[3]), 1)
        self.assertEqual(len(set.union(*groups.values())), 3)

    def test_utilization_swap_makes_room_without_spare_peer_count_slot(self):
        actors, bus = self.actors({1: {1: 1, 2: 2, 3: 3}, 2: {1: 2, 2: 1}},
                                 {1: {1}, 2: {2}}, cap=2)
        actors[0].agent.util = {1: 1.2, 2: 0.2, 3: 0.5}
        actors[1].agent.P = Params(cap_n=1)
        actors[1].agent.util = {1: 0.5, 2: 0.5}
        rounds(actors, bus, 1, "rebalance")
        self.assertEqual(self.check(actors), {1: {2, 3}, 2: {1}})

    def test_incapable_low_score_bidder_does_not_block_feasible_offload(self):
        actors, bus = self.actors({1: {1: 1, 4: 0}, 2: {2: 1, 4: 2}, 3: {2: 3}},
                                 {1: {1}, 2: {2}})
        rounds(actors, bus, 1, "rebalance")
        self.assertEqual(self.check(actors), {1: {1}, 2: {4}, 3: {2}})

    def test_lost_rebalance_bid_does_not_assign_competing_owners(self):
        actors, bus = self.actors({1: {1: 1, 4: 2}, 2: {2: 1, 4: 3}, 3: {1: 2, 2: 2}},
                                 {1: {1}, 2: {2}},
                                 loss=lambda s, d, k, q, a: k == "group_bid")
        rounds(actors, bus, 2, "rebalance")
        self.assertEqual(self.check(actors), {1: {1}, 2: {2}, 3: set()})


if __name__ == "__main__":
    unittest.main()
