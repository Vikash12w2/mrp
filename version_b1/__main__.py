import argparse
import json
from pathlib import Path
from ..deploy import pick_anchors
from ..model import load_instance
from ..params import Params
from .demo import demo
from .pipeline import run_b1


def main():
    ap = argparse.ArgumentParser(description='B1: central G2 grouping + per-group SMT')
    ap.add_argument('--input', help='original-format input directory; omit for bundled demo')
    ap.add_argument('--workers', type=int)
    ap.add_argument('--rechargers', type=int)
    ap.add_argument('--T', type=int)
    ap.add_argument('--anchors', help='x,y;x,y in recharger ID order')
    ap.add_argument('--grouping-only', action='store_true')
    ap.add_argument('--grouping-timeout-ms', type=int, default=0)
    ap.add_argument('--rlimit', type=int, default=0, help='per-SMT deterministic resource limit; 0 unlimited')
    ap.add_argument('--group-mode', choices=('two_shot', 'one_shot', 'auto'), default='two_shot')
    ap.add_argument('--max-regroupings', type=int, default=5, help='additional offline assignment attempts after failure (default: 5)')
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument('--parallel', dest='parallel', action='store_true', default=True,
                      help='plan independent groups concurrently (default); preserve collision dependencies')
    mode.add_argument('--sequential', dest='parallel', action='store_false',
                      help='plan independent groups sequentially')
    ap.add_argument('--output', type=Path, help='new JSON output file; existing files are not overwritten')
    a = ap.parse_args()
    if a.rlimit < 0 or a.grouping_timeout_ms < 0 or a.max_regroupings < 0:
        ap.error('limits must be nonnegative')
    if a.output and a.output.exists():
        ap.error('--output already exists; choose a new file')
    if a.input:
        inst = load_instance(a.input, a.workers, a.rechargers, a.T)
        anchors = pick_anchors(inst, inst.n_rech, max(inst.xmax, inst.ymax, 1))
    else:
        if any(v is not None for v in (a.workers, a.rechargers, a.T)):
            ap.error('--workers, --rechargers and --T require --input')
        inst, anchors = demo()
    if a.anchors:
        try:
            anchors = {i+1: tuple(map(int, cell.split(','))) for i, cell in enumerate(a.anchors.split(';'))}
            if any(len(c) != 2 for c in anchors.values()):
                raise ValueError()
        except ValueError:
            ap.error('--anchors must have the form "0,0;7,0"')
    p = Params(R_s=max(inst.xmax, inst.ymax, 1),
               rlimit=a.rlimit, group_mode=a.group_mode)
    result = run_b1(inst, anchors, p, a.grouping_timeout_ms, a.parallel, a.grouping_only, a.max_regroupings)
    report = result.report()
    print(json.dumps(report, indent=2))
    if a.output:
        with a.output.open('x') as stream:
            json.dump(report, stream, indent=2)
            stream.write('\n')
    return 0 if (report['grouping_status'] == 'OPTIMAL' and not report['unassigned']
                 and (a.grouping_only or report['executable'])) else 2


if __name__ == '__main__':
    raise SystemExit(main())
