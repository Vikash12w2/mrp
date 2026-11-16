from dataclasses import replace
import unittest
from unittest.mock import patch
import z3
from ...model import WorkerSpec
from ..demo import demo
from ..grouping import group_workers
from ..pipeline import run_b1


class B1Tests(unittest.TestCase):
    def test_balanced_complete_deterministic_assignment(self):
        inst, anchors = demo()
        a = group_workers(inst, anchors)
        b = group_workers(inst, anchors)
        self.assertEqual(a.status, 'OPTIMAL')
        self.assertEqual(a.grouping.groups, {1: [1], 2: [2]})
        self.assertEqual(a.grouping.groups, b.grouping.groups)
        self.assertFalse(a.grouping.unclaimed)

    def test_single_recharger_has_no_capacity_cutoff(self):
        inst, _ = demo()
        inst = replace(inst, n_rech=1, workers=tuple(replace(inst.workers[0], id=i) for i in range(1, 9)))
        r = group_workers(inst, {1: (0, 0)})
        self.assertEqual(r.objectives['assigned'], 8)
        self.assertEqual(r.grouping.groups, {1: list(range(1, 9))})

    def test_global_knowledge_can_assign_distant_worker(self):
        inst, _ = demo()
        w = WorkerSpec(1, 8, ((19, 1), (20, 1)), (1, 1))
        inst = replace(inst, xmax=22, n_rech=1, workers=(w,))
        self.assertEqual(group_workers(inst, {1: (0, 0)}).grouping.groups, {1: [1]})

    def test_obstacle_separated_worker_is_reported_unreachable(self):
        inst, _ = demo()
        inst = replace(inst, n_rech=1, workers=(inst.workers[1],),
                       obstacles=frozenset((3, y) for y in range(5)))
        r = group_workers(inst, {1: (0, 0)})
        self.assertEqual(r.grouping.groups, {1: []})
        self.assertEqual(r.grouping.unclaimed, {2: 'unreachable_in_region'})

    def test_demand_balances_when_spatial_costs_tie(self):
        inst, anchors = demo()
        anchors = {1: (0, 2), 2: (6, 2)}
        base = replace(inst.workers[0], loop=((3, 2),))
        inst = replace(inst, workers=tuple(replace(base, id=i, req=(q,))
                                           for i, q in enumerate((4, 4, 1, 1), 1)))
        r = group_workers(inst, anchors)
        self.assertEqual(sorted(map(len, r.grouping.groups.values())), [2, 2])
        self.assertEqual(list(r.objectives['demand_loads'].values()), [2.5, 2.5])

    def test_empty_instance_and_no_rechargers(self):
        inst, _ = demo()
        r = group_workers(replace(inst, workers=(), n_rech=0), {})
        self.assertEqual(r.status, 'OPTIMAL')
        self.assertEqual(r.objectives['assigned'], 0)
        r = group_workers(replace(inst, n_rech=0), {})
        self.assertEqual(len(r.grouping.unclaimed), 2)

    def test_unknown_does_not_run_planner_or_claim_optimality(self):
        inst, anchors = demo()
        with patch('z3.Optimize.check', return_value=z3.unknown), \
             patch('z3.Optimize.reason_unknown', return_value='test resource exhaustion'), \
             patch('mrp.version_b1.pipeline.run_wavefront') as planner:
            r = run_b1(inst, anchors)
        planner.assert_not_called()
        self.assertEqual(r.report()['grouping_status'], 'UNKNOWN')
        self.assertFalse(r.report()['executable'])

    def test_grouping_only_does_not_call_smt_planner(self):
        inst, anchors = demo()
        with patch('mrp.version_b1.pipeline.run_wavefront') as planner:
            r = run_b1(inst, anchors, grouping_only=True)
        planner.assert_not_called()
        self.assertEqual(r.report()['planning_status'], 'NOT_RUN')
        self.assertFalse(r.report()['executable'])

    def test_full_assignment_does_not_hide_fallback_or_failed_plans(self):
        from types import SimpleNamespace
        from ...planner import GroupPlan
        from ...twoshot import Result
        from ..pipeline import B1Result
        inst, anchors = demo()
        central = group_workers(inst, anchors)
        for fallback, served in ((True, 2), (False, 1)):
            plans = {1: GroupPlan(1, [1], anchors[1], Result(True, M=5), fallback=fallback)}
            metrics = dict(served=served, fallback_groups=[1] if fallback else [], deployment_ready=True)
            local = SimpleNamespace(inst=inst, anchors=anchors, plans=plans, conflicts=[], metrics=lambda: metrics)
            report = B1Result(central, local, 0).report()
            self.assertEqual(report['objectives']['assigned'], 2)
            self.assertFalse(report['executable'])

    def test_exclusions_keep_coverage_and_eventually_exhaust(self):
        inst, anchors = demo()
        excluded = []
        for _ in range(4):
            result = group_workers(inst, anchors, excluded=excluded, min_assigned=2)
            self.assertEqual(result.status, 'OPTIMAL')
            self.assertEqual(result.objectives['assigned'], 2)
            self.assertNotIn(result.grouping.groups, excluded)
            excluded.append(result.grouping.groups)
        result = group_workers(inst, anchors, excluded=excluded, min_assigned=2)
        self.assertEqual(result.status, 'UNSAT')

    def retry_fixture(self, reject):
        # Isolate retry orchestration from SMT search/tie-breaking. Real SMT is
        # exercised separately by the CLI demo.
        from ...planner import GroupPlan, overlap_neighbors
        from ...twoshot import Result
        calls = []
        def solve(inst, groups, anchors, params, bus, parallel):
            calls.append({r: sorted(ws) for r, ws in groups.items()})
            plans = {}
            for r, ws in groups.items():
                result = None
                if ws:
                    if reject(len(calls)):
                        result = Result(False, status='UNKNOWN', note='injected search limit')
                    else:
                        result = Result(True, T=2, M=2, nW=len(ws),
                            wpos={w.id: [None, w.loop[0], w.loop[0]] for w in inst.workers if w.id in ws},
                            rpos={1: [None, anchors[r], anchors[r]]},
                            rsweep={1: [None, {anchors[r]}]})
                plans[r] = GroupPlan(r, sorted(ws), anchors[r], result)
            return plans, [(i, r) for i, r in enumerate(anchors)], overlap_neighbors(anchors, params.R_s)
        return calls, solve

    def test_failed_partition_is_replaced_and_verified(self):
        inst, anchors = demo()
        calls, solve = self.retry_fixture(lambda attempt: attempt == 1)
        with patch('mrp.version_b1.pipeline.run_wavefront', side_effect=solve):
            result = run_b1(inst, anchors)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertTrue(result.report()['executable'])
        self.assertEqual(result.retry_status, 'SUCCESS')
        self.assertEqual(result.attempts[0]['planning_status'], 'UNKNOWN')

    def test_retry_limit_preserves_failure_diagnostics(self):
        inst, anchors = demo()
        calls, solve = self.retry_fixture(lambda attempt: True)
        with patch('mrp.version_b1.pipeline.run_wavefront', side_effect=solve):
            result = run_b1(inst, anchors, max_regroupings=1)
        self.assertEqual(len(calls), 2)
        self.assertEqual(result.retry_status, 'RETRY_LIMIT_REACHED')
        self.assertFalse(result.report()['executable'])
        self.assertEqual(result.central.objectives['assigned'], 2)

    def test_all_assignments_exhausted_without_dropping_workers(self):
        inst, anchors = demo()
        calls, solve = self.retry_fixture(lambda attempt: True)
        with patch('mrp.version_b1.pipeline.run_wavefront', side_effect=solve):
            result = run_b1(inst, anchors, max_regroupings=10)
        self.assertEqual(len(calls), 4)
        self.assertEqual(result.retry_status, 'ASSIGNMENTS_EXHAUSTED')
        self.assertEqual(result.central.objectives['assigned'], 2)
        self.assertFalse(result.report()['executable'])

    def test_zero_regroupings_means_one_planning_attempt(self):
        inst, anchors = demo()
        calls, solve = self.retry_fixture(lambda attempt: True)
        with patch('mrp.version_b1.pipeline.run_wavefront', side_effect=solve):
            result = run_b1(inst, anchors, max_regroupings=0)
        self.assertEqual(len(calls), 1)
        self.assertFalse(result.report()['executable'])

    def test_invalid_anchors_rejected(self):
        inst, _ = demo()
        with self.assertRaises(ValueError):
            group_workers(inst, {1: (0, 0), 2: (0, 0)})


if __name__ == '__main__':
    unittest.main()
