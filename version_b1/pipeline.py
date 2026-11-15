from dataclasses import dataclass, field
from math import lcm
import time
from ..bus import Bus
from ..decentralized import DResult
from ..deployment import plan_deployment
from ..planner import run_wavefront
from ..runtime import verify
from .grouping import group_workers


@dataclass
class B1Result:
    central: object
    local: object
    seconds: float
    attempts: list = field(default_factory=list)
    retry_status: str = "NOT_REQUESTED"

    def report(self):
        c, d = self.central, self.local
        out = dict(version='B1', method='G2/Z3', scope=c.scope, grouping_status=c.status,
                   grouping_reason=c.reason, grouping_seconds=c.seconds, seconds=self.seconds,
                   groups=c.grouping.groups, unassigned=c.grouping.unclaimed,
                   objectives=c.objectives, planning_status='NOT_RUN', executable=False,
                   retry_status=self.retry_status, attempts=self.attempts)
        if d is None:
            return out
        metrics = d.metrics()
        periods = [g.res.M-1 for g in d.plans.values() if g.live]
        checked = (lcm(*periods) if periods else 1) <= 3000
        complete = metrics['served'] == len(d.inst.workers)
        safe = complete and not d.conflicts and not metrics['fallback_groups'] and checked
        out.update(metrics=metrics, anchors=d.anchors, conflicts=d.conflicts,
                   full_joint_period_checked=checked,
                   planning_status='SAT' if safe else 'UNKNOWN' if any(
                       g.res and g.res.status == 'UNKNOWN' for g in d.plans.values()) else 'INCOMPLETE_OR_UNSAFE',
                   executable=safe and metrics['deployment_ready'],
                   plans={r: dict(workers=g.workers, status=g.res.status if g.res else 'IDLE',
                                  T=g.res.T if g.res else None, M=g.res.M if g.res else None,
                                  fallback=g.fallback, notes=g.notes, seconds=g.wall)
                          for r, g in d.plans.items()})
        return out


def run_b1(inst, anchors, params=None, timeout_ms=0, parallel=True, grouping_only=False,
           max_regroupings=5):
    if not isinstance(max_regroupings, int) or max_regroupings < 0:
        raise ValueError('max_regroupings must be a nonnegative integer')
    start = time.monotonic()
    excluded, history = [], []
    current = None
    required = 0
    for index in range(max_regroupings + 1):
        central = group_workers(inst, anchors, params, timeout_ms,
                                excluded=excluded, min_assigned=required)
        if central.status != 'OPTIMAL':
            history.append(dict(attempt=index + 1, grouping_status=central.status,
                                reason=central.reason, grouping_seconds=central.seconds))
            if current is None:
                current = B1Result(central, None, 0)
            current.retry_status = ('ASSIGNMENTS_EXHAUSTED' if central.status == 'UNSAT'
                                    and excluded else 'GROUPING_' + central.status)
            break
        if grouping_only:
            return B1Result(central, None, time.monotonic()-start)
        required = central.objectives['assigned']
        p = central.params
        bus = Bus(anchors, p.R_c, p.retries)
        groups = {r: set(ws) for r, ws in central.grouping.groups.items()}
        # An ownership change can alter all downstream occupancy and alignment
        # constraints. Rebuild every group to avoid reusing stale reservations.
        plans, order, neighbors = run_wavefront(inst, groups, anchors, p, bus, parallel)
        local = DResult(central.grouping, plans, order, verify(plans), bus, neighbors,
                        inst, anchors, p, 0, [])
        local.deployment = plan_deployment(inst, plans, anchors, p, bus)
        current = B1Result(central, local, 0)
        report = current.report()
        history.append(dict(attempt=index + 1, groups=central.grouping.groups,
                            grouping_status=central.status, grouping_seconds=central.seconds,
                            planning_status=report['planning_status'],
                            executable=bool(report['executable']), plans=report['plans'],
                            conflicts=report['conflicts'],
                            full_joint_period_checked=report['full_joint_period_checked'],
                            deployment_status=report['metrics']['deployment_status']))
        if report['executable']:
            current.retry_status = 'SUCCESS'
            break
        if central.grouping.unclaimed:
            # With no grouping caps, these workers have no geometric candidate.
            current.retry_status = 'UNREACHABLE_WORKERS'
            break
        excluded.append(central.grouping.groups)
        current.retry_status = 'RETRY_LIMIT_REACHED'
    current.seconds = time.monotonic()-start
    current.attempts = history
    return current
