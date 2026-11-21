import argparse
import json

from ..deploy import pick_anchors
from ..model import load_instance
from ..version_b1.demo import demo
from .simulation import run_b2


def main(run=run_b2, version='B2'):
    parser = argparse.ArgumentParser(description=f'Run {version} online recharger simulation')
    parser.add_argument('--input', help='original-format instance directory')
    parser.add_argument('--steps', type=int, help='number of online ticks')
    parser.add_argument('--anchors', help='x,y;x,y in recharger ID order')
    parser.add_argument('--trace', action='store_true')
    args = parser.parse_args()
    try:
        if args.input:
            inst = load_instance(args.input)
            anchors = {} if args.anchors else pick_anchors(inst, inst.n_rech, max(1, inst.xmax, inst.ymax))
        else:
            inst, anchors = demo()
        if args.anchors:
            anchors = {}
            for rid, pair in enumerate(args.anchors.split(';'), 1):
                x, y = map(int, pair.split(','))
                anchors[rid] = (x, y)
        result = run(inst, anchors, args.steps, trace=args.trace)
    except ValueError as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2))
    return 0 if result['ticks'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
