from __future__ import annotations
import argparse
import time
import math
from dataclasses import replace
from .centralized import run_centralized
from .decentralized import run_decentralized
from .deploy import pick_anchors
from .model import load_instance
from .params import Params, default_Rs
from .twoshot import Options


def centralized_row(res, conflicts, elapsed):
    return dict(ok=res.ok, status=res.status, T_prime=res.M if res.ok else None,
                hard=res.hard_eff if res.ok else None, soft=res.soft_eff if res.ok else None,
                coverage=100.0 if res.ok else None, time=elapsed,
                same=sum(c[3] == "same_group" for c in conflicts) if res.ok else None,
                cross=0 if res.ok else None)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--rechargers", type=int)
    ap.add_argument("--T", type=int)
    ap.add_argument("--Rs", type=int)
    ap.add_argument("--skip-central", action="store_true")
    ap.add_argument("--parallel", action="store_true")
    ap.add_argument("--anchors", help="physical setup positions and region centers, e.g. \"9,6;9,12\"")
    ap.add_argument("--group-mode", choices=["two_shot", "one_shot", "auto"], default="two_shot")
    ap.add_argument("--one-shot-H", type=int, default=0, help="hypercycle for one-shot groups that are not aligned")
    ap.add_argument("--one-shot-max", type=int, default=1, help="auto mode: one-shot for groups up to this many workers")
    ap.add_argument("--central-mode", choices=["two_shot", "one_shot"], default="two_shot")
    ap.add_argument("--refine", type=int, default=0, help="refinement proposal rounds per recharger K_refine")
    ap.add_argument("--setup-timeout", type=float, default=2.0,
                    help="setup communication/heartbeat silence limit in seconds (default: 2); movement may take longer")
    ap.add_argument("--no-collisions", action="store_true", help="disable swept-cell collision constraints in both versions")
    ap.add_argument("--phase2-cost", action="store_true",
                    help="minimize total recharger primitive cost Y in phase 2 (optional variant)")
    ap.add_argument("--worker-motion", choices=["waypoints", "straight"], default="waypoints",
                    help="legacy abstract worker edges, or assumed straight connections; supplied swaths override either")
    a = ap.parse_args()
    if not math.isfinite(a.setup_timeout) or a.setup_timeout <= 0:
        ap.error("--setup-timeout must be a positive finite number")
    inst = load_instance(a.input, a.workers, a.rechargers, a.T)
    inst = replace(inst, worker_motion=a.worker_motion)
    if a.worker_motion == "straight":
        from .model import worker_edge_cells
        if any(worker_edge_cells(inst, w, k) & inst.obstacles
               for w in inst.workers for k in range(len(w.loop))):
            ap.error("inferred straight worker paths intersect obstacles; supply actual worker_swaths.json instead")
    P = Params(R_s=a.Rs or default_Rs(inst.xmax + 1), collisions=not a.no_collisions, K_refine=a.refine,
               group_mode=a.group_mode, one_shot_H=a.one_shot_H, one_shot_max_workers=a.one_shot_max,
               phase2_optimize_cost=a.phase2_cost, setup_timeout_s=a.setup_timeout)
    print(f"instance {inst.name}: {len(inst.workers)} workers, {inst.n_rech} rechargers, T={inst.T}, R_s={P.R_s}")
    rows = {}
    if not a.skip_central:
        t = time.time()
        res, conf = run_centralized(inst, Options(collisions=not a.no_collisions, mode=a.central_mode,
                                                   phase2_optimize_cost=a.phase2_cost))
        rows["centralized"] = centralized_row(res, conf, time.time() - t)
        if res.note:
            print("centralized solver:", res.status, "|", res.note)
    anchors = ({i + 1: tuple(map(int, s.split(","))) for i, s in enumerate(a.anchors.split(";"))}
               if a.anchors else pick_anchors(inst, inst.n_rech, P.R_s))
    t = time.time()
    d = run_decentralized(inst, anchors, P, parallel=a.parallel)
    elapsed = time.time() - t
    m = d.metrics()
    status = ("SAT" if m["served"] == len(inst.workers) else
              "UNKNOWN" if any(g.res and g.res.status == "UNKNOWN" for g in d.plans.values()) else
              "PARTIAL" if m["served"] else "NO_PLAN")
    rows["decentralized"] = dict(ok=m["served"] == len(inst.workers), T_prime=m["Ms"], hard=m["hard_eff"],
                                 status=status, soft=m["soft_eff"], coverage=m["coverage"], time=elapsed, same=m["same_group_conflicts"],
                                 cross=m["cross_group_conflicts"])
    print("anchors:", anchors, "| groups:", d.grouping.groups, "| unclaimed:", d.grouping.unclaimed or "none")
    print("planning order:", d.order, "| fallback groups:", m["fallback_groups"])
    print(f"{'':14}{'status':>17}{'served':>8}{'coverage':>10}{'T_prime':>12}{'hard%':>8}{'soft%':>8}{'wall s':>9}{'same-grp':>9}{'cross-grp':>10}")
    for k, r in rows.items():
        hard = f"{r['hard']:.1f}" if r["hard"] is not None else "n/a"
        soft = f"{r['soft']:.1f}" if r["soft"] is not None else "n/a"
        coverage = f"{r['coverage']:.1f}" if r["coverage"] is not None else "n/a"
        same = str(r["same"]) if r["same"] is not None else "n/a"
        cross = str(r["cross"]) if r["cross"] is not None else "n/a"
        served = "all" if r["ok"] else "PARTIAL" if k == "decentralized" and m["served"] else "none"
        print(f"{k:14}{r['status']:>17}{served:>8}{coverage:>10}{str(r['T_prime']):>12}{hard:>8}{soft:>8}"
              f"{r['time']:9.1f}{same:>9}{cross:>10}")
    print("Wall time includes each pipeline; decentralized includes grouping, refinement and setup simulation.")
    print(f"decentralized final-plan critical-path estimate (excludes rejected refinement trials): {m['critical_path_time']:.1f}s")
    print("deployment:", m["deployment_status"],
          "| setup steps:", m["deployment_steps"], "| component start ticks:", d.deployment.start_ticks)
    for rid, path in sorted(d.deployment.paths.items()):
        print(f"  setup {rid}: {path.cells}" if path.ok else f"  setup {rid}: {path.reason}")
    if d.deployment.execution is not None:
        print("reported setup positions:", d.deployment.execution.positions)
        if d.deployment.execution.reason:
            print("setup execution:", d.deployment.execution.reason)
    if not m["deployment_ready"]:
        print("Hypercycle execution is blocked; the efficiency values above describe the candidate plans only.")
    print("messages:", dict(d.bus.stats), "| refinements accepted:", d.refined)
    for r, g in sorted(d.plans.items()):
        print(f"  recharger {r} anchor {g.anchor} workers {g.workers} T={g.res.T if g.live else None} T'={g.res.M if g.live else None}"
              f" solve {g.wall:.1f}s" + (" [fallback]" if g.fallback else ""))
        if g.res and not g.res.ok:
            print(f"    {g.res.status}: {g.res.note}")


if __name__ == "__main__":
    main()
