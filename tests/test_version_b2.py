from dataclasses import replace
import unittest

from ..version_b1.demo import demo
from ..version_b2 import RechargerController, WorkerBroadcast, run_b2


class OnlineDispatchTests(unittest.TestCase):
    def setUp(self):
        self.inst, self.anchors = demo()
        # Both nonempty group regions overlap at the first worker.
        self.anchors = {1: (0, 1), 2: (2, 0)}
        self.state = WorkerBroadcast(0, 1, (1, 1), 0, 2, 8, 1)
        self.controller = RechargerController(self.inst, self.anchors, {1: [1], 2: [2]})

    def test_boundary_tie_and_service_lock(self):
        targets = self.controller.decide(0, self.anchors, [self.state])
        self.assertEqual(list(targets), [1])
        self.assertEqual(targets[1].worker, 1)
        positions = {1: (0, 4), 2: (2, 0)}
        targets = self.controller.decide(1, positions, [replace(self.state, tick=1)])
        self.assertEqual(list(targets), [1])
        self.controller.decide(2, positions, [replace(self.state, tick=2, charge=8)])
        self.assertFalse(self.controller.locks)

    def test_stale_broadcast_does_not_extend_lock(self):
        self.controller.decide(0, self.anchors, [self.state])
        self.assertFalse(self.controller.decide(1, self.anchors, [self.state]))
        self.assertTrue(self.controller.locks)
        self.controller.decide(3, self.anchors, [self.state])
        self.assertFalse(self.controller.locks)

    def test_energy_and_time_accounting(self):
        result = run_b2(self.inst, self.anchors, steps=24, trace=True)
        for w in self.inst.workers:
            stats = result['workers'][w.id]
            self.assertGreater(stats['charging'], 0)
            self.assertEqual(stats['moving'] + stats['waiting'] + stats['charging'], 24)
            self.assertEqual(w.fch - stats['moving'] + stats['energy_received'],
                             result['final_charge'][w.id])
        for row in result['trace']:
            targets = [bid['worker'] for bid in row['targets'].values()]
            self.assertEqual(len(targets), len(set(targets)))
