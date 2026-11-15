import unittest
from unittest.mock import patch

from mrp.bus import Bus
from mrp.failure import Failure, RecoveryActor, propagate_replans
from mrp.grouping import Agent, run_grouping
from mrp.model import Instance, Prim, WorkerSpec
from mrp.negotiation import rounds
from mrp.params import Params
from mrp.planner import GroupPlan
from mrp.refinement import Refiner, refine
from mrp.twoshot import Result


def instance(workers=()):
    moves = [(0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)]
    prims = tuple(Prim(k, x, y, 1, tuple({(0, 0), (x, y)})) for k, (x, y) in enumerate(moves))
    return Instance(30, 6, frozenset(), prims, tuple(workers), 4, 1, 2)


def worker(wid, *cells):
    return WorkerSpec(wid, 20, tuple(cells), (1,) * len(cells))


def plan(rid, members, anchor, wait=0):
    return GroupPlan(rid, sorted(members), anchor,
                     Result(True, T=4, M=4, nW=len(members), p1_wait=wait) if members else None)


class LocalExtensionTests(unittest.TestCase):
    def refinement_actors(self, loss=None):
        inst = instance([worker(1, (2, 1)), worker(2, (3, 1))])
        anchors = {1: (1, 0), 2: (4, 0)}
        P = Params(R_s=5, cap_n=2, K_refine=1)
        bus = Bus(anchors, P.R_c, loss=loss)
        actors = []
        for rid, anchor in anchors.items():
            a = Agent(rid, anchor, inst, P)
            for w in inst.workers:
                a.hear(w)
            # Equal workload isolates the efficiency/transaction tests from balancing.
            a.util = {w: 0.1 for w in a.cand}
            a.neighbors = [n for n in anchors if n != rid]
            actors.append(Refiner(a, plan(rid, {rid}, anchor, wait=3), bus))
        return actors, bus

    def test_each_refinement_endpoint_solves_its_own_group_and_commits(self):
        actors, bus = self.refinement_actors()
        solved = []
        def solve_local(rid, group, inst, anchors, known, nbrs, P):
            self.assertEqual(set(anchors), {rid})
            self.assertEqual(set(nbrs), {rid})
            self.assertTrue(group <= {w.id for w in inst.workers})
            solved.append((rid, set(group), known))
            return plan(rid, group, anchors[rid])
        with patch("mrp.planner._plan_for", side_effect=solve_local):
            refine(actors, bus, 1)
        self.assertEqual([s[0] for s in solved], [1, 2])
        self.assertEqual([a.members for a in actors], [{2}, {1}])
        self.assertEqual(solved[1][2][1].workers, [2])
        self.assertEqual(sum(a.accepted for a in actors), 1)

    def test_refinement_dropped_messages_leave_both_original_plans(self):
        for kind in ("group_state", "refine_offer", "refine_accept", "refine_commit"):
            with self.subTest(kind=kind):
                actors, bus = self.refinement_actors(lambda s, d, k, q, a: k == kind)
                with patch("mrp.planner._plan_for", side_effect=lambda r, g, i, a, *rest: plan(r, g, a[r])):
                    refine(actors, bus, 1)
                self.assertEqual([a.members for a in actors], [{1}, {2}])
                self.assertEqual(sum(a.accepted for a in actors), 0)

    def test_refinement_rejects_unsolved_or_non_improving_trial(self):
        for outcome in ("unsolved", "no_gain", "fallback"):
            actors, bus = self.refinement_actors()
            def solve(r, g, i, anchors, *rest):
                if outcome == "unsolved":
                    return None
                p = plan(r, g, anchors[r], wait=3 * len(g))
                p.fallback = outcome == "fallback"
                return p
            with patch("mrp.planner._plan_for", side_effect=solve):
                refine(actors, bus, 1)
            self.assertEqual([a.members for a in actors], [{1}, {2}])

    def test_refinement_uses_real_two_shot_local_solve(self):
        actors, bus = self.refinement_actors()
        for a in actors:
            a.publish(1)
        for a in actors:
            a.read_states()
        first = actors[0].solve_local(set())
        self.assertIsNotNone(first)
        second = actors[1].solve_local({1, 2}, first)
        self.assertIsNotNone(second)
        self.assertTrue(second.live)
        self.assertEqual(second.workers, [1, 2])
        self.assertGreaterEqual(second.res.M, second.res.T)

    def test_region_and_capacity_failures_remain_explicitly_unserved(self):
        inst = instance([worker(1, (1, 1)), worker(2, (2, 1)),
                         worker(3, (2, 2), (8, 2)), worker(4, (29, 5))])
        anchors = {1: (1, 0)}
        P = Params(R_s=2, cap_n=1, K_unclaimed=2)
        result = run_grouping(inst, anchors, P, Bus(anchors, P.R_c))
        self.assertEqual(sum(map(len, result.groups.values())), 1)
        self.assertEqual(result.unclaimed[3], "loop_not_inside_any_region")
        self.assertEqual(result.unclaimed[4], "outside_all_regions")
        self.assertIn("capacity", result.unclaimed.values())
        self.assertNotIn(4, result.agents[1].heard)
        self.assertNotIn(4, {w.id for w in result.agents[1].inst.workers})

    def test_recovery_claims_only_locally_heard_orphans(self):
        inst = instance([worker(1, (2, 1)), worker(2, (24, 1))])
        P = Params(R_s=3, cap_n=2)
        anchors = {1: (1, 0), 2: (25, 0)}
        bus = Bus(anchors, P.R_c)
        actors = []
        for rid, anchor in anchors.items():
            a = Agent(rid, anchor, inst, P)
            a.neighbors = [3]  # the failed peer was locally known
            for w in inst.workers:
                if any(a.in_region(c) for c in w.loop):
                    a.hear(w)
            actor = RecoveryActor(a, (), plan(rid, (), anchor), bus)
            actor.observe(Failure("recharger", 3, 1), (10, 5))
            actors.append(actor)
        rounds(actors, bus, 2, "claim")
        self.assertEqual([a.members for a in actors], [{1}, {2}])
        self.assertFalse(bus.unreachable)

    def test_recovery_keeps_unreachable_existing_members_counted(self):
        inst = instance([worker(1, (2, 1))])
        a = Agent(1, (0, 0), inst, Params(R_s=3))
        a.hear(inst.workers[0])
        bus = Bus({1: a.anchor}, 6)
        actor = RecoveryActor(a, {1}, plan(1, {1}, a.anchor), bus)
        # Local obstacle changes cannot silently discard an existing assignment.
        actor.observe(Failure("recharger", 2, 1), (1, 0))
        rounds([actor], bus, 1, "claim")
        self.assertEqual(actor.members, {1})
        self.assertIn(1, actor.agent.util)
        propagate_replans([actor], bus)
        self.assertTrue(actor.dirty)


if __name__ == "__main__":
    unittest.main()
