import unittest
from dataclasses import replace
from mrp.model import Instance, Prim, WorkerSpec, worker_edge_cells
from mrp.twoshot import Options, phase1
from mrp.twoshot import _one_shot_result
from mrp.planner import GroupPlan
from mrp.runtime import verify
from mrp.planner import build_job
from mrp.params import Params


class WorkerSweepTests(unittest.TestCase):
    def setUp(self):
        self.worker = WorkerSpec(1, 10, ((0, 1), (4, 1)), (1, 1))
        self.inst = Instance(4, 2, frozenset(), (Prim(0, 0, 0, 0, ((0, 0),)),),
                             (self.worker,), 3, 1, 1)

    def test_legacy_edges_are_abstract(self):
        self.assertEqual(worker_edge_cells(self.inst, self.worker, 0), {(0, 1), (4, 1)})

    def test_explicit_route_overrides_inference(self):
        cells = ((0, 1), (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1))
        worker = replace(self.worker, swaths=(cells, tuple(reversed(cells))))
        self.assertEqual(worker_edge_cells(self.inst, worker, 0), set(cells))

    def test_collision_in_middle_of_worker_step(self):
        fixed = {"wprim_1_1": 1, "rx_1_1": 2, "ry_1_1": 1}
        self.assertIsNotNone(phase1(self.inst, Options(), fixed))
        straight = replace(self.inst, worker_motion="straight")
        self.assertEqual(worker_edge_cells(straight, self.worker, 0), {(x, 1) for x in range(5)})
        self.assertIsNone(phase1(straight, Options(), fixed))
        self.assertIsNotNone(phase1(straight, Options(collisions=False), fixed))
        detour = ((0, 1), (0, 0), (1, 0), (2, 0), (3, 0), (4, 0), (4, 1))
        worker = replace(self.worker, swaths=(detour, tuple(reversed(detour))))
        self.assertIsNotNone(phase1(replace(self.inst, workers=(worker,)), Options(), fixed))

    def test_local_start_can_differ_from_anchor(self):
        job = build_job(1, {1}, self.inst, {1: (2, 0)}, {}, {1: []}, Params())
        plan = phase1(job[0], job[1], {"rx_1_1": 1, "ry_1_1": 0})
        self.assertIsNotNone(plan)
        self.assertEqual((plan["rx"][1][0], plan["ry"][1][0]), (1, 0))

    def test_verifier_detects_collision_when_solver_constraints_are_off(self):
        inst = replace(self.inst, worker_motion="straight")
        p1 = phase1(inst, Options(collisions=False),
                    {"wprim_1_1": 1, "rx_1_1": 2, "ry_1_1": 1})
        self.assertIsNotNone(p1)
        result = _one_shot_result(inst, p1, 0)
        self.assertTrue(verify({1: GroupPlan(1, [1], (2, 1), result)}))


if __name__ == "__main__":
    unittest.main()
