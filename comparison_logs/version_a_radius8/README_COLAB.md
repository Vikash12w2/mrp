# Version A radius 8: incomplete-coverage follow-up

The previous current-A suite passed `R_s=6` for every A job. This follow-up selects only its nine completed outputs below 100% coverage and reruns them with `R_s=8`. Same dataset, geometry, anchors, workers, rechargers, requested T, seed zero, collision checks on, Phase 2 cost optimization off, and sequential local solving. No capacity cap is introduced.

`R_s` controls sensing and local planning boxes. `R_c=2*R_s` also changes from 12 to 16, and overlap neighbors change accordingly. This experiment measures that combined configured-radius change. A larger radius can improve geometric coverage and also increase SMT time or coordination difficulty; full coverage and safe alignment are not guaranteed.

The full-coverage A cases that failed the offline screen through fallback are not selected, because the requested criterion is incomplete coverage.

| Batch | Original job IDs | Cases (workers/rechargers, requested T) |
| --- | --- | --- |
| 1 | 45 | random-30 W6/R3 T14 |
| 2 | 63 | warehouse W6/R3 T14 |
| 3 | 43 | random-30 W6/R3 T8 |
| 4 | 61, 49 | warehouse W6/R3 T8; warehouse W2/R1 T8 |
| 5 | 41, 27 | random-30 W6/R2 T8; random-20 W6/R2 T8 |
| 6 | 59, 57 | warehouse W6/R2 T14; warehouse W6/R2 T8 |

All nine cases use 19×19 grids. Batches use prior radius-six runtimes for balancing, not predicted radius-eight runtimes. Job 45 previously took about 53 minutes and is alone; the larger radius may take longer. No solver time limits are set.

## Upload these comparison files to the existing Drive mrp folder

- `comparison_logs/run_version_a_b1_batch.py` (updated runner)
- `comparison_logs/merge_version_a_b1_results.py` (updated merger)
- Entire `comparison_logs/version_a_radius8` directory (manifest and six batches)

Use the existing version implementation files and `mrp/examples`. If Drive creates names such as `jobs (1).json`, replace the older file so the actual filename is `jobs.json`.

## Run one cell in each notebook

Set a unique `BATCH_NO` from 1 to 6. Use the existing shared backup folder visible to that notebook's mounted account. If its shortcut is named differently, edit `output` accordingly.

```python
from google.colab import drive
drive.mount('/content/drive')

from pathlib import Path
import os
import subprocess
import sys

BATCH_NO = 1  # unique value 1..6 for each notebook
repo = Path('/content/drive/MyDrive/mrp')
examples = repo / 'examples'
output = Path('/content/drive/MyDrive/mrp_backup/version_a_radius8')
runner = repo / 'comparison_logs/run_version_a_b1_batch.py'
jobs_file = repo / 'comparison_logs/version_a_radius8' / f'batch{BATCH_NO:02d}/jobs.json'

assert runner.is_file(), f'Runner missing: {runner}'
assert jobs_file.is_file(), f'Batch file missing: {jobs_file}'
assert output.parent.is_dir(), f'Shared backup folder missing: {output.parent}'
for dataset in ('input-random-20', 'input-random-30', 'input-warehouse'):
    assert (examples / dataset / 'workspace.txt').is_file(), f'Missing dataset: {dataset}'

env = os.environ.copy()
env['MRP_PROGRESS'] = '1'
print(f'Running radius-8 batch {BATCH_NO}; results: {output}', flush=True)
proc = subprocess.Popen(
    [sys.executable, '-u', str(runner), '--batch', str(BATCH_NO),
     '--suite', 'version_a_radius8', '--repo', str(repo),
     '--examples', str(examples), '--output', str(output)],
    cwd=repo.parent, env=env, stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT, text=True, bufsize=1,
)
try:
    for line in proc.stdout:
        print(line, end='', flush=True)
    code = proc.wait()
    if code:
        raise RuntimeError(f'Batch process exited with code {code}; inspect output above')
except KeyboardInterrupt:
    proc.terminate()
    proc.wait()
    raise
```

Each batch saves per-job logs, result JSON, details JSON, and a batch CSV. Logs are streamed to the notebook; a heartbeat prints every 20 seconds while solving. Existing completed non-error results in that batch are skipped when resumed. An interrupted job without a completed result restarts from the beginning, with its log overwritten. Rerunning ERROR jobs retries them. Do not run the same batch twice concurrently.

Original job IDs are retained for pairing with radius six; the separate `version_a_radius8` output directory prevents overwriting the earlier study. Results include `planning_radius`, `baseline_job_id`, and `baseline_coverage`. Evaluate coverage, fallback, conflicts, and deployment together; `SAT` alone only indicates full coverage for A.

## Merge after completion

Run once after all six notebooks finish:

```python
import subprocess, sys
subprocess.run([
    sys.executable, '-u',
    '/content/drive/MyDrive/mrp/comparison_logs/merge_version_a_b1_results.py',
    '--output', '/content/drive/MyDrive/mrp_backup/version_a_radius8',
    '--expected', '9',
], check=True)
```

This writes `version_a_radius8/results_all_batches.csv`. Keep the original radius-six outputs for the subsequent paired analysis. These instructions prepare Colab execution; no solver runs were executed locally when creating the suite.
