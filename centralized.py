from __future__ import annotations
from .model import Instance
from .planner import GroupPlan
from .runtime import verify
from .twoshot import Options, solve_isolated

def run_centralized(inst: Instance, opts: Options | None = None):
    res = solve_isolated(inst, opts or Options())
    g = GroupPlan(0, [w.id for w in inst.workers], (0, 0), res)
    conflicts = verify({0: g}) if res.ok else []
    return res, conflicts
