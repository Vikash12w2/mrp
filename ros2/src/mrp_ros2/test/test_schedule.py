import copy
import unittest
from mrp_ros2.schedule import Schedule


def plan():
    return dict(status='OK',schema=2,session='test',
        map=dict(xmax=8,ymax=6,obstacles=[]),
        setup={'r1':[[0,0],[1,0]]},setup_sweeps={'r1':[[[0,0],[1,0]]]},
        tracks={'r1':dict(path=[[1,0],[1,0],[2,0],[1,0]],
                          sweeps=[[[1,0]],[[1,0],[2,0]],[[2,0],[1,0]]]),
                'w1':dict(path=[[4,0],[4,1],[4,0]],
                          sweeps=[[[4,0],[4,1]],[[4,1],[4,0]]])})


class ScheduleTests(unittest.TestCase):
    def test_setup_holds_workers_and_wait_is_a_step(self):
        s=Schedule(plan())
        self.assertEqual(s.frame(0),{'r1':(0,0),'w1':(4,0)})
        self.assertEqual(s.transition(0),{'r1':(1,0),'w1':(4,0)})
        self.assertEqual(s.transition(1),{'r1':(1,0),'w1':(4,1)})
        self.assertEqual(s.transition(2)['r1'],(2,0))

    def test_multiple_periods_repeat_indefinitely(self):
        s=Schedule(plan())
        for tick in range(1,1000):
            self.assertEqual(s.transition(tick),s.frame(tick+1))
            self.assertEqual(s.frame(tick),s.frame(tick+6))

    def test_sweep_collision_is_rejected_before_dispatch(self):
        p=plan()
        p['tracks']['w1']['sweeps'][0].append([2,0])
        s=Schedule(p)
        s.transition(1)
        with self.assertRaisesRegex(ValueError,'collision'):
            s.transition(3)

    def test_invalid_plans_rejected(self):
        for mutate in (
            lambda p:p.update(status='PARTIAL'),
            lambda p:p['tracks']['r1']['path'].__setitem__(-1,[3,0]),
            lambda p:p['setup']['r1'].__setitem__(-1,[3,0]),
            lambda p:p['tracks']['r1']['sweeps'].pop(),
        ):
            p=plan(); mutate(p)
            with self.assertRaises(ValueError):Schedule(p)

    def test_parked_recharger_is_reserved(self):
        p=plan()
        p['setup']['r2']=[[2,0]];p['setup_sweeps']['r2']=[]
        p['tracks']['r2']=dict(path=[[2,0],[2,0]],sweeps=[[[2,0]]])
        s=Schedule(p)
        with self.assertRaisesRegex(ValueError,'collision'):s.transition(2)

    def test_out_of_bounds_and_obstacles(self):
        p=plan();p['map']['obstacles']=[[2,0]]
        with self.assertRaisesRegex(ValueError,'obstacle'):Schedule(p).transition(2)

if __name__=='__main__':unittest.main()
