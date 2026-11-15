from __future__ import annotations
from dataclasses import dataclass
from .bus import Bus
from .grouping import GroupingResult, run_grouping
from .model import Instance
from .params import Params
from .planner import GroupPlan, levels, overlap_neighbors, refine_partition, run_wavefront
from .runtime import verify


@dataclass
class DResult:
    grouping: GroupingResult
    plans: dict
    order: list
    conflicts: list
    bus: Bus
    nbrs: dict
    inst: Instance = None
    anchors: dict = None
    P: Params = None
    refined: int = 0
    log: list = None
    deployment: object = None

    def metrics(self) -> dict:
        live = [g for g in self.plans.values() if g.live]
        den = sum(g.res.nW * g.res.M for g in live)
        hard_wait = sum(g.res.p1_wait + (g.res.M - g.res.T) * g.res.nW for g in live)
        rech = sum(len(g.res.instances) + sum(h[2] for h in g.res.halts.values()) for g in live)
        served = sum(g.res.nW for g in live)
        total_workers = len(self.inst.workers) if self.inst is not None else served
        complete = served == total_workers
        served_hard = 100.0 * (den - hard_wait) / den if den else 0.0
        served_soft = 100.0 * (den - (hard_wait - rech)) / den if den else 0.0
        lvl = levels(self.nbrs)
        crit = sum(max([g.wall for r, g in self.plans.items() if lvl[r] == L] or [0]) for L in set(lvl.values()))
        return dict(hard_eff=served_hard if complete else None,
                    soft_eff=served_soft if complete else None,
                    served_hard_eff=served_hard, served_soft_eff=served_soft,
                    coverage=100.0 * served / total_workers if total_workers else 100.0,
                    served=served, Ms=[g.res.M for g in live],
                    sum_time=sum(g.wall for g in self.plans.values()), critical_path_time=crit,
                    same_group_conflicts=sum(c[3] == "same_group" for c in self.conflicts),
                    cross_group_conflicts=sum(c[3] == "cross_group" for c in self.conflicts),
                    fallback_groups=[g.rid for g in self.plans.values() if g.fallback],
                    deployment_ready=self.deployment.ready if self.deployment is not None else None,
                    deployment_status=self.deployment.status if self.deployment is not None else None,
                    deployment_steps=self.deployment.steps if self.deployment is not None and self.deployment.ready else None,
                    unclaimed=self.grouping.unclaimed)


def run_decentralized(inst: Instance, anchors: dict, P: Params, parallel=True, loss=None, setup_controller=None) -> DResult:
    bus = Bus(anchors, P.R_c, P.retries, loss)
    g = run_grouping(inst, anchors, P, bus)
    groups = {r: set(w) for r, w in g.groups.items()}
    plans, order, nbrs = run_wavefront(inst, groups, anchors, P, bus, parallel)
    refined = 0
    if P.K_refine:
        refined = refine_partition(inst, groups, plans, anchors, g.agents, nbrs, P, bus)
        g.groups = {r: sorted(s) for r, s in groups.items()}
    result = DResult(g, plans, order, verify(plans), bus, nbrs, inst, anchors, P, refined, [])
    # Exhausted ACK retries are the Version A failure signal for a peer.
    from .failure import Failure, failed_by_unreachable, recover
    failed = sorted(failed_by_unreachable(bus) & set(anchors))
    if failed:
        recovered, reports = recover(result, [Failure("recharger", rid, 1) for rid in failed], parallel)
        recovered.bus.stats.update(bus.stats)
        recovered.bus.unreachable = list(bus.unreachable) + list(recovered.bus.unreachable)
        recovered.log = reports
        from .deployment import plan_deployment
        recovered.deployment = plan_deployment(recovered.inst, recovered.plans, recovered.anchors, P, recovered.bus, setup_controller)
        return recovered
    from .deployment import plan_deployment
    result.deployment = plan_deployment(inst, plans, anchors, P, bus, setup_controller)
    return result
