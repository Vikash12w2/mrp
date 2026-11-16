# B1 baseline and anchor-adjusted A/B1 follow-up

This suite has 34 jobs in six batches:

- 22 B1-only reruns of the previous distinct workload configurations with requested T=8. All T14 cases are omitted. B1 uses its current code and its internal radius behavior; no radius is overridden by the runner.
- Six workloads where the previous Version A result had under 100% coverage, also excluding T14. Each is rerun as a matched Version A/B1 pair with newly selected, distinct recharger anchors. The anchor placement ensures every worker loop is fully contained in at least one recharger's sensing box.

For the six follow-up workloads, radius 6 could not cover all loops with the existing number of rechargers, so the suite selects radius 8. Version A uses that selected radius; paired B1 uses its current code and its own radius. Both versions use the same new anchors for a paired workload. Worker trajectories, grid, worker count, recharger count, and T are retained. New anchor values and source coverage are in `manifest.json`.

This is a placement and radius comparison. Since anchors change, compare these runs with the original outputs as a combined change, not as an isolated radius-only experiment. B1's grouping is its current global SMT grouping; B1 outputs must not be described as decentralized grouping.

## Upload to Drive

Upload the updated `comparison_logs` directory, including `run_version_a_b1_batch.py`, `merge_version_a_b1_results.py`, and this entire `version_b1_and_anchor_followup` directory, into the same `mrp` folder used for the previous Colab runs. Keep the code in `version_b1` and `examples` up to date as well.

## Run batches

Run this cell in six notebooks, setting `BATCH_NO` uniquely from 1 to 6. Set `output` to the shared backup directory visible in your mounted Drive account.

```python
from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import os, subprocess, sys

BATCH_NO = 1
repo = Path('/content/drive/MyDrive/mrp')
examples = repo / 'examples'
output = Path('/content/drive/MyDrive/mrp_backup/version_b1_and_anchor_followup')
runner = repo / 'comparison_logs/run_version_a_b1_batch.py'
jobs = repo / 'comparison_logs/version_b1_and_anchor_followup' / f'batch{BATCH_NO:02d}/jobs.json'
assert runner.is_file(), runner
assert jobs.is_file(), jobs
assert (repo / 'version_b1/pipeline.py').is_file()
assert (examples / 'input-artfloor/workspace.txt').is_file()
output.mkdir(parents=True, exist_ok=True)
env = os.environ.copy(); env['MRP_PROGRESS'] = '1'
proc = subprocess.Popen([
    sys.executable, '-u', str(runner), '--batch', str(BATCH_NO),
    '--suite', 'version_b1_and_anchor_followup', '--repo', str(repo),
    '--examples', str(examples), '--output', str(output)
], cwd=repo.parent, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
   text=True, bufsize=1)
try:
    for line in proc.stdout: print(line, end='', flush=True)
    code = proc.wait()
    if code: raise RuntimeError(f'Batch exited with code {code}')
except KeyboardInterrupt:
    proc.terminate(); proc.wait(); raise
```

The runner prints solver output and a 20 second heartbeat, stores per-job logs/results/details and per-batch `results.csv`, and skips saved completed jobs on resume. Do not run the same batch in two notebooks at once.

After all batches finish, merge with:

```python
import subprocess, sys
subprocess.run([
    sys.executable, '-u', '/content/drive/MyDrive/mrp/comparison_logs/merge_version_a_b1_results.py',
    '--output', '/content/drive/MyDrive/mrp_backup/version_b1_and_anchor_followup',
    '--expected', '34'
], check=True)
```

The combined CSV is `results_all_batches.csv`. Jobs are numbered 1–22 for B1 baseline, then alternating A/B1 pairs 23–34 for the six anchor follow-ups. No solver runs are started by preparing this suite.
