"""Merge per-batch CSVs after six notebooks finish; writes one combined CSV."""
import argparse
import csv
from pathlib import Path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--output', type=Path,
                    default=Path('/content/drive/MyDrive/mrp_backup/version_a_vs_b1'))
    ap.add_argument('--expected', type=int, default=64)
    a = ap.parse_args()
    rows, seen = [], set()
    for batch in range(1, 7):
        p = a.output / f'batch{batch:02d}' / 'results.csv'
        if not p.is_file():
            print(f'Missing batch CSV: {p}')
            continue
        with p.open(newline='', encoding='utf-8') as f:
            for row in csv.DictReader(f):
                jid = int(row['job_id'])
                if jid in seen:
                    raise ValueError(f'Duplicate job id {jid} across batches')
                seen.add(jid)
                rows.append(row)
    rows.sort(key=lambda r: int(r['job_id']))
    if not rows:
        raise RuntimeError('No per-batch results found')
    target = a.output / 'results_all_batches.csv'
    with target.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]), extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(f'Saved {len(rows)} completed rows to {target}')
    print(f"Complete jobs: {sum(bool(r.get('status')) and r['status'] != 'ERROR' for r in rows)}; errors: {sum(r.get('status') == 'ERROR' for r in rows)}")
    if len(rows) != a.expected:
        print(f'Expected {a.expected} jobs total; currently have {len(rows)}. Rerun missing batches/jobs.')
    else:
        print(f'All {a.expected} jobs are present.')


if __name__ == '__main__':
    main()
