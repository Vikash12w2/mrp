import unittest
from unittest.mock import patch

from mrp.compare import centralized_row
from mrp.model import Instance, Prim, WorkerSpec
from mrp.twoshot import Options, Result, SolverUnknown, phase2, solve


class SolverReportingTests(unittest.TestCase):
    def setUp(self):
        self.inst = Instance(3, 2, frozenset(), (Prim(0, 0, 0, 0, ((0, 0),)),),
                             (WorkerSpec(1, 10, ((1, 1),), (1,)),), 4, 1, 1)

    def test_real_resource_limit_is_unknown_not_unsat(self):
        result = solve(self.inst, Options(rlimit=1))
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "UNKNOWN")
        self.assertNotIn("UNSAT", result.note)

    def test_proven_phase1_infeasible_has_scoped_unsat_status(self):
        with patch("mrp.twoshot.phase1", return_value=None):
            result = solve(self.inst)
        self.assertEqual(result.status, "UNSAT")
        self.assertIn("configured horizon", result.note)

    def phase2_outcome(self, options, **mock_args):
        with patch("mrp.twoshot.phase1", return_value={}), patch(
                "mrp.twoshot._halts_and_instances", return_value=({1: (1, 0, 1)}, [])), patch(
                "mrp.twoshot.phase2", **mock_args) as second:
            result = solve(self.inst, options)
            return result, second.call_count

    def test_unknown_does_not_skip_to_a_larger_horizon(self):
        result, calls = self.phase2_outcome(Options(), side_effect=SolverUnknown("resource limit"))
        self.assertEqual(result.status, "UNKNOWN")
        self.assertEqual(calls, 1)

    def test_search_bound_is_not_a_global_infeasibility_claim(self):
        result, calls = self.phase2_outcome(Options(max_extra=1), return_value=None)
        self.assertEqual(result.status, "SEARCH_EXHAUSTED")
        self.assertEqual(calls, 2)
        result, _ = self.phase2_outcome(Options(align_M=4), return_value=None)
        self.assertEqual(result.status, "UNSAT")
        self.assertIn("selected phase-1 schedule", result.note)

    def test_failed_comparison_has_no_coverage_efficiency_or_collision_claim(self):
        row = centralized_row(Result(False, status="UNKNOWN"), [], 1.0)
        for key in ("coverage", "hard", "soft", "same", "cross", "T_prime"):
            self.assertIsNone(row[key])
        self.assertEqual(row["status"], "UNKNOWN")
        self.assertEqual(centralized_row(Result(True), [], 1.0)["coverage"], 100.0)

    def test_phase2_preserves_start_and_optional_cost_objective(self):
        prims = (Prim(0, 0, 0, 10, ((0, 0),)),
                 Prim(1, 1, 0, 0, ((0, 0), (1, 0))),
                 Prim(2, -1, 0, 0, ((0, 0), (-1, 0))))
        inst = Instance(1, 1, frozenset(), prims, (), 3, 1, 1)
        p1 = {"T": 3, "rx": {1: [1]}, "ry": {1: [0]}}
        for optimized in (False, True):
            result = phase2(inst, Options(phase2_optimize_cost=optimized), p1, {}, [], 3)
            self.assertIsNotNone(result)
            positions, selected, _ = result
            self.assertEqual(positions[1][1], (1, 0))
            self.assertEqual(positions[1][-1], (1, 0))
            if optimized:
                self.assertEqual(sum(prims[k - 1].cost for k in selected[1][1:]), 0)


if __name__ == "__main__":
    unittest.main()
