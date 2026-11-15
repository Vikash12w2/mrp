# Version B1: central grouping + per-group SMT

This separate package implements the **offline B1 branch** in
`Decentralized Recharger Planning - Plans.pdf`: one central unit forms groups
before execution; the existing two-shot SMT algorithm plans each group.
No Version A negotiation or ROS code is used for grouping. Existing source files
are imported, not edited or copied.

## Why G2

G2 (Z3 assignment optimization) considers every eligible worker–recharger pair
simultaneously. Unlike local swaps or heuristic clustering, it optimizes the whole
assignment for the stated criteria. It also uses the project's existing Z3
dependency. G1 is the document's general preference, but G2 fits the current
priority of reducing unassigned workers and the small benchmark group sizes.

The central unit knows the complete map, all workers and all recharger anchors.
Groups are not limited to Version A sensing boxes. Physical obstacle reachability
still applies. The planning region is the full map, too; otherwise global
assignments could be rejected merely because the local planner used a smaller box.
Anchors are physical setup positions, not movable cluster centroids.

Lexicographic objectives:

1. Maximize assigned workers, with at most one owner per worker.
2. Minimize the sum of squared group sizes (balanced worker counts).
3. Minimize pairwise differences in charging demand. Demand is average worker
   energy per step divided by maximum recharge per step.
4. Minimize mean obstacle-aware primitive-step distance from the assigned anchor
   to reachable recharge neighbors along each loop.
5. Break ties deterministically by worker and recharger IDs.

There is no utilization or worker-count cap. A single recharger can receive all
eligible workers. An unreachable worker remains explicitly unassigned. Geometric
eligibility does not prove battery/schedule feasibility: the subsequent SMT solve
makes that determination. UNKNOWN grouping results are not used as assignments.

## Run

From the parent directory of `mrp` (Z3 must already be installed):

```bash
cd ~/Downloads
# Fast central grouping only, using an included two-worker demo:
PYTHONDONTWRITEBYTECODE=1 python3 -m mrp.version_b1 --grouping-only
# Full B1 demo, including per-group SMT and deployment simulation:
PYTHONDONTWRITEBYTECODE=1 python3 -m mrp.version_b1
# Existing original-format input; replace the directory with your input folder:
PYTHONDONTWRITEBYTECODE=1 python3 -m mrp.version_b1 \
  --input /path/to/input --workers 6 --rechargers 2 --T 8 \
  --anchors '2,2;16,16' --output mrp/version_b1/result.json
```

`--group-mode two_shot` is the default; `one_shot` and `auto` are also available.
Parallel planning is enabled by default; `--sequential` disables it. Independent
wavefront groups can solve concurrently, while collision dependencies remain ordered. Global planning
regions overlap, so priority constraints often serialize planning.
`--grouping-timeout-ms` limits central optimization; `--rlimit` sets the existing
per-SMT resource limit. Both default to unlimited. Larger full-map SMT problems
can be substantially slower than Version A's small regional problems.
JSON is printed to stdout; `--output` writes a **new** file and refuses overwrite.
Exit 0 means complete grouping in grouping-only mode, or complete verified plans
with ready deployment in full mode. Exit 2 indicates incomplete/unknown/unsafe work.

## Offline collision handling and planning failures

B1 does not instantiate or call the two-step `runtime.Arbiter`. Its import of
`runtime.verify` performs offline conflict checking only. Collision avoidance
uses swept-cell SMT constraints and higher-priority groups' time-indexed occupancy.
Setup routing reserves its planned paths separately; it is not the two-step
runtime controller and remains necessary for reaching hypercycle start positions.
The reservation controller in the original Version A code is unchanged.

If a group has no successful SMT plan, its workers remain assigned in `groups`
but are not counted as served by the planning metrics. Per-group statuses and
notes distinguish UNSAT, UNKNOWN and exhausted horizon search. The overall
result is non-executable and the full-run CLI exits with code 2. B1 does not
execute a partial mission or retry indefinitely. It tries alternative assignments
offline as described below.
The shared planner can try alternative alignment horizons and an unconstrained
fallback; B1 rejects fallback plans as executable even when the fallback is SAT.
A shared-planner note mentioning "runtime collision handling" describes that
fallback, not a runtime controller enabled in B1.

## Outputs and boundaries

The JSON distinguishes `grouping_status`, `groups`, `unassigned`, assigned count,
SMT planning status, planned coverage, conflicts, fallback groups and deployment
readiness. `executable` is false if any worker is unserved, a plan uses unconstrained
fallback, a conflict is found, deployment is not ready, or the joint period exceeds
the existing verifier's 3000-step limit. This is an offline model check, not a
hardware safety certification.

The per-group planning, collision constraints, wavefront ordering and deployment
simulation reuse the original planner. Central grouping is implemented here.
Bounded offline regrouping is implemented; finding a feasible partition is not guaranteed. No online B2
auction, lazy adoption, runtime regrouping, failure-triggered regrouping or ROS
integration is included in this offline B1 branch. Those need a separate execution
and transition protocol; calling Version A recovery would use the wrong grouping
policy. Full assignment is not a promise that all workers have feasible schedules.

Tests (all new tests live here):

```bash
cd ~/Downloads
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest mrp.version_b1.tests.test_b1
```

## Offline regrouping after failure

Full B1 runs now try up to five additional assignments (`--max-regroupings 5`).
Set this to 0 for a single attempt. Each rejected partition is excluded exactly
from the next central optimization, retaining the initial maximum assigned count.
The optimizer can relax balance or distance objectives to find another partition;
it cannot silently drop workers to manufacture success.

Failures, UNKNOWN results, fallback plans, detected conflicts, insufficient joint
period verification and blocked deployment all reject an attempt. Every group is
replanned because changed ownership may change downstream timing and occupancy.
Only a complete verified result with ready deployment is accepted. The two-step
runtime reservation controller is still not used.

`attempts` records every partition tried, per-group solver outcomes, conflicts and
deployment status. `retry_status` distinguishes SUCCESS, RETRY_LIMIT_REACHED,
ASSIGNMENTS_EXHAUSTED, GROUPING_UNKNOWN and UNREACHABLE_WORKERS. On exhaustion,
the final attempted plans remain available for diagnostics but are non-executable.
Exhaustion means no untried assignment at the required coverage remains; it does
not prove physical infeasibility, especially after UNKNOWN or bounded SMT searches.
Unreachable workers stop retries because reassignment cannot create a physical path.
Each grouping timeout and SMT resource limit applies per solve; a retry count is
not a wall-clock timeout. This is offline search, not runtime failure recovery.
