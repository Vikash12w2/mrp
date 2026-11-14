from __future__ import annotations
from dataclasses import dataclass, field, replace
from .bus import Bus
from .decentralized import DResult
from .grouping import Agent, GroupingResult, summarize_unclaimed
from .negotiation import Negotiator, rounds
from .planner import GroupPlan, overlap_neighbors, run_wavefront
from .runtime import verify
from .twoshot import wrap


@dataclass(frozen=True)
class Failure:
    kind: str      # "worker" | "recharger"
    id: int
    t: int         # time step (1-based, counted from the start barrier) at which the robot stops


def declared_at(f: Failure, hb_period: int, k_missed: int) -> int:
    first_missed = ((f.t + hb_period - 1) // hb_period + (1 if f.t % hb_period == 0 else 0)) * hb_period
    return first_missed + (k_missed - 1) * hb_period


def failed_by_unreachable(bus: Bus, min_reports: int = 1) -> set:
    """A robot that exhausted ACK retries towards >= min_reports distinct senders is treated as failed."""
    cnt = {}
    for src, dst, _ in bus.unreachable:
        cnt.setdefault(dst, set()).add(src)
    return {r for r, s in cnt.items() if len(s) >= min_reports}


def _cell_at(g: GroupPlan, kind: str, ident: int, t: int):
    if not g.live:
        return g.anchor
    per = g.res.M - 1
    if kind == "worker":
        return g.res.wpos[ident][wrap(t, per)]
    return g.res.rpos[1][wrap(t, per)]


@dataclass
class Recovery:
    failure: Failure
    declared_at: int
    resolved: list = field(default_factory=list)      # recharger IDs re-solved, in order
    reassigned: dict = field(default_factory=dict)    # orphan worker -> new recharger
    unserved: list = field(default_factory=list)
    stop_cell: tuple = None
    solve_time: float = 0.0
    switch_note: str = "new plan active from the next hypercycle boundary after re-planning"


class RecoveryActor(Negotiator):
    """One survivor's sensing, membership and replan decisions; no peer references."""
    def __init__(self, agent, members, plan, bus):
        from copy import deepcopy
        super().__init__(deepcopy(agent), members, bus)
        self.plan, self.dirty = plan, False
        self.before = set(members)

    def observe(self, failure, cell):
        old = self.agent
        local = old.inst
        heard = dict(old.heard)
        neighbors = list(old.neighbors)
        if failure.kind == "worker" and failure.id in heard:
            heard.pop(failure.id)
            if failure.id in self.members:
                self.members.remove(failure.id)
                self.dirty = True
        if failure.kind == "recharger" and failure.id in neighbors:
            neighbors.remove(failure.id)
        # The harness injects physical events; only local sensing changes a map.
        if old.in_region(cell):
            local = replace(local, obstacles=local.obstacles | {cell})
            self.dirty = bool(self.members) or self.dirty
        fresh = Agent(old.id, old.anchor, local, old.P)
        fresh.neighbors = neighbors
        for worker in heard.values():
            fresh.hear(worker)
        # A now-unreachable existing member still consumes capacity until replan.
        for w in self.members:
            if w not in fresh.util:
                fresh.util[w] = old.util.get(w, float("inf"))
        self.agent = fresh

    def publish_dirty(self, epoch):
        self.epoch = epoch
        self.dirty = self.dirty or self.members != self.before
        for neighbor in self.agent.neighbors:
            self.bus.send(self.rid, neighbor, "replan_notice", (epoch, self.dirty))

    def read_dirty(self):
        reports = {m.src: m.payload[1] for m in self.receive("replan_notice")}
        # A missing higher-priority update cannot justify reusing an old plan.
        if any(reports.get(n, True) for n in self.agent.neighbors if n < self.rid):
            self.dirty = True


def propagate_replans(actors, bus):
    for _ in range(max(1, len(actors))):
        bus._group_epoch = getattr(bus, "_group_epoch", 0) + 1
        for actor in actors:
            actor.publish_dirty(bus._group_epoch)
        for actor in actors:
            actor.read_dirty()


def recover(dres: DResult, failures: list, parallel: bool = False, loss=None) -> tuple:
    P, inst, anchors = dres.P, dres.inst, dict(dres.anchors)
    groups = {r: set(g) for r, g in dres.grouping.groups.items()}
    plans = dict(dres.plans)
    agents = dres.grouping.agents
    bus = Bus(anchors, P.R_c, P.retries, loss=loss)
    reports = []
    for f in sorted(failures, key=lambda f: (declared_at(f, P.hb_period, P.k_missed), f.kind, f.id)):
        rec = Recovery(f, declared_at(f, P.hb_period, P.k_missed))
        orphans = []
        if f.kind == "worker":
            # Observer obtains the simulated physical stop cell, not an owner bid.
            owner = next((r for r, g in groups.items() if f.id in g), None)
            if owner is not None:
                rec.stop_cell = _cell_at(plans[owner], "worker", f.id, f.t)
            else:
                worker = next((w for w in inst.workers if w.id == f.id), None)
                if worker is None:
                    continue
                rec.stop_cell = worker.loop[(max(1, f.t) - 1) % len(worker.loop)]
            inst = replace(inst, workers=tuple(w for w in inst.workers if w.id != f.id),
                           obstacles=inst.obstacles | {rec.stop_cell})
        else:
            if f.id not in anchors:
                continue
            rec.stop_cell = _cell_at(plans[f.id], "recharger", f.id, f.t)
            orphans = sorted(groups.pop(f.id))  # reporting only, never given to bidders
            anchors.pop(f.id)
            plans.pop(f.id, None)
            inst = replace(inst, obstacles=inst.obstacles | {rec.stop_cell})
        actors = [RecoveryActor(agents[r], groups[r], plans[r], bus) for r in anchors]
        for actor in actors:
            actor.observe(f, rec.stop_cell)
        if f.kind == "recharger":
            # Every survivor independently notices locally sensed workers without
            # a surviving owner. No global orphan list or winner is consulted.
            rounds(actors, bus, max(1, P.K_round), "claim")
            for _ in range(max(0, P.K_unclaimed)):
                rounds(actors, bus, 1, "rebalance")
                rounds(actors, bus, 1, "claim")
        propagate_replans(actors, bus)
        groups = {a.rid: set(a.members) for a in actors}
        agents = {a.rid: a.agent for a in actors}
        resolve = {a.rid for a in actors if a.dirty}  # observer schedules local requests
        for a in actors:
            a.agent.claims = {w: a.agent.cand.get(w, float("inf")) for w in a.members}
        plans, order, nbrs = run_wavefront(inst, groups, anchors, P, bus, parallel,
                                          keep=plans, resolve=resolve)
        owners = {w: r for r, members in groups.items() for w in members}
        rec.reassigned = {w: owners[w] for w in orphans if w in owners}
        rec.unserved = [w for w in orphans if w not in owners]
        rec.resolved = [r for _, r in order]
        rec.solve_time = sum(plans[r].wall for r in rec.resolved)
        reports.append(rec)
    gr = GroupingResult({r: sorted(s) for r, s in groups.items()},
                        summarize_unclaimed(inst, groups, agents), agents)
    nbrs = overlap_neighbors(anchors, P.R_s)
    return DResult(gr, plans, [], verify(plans), bus, nbrs, inst, anchors, P, dres.refined, []), reports
