"""Prepare radius-eight reruns of completed current A cases below 100% coverage."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'mrp_backup/version_a_vs_b1'
OUT = ROOT / 'comparison_logs/version_a_radius8'


def main():
    manifest = json.loads((ROOT / 'comparison_logs/version_a_vs_b1/manifest.json').read_text())
    originals = {j['id']: j for j in manifest['jobs']}
    rows = {}
    for path in SOURCE.rglob('*.result.json'):
        row = json.loads(path.read_text())
        if row.get('version') != 'version_a' or row.get('status') == 'ERROR' or row.get('error'):
            continue
        if row.get('coverage') is None or float(row['coverage']) >= 100:
            continue
        if row['job_id'] in rows:
            raise ValueError(f"Duplicate job {row['job_id']}")
        rows[row['job_id']] = row
    if not rows:
        raise ValueError('No incomplete-coverage current Version A results found')
    jobs = []
    for jid, row in sorted(rows.items()):
        job = dict(originals[jid])
        job.update(planning_radius=8, baseline_job_id=jid,
                   baseline_coverage=row['coverage'], baseline_wall_s=row['wall_s'])
        jobs.append(job)
    batches = [[] for _ in range(6)]
    loads = [0.] * 6
    for job in sorted(jobs, key=lambda j: j['baseline_wall_s'], reverse=True):
        index = min(range(6), key=lambda b: loads[b])
        batches[index].append(job)
        loads[index] += job['baseline_wall_s']
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / 'manifest.json').write_text(json.dumps(dict(
        selection='completed current Version A outputs with coverage below 100%',
        source='mrp_backup/version_a_vs_b1', planning_radius=8,
        baseline_radius=6, jobs=jobs, batch_count=6,
        baseline_batch_load_seconds=loads), indent=2))
    for index, batch in enumerate(batches, 1):
        directory = OUT / f'batch{index:02d}'
        directory.mkdir(exist_ok=True)
        (directory / 'jobs.json').write_text(json.dumps(batch, indent=2))
    print(f'{len(jobs)} Version A radius-8 reruns')
    for i, batch in enumerate(batches, 1):
        print(f"Batch {i}: {len(batch)} jobs; IDs {[j['id'] for j in batch]}")


if __name__ == '__main__':
    main()
