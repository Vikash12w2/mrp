import unittest
from mrp_ros2.motion import interpolate, validate_segments
from test_schedule import plan
from mrp_ros2.schedule import Schedule


class MotionTests(unittest.TestCase):
    def test_interpolation_and_clamping(self):
        a={'r1':(0,0),'w1':(3,0)};b={'r1':(1,1),'w1':(3,1)}
        self.assertEqual(interpolate(a,b,.5),{'r1':(.5,.5),'w1':(3,.5)})
        self.assertEqual(interpolate(a,b,2),b)
        self.assertEqual(interpolate(a,b,-1),a)

    def test_waits_do_not_move(self):
        self.assertEqual(interpolate({'w1':(2,3)},{'w1':(2,3)},.7),{'w1':(2.,3.)})

    def test_continuous_crossing_rejected(self):
        with self.assertRaisesRegex(ValueError,'intersect'):
            validate_segments({'r1':(0,0),'w1':(1,0)}, {'r1':(1,1),'w1':(0,1)},set())

    def test_jump_through_obstacle_rejected(self):
        with self.assertRaisesRegex(ValueError,'obstacle'):
            validate_segments({'w1':(0,0)},{'w1':(2,0)},{(1,0)})

    def test_failed_actor_removed_but_cell_reserved(self):
        s=Schedule(plan());s.disabled['w1']=(2,0)
        self.assertNotIn('w1',s.frame(1))
        with self.assertRaisesRegex(ValueError,'obstacle'):s.transition(2)

if __name__=='__main__':unittest.main()
