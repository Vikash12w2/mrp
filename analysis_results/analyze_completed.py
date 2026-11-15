"""Analyze saved completed benchmarks only; never rerun or alter experiments."""
from pathlib import Path
import json
import math
from collections import Counter

import pandas as pd
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'mrp_backup' / 'study-20261114-180038'
OUT = Path(__file__).resolve().parent
KEY = ['dataset', 'grid', 'workers', 'rechargers', 'T', 'seed', 'tiles']


def table(frame):
    """Markdown without optional tabulate dependency."""
    def val(x):
        if isinstance(x, float):
            return '—' if pd.isna(x) else f'{x:.2f}'
        return str(x)
    return '\n'.join(['| ' + ' | '.join(map(str, frame.columns)) + ' |',
                      '| ' + ' | '.join(['---'] * len(frame.columns)) + ' |'] +
                     ['| ' + ' | '.join(val(v) for v in row) + ' |'
                      for row in frame.itertuples(index=False, name=None)])


def save(frame, name):
    frame.to_csv(OUT / f'{name}.csv', index=False)
    return frame


records, ignored, duplicates = [], [], []
for f in sorted(SOURCE.glob('*.result.json')):
    try:
        r = json.loads(f.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        ignored.append((f.name, str(exc)))
        continue
    if f.stem != f"{r.get('id', -1):04d}.result":
        duplicates.append(f.name)
        continue
    if r.get('status') not in {'SAT', 'PARTIAL', 'UNSAT', 'UNKNOWN', 'SEARCH_EXHAUSTED', 'NO_PLAN'}:
        ignored.append((f.name, r.get('status')))
        continue
    r['dataset'] = r['input'].replace('\\', '/').rstrip('/').split('/')[-1]
    r['source_file'] = f.name
    r['fallback_count'] = len(r.get('fallback_groups', []))
    r['message_count'] = sum(r.get('messages', {}).values()) if 'messages' in r else None
    r['group_sizes'] = [len(g) for g in r.get('groups', {}).values()]
    r['horizon_max'] = max(r.get('horizons', []), default=None)
    r['verify_steps'] = math.lcm(*(h - 1 for h in r.get('horizons', []))) if r.get('horizons') else None
    r['full_coverage'] = r.get('coverage') == 100
    r['zero_conflicts'] = r.get('conflicts') == 0
    r['screen_pass'] = (r['full_coverage'] and r['zero_conflicts'] and
                        (r['approach'] == 'centralized' or
                         (r['fallback_count'] == 0 and r.get('deployment') == 'READY')))
    records.append(r)

d = pd.DataFrame(records).sort_values('id').reset_index(drop=True)
assert d.id.is_unique
csv = pd.read_csv(SOURCE / 'results_all_completed.csv')
assert set(csv.id) == set(d.id), 'Completed CSV/JSON membership differs'
diff = []
for col in ['coverage', 'hard_eff', 'soft_eff', 'primitive_cost', 'conflicts', 'wall_s']:
    merged = csv[['id', col]].merge(d[['id', col]], on='id', suffixes=('_csv', '_json'))
    for _, r in merged.iterrows():
        a, b = r[f'{col}_csv'], r[f'{col}_json']
        if not (pd.isna(a) and pd.isna(b)) and not math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-8):
            diff.append({'id': r.id, 'field': col, 'csv': a, 'json': b})
save(pd.DataFrame(diff, columns=['id', 'field', 'csv', 'json']), 'csv_json_discrepancies')
flat = d.drop(columns=['options', 'groups', 'anchors', 'messages', 'solver_outcomes',
                       'conflict_examples', 'fallback_groups', 'unclaimed', 'horizons', 'group_sizes'], errors='ignore')
save(flat, 'completed_audited')

dec = d[d.approach == 'decentralized']
base = d[(d.variant == 'base') & (d.grid == '19x19')]
bd = base[base.approach == 'decentralized']
bc = base[base.approach == 'centralized']


def pair(left, right, keys=KEY):
    return left.merge(right, on=keys, suffixes=('_d', '_c'), validate='one_to_one')


p = pair(bd, bc)
p['speedup_c_over_d'] = p.wall_s_c / p.wall_s_d
p['hard_delta_pp'] = p.hard_eff_d - p.hard_eff_c
p['soft_delta_pp'] = p.soft_eff_d - p.soft_eff_c
p['cost_ratio_d_over_c'] = p.primitive_cost_d / p.primitive_cost_c
paircols = KEY + ['id_d', 'id_c', 'coverage_d', 'fallback_count_d', 'screen_pass_d',
                  'wall_s_d', 'wall_s_c', 'speedup_c_over_d', 'hard_eff_d', 'hard_eff_c',
                  'hard_delta_pp', 'soft_delta_pp', 'primitive_cost_d', 'primitive_cost_c',
                  'horizon_max_d', 'horizon_max_c']
save(p[paircols], 'baseline_matched')

summary = []
for (approach, variant, grid), g in d.groupby(['approach', 'variant', 'grid']):
    summary.append(dict(approach=approach, variant=variant, grid=grid, runs=len(g),
                        full=int(g.full_coverage.sum()), screen_pass=int(g.screen_pass.sum()),
                        coverage_mean=g.coverage.mean(), conflicts_runs=int((g.conflicts > 0).sum()),
                        fallback_runs=int((g.fallback_count > 0).sum()),
                        blocked=int((g.deployment != 'READY').sum()) if approach == 'decentralized' else None,
                        median_wall_s=g.wall_s.median(), total_wall_h=g.wall_s.sum()/3600,
                        mean_hard_full=g.loc[g.full_coverage, 'hard_eff'].mean(),
                        mean_soft_full=g.loc[g.full_coverage, 'soft_eff'].mean(),
                        median_messages=g.message_count.median()))
s = save(pd.DataFrame(summary), 'variant_summary')

vrows = []
for variant in sorted(set(dec.variant) - {'base', 'cost', 'cost_off_small', 'collisions_off'}):
    v = dec[(dec.variant == variant) & (dec.grid == '19x19')].copy()
    keys = [k for k in KEY if k != 'T'] if variant == 'T14' else KEY
    q = pair(v, bd, keys)
    comparable = q[(q.coverage_d == 100) & (q.coverage_c == 100)]
    vrows.append(dict(variant=variant, pairs=len(q), coverage_gains=int((q.coverage_d > q.coverage_c).sum()),
                      coverage_losses=int((q.coverage_d < q.coverage_c).sum()),
                      mean_coverage_delta_pp=(q.coverage_d-q.coverage_c).mean(),
                      full=int(q.full_coverage_d.sum()), screened=int(q.screen_pass_d.sum()),
                      conflicts_runs=int((q.conflicts_d > 0).sum()),
                      fallback_runs=int((q.fallback_count_d > 0).sum()),
                      median_time_ratio=(q.wall_s_d/q.wall_s_c).median(),
                      quality_pairs=len(comparable),
                      mean_hard_delta_pp=(comparable.hard_eff_d-comparable.hard_eff_c).mean(),
                      accepted_refinements=int(q.refinements_d.sum())))
v = save(pd.DataFrame(vrows), 'variant_changes_vs_base')

cost = pair(d[d.variant == 'cost'], d[d.variant == 'cost_off_small'], KEY + ['approach'])
cost['cost_reduction_pct'] = 100*(cost.primitive_cost_c-cost.primitive_cost_d)/cost.primitive_cost_c
cost['time_ratio_on_off'] = cost.wall_s_d/cost.wall_s_c
cost['hard_delta_pp'] = cost.hard_eff_d-cost.hard_eff_c
cost['same_horizon'] = cost.horizons_d.map(tuple) == cost.horizons_c.map(tuple)
cost_full = cost[cost.full_coverage_d & cost.full_coverage_c]
save(cost[KEY + ['approach','id_d','id_c','coverage_d','coverage_c','primitive_cost_d',
                'primitive_cost_c','cost_reduction_pct','wall_s_d','wall_s_c',
                'time_ratio_on_off','hard_delta_pp','same_horizon']], 'phase2_cost_pairs')

coll = pair(d[d.variant == 'collisions_off'], d[d.variant == 'base'], KEY + ['approach'])
save(coll[KEY + ['approach','id_d','id_c','coverage_d','coverage_c','conflicts_d','conflicts_c',
                'same_group_conflicts_d','cross_group_conflicts_d','wall_s_d','wall_s_c',
                'hard_eff_d','hard_eff_c']], 'collision_ablation_pairs')

reasons = Counter(reason for r in records for reason in r.get('unclaimed', {}).values())
reasonbase = Counter(reason for r in bd.to_dict('records') for reason in r.get('unclaimed', {}).values())
groupcounts = Counter(n for sizes in bd.group_sizes for n in sizes)
safe = p[p.screen_pass_d & p.screen_pass_c]
full = p[p.full_coverage_d & p.full_coverage_c]
workload = save(p.groupby(['workers','rechargers']).agg(
    pairs=('id_d','size'), mean_coverage_d=('coverage_d','mean'),
    full_d=('full_coverage_d','sum'), screen_pass_d=('screen_pass_d','sum'),
    median_c_s=('wall_s_c','median'), median_d_s=('wall_s_d','median'),
    median_paired_speedup=('speedup_c_over_d','median')).reset_index(), 'workload_summary')
cost_summary = cost_full.groupby('approach').agg(
    full_pairs=('id_d','size'), same_horizon=('same_horizon','sum'),
    cost_improved=('cost_reduction_pct', lambda x: int((x > 1e-8).sum())),
    median_time_ratio=('time_ratio_on_off','median'),
    mean_cost_reduction_pct=('cost_reduction_pct','mean'),
    mean_hard_delta_pp=('hard_delta_pp','mean')).reset_index()
save(cost_summary, 'phase2_cost_summary_full_coverage')
comm = save(dec[(dec.grid=='19x19') & dec.variant.isin(['base','refine2','refine5','radius8','util2','no_rebalance','no_swaps'])]
    .groupby('variant').agg(runs=('id','size'),median_events=('message_count','median'),
                            min_events=('message_count','min'),max_events=('message_count','max'))
    .reset_index(), 'communication_summary')
slow = save(d.nlargest(12, 'wall_s')[['id','dataset','grid','workers','rechargers','approach','variant','wall_s']], 'slowest_completed')
audit = dict(completed=len(d), result_files=len(list(SOURCE.glob('*.result.json'))),
             ignored=ignored, duplicate_files=duplicates, csv_discrepancies=diff,
             completed_by_grid=d.grid.value_counts().to_dict(),
             total_recorded_compute_hours=d.wall_s.sum()/3600,
             strict_pairs=safe.id_d.tolist(), base_group_size_counts=dict(groupcounts),
             unclaimed_reasons_all=dict(reasons), unclaimed_reasons_base=dict(reasonbase),
             max_verification_hyperperiod=int(d.verify_steps.max()))
(OUT/'audit.json').write_text(json.dumps(audit, indent=2), encoding='utf-8')

# Publication-style static figures, with separate coverage and quality panels.
plt.rcParams.update({'font.size': 9, 'figure.dpi': 150})
labels = [f"{r.dataset.removeprefix('input-')}\nW{r.workers}/R{r.rechargers}" for r in p.itertuples()]
fig, axes = plt.subplots(2, 1, figsize=(12, 7), constrained_layout=True)
x = list(range(len(p)))
axes[0].bar([a-.2 for a in x], p.wall_s_c, .4, label='Centralized')
axes[0].bar([a+.2 for a in x], p.wall_s_d, .4, label='Decentralized')
axes[0].set(yscale='log', ylabel='Pipeline wall time (s, log scale)', xticks=x, xticklabels=[])
axes[0].legend()
axes[1].bar([a-.2 for a in x], p.coverage_c, .4, label='Centralized')
axes[1].bar([a+.2 for a in x], p.coverage_d, .4, label='Decentralized')
axes[1].set(ylabel='Worker coverage (%)', ylim=(0,110), xticks=x, xticklabels=labels)
fig.suptitle('Matched 19×19 baseline cases — single observations, mixed runtime provenance')
fig.savefig(OUT/'baseline_time_coverage.png'); plt.close(fig)

sv = s[(s.approach == 'decentralized') & (s.grid == '19x19') & ~s.variant.isin(['cost','cost_off_small'])]
fig, axes = plt.subplots(1,2,figsize=(12,5),constrained_layout=True)
axes[0].barh(sv.variant, 100*sv.full/sv.runs, label='Full coverage', alpha=.55)
axes[0].barh(sv.variant, 100*sv.screen_pass/sv.runs, label='Also passes offline screen', height=.4)
axes[0].set(xlabel='Percentage of completed cases', xlim=(0,105)); axes[0].legend(fontsize=8)
axes[1].barh(sv.variant, sv.median_wall_s)
axes[1].set(xlabel='Median pipeline wall time (s)', xscale='log')
fig.suptitle('Decentralized variants (T14 has fewer completed cases)')
fig.savefig(OUT/'variant_coverage_validity.png'); plt.close(fig)

def fmt(x): return f'{x:.2f}'

report = f'''# Completed-run comparative study

## Scope and data audit

This study uses **{len(d)} completed jobs only**, from `mrp_backup/study-20261114-180038`. Interrupted jobs with no completed result are excluded, without rerunning them. There are {len(dec)} decentralized and {len(d)-len(dec)} centralized observations. Grids: {d.grid.value_counts().to_dict()}. All runs use seed 0, so these are single observations per configuration, not repeated statistical trials.

The centralized comparator is this project's Python/Z3 port (`centralized.py` calling `twoshot.solve_isolated`), not a fresh execution of the paper repository's original C++ binary. Collision checks are enabled in the main comparison and disabled in a separate ablation; Phase 2 cost optimization is optional and off for the main comparison. Results therefore compare the two implementations/configurations in this project, rather than directly reproduce the paper's published timings or full optimization configuration. The two-shot method is not a globally optimal joint scheduler, so a decentralized candidate can have higher measured efficiency without contradicting global optimality theory.

The completed CSV and canonical per-job JSON files contain the same job IDs. Numeric CSV/JSON discrepancies: {len(diff)}. `test_job.result.json` duplicates job 1 and is excluded; the numbered `0001.result.json` is retained. Old study folders and stale CSVs are excluded. Completed 25×25 and T14 results are included in their own sections and in the audited output. No inference is made about unfinished jobs.

Summed recorded pipeline time: **{d.wall_s.sum()/3600:.2f} compute hours**. This sum is not calendar duration because notebooks ran concurrently. Hardware, notebook concurrency, dependency versions and CPU allocation are not recorded per job; absolute timings and speed ratios are descriptive, not hardware-controlled estimates.

## Definitions and acceptance screen

- Coverage is the percentage of all requested workers included in successful local plans. A partial solution is a coverage result; it cannot be credited with full-system efficiency.
- Hard and soft efficiencies are the implementation's paper-style worker waiting-time metrics. Higher is better. The decentralized metric aggregates live groups using worker-horizon weights; unequal group periods mean it is not a measured globally synchronized execution rate. Efficiencies are left blank for partial coverage.
- Primitive cost is summed recharger primitive cost over the saved cycle plans. Different cycle lengths can change this total; a lower raw cycle cost alone does not establish a lower long-run cost rate.
- A decentralized **offline screen pass** requires 100% coverage, zero reported conflicts, no fallback groups and deployment `READY`. This is stricter than the stored `SAT` or `candidate_valid` fields. It remains a modeled offline check, not a physical execution guarantee.
- Centralized screening checks full coverage and zero conflicts; deployment is absent from that baseline and therefore cannot be compared as equivalent setup functionality.
- `SAT` in decentralized benchmark output means full coverage only: `benchmark.py:95` derives it from coverage, independently of fallback, collisions and deployment readiness.

## Centralized versus decentralized baseline: original 19×19 maps

There are {len(p)} matched baseline pairs. Centralized coverage is 100% in all {len(bc)}; decentralized full coverage is achieved in {int(bd.full_coverage.sum())}/{len(bd)}. Mean decentralized coverage is {bd.coverage.mean():.2f}%. Every baseline run reports zero modeled conflicts, but {int((bd.fallback_count>0).sum())} decentralized baseline runs use fallback. Only **{int(bd.screen_pass.sum())}/{len(bd)}** decentralized baselines pass the stricter offline screen.

Decentralized planning is faster in {int((p.speedup_c_over_d>1).sum())}/{len(p)} pairs. The median per-case centralized/decentralized time ratio is **{p.speedup_c_over_d.median():.2f}×**, ranging from {p.speedup_c_over_d.min():.2f}× to {p.speedup_c_over_d.max():.2f}×. This includes partial-coverage cases and must not be presented as speedup at equal service.

For the {len(full)} full-coverage pairs, mean decentralized-minus-centralized hard efficiency is {full.hard_delta_pp.mean():+.2f} percentage points and soft efficiency is {full.soft_delta_pp.mean():+.2f} points. Some of these plans use fallback. Restricting further to the **{len(safe)} pairs that pass both approaches' offline screens**, median time ratio is {safe.speedup_c_over_d.median():.2f}× and mean hard/soft differences are {safe.hard_delta_pp.mean():+.2f}/{safe.soft_delta_pp.mean():+.2f} percentage points. This small subset is the appropriate basis for a like-service quality claim.

{table(p[['dataset','workers','rechargers','coverage_d','fallback_count_d','screen_pass_d','wall_s_c','wall_s_d','speedup_c_over_d','hard_delta_pp']])}

![Baseline timings and coverage](baseline_time_coverage.png)

Workload-level summaries (four maps per row):

{table(workload)}

Increasing recharger count from two to three changes the search space and the assignments. These observations do not support a universal rule that an extra recharger makes SMT faster; the map and chosen schedule matter. The extreme speed ratios in the six-worker cases must be read with their coverage and screen results.

## Variant comparison

The following deltas use matched 19×19 decentralized baseline cases. T14 matches on map, worker/recharger counts and seed while intentionally changing T. Quality differences include only pairs with full coverage on both sides; offline validity must still be checked separately. Time ratios greater than 1 mean the variant took longer.

{table(v)}

![Coverage and offline validity by variant](variant_coverage_validity.png)

The best observed conservative change is **util2** (cap_util=2.0): full coverage increases from 6/12 to 7/12 and screen passes from 4/12 to 5/12, with no reported conflicts. It restores the missing sixth worker in artfloor W6/R2 (job 151 versus base job 25). Its matched median time ratio is 1.08. This supports testing util2 as the next candidate default, but the evidence is only one improved scenario and one seed; it does not establish an optimal utilization threshold or support extrapolating to 2.5 or removing the cap.

**radius8** also gains one full-coverage case, but screen passes drop from 4/12 to 3/12; seven runs use fallback, and jobs 109 and 111 have 28 and 88 reported conflicts despite collision constraints being enabled. Job 109 also has deployment BLOCKED. These collisions arise in fallback configurations; this shows that local collision constraints alone do not guarantee safe coordinated output after alignment is abandoned. radius8 is therefore not the best default on these observations.

**cap2** reduces coverage in two partial cases without improving the screen count. **cap6** yields no coverage or full-coverage efficiency improvement, consistent with region eligibility and utilization continuing to constrain assignments. **no_rebalance** and **no_swaps** produce no change in coverage or full-coverage efficiency here; that does not prove those mechanisms unnecessary on other instances.

All saved refinement counters are zero, including refine2 and refine5: these experiments show no accepted SMT refinement. They cannot establish the benefit of successful refinement. Their median time ratios are 1.08 and 1.10; small timing changes may also reflect machine variation. Additional attempts/messages can occur without a committed improvement.

Coverage failures in the 19×19 baseline are attributed to these unclaimed-worker observations: {dict(reasonbase)}. Across all completed decentralized configurations the counts are {dict(reasons)}; these repeat workers across experiments and are not unique physical workers. These diagnostics show that locality and capacity heuristics, rather than a failed local SMT solve alone, can limit service.

Baseline group sizes across all recorded rechargers: {dict(sorted(groupcounts.items()))}. There are no empty groups in this baseline subset. This describes observed assignments, not the configured worker-count cap. Raising cap_n alone does not relax cap_util or whole-loop region eligibility.

Communication counters for selected variants:

{table(comm)}

## Collision constraints ablation

{table(coll.groupby('approach').agg(pairs=('id_d','size'),off_conflict_runs=('conflicts_d',lambda x:int((x>0).sum())),on_conflict_runs=('conflicts_c',lambda x:int((x>0).sum())),off_total_conflicts=('conflicts_d','sum'),on_total_conflicts=('conflicts_c','sum')).reset_index())}

Collision-disabled plans are useful ablations but must not be ranked as executable solutions when conflicts are detected. Conflict totals count robot-pair/time overlaps rather than unique physical crashes, and different hyperperiod lengths affect the counts. The verifier ignores worker-worker pairs, uses modeled worker edges (default abstract waypoints), and caps verification at 3000 steps; the maximum computed hyperperiod in this dataset is {int(d.verify_steps.max())}. Absence of reported conflicts therefore refers to this model. For partial coverage, unserved workers are not included as live planned workers in `runtime.verify`, so their runtime interactions are not established either.

## Phase 2 primitive-cost objective

Matched cost-on/off experiments use only one recharger and one or two workers. Cost optimization is applied after the chosen Phase 1 schedule and first feasible Phase 2 horizon; it is not a global joint optimization over all Phase 1 schedules. The complete pair table is `phase2_cost_pairs.csv`.

There are {len(cost)} on/off pairs in total. One decentralized pair serves only one of the two requested workers (jobs 217/219); it remains in the detailed output but is excluded from the full-service objective summary below.

{table(cost_summary)}

All {len(cost)} on/off pairs preserve their horizon lists, and all full-coverage pairs preserve hard efficiency. On the seven full-coverage decentralized pairs, the mean cost reduction is {cost_full[cost_full.approach=='decentralized'].cost_reduction_pct.mean():.2f}% (an unweighted mean of percentage reductions). Cost-on timing can nevertheless be around 18–19 times cost-off for some one-worker cases (jobs 243 and 259). This supports retaining the objective as an explicit optional variant. A 100% reduction means zero movement primitive cost, possible for a stationary recharger; it does not mean zero total robot energy use.

Cost reductions should be interpreted on full-coverage matched pairs only; partial pairs can have costs for only the served subset. Independent solver invocations may choose different equally optimal Phase 1 schedules, so on/off route differences are not necessarily caused only by the added Phase 2 objective.

## Completed T14 and 25×25 cases

T14 is a sensitivity experiment, with {len(d[d.variant=='T14'])} completed observations. It changes no worker coverage on matched cases, raises hard efficiency by an average 27.44 percentage points on the six full-coverage pairs, and takes a median 4.67 times baseline time. It also has one conflicting result (job 204, 85 reported overlaps, with fallback). A longer initial horizon can improve candidate efficiency but is not a general safety or coverage fix. Its matched subset differs from other variants, so do not rank unmatched global means as if all cases were identical.

{table(d[d.grid=='25x25'][['id','dataset','workers','rechargers','approach','variant','coverage','conflicts','fallback_count','wall_s']])}

Only the completed 25×25 cases above are analyzed. They pad existing geometry rather than introduce a new distribution of worker loops. `benchmark.py` selects anchors before padding, so these runs do not test redistributed deployments across the larger map. They are limited sensitivity cases, not strong evidence of general grid scaling.

## Runtime and methodological limits

The benchmark measures each implementation's pipeline, not pure SMT time. Decentralized time includes grouping, refinement and deployment simulation but excludes anchor selection and input loading (timer starts later); centralized deployment is not modeled. `run_decentralized` is invoked without parallel=True, so the results do not demonstrate actual distributed wall-time scaling. Message counts are simulator event counters, including ACKs and worker broadcasts, not bytes, latency or radio bandwidth.

The repository and Version A specification retain runtime limitations: missed recharge appointments after delays are not repaired with complete worker battery/schedule resynchronization. Model checks and deployment `READY` do not establish safe perpetual execution under motion delays, packet loss, physical localization errors or recharger failures. This suite contains no dedicated runtime fault-injection/recovery comparisons. It also contains no evidence for unseen variants such as one-shot/auto group mode.

## Conclusions and recommendation

The evidence supports a useful computational trade-off: local decomposition often reduces planning time considerably, while region eligibility, grouping limits and period alignment reduce full-service coordinated success. The centralized baseline reliably serves all tested baseline workers but some six-worker solves require hours. The decentralized implementation should be reported with coverage and fallback rates alongside time and efficiency; it cannot yet be described as uniformly equivalent to the centralized baseline.

The most defensible next configuration to test is the base setup with **cap_util=2.0**, **collision constraints enabled**, **K_refine=0**, and Phase 2 cost optimization kept optional. This recommendation follows the highest screen-pass count among the equally matched grouping variants, not just the fastest time or highest conditional efficiency. It is a recommendation for further validation, not an applied code change. Region eligibility and fallback still leave unsolved service/cooperation limitations even with util2.

Before publication-quality timing claims, collect hardware-controlled repeats with pinned software versions and distinguish preprocessing, grouping, phase-one, phase-two and setup time. Before runtime safety claims, add battery-aware delayed-execution validation. These are follow-up experiments; all conclusions above use only already completed outputs.

## Reproducibility and files

Run `python analysis_results/analyze_completed.py` from the repository directory. This reads saved results and writes only analysis artifacts. `completed_audited.csv` is the normalized completed table; `baseline_matched.csv`, `variant_summary.csv`, `variant_changes_vs_base.csv`, `phase2_cost_pairs.csv`, `phase2_cost_summary_full_coverage.csv`, `collision_ablation_pairs.csv`, `workload_summary.csv`, `communication_summary.csv` and `slowest_completed.csv` hold the calculations. `audit.json` records exclusions and counts. Source references: `benchmark.py` (matrix/status/timing), `decentralized.py` (coverage/efficiency), `runtime.py` (verification), `twoshot.py` (phase objectives), `bus.py` (message counting), `VERSION_A_SPEC.md` (assumptions), and the locally supplied Kundu/Saha paper `Kundu2021iros.pdf`.
'''
(OUT/'FINAL_COMPARATIVE_STUDY.md').write_text(report, encoding='utf-8')
print(json.dumps(audit, indent=2))
print('\nBASE PAIRS\n'+p[['id_d','dataset','workers','rechargers','coverage_d','screen_pass_d','speedup_c_over_d','hard_delta_pp']].to_string(index=False))
print('\nVARIANTS\n'+v.to_string(index=False))
print('\nCOST\n'+cost[['approach','id_d','coverage_d','cost_reduction_pct','time_ratio_on_off','same_horizon']].to_string(index=False))
print('\nReport:', OUT/'FINAL_COMPARATIVE_STUDY.md')
