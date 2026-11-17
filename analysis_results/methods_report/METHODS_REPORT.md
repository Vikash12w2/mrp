# How the decentralized and semi-decentralized SMT planners work

## 1. Purpose and shared problem

This report explains the implemented algorithms, rather than comparing experimental scores. **Decentralized SMT (Version A)** assigns workers through local negotiation between rechargers. **Semi-decentralized SMT (Version B1)** assigns workers with a central optimizer and then plans their schedules in local groups. Both use Z3, an SMT solver: a program that finds decisions satisfying encoded movement, battery, recharge, and collision constraints.

A worker repeatedly follows a prescribed loop. Moving consumes battery according to its input energy model. A mobile recharger travels through free cells and replenishes a worker when the modeled charging conditions hold. The planner must decide when workers move, wait, or recharge, and where rechargers move, so the resulting schedule can repeat.

The inputs are the obstacle map, worker loops and energy requirements, battery capacities, recharge rate, recharger motion primitives, recharger anchor positions, requested horizon T, and algorithm parameters. An anchor is a recharger's physical starting location. It need not equal the starting position selected for its repeating schedule; setup routing connects these positions later.

The outputs are worker assignments, worker and recharger schedules, achieved cycle lengths, solver statuses, conflict checks, unserved-worker explanations, and setup status. A returned candidate may be partial or use fallback. Finding an assignment alone does not establish a feasible timed schedule.

## 2. Terms used throughout the report

| Term | Meaning |
| --- | --- |
| Group | One recharger and the workers assigned to it. |
| Planning region | The bounded part of the map in which a group's local schedule is solved. |
| T | Phase-1 horizon: the initial schedule length requested for a local solve. |
| M, also written T' | Extended horizon used to complete the repeating schedule in phase 2. The stored repeating period is M minus 1 because the endpoint repeats the start. |
| Reservation | A neighbor's occupied or swept cells indexed by time; the next group must avoid them. |
| Alignment | Requiring a group to finish at a neighbor-compatible extended horizon. |
| Wavefront | A priority order on overlapping planning regions, processed in dependency levels. |
| UNSAT | No solution exists for the particular encoded constraints being checked. |
| UNKNOWN | The solver did not establish feasibility or infeasibility; this is inconclusive. |
| Fallback | In Version A, a candidate found after dropping neighbor reservations/alignment. It is marked explicitly. |

The word “phase” has two levels here. The complete algorithm has discovery, assignment, planning, verification and setup stages. Inside its local planning stage, the default two-shot method has **solver phase 1 and solver phase 2**. These are different levels of the workflow.

## 3. Shared local two-shot planning

### 3.1 Solver phase 1: select movement and recharge decisions

For a fixed group and horizon T, the solver jointly models worker actions and recharger movement during that initial horizon. Workers can advance along their loops, wait or recharge. Constraints enforce loop progression, charge bounds, energy updates, valid charging encounters, obstacle avoidance and the enabled collision rules. If upstream plans exist, their time-indexed reservations are included.

The primary objective minimizes worker waiting over this horizon. Phase 1 already contains recharger trajectories and charging decisions; phase 2 is not the first place where recharger motion is considered.

If phase 1 returns no solution, there is no selected schedule for phase 2 to complete. The calling planner may try another T. If the solver returns UNKNOWN, the outcome remains inconclusive rather than becoming evidence that the assignment is impossible.

### 3.2 Solver phase 2: complete the selected schedule into a cycle

The implementation extracts the chosen recharge appointments and the information needed for workers' final recharge and cycle closure. Phase 2 fixes the selected phase-1 worker schedule and appointments, and solves the recharger schedule and remaining completion constraints through extended horizon M.

For an unaligned group, the solver searches M in increasing order within the configured completion budget, starting from an implementation-derived lower bound at least T. For an aligned group, it checks the specified M. It can skip an M that is below the completion lower bound without constructing a phase-2 problem.

If phase 2 fails at an M, it checks the next permitted M while retaining that phase-1 selection. The first feasible M is accepted. Optional recharger primitive-cost optimization can be enabled at phase 2, but is off in the reported experiments. If no permitted M succeeds, the local solve returns a failure diagnostic. This is failure for the selected schedule and search budget; it is not proof that every possible phase-1 schedule or grouping fails.

### 3.3 Why two-shot planning can need retries

Phase 1 minimizes its initial waiting objective before phase 2 knows the exact completion cost. Its selected schedule can therefore be difficult or impossible to complete at a specified M. A retry may change T, change the allowed cycle length, or eventually change the worker assignment. These are three distinct adjustments.

The code also offers one-shot and automatic mode selection. The explanation and pseudocode below describe the default two-shot workflow used in the comparison. One-shot adds cycle matching in the initial model and does not run this separate phase-2 completion loop.

## 4. Decentralized SMT: Version A

### Stage A1: create local agents and discover neighbors

Each recharger becomes an agent with a square planning region centered on its anchor, clipped to the map boundary. The distance rule is Chebyshev distance: a radius of R allows at most R cells of horizontal and vertical displacement. The communication radius is twice the planning radius. Agents exchange beacons to discover communication neighbors.

Workers broadcast their route information to agents whose regions intersect their loops. Hearing a worker does not automatically make it eligible. An agent accepts it as a candidate only if the complete loop lies within its region and its local reachability and service estimates are finite. Reachability uses valid motion primitives and their swept cells, rather than simply straight-line distance.

### Stage A2: estimate service effort and negotiate ownership

An agent estimates how soon it can reach a worker's charging neighborhood relative to the worker's battery depletion, together with travel and earliness penalties. It also computes a charging/travel workload estimate. These scores support assignment; they do not replace the later battery-constrained SMT schedule.

Agents exchange candidate information and run bounded message-based negotiation. Claim rounds assign eligible unclaimed workers using deterministic bids based on current group size, estimated workload, service score and agent ID. Rebalance rounds attempt transfers and then repeat claims so an unassigned worker may find a place. Swap rounds consider exchanges that improve group balance. Proposed transfers use votes and commit messages before ownership changes.

The current implementation has no utilization threshold that rejects workers. An explicit worker-count limit can be configured, but the default is uncapped. A legacy utilization-cap parameter is accepted and ignored; older experiments should not be confused with this current behavior.

After negotiation, unclaimed workers receive explanations such as outside all regions, whole loop not inside a region, locally unreachable, explicit capacity, or incomplete negotiation. These are assignment outcomes, not necessarily SMT failures.

### Stage A3: coordinate local planning through a wavefront

Overlapping planning regions create dependencies. For an overlap, the lower recharger ID has priority. Groups in the same dependency level can solve concurrently; later levels wait for earlier plans. A disjoint group does not need to align merely because another recharger exists.

Earlier groups publish their plans. A later group converts those schedules into time-indexed occupancy and swept-cell reservations within its own region. Compatible upstream periods determine an aligned M. Parked, empty groups are represented as stationary occupancy.

### Stage A4: try aligned schedules, then mark fallback if needed

An unaligned group initially solves with T. An aligned group can try up to K_T phase-1 horizons, starting at min(T, M) and decreasing by one, with no candidate below 4. Each local solve performs the two-shot procedure described above.

If the aligned attempts fail and neighbor reservations were present, Version A retries with those reservations removed and without a fixed M. Missing upstream plans or incompatible upstream periods can also cause independent planning with a recorded reason. A successful candidate obtained this way is marked fallback. It may collide with another group even though its own internal SMT constraints were satisfied.

The candidate is retained for diagnostics and later handling. In the comparative report, fallback candidates do not count as accepted complete plans. UNKNOWN stops the local retry path rather than triggering an unconstrained fallback.

### Stage A5: optional refinement and failure recovery

If K_refine is positive, local refinement can propose assignment changes and replan affected groups. Only qualifying improvements are retained. Refinement is disabled by default and in the comparison setup.

The pipeline checks failed acknowledgements in the communication bus. If retries mark a recharger unreachable, it invokes Version A's recovery module, which updates the mission and returns diagnostic reports; setup is then planned for the recovered result. This communication-failure path is separate from an ordinary aligned scheduling failure. Its existence does not establish complete recovery from arbitrary physical delays or missed charging appointments.

### Stage A6: verify the result and prepare physical start positions

The offline verifier checks the modeled group plans for conflicts. Setup routing then plans how rechargers reach the starting positions chosen by their repeating schedules. Workers remain at their loop starts during setup; routing considers stationary occupancy and earlier setup paths. Neighbor readiness and finishing times are propagated before the setup execution barrier opens.

The result reports coverage, conflicts, fallback flags and setup outcomes. The simulation's reservation-based execution support is distinct from B1's offline coordination. A full-coverage candidate is not automatically an accepted executable mission.

## 5. Semi-decentralized SMT: Version B1

### Stage B1: build global assignment candidates

The central grouping component reads all workers and anchors. It expands candidate evaluation to the whole map, removes count caps and disables Version A's refinement. A worker/recharger pair still needs finite reachability and service estimates; global scope does not remove obstacles.

For each eligible pair, a Boolean assignment variable represents whether that recharger owns that worker. A worker can belong to at most one group. The optimization maximizes assigned worker count before optimizing secondary grouping criteria.

### Stage B2: optimize the partition in priority order

The central optimizer uses lexicographic objectives: an earlier objective takes precedence over later objectives. After maximizing coverage, a recovery attempt prefers preserving ownership outside the failed dependency neighborhood when possible. It then minimizes peak estimated service effort, total battery-weighted workload, spatial cost, workload gaps, charging-demand imbalance and worker-count imbalance. Deterministic ownership tie-breaking comes last.

Service effort approximates charging time and travel, including distances between worker charging neighborhoods. Spatial cohesion discourages assigning one recharger widely scattered loops. These are assignment heuristics: an optimal grouping model does not prove that timed local plans exist.

If grouping is not OPTIMAL, the pipeline stops with its grouping diagnostic. A grouping-only option can return the assignment without running scheduling.

### Stage B3: construct a region for each assigned group

Each region contains the anchor, assigned workers' loop sweeps and charging neighborhoods, and traced shortest primitive paths from the anchor to reachable charging locations. A small padding is added and the box is clipped to the map. Thus B1's region follows its assigned geometry, whereas Version A's region is fixed around its anchor.

Overlaps between these group-specific boxes define the priority wavefront. Earlier plans constrain later overlapping groups. Groups in the same level can solve concurrently. If a required earlier plan is missing, failed, or period-incompatible, the dependent group is marked blocked. B1 does not discard the dependency to obtain an independent fallback.

### Stage B4: solve groups with completion-aware phase-1 tie-breaking

B1 uses the same primary phase-1 waiting objective as Version A, then adds subordinate objectives. Among schedules tied on waiting, it prefers lower estimated worst completion effort, lower total completion estimates, lower residual recharge deficits and lower initial recharger primitive cost.

Completion estimates use obstacle-aware distances to worker charging neighborhoods and back toward the recharger's cycle start. They ignore some ordering and dynamic interactions, so the actual phase-2 solver is still required. This phase-1 tie-breaking is an additional algorithmic difference beyond central grouping. It is distinct from enabling the optional phase-2 cost objective.

For aligned two-shot jobs, B1 tries at most two phase-1 horizons, also bounded by K_T and the minimum of 4. Successful plans are cached only when the complete planning inputs match, including region, horizon, energy model and upstream occupancy. Matching worker membership alone is insufficient.

### Stage B5: try bounded cycle extensions

If the joint local plan fails, B1 identifies overlap components containing failed groups. When these components also contain a successful plan with a known M, it can propose a longer common horizon: the largest relevant successful or previously forced M, plus 4. The wavefront is solved again with this forced horizon. There are at most three passes per assignment: the initial pass and two extension attempts.

The implementation uses one target computed across the affected set, which may include more than one overlap component. Unaffected jobs may be reused from the exact-input cache. A changed cycle is solved again; existing trajectories are not merely padded and declared valid. UNKNOWN, absence of a failed group, or absence of a successful affected reference cycle ends this extension path.

### Stage B6: check plans, prepare setup, and regroup if necessary

For the current assignment, B1 verifies conflicts, checks coverage and the joint-period limit, and plans setup with the shared setup module. It returns SUCCESS if the resulting mission is executable.

If any local solver returned UNKNOWN, it stops with an inconclusive planning status. If workers are unassigned because no geometric candidate exists, it reports unreachable workers. Otherwise it excludes the exact attempted partition and asks the central optimizer for a different one. Previously achieved assignment coverage becomes a lower bound, and recovery locality remains a preference rather than a hard restriction.

The default allows five additional regroupings: at most six assignment attempts. Exhausting these retries is a bounded-search outcome, not proof that every possible schedule is impossible. B1 does not invoke Version A's online runtime arbiter or its unconstrained fallback. Its regrouping is offline, before mission execution.

## 6. A small coordination example

Suppose rechargers R1 and R2 have overlapping regions and R3 is disjoint. R1 and R3 may plan together. R2 then plans while avoiding R1's reserved cells and matching the compatible cycle. If R2 fails, Version A tries shorter phase-1 horizons and may finally return an independently planned, flagged fallback candidate. B1 tries its shorter-horizon budget, may retime the affected overlap component, and can eventually change the central worker assignment. R3 is not inherently required to change its period; an identical job can be reused in B1.

## 7. Pseudocode conventions

The following is structured algorithmic pseudocode, not executable Python. Procedures such as NEGOTIATE and VERIFY represent the implemented modules explained above. A “failure” below refers to the current bounded problem; UNKNOWN is always kept distinct. T and M use the stored horizon convention, and T_MAX_EXTRA denotes the configured phase-2 extension budget.

### Algorithm 1: shared two-shot local solver

```text
PROCEDURE TWO_SHOT(group, region, T, reservations, fixed_M, variant)
    p1 ← SOLVE_PHASE_1(group, region, T, reservations,
                       minimize waiting first,
                       B1 completion tie-breaks if variant = B1)
    IF p1.status = UNKNOWN THEN RETURN UNKNOWN
    IF p1 has no schedule THEN RETURN PHASE_1_UNSAT

    appointments, completion ← EXTRACT_COMPLETION_DATA(p1)
    lower ← COMPLETION_LOWER_BOUND(completion)
    IF fixed_M exists THEN
        candidates ← [fixed_M]
    ELSE
        candidates ← max(T, lower), ..., T + T_MAX_EXTRA

    FOR M IN candidates IN increasing order DO
        IF M < lower THEN CONTINUE
        p2 ← SOLVE_PHASE_2(p1, appointments, completion, M,
                           region, reservations)
        IF p2.status = UNKNOWN THEN RETURN UNKNOWN
        IF p2 is feasible THEN RETURN COMBINE(p1, p2, T, M)
    RETURN COMPLETION_FAILED_FOR_SELECTED_SCHEDULE
END PROCEDURE
```

### Algorithm 2: decentralized SMT (Version A)

```text
PROCEDURE DECENTRALIZED_SMT(instance, anchors, parameters)
    agents ← CREATE_LOCAL_AGENTS(anchors, planning_radius)
    EXCHANGE_BEACONS_AND_WORKER_INFORMATION(agents)
    candidates ← CHECK_WHOLE_LOOP_ELIGIBILITY_AND_REACHABILITY(agents)
    groups ← NEGOTIATE(candidates,
                       claim rounds,
                       rebalance-and-reclaim rounds,
                       swap rounds)
    unclaimed ← EXPLAIN_UNASSIGNED_WORKERS(groups, candidates)

    dependencies ← ORDER_OVERLAPPING_REGIONS_BY_RECHARGER_ID(agents)
    FOR level IN TOPOLOGICAL_LEVELS(dependencies) DO
        FOR EACH group IN level IN PARALLEL WHEN ENABLED DO
            reservations, M, reason ← READ_EARLIER_NEIGHBOR_PLANS(group)
            IF a dependency is missing or incompatible THEN
                reservations ← NONE; M ← NONE
            horizons ← ALIGNMENT_HORIZONS(T, M, K_T, minimum = 4)
            result ← TRY_TWO_SHOT_HORIZONS(group, horizons,
                                           reservations, M, variant = A)
            IF result.status ≠ UNKNOWN AND result failed
               AND reservations exist THEN
                result ← TWO_SHOT(group, group.region, first(horizons),
                                   NONE, NONE, A)
                reason ← ALIGNED_PLANNING_FAILED
            fallback ← result succeeded AND reason exists
            STORE_AND_PUBLISH(result, fallback, reason)

    IF K_refine > 0 THEN
        REFINE_LOCALLY_WITH_REPLANNING_AND_IMPROVEMENT_CHECKS(groups)
    conflicts ← VERIFY_GROUP_PLANS()
    IF acknowledgement retries identify failed rechargers THEN
        result ← RECOVER_VERSION_A_MISSION(groups, plans, failed_rechargers)
    ELSE
        result ← ASSEMBLE_RESULT(groups, plans, unclaimed, conflicts)
    result.setup ← PLAN_AND_EXECUTE_SETUP_SIMULATION(result)
    RETURN result with coverage, statuses, fallback flags and setup outcome
END PROCEDURE
```

TRY_TWO_SHOT_HORIZONS stops on success or UNKNOWN. An unaligned group uses only T; an aligned group uses descending horizons from min(T, M), subject to its budget and the minimum of 4. Refinement and recovery delegate their internal decisions to the corresponding modules rather than silently changing assignments.

### Algorithm 3: semi-decentralized SMT (Version B1)

```text
PROCEDURE SEMI_DECENTRALIZED_SMT(instance, anchors, parameters)
    excluded ← empty set; required_coverage ← 0
    previous ← NONE; focus ← empty set; cache ← empty map

    FOR attempt ← 0 TO max_regroupings DO
        assignment ← OPTIMIZE_GLOBAL_GROUPING(
                         instance, anchors, excluded, required_coverage,
                         previous, focus, lexicographic objectives)
        IF assignment.status ≠ OPTIMAL THEN
            RETURN latest diagnostics with grouping stop reason
        required_coverage ← assignment.assigned_count
        forced_horizons ← empty map

        FOR cycle_attempt ← 0 TO 2 DO
            regions ← BUILD_GROUP_SPECIFIC_REGIONS(assignment)
            plans ← RESERVED_WAVEFRONT(assignment, regions,
                                       forced_horizons, cache)
            IF ALL_ASSIGNED_GROUPS_VALID(plans)
               AND NO_VERIFIED_CONFLICTS(plans)
               AND JOINT_PERIOD_WITHIN_LIMIT(plans) THEN BREAK

            failed ← groups with workers but no valid plan
            affected ← OVERLAP_COMPONENTS_TOUCHING(failed)
            live_M ← successful horizons within affected
            IF failed is empty OR live_M is empty
               OR a failed result is UNKNOWN THEN BREAK
            target ← max(live_M and previous forced values in affected) + 4
            FORCE target FOR all groups in affected

        result ← VERIFY_AND_PREPARE_SETUP(assignment, plans)
        RECORD_ATTEMPT(result)
        IF result is executable THEN RETURN SUCCESS(result)
        IF any local solver status is UNKNOWN THEN
            RETURN PLANNING_UNKNOWN(result)
        IF assignment has unassigned workers THEN
            RETURN UNREACHABLE_WORKERS(result)

        previous ← assignment.groups
        focus ← failed groups and their direct overlap neighbors
        ADD exact assignment.groups TO excluded

    RETURN latest result with RETRY_LIMIT_REACHED
END PROCEDURE
```

### Algorithm 4: B1's reserved wavefront helper

```text
PROCEDURE RESERVED_WAVEFRONT(groups, regions, forced_horizons, cache)
    dependencies ← ORDER_REGION_OVERLAPS_BY_RECHARGER_ID(regions)
    FOR level IN TOPOLOGICAL_LEVELS(dependencies) DO
        FOR EACH group IN level IN PARALLEL WHEN ENABLED DO
            IF group is empty THEN RECORD_PARKED_GROUP(); CONTINUE
            reservations, M, problem ← READ_EARLIER_NEIGHBOR_PLANS(group)
            IF problem exists THEN RECORD_DEPENDENCY_BLOCKED(); CONTINUE
            IF a forced horizon conflicts with upstream alignment THEN
                RECORD_DEPENDENCY_BLOCKED(); CONTINUE
            APPLY forced horizon IF present
            horizons ← ALIGNMENT_HORIZONS(T, M, min(K_T, 2), minimum = 4)
            job ← COMPLETE_INPUTS(group, region, horizons, reservations, M)
            IF job exists in cache THEN result ← COPY(cache[job])
            ELSE
                result ← TRY_TWO_SHOT_HORIZONS(job, variant = B1)
                IF result succeeded THEN cache[job] ← COPY(result)
            STORE result
        PUBLISH plans or failure messages to later overlapping groups
    RETURN plans
END PROCEDURE
```

## 8. What differs and what is shared

| Decision | Decentralized SMT (A) | Semi-decentralized SMT (B1) |
| --- | --- | --- |
| Worker ownership | Local claims, transfers and swaps | Global lexicographic Z3 assignment |
| Region | Fixed radius around anchor | Geometry of the assigned group and reachable connecting paths |
| Initial waiting objective | Minimize waiting | Minimize waiting, then completion-aware tie-breaks |
| Coordination | Priority wavefront for overlaps | Priority wavefront for overlaps |
| Aligned horizon budget | Up to K_T | At most min(K_T, 2) |
| After alignment failure | May drop reservations and flag fallback | Keeps reservations; bounded cycle extension, then possible regrouping |
| Reuse | Preserved plans supported during local replanning | Exact-input successful-plan cache across attempts |
| Optional refinement | Available; default off | Disabled by the grouping pipeline |
| Recovery policy | Communication-failure recovery module | Bounded offline central regrouping; UNKNOWN stops |
| Setup | Shared setup routing and barrier implementation | Shared setup routing and barrier implementation |

The implementations are offline planning and simulation pipelines with specific execution-support components. Their model checks do not establish arbitrary-delay or physical-failure safety. Defaults can be overridden, so an experiment's saved configuration remains the authority for its parameter values.
