from __future__ import annotations
import argparse
from .decentralized import run_decentralized
from .deploy import pick_anchors
from .failure import Failure, declared_at, recover
from .model import load_instance
from .params import Params, default_Rs

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--workers", type=int)
    ap.add_argument("--rechargers", type=int)
    ap.add_argument("--T", type=int)
    ap.add_argument("--fail", action="append", default=[], help="worker:ID@STEP or recharger:ID@STEP")
    ap.add_argument("--hb", type=int, default=1)
    ap.add_argument("--k", type=int, default=3)
    a = ap.parse_args()
    inst = load_instance(a.input, a.workers, a.rechargers, a.T)
    P = Params(R_s=default_Rs(inst.xmax + 1), hb_period=a.hb, k_missed=a.k)
    anchors = pick_anchors(inst, inst.n_rech, P.R_s)
    d = run_decentralized(inst, anchors, P)
    if not d.deployment.ready:
        print("Initial deployment is blocked; do not start the hypercycle or inject runtime failures.")
        for rid, path in d.deployment.paths.items():
            if not path.ok:
                print(f"  recharger {rid}: {path.reason}")
        return
    m = d.metrics()
    pct = lambda value: f"{value:.1f}" if value is not None else "n/a"
    print(f"BEFORE: groups {d.grouping.groups} T' {m['Ms']} hard {pct(m['hard_eff'])} soft {pct(m['soft_eff'])}")
    fails = []
    for s in a.fail:
        kind, rest = s.split(":")
        i, t = rest.split("@")
        fails.append(Failure(kind, int(i), int(t)))
    d2, reports = recover(d, fails)
    for r in reports:
        f = r.failure
        print(f"{f.kind} {f.id} stopped at step {f.t} (cell {r.stop_cell}); declared failed at step {r.declared_at}"
              f" (hb={P.hb_period}, k={P.k_missed})")
        print(f"   re-solved groups (ID order): {r.resolved} in {r.solve_time:.1f}s | reassigned: {r.reassigned or '-'}"
              f" | unserved: {r.unserved or '-'}")
    m2 = d2.metrics()
    print(f"AFTER : groups {d2.grouping.groups} T' {m2['Ms']} hard {pct(m2['hard_eff'])} soft {pct(m2['soft_eff'])} "
          f"cross-group conflicts {m2['cross_group_conflicts']} unclaimed/unserved {d2.grouping.unclaimed or 'none'}")
    print("note:", reports[0].switch_note if reports else "no failures")


if __name__ == "__main__":
    main()
