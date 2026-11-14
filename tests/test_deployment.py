import unittest
from itertools import combinations
from mrp.bus import Bus
from mrp.deployment import plan_deployment, SimulatedSetupController, SetupExecutor
from mrp.model import Instance, Prim, WorkerSpec
from mrp.params import Params
from mrp.planner import GroupPlan
from mrp.twoshot import Result


class DeploymentTests(unittest.TestCase):
    def build(self, anchors, goals, obstacles=(), workers=(), radius=5, loss=None, controller=None):
        ds = [(0, 0), (1, 0), (0, 1), (-1, 0), (0, -1)]
        prims = tuple(Prim(i, dx, dy, 0, tuple({(0, 0), (dx, dy)}))
                      for i, (dx, dy) in enumerate(ds))
        inst = Instance(6, 4, frozenset(obstacles), prims, tuple(workers), 4, 1, len(anchors))
        plans = {r: GroupPlan(r, [], a, Result(True, rpos={1: [None, goals[r]]}))
                 for r, a in anchors.items()}
        P = Params(R_s=radius)
        bus = Bus(anchors, P.R_c, loss=loss)
        result = plan_deployment(inst, plans, anchors, P, bus, controller)
        return result, bus

    def assert_no_collisions(self, result):
        self.assertTrue(result.ready)
        for a, b in combinations(result.paths.values(), 2):
            for tick in range(result.steps + 1):
                self.assertFalse(a.occupied(tick) & b.occupied(tick))

    def test_obstacle_detour(self):
        result, _ = self.build({1: (0, 0)}, {1: (2, 0)}, obstacles={(1, 0)})
        self.assertTrue(result.ready)
        path = result.paths[1]
        self.assertEqual((path.cells[0], path.cells[-1]), ((0, 0), (2, 0)))
        self.assertGreater(len(path.sweeps), 2)
        self.assertTrue(all((1, 0) not in s for s in path.sweeps))

    def test_crossing_routes_and_parked_endpoints(self):
        result, bus = self.build({1: (0, 1), 2: (2, 0)}, {1: (3, 1), 2: (2, 2)})
        self.assert_no_collisions(result)
        self.assertEqual(result.start_ticks[1], result.start_ticks[2])
        self.assertGreaterEqual(bus.stats["setup_path"], 1)

    def test_workers_wait_during_setup(self):
        w = WorkerSpec(1, 10, ((1, 0), (1, 1)), (1, 1))
        result, _ = self.build({1: (0, 0)}, {1: (2, 0)}, workers=[w])
        self.assertTrue(result.ready)
        self.assertTrue(all((1, 0) not in s for s in result.paths[1].sweeps))

    def test_unreachable_goal_blocks_start(self):
        result, _ = self.build({1: (0, 0)}, {1: (2, 0)}, obstacles={(1, y) for y in range(5)})
        self.assertFalse(result.ready)
        self.assertFalse(result.paths[1].ok)

    def test_persistent_route_loss_waits_without_declaring_failure(self):
        result, bus = self.build({1: (0, 1), 2: (2, 0)}, {1: (3, 1), 2: (2, 2)},
                                 loss=lambda s, d, k, q, a: k == "setup_path")
        self.assertFalse(result.ready)
        self.assertFalse(result.paths[2].ok)
        self.assertFalse(bus.unreachable)
        self.assertEqual(result.status, "TIMEOUT")

    def test_periodic_broadcasts_recover_transient_losses(self):
        dropped = {"setup_path": 5, "setup_ready": 3, "setup_position": 4}
        def loss(s, d, kind, q, a):
            if dropped.get(kind, 0):
                dropped[kind] -= 1
                return True
            return False
        result, bus = self.build({1: (0, 1), 2: (2, 0)}, {1: (3, 1), 2: (2, 2)}, loss=loss)
        self.assertTrue(result.ready)
        self.assertFalse(bus.unreachable)
        self.assertGreater(bus.stats["periodic_drop"], 0)

    def test_position_loss_keeps_arrival_barrier_closed_and_can_resume(self):
        result, bus = self.build({1: (0, 1), 2: (2, 0)}, {1: (3, 1), 2: (2, 2)},
                                 loss=lambda s, d, k, q, a: k == "setup_position")
        self.assertTrue(result.plan_ready)
        self.assertFalse(result.ready)
        self.assertEqual(result.status, "WAITING")
        self.assertEqual(result.start_ticks, {})
        bus.loss = None
        self.assertTrue(result.execution.retry())
        self.assertTrue(result.ready)

    def test_observed_arrival_can_be_delayed(self):
        class SlowController(SimulatedSetupController):
            def __init__(self, anchors):
                super().__init__(anchors)
                self.pending = {}
            def command(self, rid, destination):
                self.pending[rid] = [destination, 3]
            def position(self, rid):
                if rid in self.pending:
                    dest, remaining = self.pending[rid]
                    if remaining == 0:
                        self.positions[rid] = dest
                        del self.pending[rid]
                    else:
                        self.pending[rid][1] -= 1
                return self.positions[rid]
        anchors = {1: (0, 0)}
        result, _ = self.build(anchors, {1: (2, 0)}, controller=SlowController(anchors))
        self.assertTrue(result.ready)
        self.assertEqual(result.execution.positions[1], (2, 0))
        self.assertGreater(result.steps, len(result.paths[1].sweeps) + 1)

    def test_wall_clock_wait_is_capped_and_does_not_reset_on_poll(self):
        anchors = {1: (0, 1), 2: (2, 0)}
        result, bus = self.build(anchors, {1: (3, 1), 2: (2, 2)})
        now = [0.0]
        bus.loss = lambda s, d, k, q, a: k == "setup_position"
        result.execution = SetupExecutor(result, anchors, Params(setup_timeout_s=2.0), bus,
                                         clock=lambda: now[0])
        self.assertFalse(result.execution.poll())
        now[0] = 1.9
        self.assertFalse(result.execution.poll())
        self.assertFalse(result.execution.timed_out)
        now[0] = 2.01
        self.assertFalse(result.execution.poll())
        self.assertEqual(result.status, "TIMEOUT")
        self.assertEqual(result.start_ticks, {})

    def test_movement_longer_than_timeout_is_allowed_with_live_heartbeats(self):
        class HoldingController(SimulatedSetupController):
            released = False
            def __init__(self, anchors):
                super().__init__(anchors)
                self.targets = {}
            def command(self, rid, destination):
                self.targets[rid] = destination
            def position(self, rid):
                if self.released and rid in self.targets:
                    self.positions[rid] = self.targets[rid]
                return self.positions[rid]
        anchors = {1: (0, 1), 2: (2, 0)}
        result, bus = self.build(anchors, {1: (3, 1), 2: (2, 2)})
        now = [0.0]
        controller = HoldingController(anchors)
        result.execution = SetupExecutor(result, anchors, Params(setup_timeout_s=2.0), bus,
                                         controller=controller, clock=lambda: now[0])
        for second in range(7):
            now[0] = float(second)
            self.assertFalse(result.execution.run(max_polls=1))
            self.assertFalse(result.execution.timed_out)
        self.assertEqual(result.status, "WAITING")
        self.assertEqual(result.start_ticks, {})
        controller.released = True
        self.assertTrue(result.execution.run())
        self.assertTrue(result.ready)

    def test_missing_local_telemetry_times_out(self):
        class SilentController(SimulatedSetupController):
            def position(self, rid):
                return None
        anchors = {1: (0, 0)}
        result, bus = self.build(anchors, {1: (2, 0)})
        now = [0.0]
        result.execution = SetupExecutor(result, anchors, Params(), bus,
                                         controller=SilentController(anchors), clock=lambda: now[0])
        self.assertFalse(result.execution.poll())
        now[0] = 2.01
        self.assertFalse(result.execution.poll())
        self.assertEqual(result.status, "TIMEOUT")

    def test_unexpected_reported_position_blocks_execution(self):
        controller = SimulatedSetupController({1: (6, 4)})
        result, _ = self.build({1: (0, 0)}, {1: (2, 0)}, controller=controller)
        self.assertFalse(result.ready)
        self.assertEqual(result.status, "BLOCKED")

    def test_neighbor_barrier_propagates_through_chain(self):
        result, bus = self.build({1: (0, 1), 2: (3, 1), 3: (6, 1)},
                                 {1: (1, 1), 2: (3, 2), 3: (5, 3)}, radius=2)
        self.assert_no_collisions(result)
        self.assertEqual(len(set(result.start_ticks.values())), 1)
        self.assertFalse(bus.in_range(1, 3))
        self.assertFalse(bus.unreachable)


if __name__ == "__main__":
    unittest.main()
