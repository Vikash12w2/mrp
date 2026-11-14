import unittest
from pathlib import Path
from mrp.benchmark import matrix, tiled
from mrp.model import Instance, Prim, WorkerSpec


class BenchmarkTests(unittest.TestCase):
    def test_tiles_preserve_worker_energy_and_translate_geometry(self):
        worker = WorkerSpec(1, 10, ((1, 1), (2, 1)), (2, 3),
                            (((1, 1), (2, 1)), ((2, 1), (1, 1))))
        source = Instance(3, 3, frozenset({(0, 0)}),
                          (Prim(0, 0, 0, 0, ((0, 0),)),), (worker,), 8, 1, 2)
        result = tiled(source, 2, 4)
        self.assertEqual((result.xmax + 1, result.ymax + 1), (8, 8))
        self.assertEqual(len({w.id for w in result.workers}), 4)
        self.assertEqual(result.obstacles, {(0, 0), (4, 0), (0, 4), (4, 4)})
        self.assertEqual(result.workers[1].loop, ((5, 1), (6, 1)))
        self.assertTrue(all(w.req == (2, 3) and w.fch == 10 for w in result.workers))
        self.assertIn((5, 1), result.workers[1].swaths[0])

    def test_matrix_keeps_grids_small_and_cost_cases_matched(self):
        jobs = matrix(Path("examples"), 1)
        self.assertEqual(len({j["id"] for j in jobs}), len(jobs))
        self.assertTrue(all(j["tiles"] == 1 and j.get("grid_side", 19) <= 25 for j in jobs))
        self.assertTrue(any(j.get("grid_side") == 25 for j in jobs))
        for job in jobs:
            if job["options"].get("phase2_optimize_cost"):
                self.assertIn(job["workers"], (1, 2))
                self.assertEqual(job["rechargers"], 1)
                self.assertTrue(any(other["input"] == job["input"]
                                    and other.get("grid_side") == job.get("grid_side")
                                    and other["workers"] == job["workers"]
                                    and other["approach"] == job["approach"]
                                    and other["seed"] == job["seed"]
                                    and other["variant"] == "cost_off_small" for other in jobs))
        first_cost = next(i for i, j in enumerate(jobs) if j["variant"] == "cost")
        self.assertTrue(any(j["variant"] == "refine5" for j in jobs[:first_cost]))
        self.assertEqual(jobs[-1]["approach"], "centralized")
        self.assertEqual(jobs[-1]["variant"], "cost")

    def test_collision_off_cases_match_baseline_scenario_and_approach(self):
        jobs = matrix(Path("examples"), 1)
        off = [j for j in jobs if j["variant"] == "collisions_off"]
        self.assertEqual(len(off), 32)
        for job in off:
            self.assertFalse(job["options"]["collisions"])
            self.assertTrue(any(other["variant"] == "base"
                                and all(other.get(k) == job.get(k) for k in
                                        ("input", "workers", "rechargers", "T", "grid_side", "approach", "seed"))
                                for other in jobs))


if __name__ == "__main__":
    unittest.main()
