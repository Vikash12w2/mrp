# Current Version A vs B1 Colab run

This suite contains **32 distinct completed input configurations** from the previous decentralized study, paired across the current Version A and B1 implementations (**64 jobs; six batches**). The previous study's parameter variants are deduplicated by actual workload: cap/refinement/radius/collision toggles are not rerun as separate experiments. Distinct worker counts, recharger counts, horizons (including completed T14 cases), datasets, and completed 25x25 inputs are retained. Each pair uses the physical anchors saved by the prior Version A run.

Version A runs with its current defaults, collision checks enabled, and Phase 2 cost optimization disabled. B1 uses its full-map grouping/planning scope, the same input, horizon, worker/recharger counts and anchors, collision checks enabled, and up to five offline regrouping attempts. Both are run sequentially per job to report pipeline wall time consistently; the batches can run in separate Colab notebooks. B1's central grouping is intrinsic to B1.

## Put files in Drive

Upload the updated whole `mrp` folder to the same first-account Google Drive location used for the old runs. It must contain:

- `mrp/comparison_logs/run_version_a_b1_batch.py`
- `mrp/comparison_logs/merge_version_a_b1_results.py`
- `mrp/comparison_logs/version_a_vs_b1/batch01` through `batch06`
- `mrp/examples/input-artfloor`, `input-warehouse`, `input-random-20`, and `input-random-30`
- `mrp/version_b1`

Keep outputs in the shared first-account `mrp_backup` folder. The runner creates its own `version_a_vs_b1` subfolder there; it does not overwrite the prior study.

## Run each batch in its own Colab notebook

Run this setup cell in all six notebooks (change only `BATCH_NO`):

```python
from google.colab import drive
drive.mount('/content/drive')

from pathlib import Path
import subprocess, sys

BATCH_NO = 1  # use 1..6, each notebook must have a unique number
repo_parent = Path('/content/drive/MyDrive')
repo = repo_parent / 'mrp'
examples = repo / 'examples'
output = repo_parent / 'mrp_backup' / 'version_a_vs_b1'

assert (repo / 'comparison_logs' / 'run_version_a_b1_batch.py').is_file()
assert (repo / 'version_b1' / 'pipeline.py').is_file()
assert (examples / 'input-artfloor' / 'workspace.txt').is_file()
assert (repo / 'comparison_logs' / 'version_a_vs_b1' / f'batch{BATCH_NO:02d}' / 'jobs.json').is_file()
output.mkdir(parents=True, exist_ok=True)

print(f'Running batch {BATCH_NO}; results go to {output}', flush=True)
subprocess.run([
    sys.executable, '-u', str(repo / 'comparison_logs' / 'run_version_a_b1_batch.py'),
    '--batch', str(BATCH_NO), '--repo', str(repo), '--examples', str(examples),
    '--output', str(output)
], cwd=repo_parent, check=True)
```

Use the same shared first-account Drive folder in every notebook. Do not run the same batch number twice at the same time. Different batches write separate directories. If a notebook stops, rerun its same cell; existing completed job JSONs are skipped and the batch CSV is rebuilt from saved result files. Solver jobs have no time limit and may take a long time. The notebook prints a 20-second heartbeat while a job runs.

The output includes one JSON result, details JSON and log per job, plus a `results.csv` inside each batch folder. The runner never has multiple notebooks append to the same CSV.

## Combine results

After all six batches finish, run in one notebook:

```python
from pathlib import Path
import subprocess, sys

repo = Path('/content/drive/MyDrive/mrp')
output = Path('/content/drive/MyDrive/mrp_backup/version_a_vs_b1')
subprocess.run([
    sys.executable, str(repo / 'comparison_logs' / 'merge_version_a_b1_results.py'),
    '--output', str(output)
], check=True)
```

This writes `mrp_backup/version_a_vs_b1/results_all_batches.csv` and reports missing/error rows. Existing results remain intact.

The six batch manifests are also generated locally by running `python comparison_logs/build_version_a_b1_suite.py` from the repository root; re-run that only to recreate the identical manifests before beginning a study. Do not regenerate batches while notebooks are running.
