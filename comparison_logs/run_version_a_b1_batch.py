"""Run one matched Version A / B1 batch in Colab from saved job JSON."""
import argparse
import csv
from dataclasses import replace
import json
import sys
import threading
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

PARENT = Path('/content/drive/MyDrive')
REPO = PARENT / 'mrp'
INPUT_ROOT = REPO / 'examples'
OUTPUT_ROOT = PARENT / 'mrp_backup' / 'version_a_vs_b1'
sys.path.insert(0, str(PARENT))

from mrp.benchmark import tiled
from mrp.model import load_instance
from mrp.params import Params
from mrp.decentralized import run_decentralized
from mrp.version_b1.pipeline import run_b1

FIELDS = ['job_id','case_id','version','dataset','grid','workers','rechargers','T',
          'status','executable','coverage','hard_eff','soft_eff','primitive_cost',
          'conflicts','same_group_conflicts','cross_group_conflicts','fallback_groups',
          'deployment','horizons','grouping_status','retry_status','attempts','wall_s','error',
          'planning_radius','baseline_job_id','baseline_coverage']


def run_one(job, examples):
    source = examples / job['dataset']
    if not (source / 'workspace.txt').is_file():
        raise FileNotFoundError(f'Missing dataset workspace: {source / "workspace.txt"}')
    available = len(list(source.glob('rob*_traj.txt')))
    inst = load_instance(str(source), min(available, job['workers']), job['rechargers'], job['T'])
    inst = tiled(inst, job.get('tiles', 1), job['workers'])
    side = int(job['grid_side'])
    if side < max(inst.xmax + 1, inst.ymax + 1):
        raise ValueError(f'Grid {side} cannot contain loaded instance {inst.xmax+1}x{inst.ymax+1}')
    inst = replace(inst, xmax=side - 1, ymax=side - 1)
    anchors = {int(k): tuple(v) for k,v in job['anchors'].items()}
    if len(anchors) != job['rechargers']:
        raise ValueError('Saved anchor count does not match recharger count')

    start = time.monotonic()
    if job['version'] == 'version_a':
        params = Params(R_s=int(job.get('planning_radius', 6)), seed=0,
                        collisions=True, phase2_optimize_cost=False)
        result = run_decentralized(inst, anchors, params,
                                   parallel=bool(job.get('parallel', True)))
        m = result.metrics()
        full = m['coverage'] == 100
        fallback = m['fallback_groups']
        conflicts = len(result.conflicts)
        executable = bool(full and not conflicts and not fallback and m['deployment_ready'])
        statuses = [g.res.status for g in result.plans.values() if g.res]
        return dict(status='SAT' if full else 'PARTIAL', executable=executable,
                    coverage=m['coverage'], hard_eff=m['hard_eff'], soft_eff=m['soft_eff'],
                    primitive_cost=sum(g.res.primitive_cost for g in result.plans.values() if g.live),
                    conflicts=conflicts, same_group_conflicts=m['same_group_conflicts'],
                    cross_group_conflicts=m['cross_group_conflicts'], fallback_groups=fallback,
                    deployment=m['deployment_status'], horizons=m['Ms'],
                    grouping_status='LOCAL_NEGOTIATION', retry_status='', attempts=0,
                    details=dict(groups=result.grouping.groups, unclaimed=m['unclaimed'],
                                 solver_statuses=statuses, messages=dict(result.bus.stats)))
    if job['version'] == 'version_b1':
        # B1 groups globally over the map; same physical anchors, loops, and
        # requested horizon are used. Full-map neighborhood scope is intrinsic to B1.
        params = Params(R_s=side, seed=0, collisions=True, phase2_optimize_cost=False)
        result = run_b1(inst, anchors, params, timeout_ms=0,
                        parallel=bool(job.get('parallel', True)),
                        grouping_only=False, max_regroupings=5)
        report = result.report()
        m = report.get('metrics') or {}
        plans = report.get('plans') or {}
        horizons = [p['M'] for p in plans.values() if p.get('M')]
        return dict(status=report.get('planning_status'), executable=report.get('executable'),
                    coverage=m.get('coverage'), hard_eff=m.get('hard_eff'), soft_eff=m.get('soft_eff'),
                    primitive_cost=sum(g.res.primitive_cost for g in result.local.plans.values()
                                       if g.live) if result.local else None,
                    conflicts=len(report.get('conflicts') or []),
                    same_group_conflicts=m.get('same_group_conflicts'),
                    cross_group_conflicts=m.get('cross_group_conflicts'),
                    fallback_groups=m.get('fallback_groups'),
                    deployment=m.get('deployment_status'), horizons=horizons,
                    grouping_status=report.get('grouping_status'),
                    retry_status=report.get('retry_status'), attempts=len(report.get('attempts') or []),
                    details=report)
    raise ValueError(f"Unknown version: {job['version']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--batch', type=int, required=True, choices=range(1,7))
    ap.add_argument('--repo', type=Path, default=REPO)
    ap.add_argument('--examples', type=Path, default=INPUT_ROOT)
    ap.add_argument('--output', type=Path, default=OUTPUT_ROOT)
    ap.add_argument('--suite', default='version_a_vs_b1',
                    help='Suite directory name inside repo/comparison_logs')
    a = ap.parse_args()
    batch_file = a.repo / 'comparison_logs' / a.suite / f'batch{a.batch:02d}' / 'jobs.json'
    if not batch_file.is_file():
        raise FileNotFoundError(f'Batch file missing: {batch_file}; upload the generated suite with the repo.')
    jobs = json.loads(batch_file.read_text(encoding='utf-8'))
    a.output.mkdir(parents=True, exist_ok=True)
    if a.suite != 'version_a_vs_b1' and a.output == OUTPUT_ROOT:
        raise ValueError('Specify a separate --output folder for a different suite')
    batch_dir = a.output / f'batch{a.batch:02d}'
    batch_dir.mkdir(parents=True, exist_ok=True)
    csv_path = batch_dir / 'results.csv'
    done_ids = set()
    for p in batch_dir.glob('*.result.json'):
        try:
            saved = json.loads(p.read_text(encoding='utf-8'))
            if saved.get('status') != 'ERROR':
                done_ids.add(int(saved['job_id']))
        except (ValueError, OSError, KeyError):
            continue
    print(f'Batch {a.batch}: {len(jobs)} paired jobs; {len(done_ids)} already complete', flush=True)
    out_rows = []
    console = sys.stdout
    class Tee:
        def __init__(self, log):
            self.log = log
        def write(self, text):
            self.log.write(text)
            console.write(text)
            self.flush()
            return len(text)
        def flush(self):
            self.log.flush()
            console.flush()
    for ix, job in enumerate(jobs, 1):
        jid = int(job['id'])
        if jid in done_ids:
            print(f'[{ix}/{len(jobs)}] job {jid} already saved; skip', flush=True)
            continue
        log_file = batch_dir / f'{jid:04d}.log'
        result_file = batch_dir / f'{jid:04d}.result.json'
        print(f"[{ix}/{len(jobs)}] job {jid} case {job['case_id']} {job['version']} "
              f"{job['dataset']} {job['grid_side']}x{job['grid_side']} "
              f"W={job['workers']} R={job['rechargers']} T={job['T']}", flush=True)
        t0 = time.monotonic()
        stop_heartbeat = threading.Event()
        def heartbeat():
            while not stop_heartbeat.wait(20):
                print(f'  still running: {time.monotonic()-t0:.0f}s elapsed', file=console, flush=True)
        heartbeat_thread = threading.Thread(target=heartbeat, daemon=True)
        heartbeat_thread.start()
        row = {k: job.get(k) for k in ('case_id','version','dataset','workers','rechargers','T')}
        row.update(job_id=jid, grid=f"{job['grid_side']}x{job['grid_side']}")
        row.update(planning_radius=job.get('planning_radius', 6 if job['version'] == 'version_a' else job['grid_side'] - 1),
                   baseline_job_id=job.get('baseline_job_id'), baseline_coverage=job.get('baseline_coverage'))
        try:
            with log_file.open('w', encoding='utf-8') as log, redirect_stdout(Tee(log)), redirect_stderr(Tee(log)):
                result = run_one(job, a.examples)
            row.update({k:v for k,v in result.items() if k != 'details'})
            row['wall_s'] = time.monotonic()-t0
            row['error'] = ''
            (batch_dir / f'{jid:04d}.details.json').write_text(
                json.dumps(result.get('details'), indent=2, default=str), encoding='utf-8')
        except Exception as exc:
            row.update(status='ERROR', executable=False, wall_s=time.monotonic()-t0,
                       error=f'{type(exc).__name__}: {exc}')
            with log_file.open('a', encoding='utf-8') as log:
                log.write('\n'+traceback.format_exc())
        finally:
            stop_heartbeat.set()
            heartbeat_thread.join()
        row['horizons'] = json.dumps(row.get('horizons'), separators=(',',':'))
        row['fallback_groups'] = json.dumps(row.get('fallback_groups'), separators=(',',':'))
        result_file.write_text(json.dumps(row, indent=2), encoding='utf-8')
        out_rows.append(row)
        print(f"  {row['status']} coverage={row.get('coverage')} executable={row.get('executable')} "
              f"wall={row['wall_s']:.1f}s", flush=True)

    existing = [json.loads(p.read_text(encoding='utf-8'))
                for p in batch_dir.glob('*.result.json')]
    by_id = {int(r['job_id']): r for r in existing}
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(by_id[k] for k in sorted(by_id))
    print(f'Batch {a.batch} saved {len(by_id)}/{len(jobs)} results to {csv_path}', flush=True)


if __name__ == '__main__':
    main()
