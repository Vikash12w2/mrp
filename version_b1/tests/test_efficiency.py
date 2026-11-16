from dataclasses import replace
import unittest
from unittest.mock import patch
from ..demo import demo
from ..geometry import group_box, overlaps, distances, charging_cells
from ..grouping import group_workers
from ..twoshot import phase1
from ...twoshot import Options, phase1 as original_phase1
from ...model import WorkerSpec


class EfficiencyTests(unittest.TestCase):
    def test_region_failure_expands_and_rebuilds_plan(self):
        from ...bus import Bus
        from ...params import Params
        from ...twoshot import Result
        from ..planner import run_wavefront
        inst, _ = demo()
        inst = replace(inst, xmax=20, workers=(inst.workers[0],), n_rech=1)
        anchors, params = {1: (0, 0)}, Params(R_s=20)
        seen = []
        def solve(job):
            sub, opts, _ = job
            seen.append(opts.box)
            if len(seen) == 1:
                return Result(False, status='UNSAT'), 0.1
            return Result(True, T=2, M=2, nW=1,
                wpos={1: [None, (1, 1), (1, 1)]},
                rpos={1: [None, (0, 0), (0, 0)]},
                rsweep={1: [None, {(0, 0)}]}), 0.1
        with patch('mrp.version_b1.planner.solve_job', side_effect=solve):
            plans, _, _ = run_wavefront(inst, {1: {1}}, anchors, params,
                                         Bus(anchors, params.R_c), False)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(seen[0], seen[1])
        self.assertTrue(plans[1].live)
        self.assertEqual(len(plans[1].region_history), 2)

    def test_failed_higher_plan_never_becomes_unconstrained_fallback(self):
        from ...bus import Bus
        from ...params import Params
        from ...twoshot import Result
        from ..planner import run_wavefront
        inst, anchors = demo()
        params = Params(R_s=20)
        with patch('mrp.version_b1.planner.solve_job', return_value=(Result(False, status='UNSAT'), 0.1)):
            plans, _, _ = run_wavefront(inst, {1: {1}, 2: {2}}, anchors, params,
                                         Bus(anchors, params.R_c), False)
        self.assertEqual(plans[2].res.status, 'DEPENDENCY_BLOCKED')
        self.assertTrue(all(not g.fallback for g in plans.values()))

    def test_spatial_grouping_allows_unequal_counts(self):
        inst, anchors = demo()
        inst = replace(inst, xmax=20, workers=tuple(
            WorkerSpec(i, 8, (cell,), (1,)) for i, cell in enumerate(
                ((2, 1), (2, 2), (3, 1), (18, 1)), 1)))
        result = group_workers(inst, {1: (0, 0), 2: (20, 0)})
        self.assertEqual(result.grouping.groups, {1: [1, 2, 3], 2: [4]})

    def test_regions_are_local_and_can_be_independent(self):
        inst, _ = demo()
        inst = replace(inst, xmax=30)
        w1 = WorkerSpec(1, 8, ((2, 2),), (1,))
        w2 = WorkerSpec(2, 8, ((27, 2),), (1,))
        left = group_box(inst, (0, 0), [w1])
        right = group_box(inst, (30, 0), [w2])
        self.assertFalse(overlaps(left, right))
        self.assertLess(left[1], inst.xmax)

    def test_region_includes_obstacle_detour(self):
        inst, _ = demo()
        inst = replace(inst, ymax=7, obstacles=frozenset((3, y) for y in range(6)))
        w = WorkerSpec(1, 8, ((6, 1),), (1,))
        box = group_box(inst, (0, 1), [w], padding=0)
        self.assertGreaterEqual(box[3], 6)
        reachable = distances(inst, [(0, 1)], box=box)
        self.assertTrue(charging_cells(inst, w.loop[0]) & reachable.keys())

    def test_phase1_preserves_primary_wait_optimum(self):
        inst, _ = demo()
        inst = replace(inst, xmax=3, ymax=3, workers=(inst.workers[0],), n_rech=1, T=5)
        options = Options(box=(0, 3, 0, 3))
        before = original_phase1(inst, options)
        after = phase1(inst, options)
        self.assertIsNotNone(after)
        self.assertEqual(after['wait'], before['wait'])


if __name__ == '__main__':
    unittest.main()
