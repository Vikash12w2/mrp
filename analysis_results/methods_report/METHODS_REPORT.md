# How the decentralized and semi-decentralized SMT planners work

## 1. Purpose and shared problem

**Decentralized SMT (Version A)** assigns workers through local negotiation between rechargers. **Semi-decentralized SMT (Version B1)** assigns workers with a central optimizer and then plans their schedules in local groups. Both use Z3, an SMT solver: a program that finds decisions satisfying encoded movement, battery, recharge, and collision constraints.

A worker repeatedly follows a prescribed loop. Moving consumes battery according to its input energy model. A mobile recharger travels through free cells and replenishes a worker when the modeled charging conditions hold. The planner must decide when workers move, wait, or recharge, and where rechargers move, so the resulting schedule can repeat.

The inputs are the obstacle map, worker loops and energy requirements, battery capacities, recharge rate, recharger motion primitives, recharger anchor positions, requested horizon T, and algorithm parameters. An anchor is a recharger's physical starting location. It need not equal the starting position selected for its repeating schedule; setup routing connects these positions later.

The outputs are worker assignments, worker and recharger schedules, achieved cycle lengths, solver statuses, conflict checks, unserved-worker explanations, and setup status. A returned candidate may be partial or use fallback. Finding an assignment alone does not establish a feasible timed schedule.

### Notation

Worker i follows loop L_i, has battery capacity B_i, consumes e_i,k on loop edge k, and can receive at most delta charge per step. Recharger r starts physically at anchor a_r. Its assigned worker set is G_r. An indicator 1[condition] equals one when its condition is true. Distances count valid motion primitives, including obstacle and swept-footprint checks.

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

### 3.1 Phase 1: choose actions and minimize waiting

For a fixed group and horizon T, the solver jointly chooses worker actions and recharger trajectories. The following state updates and constraints define the choice; upstream reservations are added when the group has higher-priority neighbors.

At time t, k_i,t is worker i's loop index, b_i,t its battery, u_i,t its action, and q_r,t the recharger position. A worker's position is the loop cell indexed by k_i,t. Initially k_i,1 = 1 and b_i,1 = B_i. The worker returns to its initial position at T, and its battery stays bounded:

$$
0\leq b_{i,t}\leq B_i,\qquad v_{i,k_{i,T}}=v_{i,1}.
$$

Each selected action implies one update. The successor function wraps the last loop index back to the first:

$$
u_{i,t}=\mathrm{move}\Rightarrow k_{i,t+1}=\mathrm{next}(k_{i,t}),\quad b_{i,t+1}=b_{i,t}-e_{i,k_{i,t}}.
$$

$$
u_{i,t}=\mathrm{wait}\Rightarrow k_{i,t+1}=k_{i,t},\quad b_{i,t+1}=b_{i,t}.
$$

$$
u_{i,t}=\mathrm{charge}\Rightarrow k_{i,t+1}=k_{i,t},\quad b_{i,t+1}=b_{i,t}+c_{i,t},\quad 1\leq c_{i,t}\leq\delta.
$$

Charging additionally requires a non-full battery, an adjacent assigned recharger and that recharger's stationary primitive during the transition. Upper battery bounds prevent overcharging. In this model waiting preserves charge. The movement equation shows the default loop-index-based cost mode.

For primitive p with displacement d_p and footprint S_p, recharger motion and free-space constraints are:

$$
q_{r,t+1}=q_{r,t}+d_{p_{r,t}},\qquad q_{r,t}+S_{p_{r,t}}\subseteq\mathcal{B}_r.
$$

The region contains free cells only. Enabled collision constraints exclude intersections with modeled worker edges/endpoints and other recharger sweeps. Both versions first minimize non-work transitions, including charging:

$$
J_{\mathrm{wait}}=\sum_{i\in G_r}\sum_{t=1}^{T-1}\mathbf{1}[u_{i,t}\ne\mathrm{move}],\qquad \min J_{\mathrm{wait}}.
$$

If phase 1 is UNSAT, there is no selected schedule to complete and the caller can try another T. UNKNOWN stops that local retry path. A successful phase 1 passes its worker path and recharge appointments to phase 2.

### 3.2 Phase 2: complete recharge and close the cycle

Let h_i be the last phase-1 state at which worker i's battery changed. Its final recharge duration is:

$$
d_i=\left\lceil\frac{B_i-b_{i,h_i}}{\delta}\right\rceil,\qquad M_{\mathrm{lower}}=\max_i h_i+\max_i d_i.
$$

This is the implementation's lower bound, not max_i(h_i+d_i). Each intermediate appointment fixes the recharger at its recorded cell at t and t+1. For final charging, the solver selects exactly one start s_i in [h_i, M-d_i] and a charging neighbor of the worker's loop start. The recharger remains there for d_i+1 states, which represent d_i stationary transitions. Recharger cycle endpoints satisfy:

$$
q_{r,1}=q^{(1)}_{r,1},\qquad q_{r,M}=q_{r,1}.
$$

For an unaligned job, M is searched from max(T, M_lower) through T plus the configured extension budget. A fixed aligned M is checked only when it meets the lower bound. The worker trajectory and intermediate appointments stay fixed, but phase-2 recharger movement between those appointments can change.

The first feasible permitted M is accepted. If no permitted M succeeds, the caller receives completion failure for that selected phase-1 schedule. B1 adds the phase-1 completion tie-breaks described in B4; the primary waiting objective remains the same.

## 4. Decentralized SMT: Version A

### Stage A1: discover neighbors and compute eligibility

Each recharger creates an agent at its anchor. Agents exchange beacons within communication radius R_c. A worker broadcasts its loop to agents whose planning regions intersect it.

Version A clips a square region to the map. With free-cell set F, planning radius R_s and communication radius R_c:

$$
\mathcal{B}_r=\{(x,y)\in\mathcal{F}:\max(|x-a_{r,x}|,|y-a_{r,y}|)\leq R_s\},\qquad R_c=2R_s.
$$

Breadth-first search starts at a_r and expands only primitives whose complete swept cells stay free and inside the region. D_r(v) is the minimum resulting distance to any free cell adjacent to worker cell v. Candidate i must have its entire loop inside the region, and finite service/workload estimates. Thus region intersection allows hearing a worker; whole-loop containment and reachability allow bidding for it.

### Stage A2: compute bids and negotiate assignments

Let tau_i be the number of sequential loop moves affordable before the next move would exceed the available charge. The agent scans k = 0 through tau_i for the first loop occurrence satisfying D_r(v_i,k) <= k. Its travel distance is t_arr. If no occurrence satisfies that test, it uses the reachable depletion location's distance; an unreachable depletion location makes the score infinite.

$$
S_{ri}=u_1\max(0,t_{\mathrm{arr}}-\tau_i)+u_2t_{\mathrm{arr}}+u_3\max(0,\tau_i-t_{\mathrm{arr}}).
$$

The terms represent late arrival, travel and earliness. Default weights are 1, 0.1 and 0.01. Let ebar_i be mean loop movement cost and Dbar_ri mean distance over reachable loop charging neighborhoods. The workload estimate is:

$$
U_{ri}=\frac{\bar e_i}{\delta}+\frac{\bar e_i}{B_i}\bar D_{ri}.
$$

Using current neighbor membership snapshots, an eligible agent compares bids lexicographically, smallest first:

$$
\mathrm{bid}_{ri}=\left(|G_r|,\ \sum_{j\in G_r}U_{rj}+U_{ri},\ S_{ri},\ r\right).
$$

It claims at most one worker per round and only after receiving all required neighbor states. Group count is considered first, then estimated workload, service score and ID. For two neighboring groups, the balance cost is:

$$
C_{rs}=\left(|G_r|^2+|G_s|^2,\ \left(\sum_{i\in G_r}U_{ri}\right)^2+\left(\sum_{i\in G_s}U_{si}\right)^2\right).
$$

Swap mode accepts a better C, or a tie in C with a lower summed service score, within the configured tolerance. Rebalance mode searches feasible transfers/exchanges that additionally admit an unclaimed worker; it need not improve balance alone. An offer specifies both agents' before/after memberships. Each agent votes for its preferred valid offer; matching votes and a successfully delivered commit update ownership.

The negotiation sequence is active in the current implementation. Initial claim rounds use a budget equal to the maximum of 1, K_round and the sum of local candidate counts. Each of K_unclaimed cycles runs one rebalance round followed by one new claim round. With K_swap positive, swap rounds use a budget of max(K_swap, largest candidate count squared). Defaults are K_round = 20, K_unclaimed = 10 and K_swap = 10. Under reliable messaging, unchanged claim/swap rounds can stop early.

After these rounds, agents report unclaimed workers with the applicable reason: no hearing region, no whole-loop region, unreachable service, incomplete negotiation, or an explicitly configured count limit. The evaluated setup uses no count cap.

### Stage A3: build the wavefront and reserve upstream motion

An overlap creates a directed dependency from lower ID r to higher ID s. The level is computed recursively:

$$
\ell(s)=0\ \mathrm{if\ no\ predecessors};\qquad \ell(s)=1+\max_{r\rightarrow s}\ell(r)\ \mathrm{otherwise}.
$$

Groups at the same level are dispatched together. Earlier worker positions/edges and recharger sweeps form reservations E_s(t), clipped to s's region. Its recharger footprint must avoid those time-indexed cells. Worker constraints use the earlier rechargers' occupancy/sweeps. An empty group contributes its parked anchor.

### Stage A4: align horizons and return flagged fallback candidates

All live predecessors must have the same period P_r = M_r - 1 for alignment. Their common M determines the candidate phase-1 horizons:

$$
T_k=\min(T,M)-k,\qquad T_k\geq4.
$$

Version A uses k = 0 through K_T-1; an unaligned group tries T only.

Each candidate horizon runs the shared two-shot solver. A successful aligned result is published to lower-priority overlapping groups. If the attempts fail while reservations exist, Version A retries from the first candidate T with reservations removed and no fixed M. Missing or incompatible upstream plans can likewise lead to independent solving with a recorded dependency reason.

A successful independent result is stored in the group plan with fallback = true and published through the ordinary plan-message path. The final verifier checks it together with the other plans, and the result exposes both conflicts and fallback flags. There is no separate automatic fallback-repair step in this pipeline. The comparison excludes flagged fallback plans from accepted complete results. UNKNOWN returns immediately instead of launching the independent retry.

### Stage A5: detect failed rechargers, reassign and replan

Version A invokes recovery when exhausted acknowledgement retries mark a destination recharger unreachable. The bus records failed deliveries as (sender, destination, message type); the current default treats at least one distinct sender reporting the destination unreachable as a failure signal. The pipeline calls the recovery procedure with a recharger-failure event. This is separate from the fallback path in A4.

For a failed recharger f, recovery obtains its modeled stop cell from its periodic plan, removes its anchor, group and plan, and marks the stop cell as an obstacle. Surviving agents keep their own previous memberships, locally heard worker information and neighbor views. Each agent that senses the stop cell updates its local obstacle map and recomputes reachability and candidate scores.

The survivors rerun the same claim, rebalance and swap negotiation from A2. Workers previously owned by f become claimable where a survivor already has local eligibility information and sees no surviving owner. The removed group's worker list is used for reporting reassignment, not as a global auction input. A worker with no eligible successful new owner is reported unserved.

Each surviving agent marks itself dirty when its membership changes or a newly sensed obstacle affects a nonempty group. Dirty notices propagate to lower-priority neighbors: a changed or missing higher-priority notice requires replanning, because its old reservations may no longer apply. With D_r denoting that dirty flag:

$$
D_r\leftarrow D_r\vee\bigvee_{s\in N(r),\ s<r}\left(D_s\vee\mathbf{1}[\mathrm{notice}_s\ \mathrm{missing}]\right).
$$

The wavefront planner retains plans for unchanged agents and solves the dirty set again using the updated groups, maps and upstream reservations. Recovery verifies the updated plans and returns reassigned workers, unserved workers and the list of re-solved groups. Setup is then prepared for the resulting mission.

The recovery procedure also accepts an explicitly supplied worker-failure event. In that case it removes the worker from the mission and its owner's membership, marks the stop cell as an obstacle, and propagates the affected replanning notices. This worker-event path is distinct from the automatic acknowledgement-triggered recharger path described above.

### Stage A6: verify schedules and prepare the start barrier

For live group periods P_r = M_r-1, verification repeats their trajectories over H = min(lcm(P_r), 3000). It compares modeled cell/sweep intersections for pairs involving a recharger; worker-worker pairs are skipped. B1 additionally requires the full joint period to fit within that limit for its accepted joint schedule.

Setup routes each recharger from anchor a_r to its planned q_r,1 using a time-expanded search. Candidate transitions are primitive moves or waits, filtered against stationary worker starts and earlier setup reservations. Readiness and completion time are propagated across neighbors by AND and MAX, respectively:

$$
\mathrm{ready}'_r=\mathrm{ready}_r\wedge\bigwedge_{s\in N(r)}\mathrm{ready}_s,\qquad f'_r=\max(\{f_r\}\cup\{f_s:s\in N(r)\}).
$$

The setup executor then tracks positions and waits for the component's start barrier. This connects the physical anchor configuration to the repeating trajectories selected by SMT.

## 5. Semi-decentralized SMT: Version B1

### Stage B1: construct global assignment candidates

The central grouping component evaluates all worker/recharger pairs over the map using finite reachability and service scores. Candidate evaluation uses the same charging-neighborhood geometry as A1/A2, with global rather than anchor-radius scope. There is one binary assignment variable for every eligible pair.

### Stage B2: solve the assignment objectives in order

Let x_ri be binary for an eligible recharger/worker pair. Other pairs are excluded. Ownership and preserved coverage satisfy:

$$
\sum_r x_{ri}\leq1,\qquad A=\sum_{r,i}x_{ri},\qquad A\geq A_{\mathrm{previous}}\ \mathrm{on\ retries}.
$$

Define charging demand rho_i = ebar_i/delta and average anchor-to-loop distance Dbar_ri. Let D_ij be the symmetric mean nearest-loop distance between charging neighborhoods, computed by primitive-based shortest paths. B1 constructs:

$$
n_r=\sum_i x_{ri},\quad L_r=\sum_i\rho_i x_{ri},\quad U_r=\sum_i x_{ri}\left(\rho_i+\frac{\bar e_i}{B_i}\bar D_{ri}\right).
$$

Its service estimate adds expected charging over T, a round-trip proxy and inter-worker travel:

$$
F_r=T L_r+2O_r+\sum_{i<j}D_{ij}x_{ri}x_{rj},\qquad O_r\geq0,\quad O_r\geq\bar D_{ri}x_{ri}.
$$

Spatial cost combines anchor travel and within-group pair distances:

$$
C_{\mathrm{space}}=\sum_{r,i}\bar D_{ri}x_{ri}+\sum_r\sum_{i<j}D_{ij}x_{ri}x_{rj}.
$$

The products are logical conjunctions in the implementation. Disconnected pair estimates receive a large map-area penalty rather than making the pair ineligible. The optimizer uses this lexicographic objective:

$$
\min_{\mathrm{lex}}\left(-A,\ C_{\mathrm{keep}},\ \max_r F_r,\ \sum_r U_r,\ C_{\mathrm{space}},\ \sum_{r<s}|U_r-U_s|,\ \sum_{r<s}|L_r-L_s|,\ \sum_r n_r^2\right).
$$

C_keep is included only on recovery with a previous assignment and a nonempty focus: it counts ownership changes outside that focus. Final individual-worker ID tie-breaks follow these objectives. Count squares use an equivalent linear encoding:

$$
n_r^2=\sum_{k=1}^{N_r}(2k-1)\mathbf{1}[n_r\geq k].
$$

An exact attempted partition is excluded by requiring at least one eligible ownership Boolean to differ. No failed worker subset is globally banned. The next optimization can change the failed groups and prefer preserving unrelated ownership.

Only an OPTIMAL grouping proceeds to scheduling. The assignment objective is an estimate of service effort; timed feasibility is established by local SMT planning in the next stages.

### Stage B3: construct regions and their overlap dependencies

Each region contains the anchor, assigned workers' loop sweeps and charging neighborhoods, and traced shortest primitive paths from the anchor to reachable charging locations. A small padding is added and the box is clipped to the map. Thus B1's region follows its assigned geometry, whereas Version A's region is fixed around its anchor.

Overlaps between these group-specific boxes define the priority wavefront. Earlier plans constrain later overlapping groups. Groups in the same level can solve concurrently. If a required earlier plan is missing, failed, or period-incompatible, the dependent group is marked blocked. B1 does not discard the dependency to obtain an independent fallback.

### Stage B4: choose completion-aware phase-1 schedules

For an end-state battery deficit and each worker's start charging neighborhood, B1 estimates travel from the end recharger position to the worker, recharge duration, and return to the recharger's selected cycle start. For a local one-recharger group:

$$
\widehat d_i=\left\lceil\frac{B_i-b_{i,T}}{\delta}\right\rceil,\qquad \widehat C_i=D(q_{r,T},L_i[1])+\widehat d_i+D(L_i[1],q_{r,1}).
$$

D here measures shortest travel to/from the charging neighborhood, not to the worker's occupied cell. A general multi-recharger call uses the smallest individual estimate. After the primary waiting objective, B1 minimizes:

$$
\left(\max_i\widehat C_i,\ \sum_i\widehat C_i,\ \sum_i\widehat d_i,\ \sum_r\sum_{t=1}^{T-1}\mathrm{cost}(p_{r,t})\right)_{\mathrm{lex}}.
$$

These subordinate objectives select among schedules tied on waiting. They do not replace phase-2 feasibility checking, and they are separate from optional phase-2 cost optimization.

Aligned groups try at most min(K_T, 2) horizons from min(T, M) downward, excluding values below 4; unaligned groups initially use T. Successful jobs are cached by their complete inputs, including assigned workers, map, region, horizon, energy parameters and upstream reservations. Reuse requires an exact match of those inputs.

### Stage B5: retime affected overlap components

After a failed wavefront pass, B1 finds the connected overlap components containing failed groups. A live plan in the affected set supplies a reference cycle for extension.

B1's cycle-extension target is:

$$
M_{\mathrm{new}}=\max(\{M_r:\ r\ \mathrm{live\ and\ affected}\}\cup\{M_r^{\mathrm{forced}}:\ r\ \mathrm{affected}\})+4.
$$

Affected means connected by region overlap to a failed group. A usable live reference is required. B1 makes at most two extensions after the initial pass; each changed job is solved again.

If there is no failed group, no successful affected reference cycle, or a failed solver result is UNKNOWN, the extension loop stops. Unchanged jobs can use the exact-input cache during a subsequent pass.

### Stage B6: verify, prepare setup, and choose another assignment

For the current assignment, B1 verifies conflicts, checks coverage and the joint-period limit, and plans setup with the shared setup module. It returns SUCCESS if the resulting mission is executable.

If any local solver returned UNKNOWN, it stops with an inconclusive planning status. If workers are unassigned because no geometric candidate exists, it reports unreachable workers. Otherwise it excludes the exact attempted partition and asks the central optimizer for a different one. Previously achieved assignment coverage becomes a lower bound, and recovery locality remains a preference rather than a hard restriction.

The default allows five additional regroupings: at most six assignment attempts. Exhausting these retries is a bounded-search outcome, not proof that every possible schedule is impossible. B1 does not invoke Version A's online runtime arbiter or its unconstrained fallback. Its regrouping is offline, before mission execution.

## 6. Pseudocode

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

    conflicts ← VERIFY_GROUP_PLANS()
    IF acknowledgement retries identify failed rechargers THEN
        result ← RECOVER_VERSION_A_MISSION(groups, plans, failed_rechargers)
    ELSE
        result ← ASSEMBLE_RESULT(groups, plans, unclaimed, conflicts)
    result.setup ← PLAN_AND_EXECUTE_SETUP_SIMULATION(result)
    RETURN result with coverage, statuses, fallback flags and setup outcome
END PROCEDURE
```

TRY_TWO_SHOT_HORIZONS stops on success or UNKNOWN. An unaligned group uses only T; an aligned group uses descending horizons from min(T, M), subject to its budget and the minimum of 4. RECOVER_VERSION_A_MISSION follows Algorithm 5 below.

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

### Algorithm 5: Version A recovery

```text
PROCEDURE RECOVER_VERSION_A_MISSION(result, failure)
    stop_cell ← POSITION_AT_FAILURE_TIME(failure, existing_plan)
    REMOVE failed recharger's group, anchor and plan
    ADD stop_cell TO mission obstacles
    FOR EACH surviving agent DO
        UPDATE locally sensed map and remove failed neighbor
        RECOMPUTE reachability, candidates and service estimates
    groups ← NEGOTIATE_FROM_SURVIVING_MEMBERSHIPS()
    dirty ← agents with changed membership or affected local geometry
    PROPAGATE dirty notices along higher-to-lower dependencies
    plans ← WAVEFRONT_REPLAN(dirty, keep unchanged plans)
    RETURN updated mission, verified conflicts,
           reassigned workers, unserved workers and replanned groups
END PROCEDURE
```

Algorithm 5 describes the automatic recharger-failure path. For a supplied worker-failure event, remove that worker and update its owner's membership instead of removing a recharger; then propagate dirty notices and replan.

## 7. What differs and what is shared

| Decision | Decentralized SMT (A) | Semi-decentralized SMT (B1) |
| --- | --- | --- |
| Worker ownership | Local claims, transfers and swaps | Global lexicographic Z3 assignment |
| Region | Fixed radius around anchor | Geometry of the assigned group and reachable connecting paths |
| Initial waiting objective | Minimize waiting | Minimize waiting, then completion-aware tie-breaks |
| Coordination | Priority wavefront for overlaps | Priority wavefront for overlaps |
| Aligned horizon budget | Up to K_T | At most min(K_T, 2) |
| After alignment failure | May drop reservations and flag fallback | Keeps reservations; bounded cycle extension, then possible regrouping |
| Reuse | Preserved plans supported during local replanning | Exact-input successful-plan cache across attempts |
| Recovery policy | Communication-failure recovery module | Bounded offline central regrouping; UNKNOWN stops |
| Setup | Shared setup routing and barrier implementation | Shared setup routing and barrier implementation |
