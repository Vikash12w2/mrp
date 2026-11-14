# mrp: centralized two-shot (ported) + decentralized Version A

Python (z3py) package structured after the centralized repo
`MobileRobotRecharging/mobile-recharger-path-planning-scheduling` (Kundu & Saha, IROS 2021).
The decentralized version calls **the same two-shot core** once per group (`twoshot.solve`).

## Layout

| Module | Role |
|---|---|
| `model.py` | Instance/workers/primitives + loader for the repo's input files |
| `twoshot.py` | Phase 1 + phase 2 encoding, T' search, hard/soft efficiency, repo collision constraints |
| `centralized.py` | My Python port of the repo's two-shot on the whole instance |
| `grouping.py` / `negotiation.py` | Discovery and per-recharger message-based auctions, offloads and swaps |
| `planner.py` / `refinement.py` | Local SMT planning and bilateral refinement through neighbor messages |
| `failure.py`, `failure_demo.py` | Deterministic failure handling (heartbeats, re-plan) |
| `runtime.py` | `verify()` conflict counter + 2-step-reservation arbiter |
| `decentralized.py` | Version A pipeline + metrics |
| `bus.py`, `params.py`, `deploy.py` | Messages (ACK/retry), parameters, coverage-aware anchor placement |
| `compare.py` | Runs both versions on one repo input directory |
| `baseline/` | Scripts that run the **pristine repo** (two-shot, one-shot) |
| `crosscheck/`, `tests/` | Encoding checks against the repo; unit tests |

## Run

```
pip install z3-solver
python -m mrp.compare --input <repo>/.../examples/input-warehouse --workers 4 --rechargers 2 --T 14 [--Rs 6] [--no-collisions] [--phase2-cost] [--refine K] [--anchors "9,6;9,12"] [--skip-central]
python -m mrp.failure_demo --input <dir> --workers 4 --rechargers 2 --T 14 --fail worker:1@5 --fail recharger:2@9
python -m mrp.tests.test_failure
REPO_INPUT=<dir> python -m mrp.tests.test_recover_logic        # recovery logic with a solver stand-in
REPO=<.../mobile-recharger-path-planning-scheduling> baseline/run_two.sh 4 2 14     # pristine repo two-shot
REPO=... baseline/run_one.sh 2 1 21 900                                             # pristine repo one-shot (capped)
```

## What matches the repo
- Model: each loop waypoint is one time step; per-step energy `req`; 21-primitive set from `2d_template.txt`; recharge amount in (0, delta_max]; 8 neighbour offsets.
- Phase-1 objective and hard/soft efficiency retain the supplied port's conventions. Phase 2 searches T' upward with no secondary objective by default, matching the supplied code; `--phase2-cost` enables the paper's optional total primitive-cost objective Y at the first feasible T'.
- Same formulation style as the repo (`waitcount`/`rechcount` Int variables, displacement helper variables): this closed the speed gap (2 workers/1 recharger phase 1: 11.6 s -> 5.7 s; 2 workers/2 rechargers with collisions: 98 s -> 14.7 s).
- Swept-cell collision constraints follow the paper's intermediate-cell definition and are **ON by default**; the reference repository ships its collision calls commented out. `--no-collisions` disables these checks.
- Earlier cross-check results in this README predate the current region-sweep, collision, and phase-2 start-location constraints; rerun them before treating the recorded numbers as validation of the current encoding.
- Repo quirks kept: step energy indexed by time, a last-recharge visit for every worker, one recharger may charge two workers at once (`Options.exclusive_recharge` to forbid).

## Decentralized Version A (summary)
Workers only broadcast. Rechargers sense workers whose whole loop lies inside their region (Chebyshev radius R_s around the fixed anchor), claim by lowest (S, ID) within capacity, rebalance unclaimed workers with a neighbour (one step), swap pairwise, then plan group by group in ID priority with higher-priority neighbours' occupancy as time-indexed constraints and T' forced equal (phase-1 T tuned down). Failed alignment -> plan unconstrained and flag runtime fallback. Full rules and assumptions: `VERSION_A_SPEC.md`. Known simplifications are below.

Initial grouping no longer has a global unclaimed-worker selector. Each `Negotiator` owns one recharger's membership and reads only copied, round-tagged messages from overlapping neighbors. Local capacity-aware auctions assign workers; bilateral proposals, votes and acknowledged commits handle offloads and improving swaps. Missing current-round state prevents a new local decision. The simulator only schedules those handlers and collects results. Fixed local round budgets can leave workers unassigned; they do not promise the global best grouping. Workers remain passive broadcasters.

This protocol assumes discovered neighbors and the synchronous Bus's acknowledged-delivery contract. ROS 2 deployment will need transaction persistence and recovery for asynchronous commits. SMT refinement now runs through endpoint handlers: each recharger solves its own trial, exchanges a proposal/acceptance, and installs only its own plan after commit. Failure recovery uses the same local worker auctions and neighbor replan notices; no global best orphan assignment remains. The simulator schedules handlers, injects physical events and measures results; it does not choose those outcomes.

Unclaimed workers are retained in reporting: `capacity` when no eligible recharger has room, `negotiation_incomplete` when bounded rounds or missing communication leave a possible claim unresolved, `loop_not_inside_any_region` for partially heard loops that fit no region, `unreachable_in_region` for locally unreachable loops, and `outside_all_regions` for workers no recharger hears. Offloads address worker-count caps; exchanges can free utilization capacity. Both endpoints must still meet both caps. These diagnostics belong to the experiment observer and do not give robots global worker knowledge.

`--refine K` now gives each recharger K local proposal rounds, rather than a shared global trial budget.

**Anchors / R_s (experiment setup, not a recharger controller):** `deploy.pick_anchors(inst, k, R_s)` chooses anchors that maximise covered workers. On the warehouse map, 6 workers need R_s=8 with 2 rechargers (or 4 rechargers at R_s=6); at R_s=6 with 2 rechargers only 3-4 workers are served and the rest are reported unclaimed.

## One-shot mode (alternative, for testing)
`Options(mode="one_shot")` is the repo's monolithic variant: phase 1 over ONE fixed hypercycle H (no phase 2), with the repo's matching constraints (each worker ends at its start cell with its starting charge, the recharger ends where it started), objective `total_wait`, same time-indexed step energy. Result: T' = H.
- Centralized: `python -m mrp.compare ... --central-mode one_shot` (H = `--T`).
- Decentralized: `--group-mode one_shot` (every group), or `--group-mode auto --one-shot-max N` (groups of <= N workers). H for a group with no higher-priority neighbour is `--one-shot-H` (default: `--T`); an aligned group uses its neighbours' T' as H.
- Checked against the repo's one-shot (pristine, collisions off): 1 worker/1 recharger H=14 -> min total_wait 5 in both; 2 workers/1 recharger H=14 -> 12 in both. Recharge counts of the returned models can differ (ties), so soft efficiency can differ.
- It is slow: the repo's one-shot did not finish 2 workers/1 recharger at H=21 in 900 s, so it is only practical for tiny groups.
- Example (3 workers/2 rechargers, anchors (7,2),(14,6), collisions off): two-shot only -> T' 16/16, hard 60.4, soft 75.0; one-shot for the 1-worker group at H=17 -> T' 17/17, hard 68.6, soft 82.4. Not a clean comparison: H=17 was chosen by hand and a longer T' raises the percentage.

## Baseline vs the repo's code
- `baseline/run_two.sh`, `baseline/run_one.sh` run the **repo's own code**, built with its own Makefile. What I changed is environment only: the input config (`workspace.txt`, and copying the `robN_traj.txt`/obstacle/template files into the folder the code reads from), a `time` shim (the repo shells out to `time`), the working directory the code expects, and, for the one-shot, converting the *print statements* of the generated Python 2 script (no Python 2 here) in a scratch copy. The repo's source is not edited.
- `centralized.py` / `twoshot.py` are **my Python port, not identical**: same model and objective, formulation now in the repo's style, but halt-time computation, the `rechassigned` variables, collision constraints (ON here, commented out in the repo) and solver interface differ. Equivalence was checked on small cases only (see Cross-checks).
- In `compare.py`, the "centralized" row is my port. The repo-baseline numbers in the results table come from the pristine repo runs.
- Test-only: a scratch copy of the repo with its two collision calls uncommented was used to validate my collision constraints; it is not the baseline.

## Reproducibility
Z3's tie-breaking among equally optimal models depends on what was solved earlier in the same process (observed: T' 21 vs 20 for the same decentralized run). Every solve therefore runs in a fresh spawned process (`solve_isolated`) with a fixed seed. Phase-1 optima are not unique, so different Z3 versions can still give different T'.

## Failure handling (`failure.py`)
Fail-stop robots. Heartbeat every `hb_period` steps; declared failed after `k_missed` misses (`declared_at`), or when ACK retries are exhausted. A failed robot's stop cell becomes an obstacle. Worker failure: owner drops it, re-solves, and every lower-priority overlapping group is re-solved in ID order. Recharger failure: survivors discover locally sensed workers without a surviving owner through fresh neighbor snapshots, bid locally and use bilateral capacity offloads/exchanges when possible. Unassignable workers remain unserved. Local obstacle/membership changes trigger neighbor replan notices; there is no global assignment search or dependency selector. New plans take effect at the next hypercycle boundary; workers keep the old plan until then and the model assumes a fresh start state (full charge) at that boundary.

## Results (warehouse inputs, T=14, one run each, single CPU)
Pristine repo two-shot, as shipped (collisions off):

| W/R | T' | hard % | soft % | time |
|---|---|---|---|---|
| 2/1 | 21 | 61.9 | 73.8 | 6 s |
| 3/2 | 19 | 68.4 | 82.5 | 20 s |
| 4/2 | 23 | 56.5 | 69.6 | 43 s |
| 6/2 | 25 | 52.0 | 62.0 | 666 s |

Pristine repo one-shot, 2 workers/1 recharger, hlen 21: did not finish in 900 s; minimum total_wait is 11 or 12 (<=10 unsat, a 12 model exists) -> hard efficiency 71.4-73.8% (two-shot: 61.9%).

Decentralized (collisions off unless stated):

| W/R | R_s | T' per group | hard % | soft % | solve time (sum of groups) |
|---|---|---|---|---|---|
| 3/2 | 6 | 17, 17 | 56.9 | 70.6 | 39 s |
| 3/2 collisions on | 6 | 17, 17 | 56.9 | 68.6 | 51 s |
| 4/2 | 6 | 20, 20 | 60.0 | 71.2 | 52 s |
| 4/2 collisions on | 6 | 19, 19 | 55.3 | 67.1 | 163 s |
| 6/2 | 8 | 25, 25 | 49.3 | 58.7 | 45 s |
| 3/2, central anchors, with refinement | 12 | 21, 21 | 61.9 | 74.6 | (one refinement accepted; without it one group of 3: T' 24, hard 54.2) |

Reading: decentralized is lower on 3 workers, higher on 4, slightly lower on 6; the solve-time advantage appears at 6 workers. Single runs; T' varies with solver tie-breaking.

## Limitations
- Runtime arbiter: head-on swaps handled by swath overlap, only the lower-priority robot deviates; no hypercycle re-sync.
- Legacy worker input defines abstract waypoint transitions, matching the reference code. Supplied worker swaths enable intermediate-path collision checks throughout planning and verification.
- Grouping can leave one recharger with all workers when capacity allows (`cap_util`, `cap_n`); refinement can fix some of that. The utilisation estimate is a heuristic.
- Recovery re-solves take minutes; partial-charge restart states are not modeled; recharger-failure reassignment was verified end to end only with a solver stand-in.
- The one-shot baseline was run once (capped).

## Physical starts and worker path geometry

Phase 1 chooses each local recharger's hypercycle start within its region. Phase 2 preserves that selected start and returns there. Anchors are the physical setup positions and grouping centers; use `--anchors` for already deployed robots. Automatic anchors propose initial deployment positions.

`deployment.py` plans setup routes from those anchors to the selected starts. Workers remain stationary at their initial cells with full charge until setup completes. Each robot searches inside its region using motion-primitive sweeps, stationary worker cells, neighboring robots' anchors, and setup paths received from higher-priority overlapping neighbors. Lower-priority neighbors avoid those time-indexed sweeps and final parked positions. Higher-priority robots avoid lower neighbors' anchors throughout setup. This conservative policy may reject a setup requiring a joint maneuver; it reports a blocked deployment instead of allowing a collision.

Setup paths, readiness, and current positions are periodic broadcasts over neighbor links. Dropping one packet does not declare a peer failed; subsequent publications can recover it. The broadcast period is `Params.setup_broadcast_period` in simulated ticks. `--setup-timeout 2` / `Params.setup_timeout_s` limits communication or heartbeat silence, not movement duration. During execution each fresh position report renews that link's watchdog even if the robot has not yet reached its destination. Reports from an old session/stage or stale/replayed reports do not renew it. Local controller telemetry can return `None` when unavailable; that silence is watched too. Two seconds without valid reports produces `TIMEOUT` and holds the barrier. Initial setup-path/readiness requests also have a finite publication budget. A route that cannot be found is `BLOCKED`.

`SetupExecutor` supplies a resumable synchronized simulator. It commands one reserved motion step, polls each controller's reported current cell, and broadcasts `(session, stage, timestamp, position)` to neighbors. Fresh reports must match the expected cells before the simulator advances to the next movement. Intermediate progress can take multiple polls; stale reports cannot open the barrier. A position outside the current reserved sweep blocks execution. Only after confirmed arrival at every selected start does `d.deployment.ready` become true and `start_ticks` receive executable times. The CLI displays the reported final positions. Setup steps and paths remain separate from hypercycle efficiency.

The default `SimulatedSetupController` completes moves ideally. A later ROS 2 adapter can replace its nonblocking `command(rid, destination)` and `position(rid)` methods through `run_decentralized(..., setup_controller=adapter)`; the bus will also need a ROS transport. `d.deployment.execution.poll()` can be called from a timer instead of using a blocking loop. A bounded `execution.run()` call returns `WAITING` if movement is still in progress and communication remains healthy; subsequent polling continues the retained movement without a timeout. After an actual telemetry timeout, `execution.retry()` explicitly resumes and resets the silence watchdog. Planning-stage communication timeouts can be retried by rerunning setup before any movement begins. These methods enforce a grid-cell execution contract; they are not yet a ROS node or a continuous-motion controller. Mid-execution recovery retains its existing full-charge boundary assumption and does not reuse this initial setup stage.

An optional `worker_swaths.json` beside `workspace.txt` specifies absolute occupied grid cells per loop edge. The keys are worker IDs as strings, and each value contains one list of cells per edge, including the closing edge. Both endpoints must be included, and cells must be free and inside the map. Example for a two-waypoint loop:

```json
{"1": [[[0,1],[1,1],[2,1]], [[2,1],[1,1],[0,1]]]}
```

The same cells are checked by both SMT phases, shared neighbor reservations, verification, and runtime. `--worker-motion straight` instead infers connections from matching primitives or straight segment supercovers. It is an assumption, and the CLI rejects inferred paths that intersect obstacles. The warehouse dataset needs actual routes for this variant because its inferred straight connections intersect obstacles. The default `waypoints` retains the source repository's abstract worker-motion semantics.

## Comparison reporting

Solver outcomes distinguish SAT, proven UNSAT at the stated model/horizon, UNKNOWN (for example a resource limit), and SEARCH_EXHAUSTED (no phase-2 plan within the configured bound for the selected phase-1 schedule). An UNKNOWN smaller horizon stops the search; it is never skipped and relabeled infeasible. Failed centralized runs show coverage, efficiency, horizon and collision counts as n/a. Per-group failure status and its reason are printed separately.

The comparison table reports measured wall time for each whole pipeline. Decentralized wall time includes grouping, optional refinement and setup simulation; it must not be described as SMT-only speedup. The separate final-plan critical-path estimate excludes rejected refinement trials and is not total measured runtime.

The expanded benchmark includes `collisions_off` runs paired with every main baseline scenario for both approaches. These disable the SMT collision constraints while keeping post-solve trajectory verification enabled. CSV records the constraint setting and same-group/cross-group overlap counts; detailed JSON stores up to 20 examples (time, robot identities, group relationship). Setup routing and neighbor external reservations retain their own checks. Results describe modeled waypoint/supplied-swath occupancy; legacy files cannot establish unknown continuous worker paths.
