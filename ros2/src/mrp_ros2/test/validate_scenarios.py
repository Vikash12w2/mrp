"""Slow source-planner validation of all bundled 19x19 scenarios (no ROS needed)."""
import importlib
import json
from pathlib import Path
import sys
import time


def main():
    root=Path(__file__).resolve().parents[4]
    sys.path.insert(0,str(root.parent))
    model=importlib.import_module(root.name+'.model')
    params=importlib.import_module(root.name+'.params')
    dec=importlib.import_module(root.name+'.decentralized')
    base=root/'ros2/src/mrp_ros2/config/scenarios'
    for name,settings in json.loads((base/'scenarios.json').read_text()).items():
        begin=time.monotonic()
        inst=model.load_instance(str(base/name))
        assert (inst.xmax,inst.ymax)==(18,18)
        anchors={int(k):tuple(v) for k,v in settings['anchors'].items()}
        result=dec.run_decentralized(inst,anchors,params.Params(R_s=settings['R_s'],cap_util=settings['cap_util'],phase2_optimize_cost=True))
        assert not result.grouping.unclaimed,(name,result.grouping.unclaimed)
        assert not result.conflicts,(name,result.conflicts[:3])
        assert result.deployment.ready,(name,result.deployment.status)
        assert all(g.live for g in result.plans.values()),name
        print(f'PASS {name}: workers={len(inst.workers)}, rechargers={len(anchors)}, {time.monotonic()-begin:.1f}s',flush=True)

if __name__=='__main__':main()
