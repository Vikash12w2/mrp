from collections import defaultdict
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OLD = ROOT / 'mrp_backup' / 'study-20261114-180038'
OUT = ROOT / 'comparison_logs' / 'version_a_vs_b1'
DATASETS = {'input-artfloor', 'input-warehouse', 'input-random-20', 'input-random-30'}


def main():
    by_case = {}
    for path in OLD.glob('[0-9][0-9][0-9][0-9].result.json'):
        row = json.loads(path.read_text(encoding='utf-8'))
        if row.get('approach') != 'decentralized':
            continue
        dataset = row['input'].replace('\\', '/').rstrip('/').split('/')[-1]
        if dataset not in DATASETS:
            continue
        side = int(row['grid'].split('x')[0])
        case = dict(dataset=dataset, workers=int(row['workers']),
                    rechargers=int(row['rechargers']), T=int(row['T']),
                    grid_side=side, tiles=int(row.get('tiles', 1)))
        key = (case['dataset'], case['workers'], case['rechargers'], case['T'],
               case['grid_side'], case['tiles'])
        # Prefer the old base configuration for stable physical anchors.
        priority = 0 if row['variant'] == 'base' else 1
        if key not in by_case or priority < by_case[key][0]:
            anchors = row.get('anchors')
            if anchors:
                anchors = {str(k): list(v) for k, v in anchors.items()}
            by_case[key] = (priority, case, anchors, row['id'], row['variant'])

    cases = []
    for case_id, (key, (_prio, case, anchors, old_id, old_variant)) in enumerate(sorted(by_case.items()), 1):
        if not anchors:
            raise RuntimeError(f'No saved Version A anchors for {key}')
        cases.append(dict(case_id=case_id, **case, anchors=anchors,
                          previous_A_job_id=old_id, previous_A_variant=old_variant,
                          input=f"examples/{case['dataset']}"))

    jobs = []
    job_id = 1
    for case in cases:
        for version in ('version_a', 'version_b1'):
            jobs.append(dict(id=job_id, **case, version=version))
            job_id += 1

    # Greedy balance by estimated historical A runtime; pair each case's versions
    # in the same batch so a notebook produces directly matched outcomes.
    history = {}
    for p in OLD.glob('[0-9][0-9][0-9][0-9].result.json'):
        r = json.loads(p.read_text(encoding='utf-8'))
        if r.get('approach') == 'decentralized':
            ds = r['input'].replace('\\', '/').rstrip('/').split('/')[-1]
            k = (ds, int(r['workers']), int(r['rechargers']), int(r['T']),
                 int(r['grid'].split('x')[0]), int(r.get('tiles', 1)))
            history[k] = max(history.get(k, 0), float(r.get('wall_s') or 0))
    batches = [[] for _ in range(6)]
    loads = [0.0] * 6
    for case in sorted(cases, key=lambda c: history.get((c['dataset'], c['workers'], c['rechargers'], c['T'], c['grid_side'], c['tiles']), 0), reverse=True):
        i = min(range(6), key=lambda j: loads[j])
        pair = [j for j in jobs if j['case_id'] == case['case_id']]
        batches[i].extend(pair)
        loads[i] += history.get((case['dataset'], case['workers'], case['rechargers'], case['T'], case['grid_side'], case['tiles']), 60) * 2

    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        source_study=str(OLD.relative_to(ROOT)), selection='unique completed decentralized input configurations',
        cases=cases, jobs=jobs, batch_count=6, estimated_batch_load_seconds=loads), indent=2), encoding='utf-8')
    for i, batch in enumerate(batches, 1):
        batch_dir = OUT / f'batch{i:02d}'
        batch_dir.mkdir(exist_ok=True)
        (batch_dir / 'jobs.json').write_text(json.dumps(batch, indent=2), encoding='utf-8')
    print(f'Cases: {len(cases)}; matched jobs: {len(jobs)}; batches: 6')
    print('Per-batch job counts:', [len(b) for b in batches])
    print('Estimated load (hours):', [round(x / 3600, 2) for x in loads])
    for c in cases:
        print(f"case {c['case_id']:02d}: {c['dataset']} {c['grid_side']}x{c['grid_side']} "
              f"W={c['workers']} R={c['rechargers']} T={c['T']} (old job {c['previous_A_job_id']})")


if __name__ == '__main__':
    main()
