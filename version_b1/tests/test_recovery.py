from dataclasses import replace
import unittest
from unittest.mock import patch, MagicMock
from ..demo import demo
from ..planner import run_wavefront
from ..twoshot import solve_isolated
from ...bus import Bus
from ...params import Params
from ...twoshot import Result


class RecoveryTests(unittest.TestCase):
    def fixture(self):
        inst, anchors = demo()
        calls = []
        def solve(job):
            sub, opts, horizons = job
            ids = tuple(w.id for w in sub.workers)
            calls.append((ids, opts.align_M, opts.ext.period if opts.ext else None))
            M = opts.align_M or 8
            if ids == (2,) and M == 8:
                return Result(False, status='UNSAT'), .1
            r = ids[0]
            return Result(True, T=8, M=M, nW=len(ids),
                wpos={w.id: [None]+[w.loop[0]]*M for w in sub.workers},
                rpos={1: [None]+[anchors[r]]*M},
                rsweep={1: [None]+[{anchors[r]} for _ in range(M-1)]}), .1
        return inst, anchors, calls, solve

    def test_cycle_extension_rebuilds_upstream_and_occupancy(self):
        inst, anchors, calls, solve = self.fixture()
        params = Params(R_s=20)
        with patch('mrp.version_b1.planner.solve_job', side_effect=solve):
            plans, _, _ = run_wavefront(inst, {1:{1},2:{2}}, anchors, params,
                                        Bus(anchors, params.R_c), False)
        self.assertEqual(calls, [((1,),None,None), ((2,),8,7),
                                 ((1,),12,None), ((2,),12,11)])
        self.assertTrue(all(g.live and g.res.M == 12 for g in plans.values()))
        self.assertTrue(plans[1].region_history[-1]['valid'])

    def test_cache_reuses_only_matching_inputs(self):
        inst, anchors, calls, solve = self.fixture()
        inst = replace(inst, workers=(inst.workers[0],), n_rech=1)
        anchors={1:anchors[1]}
        params, cache = Params(R_s=20), {}
        with patch('mrp.version_b1.planner.solve_job', side_effect=solve):
            for scenario in (inst, inst, replace(inst, delta_max=3)):
                run_wavefront(scenario, {1:{1}}, anchors, params,
                              Bus(anchors, params.R_c), False, cache=cache)
        self.assertEqual(len(calls), 2)

    def test_isolated_solver_waits_without_time_cutoff(self):
        inst, _ = demo()
        context = MagicMock()
        pool = context.Pool.return_value.__enter__.return_value
        expected = Result(False, status='SEARCH_EXHAUSTED')
        pool.apply.return_value = expected
        with patch('multiprocessing.get_context', return_value=context):
            result = solve_isolated(inst)
        self.assertIs(result, expected)
        pool.apply.assert_called_once()
        pool.apply_async.assert_not_called()

    def test_conflicting_candidate_not_accepted(self):
        inst, anchors, _, solve = self.fixture()
        def unsafe(job):
            result, wall = solve(job)
            if result.ok:
                result.rsweep = {1:[None]+[{(0,0)} for _ in range(result.M-1)]}
            return result, wall
        params = Params(R_s=20)
        with patch('mrp.version_b1.planner.solve_job', side_effect=unsafe):
            plans, _, _ = run_wavefront(inst, {1:{1},2:{2}}, anchors, params,
                                        Bus(anchors, params.R_c), False)
        self.assertFalse(plans[1].region_history[-1]['valid'])
