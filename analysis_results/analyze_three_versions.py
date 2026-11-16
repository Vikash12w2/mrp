"""Compare saved runs; never launch planners. Run from any directory."""
import argparse
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
KEY = ['dataset', 'grid', 'workers', 'rechargers', 'T']


def unpack(value):
    return json.loads(value) if isinstance(value, str) else (value or [])


def table(df):
    def cell(v):
        if isinstance(v, float):
            return f'{v:.2f}' if math.isfinite(v) else '—'
        return str(v)
    return '| ' + ' | '.join(df.columns) + ' |\n| ' + ' | '.join(['---'] * len(df.columns)) + ' |\n' + '\n'.join('| ' + ' | '.join(map(cell, row)) + ' |' for row in df.itertuples(index=False, name=None))


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--runs', type=Path, default=ROOT / 'mrp_backup/version_a_b1_parallel')
    ap.add_argument('--central', type=Path, default=ROOT / 'mrp_backup/study-20261114-180038')
    ap.add_argument('--output', type=Path, default=ROOT / 'analysis_results/three_versions')
    args = ap.parse_args()
    out = args.output
    out.mkdir(parents=True, exist_ok=True)
    rows, issues, detail_rows = [], [], []
    for path in sorted(args.runs.rglob('*.result.json')):
        r = json.loads(path.read_text(encoding='utf-8-sig'))
        if r.get('version') not in ('version_a', 'version_b1'):
            continue
        r['method'] = 'A' if r['version'] == 'version_a' else 'B1'
        r['source'] = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        r['horizons'] = unpack(r.get('horizons'))
        r['fallback_count'] = len(unpack(r.get('fallback_groups')))
        r['joint_period'] = math.lcm(*(m - 1 for m in r['horizons'])) if r['horizons'] else 0
        r['plan_pass'] = (r.get('coverage') == 100 and r.get('conflicts') == 0 and
                          not r['fallback_count'] and 0 < r['joint_period'] <= 3000 and
                          r.get('status') == 'SAT' and not r.get('error'))
        r['executable_pass'] = r['plan_pass'] and r.get('executable') is True and r.get('deployment') == 'READY'
        rows.append(r)
        dp = path.with_name(path.name.replace('.result.', '.details.'))
        if dp.exists():
            d = json.loads(dp.read_text(encoding='utf-8-sig')) or {}
            if r['method'] == 'B1':
                if d.get('executable') != r.get('executable'):
                    issues.append({'source': str(path), 'issue': 'details/result executable mismatch'})
                for rid, p in (d.get('plans') or {}).items():
                    detail_rows.append(dict(case_id=r['case_id'], job_id=r['job_id'], group=rid,
                                            workers=p.get('workers'), box=p.get('planning_box'),
                                            seconds=p.get('seconds'), T=p.get('T'), M=p.get('M'),
                                            status=p.get('status'), notes=p.get('notes')))
    current = pd.DataFrame(rows)
    if current.empty:
        raise ValueError('No A/B1 result JSON files found')
    if current.duplicated(['case_id', 'method']).any():
        raise ValueError('Duplicate case/method results; select one run suite explicitly')
    manifest_path = ROOT / 'comparison_logs' / args.runs.name / 'manifest.json'
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        expected = {j['id']: j for j in manifest['jobs']}
        if set(current.job_id) != set(expected):
            raise ValueError('Saved job IDs differ from suite manifest')
        for r in rows:
            for field in ['case_id', 'version', 'dataset', 'workers', 'rechargers', 'T']:
                if r[field] != expected[r['job_id']][field]:
                    raise ValueError(f'Manifest mismatch: {r["job_id"]} {field}')
    base = current[current.case_id.astype(str).str.startswith('base-')].copy()
    target_keys = set(map(tuple, base[KEY].values))
    central = []
    for path in sorted(args.central.glob('*.result.json')):
        r = json.loads(path.read_text(encoding='utf-8-sig'))
        if r.get('approach') != 'centralized' or r.get('variant') not in ('base', 'cost_off_small'):
            continue
        r['dataset'] = r['input'].replace('\\', '/').rstrip('/').split('/')[-1]
        if tuple(r[k] for k in KEY) not in target_keys:
            continue
        if not r.get('collisions_enabled', True) or r.get('options', {}).get('phase2_optimize_cost', False):
            continue
        r.update(method='Centralized', job_id=r['id'], source=str(path.relative_to(ROOT)), executable_pass=None)
        r['plan_pass'] = r['status'] == 'SAT' and r.get('coverage') == 100 and r.get('conflicts') == 0
        # Reporting convention: accept a valid returned centralized result.
        r['executable_pass'] = r['plan_pass']
        central.append(r)
    cdf = pd.DataFrame(central)
    if not cdf.empty and cdf.duplicated(KEY).any():
        raise ValueError('Ambiguous centralized matches')
    all_rows = pd.concat([current, cdf], ignore_index=True)
    all_rows.to_csv(out / 'all_runs.csv', index=False)
    pd.DataFrame(detail_rows).to_csv(out / 'b1_group_diagnostics.csv', index=False)
    pd.DataFrame(issues, columns=['source', 'issue']).to_csv(out / 'audit_issues.csv', index=False)
    wide = base.pivot(index=['case_id'] + KEY, columns='method', values=['wall_s', 'coverage', 'plan_pass', 'executable_pass', 'hard_eff', 'soft_eff', 'primitive_cost', 'conflicts', 'fallback_count', 'attempts'])
    wide.columns = [f'{method}_{metric}' for metric, method in wide.columns]
    wide = wide.reset_index().merge(cdf[KEY + ['wall_s', 'plan_pass', 'hard_eff', 'soft_eff', 'primitive_cost']].rename(columns={k: f'Centralized_{k}' for k in ['wall_s', 'plan_pass', 'hard_eff', 'soft_eff', 'primitive_cost']}), on=KEY, how='left', validate='many_to_one')
    for column in wide.columns:
        if any(column.endswith('_' + metric) for metric in ['wall_s', 'coverage', 'hard_eff', 'soft_eff', 'primitive_cost']):
            wide[column] = pd.to_numeric(wide[column], errors='raise')
    wide['A_speedup_vs_central'] = wide.Centralized_wall_s / wide.A_wall_s
    wide['B1_speedup_vs_central'] = wide.Centralized_wall_s / wide.B1_wall_s
    wide['B1_runtime_over_A'] = wide.B1_wall_s / wide.A_wall_s
    wide.to_csv(out / 'matched_base_cases.csv', index=False)
    missing = wide[wide.Centralized_wall_s.isna()][['case_id'] + KEY]
    missing.to_csv(out / 'missing_centralized_cases.csv', index=False)
    summaries = []
    for scope, frame in [('All A/B1 (including anchor followups)', current), ('Base suite', pd.concat([base, cdf], ignore_index=True))]:
        for method, g in frame.groupby('method'):
            valid = g[g.plan_pass == True]
            summaries.append(dict(scope=scope, method=method, runs=len(g), plan_pass=int(g.plan_pass.sum()),
                                  executable_pass=int(g.executable_pass.fillna(False).sum()),
                                  mean_coverage=g.coverage.mean(), median_wall_s=g.wall_s.median(), total_wall_s=g.wall_s.sum(),
                                  mean_hard_eff_pass=valid.hard_eff.mean(), mean_soft_eff_pass=valid.soft_eff.mean()))
    summary = pd.DataFrame(summaries)
    summary.to_csv(out / 'summary.csv', index=False)
    breakdown = base.groupby(['method', 'dataset'], as_index=False).agg(runs=('job_id', 'size'), executable=('executable_pass', 'sum'), mean_coverage=('coverage', 'mean'), median_wall_s=('wall_s', 'median'))
    breakdown.to_csv(out / 'by_dataset.csv', index=False)
    base.groupby(['method', 'workers', 'rechargers'], as_index=False).agg(runs=('job_id', 'size'), executable=('executable_pass', 'sum'), median_wall_s=('wall_s', 'median')).to_csv(out / 'by_workload.csv', index=False)
    common = wide[(wide.A_plan_pass == True) & (wide.B1_plan_pass == True) & (wide.Centralized_plan_pass == True)].copy()
    common.to_csv(out / 'common_success_cases.csv', index=False)
    conditional = []
    for method in ['Centralized', 'A', 'B1']:
        conditional.append(dict(method=method, common_cases=len(common), median_wall_s=common[f'{method}_wall_s'].median(), mean_hard_eff=common[f'{method}_hard_eff'].mean(), mean_soft_eff=common[f'{method}_soft_eff'].mean(), mean_primitive_cost=common[f'{method}_primitive_cost'].mean()))
    cond = pd.DataFrame(conditional)
    cond.to_csv(out / 'common_success_summary.csv', index=False)
    matched = wide.dropna(subset=['Centralized_wall_s'])
    wins = pd.DataFrame([dict(method=m, matched=len(matched), faster_than_central=int((matched[f'{m}_speedup_vs_central'] > 1).sum()), median_speedup=matched[f'{m}_speedup_vs_central'].median(), geometric_mean_speedup=math.exp(matched[f'{m}_speedup_vs_central'].map(math.log).mean())) for m in ['A', 'B1']])
    wins.to_csv(out / 'paired_speedups.csv', index=False)
    follow = current[~current.case_id.astype(str).str.startswith('base-')]
    follow.to_csv(out / 'anchor_followups.csv', index=False)
    fig, axes = plt.subplots(1, 3, figsize=(15, 4))
    colors = {'Centralized': '#555555', 'A': '#2878b5', 'B1': '#de8f05'}
    for m in colors:
        g = pd.concat([base, cdf], ignore_index=True).query('method == @m')
        axes[0].plot(range(1, len(g)+1), sorted(g.wall_s), label=m, color=colors[m])
    axes[0].set(yscale='log', xlabel='Rank within method', ylabel='Wall time (s)', title='Base cases: runtime distribution')
    axes[0].legend()
    for m in ['A', 'B1']:
        axes[1].scatter(matched.Centralized_wall_s, matched[f'{m}_wall_s'], label=m, alpha=.7, color=colors[m])
    vals = matched[['Centralized_wall_s', 'A_wall_s', 'B1_wall_s']]
    lo, hi = vals.min().min(), vals.max().max()
    axes[1].plot([lo,hi], [lo,hi], '--', color='gray')
    axes[1].set(xscale='log', yscale='log', xlabel='Centralized wall time (s)', ylabel='A/B1 wall time (s)', title='Matched scenarios')
    axes[1].legend()
    for i,m in enumerate(colors):
        axes[2].bar(i, cond.loc[cond.method == m, 'mean_hard_eff'].iloc[0], color=colors[m])
    axes[2].set(xticks=range(3), xticklabels=list(colors), ylabel='Hard efficiency (%)', title=f'Common planning successes (n={len(common)})')
    fig.tight_layout()
    fig.savefig(out / 'comparison.png', dpi=180)
    plt.close(fig)
    report = f'''# Centralized, Version A and B1: saved-run comparison

Inputs: `{args.runs}` and `{args.central}`. Generated from saved JSON results; no solver reruns.

## Scope and interpretation

The A/B1 suite contains {len(current)} runs: {len(base)} base runs plus {len(follow)} anchor followup runs. Match scenarios by dataset, grid, workers, rechargers and requested T; followups remain separate because their anchors differ. Centralized matches exist for {len(matched)}/{len(wide)} base scenarios. All individual source paths are recorded in all_runs.csv. Missing cases are listed explicitly and are not counted as failures.

Centralized means this project's Python/Z3 centralized two-shot comparator, not a fresh execution of the paper's original C++ implementation. For this comparison, a centralized result with SAT, full coverage and no reported conflicts counts as a pass. The executable_pass analysis column uses that reporting convention for centralized results; it does not alter the saved solver outputs. A/B1 use their recorded executable acceptance checks, including no unconstrained fallback and a verified-period limit of 3000 steps.

The suite uses collision constraints and disables optional phase-2 cost optimization. A uses local grouping and regional planning; B1 uses global grouping and group-specific regions. These are complete method comparisons, not an isolated test of grouping. Historical centralized timings and newer parallel A/B1 runs are not controlled hardware repetitions. Report speedups as observed ratios; hardware, notebook load and parallelism may contribute. No confidence intervals or statistical significance claims are supported by one run per scenario. Two-shot planning does not guarantee global joint optimality.

## Overall results

{table(summary)}

Efficiency above is conditional on each method's planning successes, which may be different cases. Partial coverage must not be interpreted as a complete-mission efficiency result.

## Matched runtime ratios

{table(wins)}

Speedup is centralized wall time divided by the method's wall time. These ratios include failed/partial A runs, so a fast runtime does not imply successful completion.

## Same scenarios, all three planning passes

{table(cond)}

This comparison uses only the {len(common)} common planning successes. Primitive cost is the recorded per-hypercycle sum; differing cycle lengths limit its interpretation as an energy-rate comparison.

## B1 versus A

B1 is faster on {int((wide.B1_wall_s < wide.A_wall_s).sum())}/{len(wide)} base scenarios. The median paired B1/A runtime ratio is {wide.B1_runtime_over_A.median():.2f}. Full executable successes: A {int(base[base.method == 'A'].executable_pass.sum())}/{len(base[base.method == 'A'])}; B1 {int(base[base.method == 'B1'].executable_pass.sum())}/{len(base[base.method == 'B1'])}. Interpret the runtime difference alongside the completion difference.

On the {len(common)} common successful scenarios, the median paired observed speedup versus centralized is {common.A_speedup_vs_central.median():.2f} for A and {common.B1_speedup_vs_central.median():.2f} for B1. B1's mean hard-efficiency advantage over A on these same cases is {cond.loc[cond.method == 'B1', 'mean_hard_eff'].iloc[0] - cond.loc[cond.method == 'A', 'mean_hard_eff'].iloc[0]:.2f} percentage points. This avoids comparing efficiency on different success subsets.

B1's main advantage in this suite is complete executable coverage; its cost is a long runtime tail. A is attractive for quick candidate plans, but its short runtimes include incomplete or fallback outcomes. The results support B1 for the completed offline planning task, while leaving predictability of planning time as a performance issue. The saved aggregate runtimes alone do not identify which solver constraints caused individual slow cases.

### Dataset breakdown (base suite)

{table(breakdown)}

Slowest base B1 runs:

{table(wide.nlargest(5, 'B1_wall_s')[['case_id', 'dataset', 'workers', 'rechargers', 'A_wall_s', 'B1_wall_s', 'B1_runtime_over_A']])}

## Anchor followups

{table(follow[['case_id', 'method', 'dataset', 'workers', 'rechargers', 'coverage', 'executable_pass', 'wall_s']])}

Followups are additional layouts, not repeated measurements. Do not pool their timings into base-case speedup claims.

## Data checks and artifacts

Found {len(issues)} details/result executable discrepancies. Duplicate case/method results and ambiguous centralized matches cause the analysis to stop. JSON result files are authoritative; combined CSV files may be stale after independent notebook runs. This analysis does not reverify saved trajectories or measure online runtime collision handling.

See matched_base_cases.csv for every base comparison, common_success_cases.csv for comparable efficiency cases, b1_group_diagnostics.csv for group timing and notes, missing_centralized_cases.csv for missing comparisons, and comparison.png for plots.
'''
    (out / 'REPORT.md').write_text(report, encoding='utf-8')
    print(table(summary))
    print('\n' + table(wins))
    print(f'\nReport: {out / "REPORT.md"}')


if __name__ == '__main__':
    main()
