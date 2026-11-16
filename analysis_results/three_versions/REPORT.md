# Centralized, Version A and B1: saved-run comparison

Inputs: `C:\Users\vikas\Downloads\mrp_version_a\mrp\mrp_backup\version_a_b1_parallel` and `C:\Users\vikas\Downloads\mrp_version_a\mrp\mrp_backup\study-20261114-180038`. Generated from saved JSON results; no solver reruns.

## Scope and interpretation

The A/B1 suite contains 56 runs: 44 base runs plus 12 anchor followup runs. Match scenarios by dataset, grid, workers, rechargers and requested T; followups remain separate because their anchors differ. Centralized matches exist for 22/22 base scenarios. All individual source paths are recorded in all_runs.csv. Missing cases are listed explicitly and are not counted as failures.

Centralized means this project's Python/Z3 centralized two-shot comparator, not a fresh execution of the paper's original C++ implementation. For this comparison, a centralized result with SAT, full coverage and no reported conflicts counts as a pass. The executable_pass analysis column uses that reporting convention for centralized results; it does not alter the saved solver outputs. A/B1 use their recorded executable acceptance checks, including no unconstrained fallback and a verified-period limit of 3000 steps.

The suite uses collision constraints and disables optional phase-2 cost optimization. A uses local grouping and regional planning; B1 uses global grouping and group-specific regions. These are complete method comparisons, not an isolated test of grouping. Historical centralized timings and newer parallel A/B1 runs are not controlled hardware repetitions. Report speedups as observed ratios; hardware, notebook load and parallelism may contribute. No confidence intervals or statistical significance claims are supported by one run per scenario. Two-shot planning does not guarantee global joint optimality.

## Overall results

| scope | method | runs | plan_pass | executable_pass | mean_coverage | median_wall_s | total_wall_s | mean_hard_eff_pass | mean_soft_eff_pass |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| All A/B1 (including anchor followups) | A | 28 | 18 | 18 | 94.05 | 37.57 | 1737.97 | 27.48 | 33.27 |
| All A/B1 (including anchor followups) | B1 | 28 | 28 | 28 | 100.00 | 207.98 | 21197.08 | 33.71 | 39.55 |
| Base suite | A | 22 | 13 | 13 | 92.42 | 30.30 | 802.83 | 30.34 | 36.89 |
| Base suite | B1 | 22 | 22 | 22 | 100.00 | 41.37 | 17372.29 | 34.15 | 40.32 |
| Base suite | Centralized | 22 | 22 | 22 | 100.00 | 84.10 | 43330.97 | 24.09 | 28.52 |

Efficiency above is conditional on each method's planning successes, which may be different cases. Partial coverage must not be interpreted as a complete-mission efficiency result.

## Matched runtime ratios

| method | matched | faster_than_central | median_speedup | geometric_mean_speedup |
| --- | --- | --- | --- | --- |
| A | 22 | 21 | 2.85 | 5.26 |
| B1 | 22 | 13 | 1.20 | 1.42 |

Speedup is centralized wall time divided by the method's wall time. These ratios include failed/partial A runs, so a fast runtime does not imply successful completion.

## Same scenarios, all three planning passes

| method | common_cases | median_wall_s | mean_hard_eff | mean_soft_eff | mean_primitive_cost |
| --- | --- | --- | --- | --- | --- |
| Centralized | 13 | 43.54 | 22.63 | 26.82 | 72.69 |
| A | 13 | 15.93 | 30.34 | 36.89 | 46.15 |
| B1 | 13 | 15.10 | 36.47 | 43.27 | 28.77 |

This comparison uses only the 13 common planning successes. Primitive cost is the recorded per-hypercycle sum; differing cycle lengths limit its interpretation as an energy-rate comparison.

## B1 versus A

B1 is faster on 5/22 base scenarios. The median paired B1/A runtime ratio is 2.21. Full executable successes: A 13/22; B1 22/22. Interpret the runtime difference alongside the completion difference.

On the 13 common successful scenarios, the median paired observed speedup versus centralized is 2.55 for A and 3.32 for B1. B1's mean hard-efficiency advantage over A on these same cases is 6.14 percentage points. This avoids comparing efficiency on different success subsets.

B1's main advantage in this suite is complete executable coverage; its cost is a long runtime tail. A is attractive for quick candidate plans, but its short runtimes include incomplete or fallback outcomes. The results support B1 for the completed offline planning task, while leaving predictability of planning time as a performance issue. The saved aggregate runtimes alone do not identify which solver constraints caused individual slow cases.

### Dataset breakdown (base suite)

| method | dataset | runs | executable | mean_coverage | median_wall_s |
| --- | --- | --- | --- | --- | --- |
| A | input-artfloor | 6 | 6 | 100.00 | 21.49 |
| A | input-random-20 | 5 | 4 | 96.67 | 33.54 |
| A | input-random-30 | 5 | 2 | 90.00 | 64.84 |
| A | input-warehouse | 6 | 1 | 83.33 | 32.26 |
| B1 | input-artfloor | 6 | 6 | 100.00 | 18.59 |
| B1 | input-random-20 | 5 | 5 | 100.00 | 21.19 |
| B1 | input-random-30 | 5 | 5 | 100.00 | 190.52 |
| B1 | input-warehouse | 6 | 6 | 100.00 | 1158.77 |

Slowest base B1 runs:

| case_id | dataset | workers | rechargers | A_wall_s | B1_wall_s | B1_runtime_over_A |
| --- | --- | --- | --- | --- | --- | --- |
| base-22 | input-warehouse | 6 | 3 | 79.31 | 7238.80 | 91.28 |
| base-20 | input-warehouse | 3 | 2 | 38.50 | 3620.71 | 94.05 |
| base-19 | input-warehouse | 3 | 2 | 47.70 | 1924.13 | 40.34 |
| base-5 | input-artfloor | 6 | 2 | 78.66 | 1767.39 | 22.47 |
| base-6 | input-artfloor | 6 | 3 | 65.20 | 939.67 | 14.41 |

## Anchor followups

| case_id | method | dataset | workers | rechargers | coverage | executable_pass | wall_s |
| --- | --- | --- | --- | --- | --- | --- | --- |
| follow-49 | A | input-warehouse | 2 | 1 | 100.00 | True | 10.42 |
| follow-49 | B1 | input-warehouse | 2 | 1 | 100.00 | True | 14.11 |
| follow-41 | A | input-random-30 | 6 | 2 | 100.00 | True | 95.30 |
| follow-41 | B1 | input-random-30 | 6 | 2 | 100.00 | True | 338.19 |
| follow-43 | A | input-random-30 | 6 | 3 | 100.00 | True | 204.26 |
| follow-43 | B1 | input-random-30 | 6 | 3 | 100.00 | True | 1294.93 |
| follow-57 | A | input-warehouse | 6 | 2 | 100.00 | True | 238.41 |
| follow-57 | B1 | input-warehouse | 6 | 2 | 100.00 | True | 510.67 |
| follow-27 | A | input-random-20 | 6 | 2 | 100.00 | True | 121.05 |
| follow-27 | B1 | input-random-20 | 6 | 2 | 100.00 | True | 277.21 |
| follow-61 | A | input-warehouse | 6 | 3 | 100.00 | False | 265.71 |
| follow-61 | B1 | input-warehouse | 6 | 3 | 100.00 | True | 1389.69 |

Followups are additional layouts, not repeated measurements. Do not pool their timings into base-case speedup claims.

## Data checks and artifacts

Found 0 details/result executable discrepancies. Duplicate case/method results and ambiguous centralized matches cause the analysis to stop. JSON result files are authoritative; combined CSV files may be stale after independent notebook runs. This analysis does not reverify saved trajectories or measure online runtime collision handling.

See matched_base_cases.csv for every base comparison, common_success_cases.csv for comparable efficiency cases, b1_group_diagnostics.csv for group timing and notes, missing_centralized_cases.csv for missing comparisons, and comparison.png for plots.
