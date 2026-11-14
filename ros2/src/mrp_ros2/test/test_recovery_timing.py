import unittest
from mrp_ros2.recovery_timing import declaration_tick


class RecoveryTimingTests(unittest.TestCase):
    def test_failure_on_slot_skips_that_slot(self):
        self.assertEqual(declaration_tick(5,5,1,3),8)
        self.assertEqual(declaration_tick(6,5,2,3),12)

    def test_failure_between_slots_uses_next_slot(self):
        self.assertEqual(declaration_tick(5,5,2,3),10)
        self.assertEqual(declaration_tick(7,5,2,3),12)

    def test_setup_offset_does_not_change_missed_count(self):
        for tick in range(20):
            for period in (1,2,3):
                self.assertEqual(declaration_tick(tick+9,9,period,3),declaration_tick(tick,0,period,3)+9)

    def test_rejects_setup_failure_or_invalid_policy(self):
        for args in ((4,5,1,3),(5,5,0,3),(5,5,1,0)):
            with self.assertRaises(ValueError):declaration_tick(*args)

if __name__=='__main__':unittest.main()
