import argparse
import csv
from dataclasses import replace
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

DEFAULT_INPUT = Path(r"C:\Users\vikas\Downloads\MobileRobotRecharging-main\MobileRobotRecharging-main\mobile-recharger-path-planning-scheduling\approach_two-shot\examples")


def tiled(inst, size, count):
    """Repeat obstacle/route geometry without stretching worker motion or energy."""
    sx, sy = inst.xmax + 1, inst.ymax + 1
    offsets = [(x * sx, y * sy) for y in range(size) for x in range(size)]
    workers = []
    for worker in inst.workers:
        for dx, dy in offsets:
            workers.append(replace(worker, id=len(workers) + 1,
                                   loop=tuple((x + dx, y + dy) for x, y in worker.loop),
                                   swaths=tuple(tuple((x + dx, y + dy) for x, y in edge)
                                                for edge in worker.swaths)))
    if count > len(workers):
        raise ValueError("Requested more workers than the source/tiled scenario provides")
    return replace(inst, xmax=sx * size - 1, ymax=sy * size - 1,
                   obstacles=frozenset((x + dx, y + dy) for dx, dy in offsets for x, y in inst.obstacles),
                   workers=tuple(workers[:count]))


def scenario_anchors(inst):
    """Fixed experiment input layout, shared by every variant of this scenario."""
    from .deploy import pick_anchors
    if inst.xmax < 20 and inst.n_rech <= 3:
        return pick_anchors(inst, inst.n_rech, 6)
    # Avoid an exponential global coverage search during synthetic case setup.
    cols = math.ceil(math.sqrt(inst.n_rech))
    rows = math.ceil(inst.n_rech / cols)
    occupied = {w.loop[0] for w in inst.workers}
    free = [(x, y) for x in range(inst.xmax + 1) for y in range(inst.ymax + 1)
            if inst.free((x, y)) and (x, y) not in occupied]
    anchors = {}
    for k in range(inst.n_rech):
        target = ((k % cols + .5) * (inst.xmax + 1) / cols,
                  (k // cols + .5) * (inst.ymax + 1) / rows)
        cell = min((c for c in free if c not in anchors.values()),
                   key=lambda c: (max(abs(c[0] - target[0]), abs(c[1] - target[1])), c))
        anchors[k + 1] = cell
    return anchors


def run_case(case):
    from .centralized import run_centralized
    from .decentralized import run_decentralized
    from .model import load_instance
    from .params import Params
    from .twoshot import Options
    source = Path(case["input"])
    available = len(list(source.glob("rob*_traj.txt")))
    inst = load_instance(str(source), min(available, case["workers"]), case["rechargers"], case["T"])
    inst = tiled(inst, case["tiles"], case["workers"])
    anchors = scenario_anchors(inst) if case["approach"] == "decentralized" else None
    if case.get("grid_side"):
        side = case["grid_side"]
        if side > 25 or side <= max(inst.xmax, inst.ymax):
            raise ValueError("Padded grids must contain the source map and be at most 25x25")
        inst = replace(inst, xmax=side - 1, ymax=side - 1)
    os.environ["MRP_PROGRESS"] = "1"
    start = time.monotonic()
    row = dict(case, grid=f"{inst.xmax + 1}x{inst.ymax + 1}",
               collisions_enabled=case["options"].get("collisions", True))
    options = case["options"]
    if case["approach"] == "centralized":
        result, conflicts = run_centralized(inst, Options(collisions=options.get("collisions", True), phase2_optimize_cost=options.get("phase2_optimize_cost", False), seed=case["seed"]))
        row.update(status=result.status, coverage=100 if result.ok else None,
                   hard_eff=result.hard_eff if result.ok else None,
                   soft_eff=result.soft_eff if result.ok else None,
                   primitive_cost=result.primitive_cost if result.ok else None,
                   conflicts=len(conflicts) if result.ok else None,
                   horizons=[result.M] if result.ok else [], note=result.note,
                   deployment="NOT_MODELED_BY_BASELINE")
        row.update(same_group_conflicts=len(conflicts) if result.ok else None,
                   cross_group_conflicts=0 if result.ok else None,
                   conflict_examples=conflicts[:20] if result.ok else [])
    else:
        result = run_decentralized(inst, anchors, Params(R_s=6, seed=case["seed"], **options)) if "R_s" not in options else run_decentralized(inst, anchors, Params(seed=case["seed"], **options))
        metrics = result.metrics()
        row.update(status="SAT" if metrics["coverage"] == 100 else "PARTIAL",
                   coverage=metrics["coverage"], hard_eff=metrics["hard_eff"], soft_eff=metrics["soft_eff"],
                   primitive_cost=sum(g.res.primitive_cost for g in result.plans.values() if g.live),
                   conflicts=len(result.conflicts), horizons=metrics["Ms"], deployment=metrics["deployment_status"],
                   groups=result.grouping.groups, anchors=anchors, unclaimed=metrics["unclaimed"],
                   messages=dict(result.bus.stats), fallback_groups=metrics["fallback_groups"],
                   refinements=result.refined,
                   solver_outcomes={r: dict(status=g.res.status, note=g.res.note) for r, g in result.plans.items() if g.res})
        row.update(same_group_conflicts=metrics["same_group_conflicts"],
                   cross_group_conflicts=metrics["cross_group_conflicts"],
                   conflict_examples=result.conflicts[:20])
        row["candidate_valid"] = metrics["coverage"] == 100 and not result.conflicts and not metrics["fallback_groups"]
    row["wall_s"] = time.monotonic() - start
    return row


def matrix(root, repeats):
    scenarios = []
    for name in ("input-warehouse", "input-artfloor", "input-random-20", "input-random-30"):
        for workers, rechargers in ((3, 2), (6, 2), (6, 3)):
            scenarios.append(dict(input=str(root / name), workers=workers, rechargers=rechargers, T=8, tiles=1))
    variants = [("base", {}), ("refine2", {"K_refine": 2}), ("refine5", {"K_refine": 5}),
                ("radius8", {"R_s": 8}), ("cap2", {"cap_n": 2}), ("cap6", {"cap_n": 6}),
                ("no_rebalance", {"K_unclaimed": 0}),
                ("no_swaps", {"K_swap": 0})]
    scenarios += [dict(input=str(root / name), workers=w, rechargers=r,
                       T=8, tiles=1, grid_side=25, geometry="padded_source")
                  for name in ("input-warehouse", "input-artfloor")
                  for w, r in ((3, 2), (6, 3))]
    jobs = []
    # Cover maps at the smallest workload first; expensive cost optimization is last.
    for variant, options in variants:
        for seed in range(repeats):
            for scenario in sorted(scenarios, key=lambda s: (s["workers"], s["input"])):
                approaches = ("decentralized", "centralized") if variant == "base" else ("decentralized",)
                for approach in approaches:
                    jobs.append(dict(scenario, approach=approach, variant=variant, options=options, seed=seed))
                    if variant == "base":
                        jobs.append(dict(scenario, approach=approach, variant="collisions_off",
                                         options={"collisions": False}, seed=seed))
    # Longer local hypercycle variant; cost comparisons use only the small load.
    for scenario in scenarios:
        jobs.append(dict(scenario, T=14, approach="decentralized", variant="T14", options={}, seed=0))
    for name in ("input-warehouse", "input-artfloor", "input-random-20", "input-random-30"):
        for workers in (1, 2):
            for side in (19, 25):
                scenario = dict(input=str(root / name), workers=workers, rechargers=1, T=8,
                                tiles=1, grid_side=side, geometry="source" if side == 19 else "padded_source")
                for seed in range(repeats):
                    for variant, options in (("cost_off_small", {}), ("cost", {"phase2_optimize_cost": True})):
                        for approach in ("decentralized", "centralized"):
                            jobs.append(dict(scenario, approach=approach, variant=variant, options=options, seed=seed))
    for k, job in enumerate(jobs, 1):
        job["id"] = k
    return jobs


def stop_child(proc):
    if proc.poll() is None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            proc.kill()
        proc.wait()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hours", type=float, default=None,
                        help="optional overall limit in hours; omitted means no time limit")
    parser.add_argument("--input-root", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--plan-only", action="store_true")
    parser.add_argument("--worker", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        job = json.loads(args.worker.read_text())
        row = run_case(job)
        args.worker.with_suffix(".result.json").write_text(json.dumps(row, indent=2))
        return
    if (args.hours is not None and (not math.isfinite(args.hours) or args.hours <= 0)) or args.repeats < 1:
        parser.error("hours and repeats must be positive")
    jobs = matrix(args.input_root.resolve(), args.repeats)
    output = Path(__file__).parent / "comparison_logs" / time.strftime("study-%Y%m%d-%H%M%S")
    output.mkdir(parents=True)
    (output / "manifest.json").write_text(json.dumps(jobs, indent=2))
    budget = f"total session budget {args.hours:g}h" if args.hours is not None else "no overall time limit"
    print(f"Queued {len(jobs)} comparisons; {budget}; no per-run timeout.", flush=True)
    print(f"Results: {output}", flush=True)
    if args.hours is not None:
        print("At the overall deadline an unfinished run is interrupted and labeled BUDGET_STOP, never UNSAT.", flush=True)
    if args.plan_only:
        return
    deadline = time.monotonic() + args.hours * 3600 if args.hours is not None else math.inf
    fields = ["id", "input", "tiles", "grid", "workers", "rechargers", "T", "variant", "approach", "seed", "status", "coverage", "hard_eff", "soft_eff", "primitive_cost", "collisions_enabled", "conflicts", "same_group_conflicts", "cross_group_conflicts", "deployment", "wall_s"]
    with (output / "results.csv").open("w", newline="") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for job in jobs:
            if time.monotonic() >= deadline:
                break
            path = output / f"{job['id']:04d}.json"
            path.write_text(json.dumps(job))
            logpath = path.with_suffix(".log")
            start = time.monotonic()
            interrupted = False
            print(f"[{job['id']}/{len(jobs)}] {Path(job['input']).name} grid={job.get('grid_side', 19)}x{job.get('grid_side', 19)} W={job['workers']} R={job['rechargers']} {job['approach']} {job['variant']}", flush=True)
            with logpath.open("w") as logfile, logpath.open() as reader:
                proc = subprocess.Popen([sys.executable, "-u", "-m", "mrp.benchmark", "--worker", str(path)], stdout=logfile, stderr=subprocess.STDOUT, cwd=Path(__file__).parent.parent)
                next_notice = time.monotonic() + 20
                try:
                    while proc.poll() is None and time.monotonic() < deadline:
                        time.sleep(1)
                        text = reader.read()
                        if text:
                            print(text, end="", flush=True)
                        if time.monotonic() >= next_notice:
                            remaining = f"; session remaining {(deadline - time.monotonic()) / 60:.1f}min" if args.hours is not None else ""
                            print(f"  still running: {time.monotonic() - start:.0f}s{remaining}", flush=True)
                            next_notice = time.monotonic() + 20
                except KeyboardInterrupt:
                    stop_child(proc)
                    interrupted = True
                stopped = proc.poll() is None
                if stopped:
                    stop_child(proc)
                print(reader.read(), end="", flush=True)
            result_path = path.with_suffix(".result.json")
            row = json.loads(result_path.read_text()) if result_path.exists() else dict(job, status="INTERRUPTED" if interrupted else "BUDGET_STOP" if stopped else "ERROR", wall_s=time.monotonic() - start)
            if not result_path.exists():
                result_path.write_text(json.dumps(row, indent=2))
            writer.writerow(row)
            csvfile.flush()
            print(f"  {row['status']} coverage={row.get('coverage')} hard={row.get('hard_eff')} conflicts={row.get('conflicts')} wall={row['wall_s']:.1f}s", flush=True)
            if interrupted:
                break
    print(f"Session finished. Completed results: {output / 'results.csv'}; unstarted cases remain in manifest.json.", flush=True)


if __name__ == "__main__":
    main()
