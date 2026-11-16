# Parallel Version A versus B1 rerun

This suite has **56 jobs in six batches**. It reruns the 22 non-T14 workload configurations from the previous A/B1 suite as matched pairs (44 jobs), plus the six non-T14 low-coverage anchor follow-ups as matched A/B1 pairs (12 jobs). Centralized runs are not included. B1 baseline jobs already present in the A/B1 workload pairs are not duplicated.

Both planners now allow independent recharger-group computations to run in parallel (`parallel=True`). Jobs within each batch still run one at a time, and batches can run concurrently in separate Colab notebooks. Parallel solver work can change wall time and may affect repeatability; compare these timings with the old sequential runs cautiously.

Version A uses radius 6 on the original matched cases. For the six anchor follow-ups, it uses radius 8 with the new anchors that place every worker loop inside at least one A region. B1 uses its current internal radius behavior in every case. Follow-up A/B1 pairs share their new anchors. T14 is excluded throughout.

## Upload to Drive

Upload the updated `comparison_logs/run_version_a_b1_batch.py` and the complete `comparison_logs/version_a_b1_parallel` directory into your Drive `mrp` folder. Keep the current `version_b1` and `examples` code/data in that folder.

## Run a batch

Use a separate Colab notebook for each batch and set `BATCH_NO` uniquely from 1 to 6. Set `output` to the shared backup folder visible to that notebook.

```python
from google.colab import drive
drive.mount('/content/drive')
from pathlib import Path
import os, subprocess, sys

BATCH_NO = 1  # unique value 1..6
repo = Path('/content/drive/MyDrive/mrp')
examples = repo / 'examples'
output = Path('/content/drive/MyDrive/mrp_backup/version_a_b1_parallel')
runner = repo / 'comparison_logs/run_version_a_b1_batch.py'
jobs = repo / 'comparison_logs/version_a_b1_parallel' / f'batch{BATCH_NO:02d}/jobs.json'
assert runner.is_file(), runner
assert jobs.is_file(), jobs
assert (repo / 'version_b1/pipeline.py').is_file()
assert (examples / 'input-artfloor/workspace.txt').is_file()
output.mkdir(parents=True, exist_ok=True)
env = os.environ.copy(); env['MRP_PROGRESS'] = '1'
proc = subprocess.Popen([
    sys.executable, '-u', str(runner), '--batch', str(BATCH_NO),
    '--suite', 'version_a_b1_parallel', '--repo', str(repo),
    '--examples', str(examples), '--output', str(output)
], cwd=repo.parent, env=env, stdout=subprocess.PIPE,
   stderr=subprocess.STDOUT, text=True, bufsize=1)
try:
    for line in proc.stdout: print(line, end='', flush=True)
    code = proc.wait()
    if code: raise RuntimeError(f'Batch exited with code {code}')
except KeyboardInterrupt:
    proc.terminate(); proc.wait(); raise
```

The runner streams solver logs and a 20-second heartbeat, and stores each job's log, JSON result and details plus a per-batch `results.csv`. Completed jobs are skipped when a batch is resumed; interrupted jobs without a saved result restart. Do not run the same batch in two notebooks at once.

## Merge after all batches finish

```python
import subprocess, sys
subprocess.run([
    sys.executable, '-u', '/content/drive/MyDrive/mrp/comparison_logs/merge_version_a_b1_results.py',
    '--output', '/content/drive/MyDrive/mrp_backup/version_a_b1_parallel',
    '--expected', '56'
], check=True)
```

The combined CSV is saved as `results_all_batches.csv` in the new output folder. The original sequential results remain in `mrp_backup/version_a_vs_b1` and `mrp_backup/version_b1_and_anchor_followup`.
