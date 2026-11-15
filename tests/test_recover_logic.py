import os
from mrp import planner
from mrp.decentralized import run_decentralized
from mrp.deploy import pick_anchors
from mrp.failure import Failure, recover
from mrp.model import load_instance
from mrp.params import Params
from mrp.twoshot import Result

def fake_solve(job):
    sub, opts, reason, T_list = job
    T = T_list[0]
    M = T + 4
    pos = (opts.box[0], opts.box[2])
    res = Result(True, T=T, M=M, nW=len(sub.workers),
                 wpos={w.id: [None] + [w.loop[0]] * M for w in sub.workers},
                 rpos={1: [None] + [pos] * M}, rsweep={1: [None] + [{pos}] * (M - 1)},
                 halts={w.id: (1, 0, 0) for w in sub.workers})
    return res, False, reason, 0.0


def build(inst, R_s):
    P = Params(R_s=R_s)
    return run_decentralized(inst, pick_anchors(inst, inst.n_rech, R_s), P)


def main():
    D = os.environ["REPO_INPUT"]
    planner.solve_group_job = fake_solve
    inst = load_instance(D, 4, 2, 14)
    # A: worker failure -> owner re-solved, orphan-free
    d = build(inst, 6)
    d2, rep = recover(d, [Failure("worker", 1, 5)])
    owner = next(r for r, g in d.grouping.groups.items() if 1 in g)
    assert 1 not in d2.grouping.groups[owner] and owner in rep[0].resolved and rep[0].stop_cell in d2.inst.obstacles
    # C: recharger failure, no overlap -> orphans unserved, survivor untouched
    dC, repC = recover(d, [Failure("recharger", 2, 9)])
    assert repC[0].unserved == [2, 3] and set(repC[0].resolved) <= {1} and 2 not in dC.anchors and dC.plans[1].live
    # B: recharger failure with overlapping regions -> orphans reassigned to the survivor (capacity permitting)
    big = run_decentralized(inst, {1: (9, 6), 2: (9, 12)}, Params(R_s=12, cap_n=4))   # both regions cover every loop
    dB, repB = recover(big, [Failure("recharger", 2, 9)])
    assert set(repB[0].reassigned) | set(repB[0].unserved) == set(big.grouping.groups[2])
    assert all(r == 1 for r in repB[0].reassigned.values()) and len(repB[0].reassigned) == len(big.grouping.groups[2]) > 0
    assert sorted(dB.grouping.groups[1]) == sorted(big.grouping.groups[1] + big.grouping.groups[2])
    print("B: reassigned", repB[0].reassigned, "unserved", repB[0].unserved, "resolved", repB[0].resolved)
    # D: deterministic
    d3, rep3 = recover(d, [Failure("worker", 1, 5)])
    assert rep3[0].resolved == rep[0].resolved and d3.grouping.groups == d2.grouping.groups
    # E: two failures are applied in declared-at order
    dE, repE = recover(big, [Failure("worker", 3, 5), Failure("worker", 1, 2)])
    assert [r.failure.id for r in repE] == [1, 3]
    print("recovery logic tests passed")


if __name__ == "__main__":
    main()
