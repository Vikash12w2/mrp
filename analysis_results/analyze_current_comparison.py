"""Analyze saved outputs only; no planner execution or source modification."""
import csv
import json
import math
from pathlib import Path

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
NEW = ROOT / 'mrp_backup/version_a_vs_b1'
OLD = ROOT / 'mrp_backup/study-20261114-180038'
OUT = ROOT / 'analysis_results/current_comparison'
KEY = ['dataset', 'grid', 'workers', 'rechargers', 'T']


def table(frame):
    def fmt(x):
        if pd.isna(x):
            return '—'
        return f'{x:.2f}' if isinstance(x, float) else str(x)
    return '| ' + ' | '.join(frame.columns) + ' |\n| ' + ' | '.join(['---'] * len(frame.columns)) + ' |\n' + '\n'.join(
        '| ' + ' | '.join(fmt(v) for v in row) + ' |' for row in frame.itertuples(index=False, name=None))


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((ROOT / 'comparison_logs/version_a_vs_b1/manifest.json').read_text())
    cases = {c['case_id']: c for c in manifest['cases']}
    csv_rows = list(csv.DictReader((NEW / 'results_all_batches.csv').open(encoding='utf-8-sig')))
    by_csv = {int(r['job_id']): r for r in csv_rows}
    issues, current, details, sources = [], [], {}, {}
    for path in sorted(NEW.rglob('*.result.json')):
        r = json.loads(path.read_text(encoding='utf-8'))
        jid = r['job_id']
        if jid in details:
            raise ValueError(f'Duplicate result job {jid}')
        dp = path.with_name(path.name.replace('.result.', '.details.'))
        details[jid] = json.loads(dp.read_text(encoding='utf-8')) if dp.exists() else {}
        sources[jid] = path
        cr = by_csv.get(jid)
        if cr is None:
            issues.append(dict(job_id=jid, field='missing CSV row'))
        else:
            for k, v in r.items():
                cv = cr.get(k, '')
                if v is None and cv == '':
                    continue
                if isinstance(v, (float, int)) and not isinstance(v, bool):
                    equal = cv != '' and math.isclose(float(cv), float(v), abs_tol=1e-8, rel_tol=1e-10)
                else:
                    equal = str(v) == cv
                if not equal:
                    issues.append(dict(job_id=jid, field=k, json=str(v), csv=cv))
        case = cases[r['case_id']]
        for k in ['dataset', 'workers', 'rechargers', 'T', 'version']:
            expected = next(j[k] for j in manifest['jobs'] if j['id'] == jid)
            if r[k] != expected:
                raise ValueError(f'Manifest mismatch: {jid} {k}')
        if r['grid'] != f"{case['grid_side']}x{case['grid_side']}":
            raise ValueError('Grid mismatch')
        if r['status'] == 'ERROR' or r.get('error'):
            continue
        horizons = json.loads(r['horizons'])
        joint = math.lcm(*(m - 1 for m in horizons)) if horizons else 1
        r.update(method='Current A' if r['version'] == 'version_a' else 'B1',
                 screen=bool(r['executable']) and joint <= 3000,
                 fallback_count=len(json.loads(r['fallback_groups'])),
                 joint_period=joint, source=str(path.relative_to(ROOT)))
        current.append(r)
    if set(by_csv) != set(details):
        raise ValueError('CSV/JSON IDs differ')
    if len(by_csv) != len(csv_rows):
        raise ValueError('Duplicate combined CSV IDs')
    pd.DataFrame(issues, columns=['job_id', 'field', 'json', 'csv']).to_csv(OUT / 'csv_json_discrepancies.csv', index=False)
    current_df = pd.DataFrame(current)
    historical = []
    for path in sorted(OLD.glob('[0-9][0-9][0-9][0-9].result.json')):
        r = json.loads(path.read_text(encoding='utf-8'))
        if r['variant'] not in {'base', 'cost_off_small', 'T14'} or r['status'] not in {'SAT', 'PARTIAL'}:
            continue
        r['dataset'] = r['input'].replace('\\', '/').split('/')[-1]
        matches = [c for c in cases.values() if (c['dataset'], f"{c['grid_side']}x{c['grid_side']}", c['workers'], c['rechargers'], c['T']) == tuple(r[k] for k in KEY)]
        if not matches:
            continue
        c = matches[0]
        if r['approach'] == 'decentralized' and r['id'] != c['previous_A_job_id']:
            raise ValueError('Historical A selection is not manifest source')
        if r['approach'] == 'decentralized' and r['anchors'] != c['anchors']:
            raise ValueError('Historical A anchors differ')
        fallback = len(r.get('fallback_groups') or [])
        joint = math.lcm(*(m - 1 for m in r['horizons']))
        screen = r['coverage'] == 100 and r['conflicts'] == 0 and joint <= 3000
        if r['approach'] == 'decentralized':
            screen = screen and not fallback and r['deployment'] == 'READY'
        r.update(case_id=c['case_id'], job_id=r['id'], method='Previous capped A' if r['approach'] == 'decentralized' else 'Previous centralized',
                 screen=screen, fallback_count=fallback, joint_period=joint, source=str(path.relative_to(ROOT)))
        historical.append(r)
    data = pd.concat([pd.DataFrame(historical), current_df], ignore_index=True)
    data.to_csv(OUT / 'audited_results.csv', index=False)
    pair_records = []
    comparisons = [('Current A', 'B1'), ('Previous capped A', 'Current A'), ('Previous centralized', 'Current A'), ('Previous centralized', 'B1')]
    summary = []
    for left, right in comparisons:
        pairs = data[data.method == left].merge(data[data.method == right], on=KEY + ['case_id'], suffixes=('_left', '_right'))
        valid = pairs[pairs.screen_left & pairs.screen_right]
        stats = dict(left=left, right=right, matched=len(pairs), screen_left=int(pairs.screen_left.sum()), screen_right=int(pairs.screen_right.sum()),
                     mean_coverage_left=pairs.coverage_left.mean(), mean_coverage_right=pairs.coverage_right.mean(),
                     coverage_gains=int((pairs.coverage_right > pairs.coverage_left + 1e-8).sum()),
                     median_time_right_over_left=(pairs.wall_s_right / pairs.wall_s_left).median(),
                     both_screen=len(valid), mean_hard_right_minus_left=(valid.hard_eff_right - valid.hard_eff_left).mean(),
                     mean_soft_right_minus_left=(valid.soft_eff_right - valid.soft_eff_left).mean(),
                     median_time_right_over_left_screen=(valid.wall_s_right / valid.wall_s_left).median(),
                     right_faster_screen=int((valid.wall_s_right < valid.wall_s_left).sum()),
                     right_hard_better_screen=int((valid.hard_eff_right > valid.hard_eff_left + 1e-8).sum()),
                     right_hard_equal_screen=int(((valid.hard_eff_right-valid.hard_eff_left).abs() <= 1e-8).sum()),
                     mean_cost_right_minus_left=(valid.primitive_cost_right - valid.primitive_cost_left).mean())
        summary.append(stats)
        pairs['left'], pairs['right'] = left, right
        pairs['time_right_over_left'] = pairs.wall_s_right / pairs.wall_s_left
        pairs['hard_right_minus_left_pp'] = (pairs.hard_eff_right - pairs.hard_eff_left).where(pairs.screen_left & pairs.screen_right)
        pair_records.append(pairs)
    pairs_all = pd.concat(pair_records, ignore_index=True)
    pairs_all.to_csv(OUT / 'matched_pairs.csv', index=False)
    pd.DataFrame(summary).to_csv(OUT / 'pair_summary.csv', index=False)
    common_ids = set.intersection(*(set(data[data.method == m].case_id) for m in data.method.unique()))
    common = data[data.case_id.isin(common_ids)]
    common.pivot(index=['case_id']+KEY, columns='method', values=['coverage','screen','hard_eff','soft_eff','wall_s','primitive_cost']).to_csv(OUT / 'four_method_matrix.csv')
    agg = common.groupby('method').agg(runs=('job_id','size'), full_coverage=('coverage',lambda s: int((s == 100).sum())), mean_coverage=('coverage','mean'),
                                      screen_pass=('screen','sum'), median_wall_s=('wall_s','median'), conflict_runs=('conflicts',lambda s: int((s > 0).sum())), fallback_runs=('fallback_count',lambda s: int((s > 0).sum()))).reset_index()
    agg.to_csv(OUT / 'common_case_summary.csv', index=False)
    common[common.grid == '19x19'].groupby('method').agg(
        runs=('job_id','size'), mean_coverage=('coverage','mean'), screen_pass=('screen','sum')
    ).to_csv(OUT / 'common_19x19_summary.csv')
    all_pass_ids = set.intersection(*(set(common[(common.method == m) & common.screen].case_id) for m in common.method.unique()))
    quality = common[common.case_id.isin(all_pass_ids)].groupby('method').agg(
        cases=('job_id','size'), mean_hard_eff=('hard_eff','mean'), mean_soft_eff=('soft_eff','mean'),
        median_wall_s=('wall_s','median'), mean_primitive_cost=('primitive_cost','mean')).reset_index()
    quality.to_csv(OUT / 'all_four_screened_quality.csv', index=False)
    current_df[~current_df.screen][['job_id','case_id']+KEY+['status','coverage','fallback_count','conflicts','deployment','source']].to_csv(OUT / 'current_a_rejected.csv', index=False)
    matrix = data[['case_id']+KEY+['method','job_id','coverage','screen','hard_eff','soft_eff','primitive_cost','wall_s','fallback_count','conflicts']].pivot(
        index=['case_id']+KEY, columns='method')
    matrix.to_csv(OUT / 'all_completed_case_matrix.csv')
    dataset_summary = current_df.groupby(['dataset','method']).agg(runs=('job_id','size'),mean_coverage=('coverage','mean'),screen_pass=('screen','sum'),median_wall_s=('wall_s','median')).reset_index()
    dataset_summary.to_csv(OUT / 'dataset_summary.csv', index=False)
    # B1 retries and grouping time are recorded per attempt, not just final grouping.
    retry_rows = []
    for r in current:
        if r['version'] == 'version_b1':
            d = details[r['job_id']]
            grouping_s = sum(a.get('grouping_seconds', 0) for a in d['attempts'])
            retry_rows.append(dict(job_id=r['job_id'],case_id=r['case_id'],attempts=len(d['attempts']),grouping_s=grouping_s,wall_s=r['wall_s'],groups=json.dumps(d['groups'])))
    pd.DataFrame(retry_rows).to_csv(OUT / 'b1_retries.csv', index=False)
    audit = dict(current_results=len(current),matched_current_pairs=int(summary[0]['matched']),missing_job_ids=sorted(set(j['id'] for j in manifest['jobs'])-set(details)),
                 csv_json_discrepancies=len(issues),historical_comparable_results=len(historical),common_four_method_cases=len(common_ids),max_checked_joint_period=int(data.joint_period.max()))
    (OUT/'audit.json').write_text(json.dumps(audit,indent=2))
    # Export figures for sharing.
    fig, axes = plt.subplots(1, 2, figsize=(11,4.8), constrained_layout=True)
    order=['Previous centralized','Previous capped A','Current A','B1']
    ag=agg.set_index('method').loc[order]
    axes[0].bar(range(4),ag.mean_coverage,color=['#666666','#d9a441','#2673b8','#339966'])
    axes[0].set(ylabel='Mean coverage (%)',ylim=(0,110),title=f'Identical {len(common_ids)} case configurations')
    axes[1].bar(range(4),ag.screen_pass,color=['#666666','#d9a441','#2673b8','#339966'])
    axes[1].set(ylabel='Offline screen passes',ylim=(0,len(common_ids)+2),title='Centralized excludes deployment check')
    for ax in axes:
        ax.set_xticks(range(4),order,rotation=25,ha='right')
    fig.savefig(OUT/'four_method_coverage.png',dpi=180)
    plt.close(fig)
    ab=pairs_all[(pairs_all.left=='Current A') & (pairs_all.right=='B1')]
    valid=ab[ab.screen_left & ab.screen_right]
    fig, axes=plt.subplots(1,2,figsize=(11,4.6),constrained_layout=True)
    for ax,col,label in [(axes[0],'wall_s','Planning time (s)'),(axes[1],'hard_eff','Hard efficiency (%)')]:
        sub=ab if col=='wall_s' else valid
        ax.scatter(sub[col+'_left'],sub[col+'_right'],color='#2673b8')
        lo=min(sub[col+'_left'].min(),sub[col+'_right'].min())*.8
        hi=max(sub[col+'_left'].max(),sub[col+'_right'].max())*1.1
        ax.plot([lo,hi],[lo,hi],'--',color='grey')
        ax.set(xlabel='Current A: '+label,ylabel='B1: '+label)
        if col=='wall_s':
            ax.set_xscale('log'); ax.set_yscale('log')
        ax.set_title('All 31 matched outcomes' if col=='wall_s' else f'Both pass offline screen: {len(valid)} cases')
    fig.savefig(OUT/'a_b1_time_efficiency.png',dpi=180)
    plt.close(fig)
    print(json.dumps(audit,indent=2))
    print(pd.DataFrame(summary).to_string(index=False))
    print(agg.to_string(index=False))
    print('B1 retry counts',pd.Series([r['attempts'] for r in retry_rows]).value_counts().to_dict())
    print('B1 total grouping seconds',sum(r['grouping_s'] for r in retry_rows))
    print('Current A accepted count',int(current_df[current_df.method=='Current A'].screen.sum()))
    # Report is generated separately after interpreting audited tables.


if __name__ == '__main__':
    main()
