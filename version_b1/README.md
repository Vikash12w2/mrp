# Version B1: central grouping + per-group SMT

This separate package implements the **offline B1 branch** in
`Decentralized Recharger Planning - Plans.pdf`: one central unit forms groups
before execution; a B1 variant of the two-shot SMT algorithm plans each group.
No Version A negotiation or ROS code is used for grouping. Existing source files
are not edited. B1's `twoshot.py` contains a phase-1 encoding derived from the
original, with additional secondary objectives; shared phase-2 constraints remain
imported. Encoding fixes in the original phase 1 must also be ported to this copy.

## Why G2

G2 (Z3 assignment optimization) considers every eligible worker–recharger pair
simultaneously. Unlike local swaps or heuristic clustering, it optimizes the whole
assignment for the stated criteria. It also uses the project's existing Z3
dependency. G1 is the document's general preference, but G2 fits the current
priority of reducing unassigned workers and the small benchmark group sizes.

The central unit knows the complete map, all workers and all recharger anchors.
Groups are not limited to Version A sensing boxes. Physical obstacle reachability
still applies. Each group now plans within its own obstacle-aware region, with
a new region computed for each new assignment; global knowledge does not force global planning.
Anchors are physical setup positions, not movable cluster centroids.

Lexicographic objectives:

1. Maximize assigned workers, with at most one owner per worker.
2. During recovery, prefer retaining ownership outside the failed groups and
   their direct dependency/exchange neighbors, without forbidding wider changes.
3. Minimize the busiest recharger's estimated service effort: charging demand
   over T, twice the maximum mean anchor-to-loop distance (return travel), and
   pairwise worker charging-neighborhood distances.
4. Minimize total consumption/capacity-weighted travel plus charging demand,
   then spatial cost, workload imbalance, demand imbalance and count imbalance.
5. Break remaining ties deterministically by worker and recharger IDs.

These are scheduling estimates, not deadlines or exact tours. Waiting consumes
no battery in the SMT model, so battery urgency is a heuristic, not a hard cutoff.
Unequal group sizes are allowed. Only the subsequent SMT schedule establishes
feasibility under the model.

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
wavefront groups can solve concurrently. Only overlapping planning boxes create
hypercycle dependencies; overlapping groups remain ordered by recharger ID.
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
B1 can try alternative alignment horizons, then returns failure for central regrouping. It never
drops higher-priority collision reservations to obtain an unconstrained fallback.

## Outputs and boundaries

The JSON distinguishes `grouping_status`, `groups`, `unassigned`, assigned count,
SMT planning status, planned coverage, conflicts, fallback groups and deployment
readiness. `executable` is false if any worker is unserved, a plan uses unconstrained
fallback, a conflict is found, deployment is not ready, or the joint period exceeds
the existing verifier's 3000-step limit. This is an offline model check, not a
hardware safety certification.

B1 implements grouping and region-aware wavefront planning here, while reusing
the original collision encoding helpers, phase 2, verification and setup simulation.
Bounded offline regrouping is implemented; finding a feasible partition is not guaranteed. No online B2
auction, lazy adoption, runtime regrouping, failure-triggered regrouping or ROS
integration is included in this offline B1 branch. Those need a separate execution
and transition protocol; calling Version A recovery would use the wrong grouping
policy. Full assignment is not a promise that all workers have feasible schedules.

Tests (all new tests live here):

```bash
cd ~/Downloads
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest mrp.version_b1.tests.test_b1 mrp.version_b1.tests.test_efficiency
```

## Offline regrouping after failure

Full B1 runs now try up to five additional assignments (`--max-regroupings 5`).
Set this to 0 for a single attempt. Each rejected partition is excluded exactly
from the next central optimization, retaining the initial maximum assigned count.
The optimizer can relax balance or distance objectives to find another partition;
it cannot silently drop workers to manufacture success.

Failures, UNKNOWN results, fallback plans, detected conflicts, insufficient joint
period verification and blocked deployment all reject an attempt. Successful plans are cached within one B1 run. Reuse requires identical solver
inputs, including worker energy data, map, region, cycle and upstream swept
occupancy. Changed dependencies trigger replanning.
Only a complete verified result with ready deployment is accepted. The two-step
runtime reservation controller is still not used.

`attempts` records every partition tried, per-group solver outcomes, conflicts and
deployment status. `retry_status` distinguishes SUCCESS, RETRY_LIMIT_REACHED,
ASSIGNMENTS_EXHAUSTED, GROUPING_UNKNOWN and UNREACHABLE_WORKERS. On exhaustion,
the final attempted plans remain available for diagnostics but are non-executable.
Exhaustion means no untried assignment at the required coverage remains; it does
not prove physical infeasibility, especially after UNKNOWN or bounded SMT searches.
Unreachable workers stop retries because reassignment cannot create a physical path.
Each group job has a 60-second planning budget shared by at most two local
horizon attempts. A killed solver returns UNKNOWN, not UNSAT. Grouping has its
separate timeout; there is no overall mission-search wall-clock deadline. This is offline search, not runtime failure recovery.

## Group-specific planning areas and phase-1 selection

Initial boxes contain the anchor, full worker loops and supplied worker sweeps,
reachable charging neighbors, and shortest primitive paths from the anchor to
those neighbors (including obstacle detours), with a one-cell margin. Idle
rechargers occupy a single-cell box. Box overlap determines possible inter-group
interaction. Geometry is conservative: intersecting boxes may still contain
non-interacting routes.

Each partition starts with local regions and the initial cycle choices. If an
aligned group fails after bounded local horizon retries, B1 can try two longer
shared cycles, increasing by four time points each time. Only failed overlap
components are retimed; all affected schedules and upstream occupancy are rebuilt.
A solver timeout returns directly to regrouping. There is no padding of existing
trajectories, and final full-joint-period verification remains mandatory.
Cycle attempts and forced cycles appear in `region_attempts`; cache hits appear
in plan notes. These bounded candidates can miss feasible intermediate cycles.
There are no expanded-box/full-map retries. Failed partitions are excluded and
regrouped at the same coverage. Geometry or search failure does not prove global
infeasibility.
Setup paths remain separately coordinated over the full map, allowing robots to
reach their selected start positions without weakening hypercycle constraints.

Phase 1 still minimizes worker waiting first. Among tied solutions, B1 minimizes
an obstacle-aware completion estimate (end-to-worker charging location, remaining
charge duration, and worker-to-cycle-start travel), then total completion estimate,
remaining charge duration, and phase-1 movement cost. These estimates ignore
multi-worker visit ordering and dynamic obstacles; phase 2 enforces those actual
constraints and determines the final cycle length. One-shot planning keeps its
original objective because it already closes the cycle within one solve.

The output includes planning boxes, region attempts, phase-1 waiting, completion
steps and per-group efficiency. The first complete verified partition is still
accepted: there is no post-success enumeration of assignments or schedules.
These changes improve the heuristic selection, not a guarantee of globally
optimal efficiency. Alignment retries and extra SMT objectives can add solve time;
compare quality and wall time on matched input/anchor/settings before claiming a
benchmark-wide gain. Previously saved study results describe the old B1 policy.
