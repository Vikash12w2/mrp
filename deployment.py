from collections import deque
from dataclasses import dataclass, field
from time import monotonic
from .grouping import box_of
from .planner import overlap_neighbors


@dataclass
class SetupPath:
    rid: int
    cells: list = field(default_factory=list)  # physical anchor through selected start
    sweeps: list = field(default_factory=list)
    reason: str = ""
    pending: bool = False
    timed_out: bool = False

    @property
    def ok(self):
        return bool(self.cells) and not self.reason

    def occupied(self, tick):
        return self.sweeps[tick] if tick < len(self.sweeps) else {self.cells[-1]}


@dataclass
class Deployment:
    paths: dict
    start_ticks: dict
    local_ready: dict
    pending: bool = False
    execution: object = None
    planned_start_ticks: dict = field(default_factory=dict)
    timed_out: bool = False

    def __post_init__(self):
        self.planned_start_ticks = dict(self.start_ticks)
        # No executable start time exists until current-position reports confirm arrival.
        self.start_ticks = {}

    @property
    def plan_ready(self):
        return all(self.local_ready.values())

    @property
    def ready(self):
        return self.plan_ready and self.execution is not None and self.execution.arrived

    @property
    def status(self):
        if self.ready:
            return "READY"
        if self.timed_out or (self.execution is not None and self.execution.timed_out):
            return "TIMEOUT"
        if self.execution is not None and self.execution.reason:
            return "BLOCKED"
        return "WAITING" if self.pending or self.plan_ready else "BLOCKED"

    @property
    def steps(self):
        return max((self.start_ticks or self.planned_start_ticks).values(), default=0)


def _exchange(bus, nbrs, kind, payloads, P):
    bus._setup_epoch = getattr(bus, "_setup_epoch", 0) + 1
    epoch = bus._setup_epoch
    views = {r: {} for r in nbrs}
    deadline = monotonic() + max(0.001, P.setup_timeout_s)
    for _ in range(max(1, P.K_wait)):
        if monotonic() >= deadline:
            break
        for r, payload in payloads.items():
            bus.broadcast(r, kind, (epoch, payload))
        for r in nbrs:
            for m in bus.drain(r, kind):
                if m.src in nbrs[r] and isinstance(m.payload, tuple) and m.payload[0] == epoch:
                    views[r][m.src] = m.payload[1]
        if all(all(n in views[r] for n in nbrs[r]) for r in nbrs):
            return views, True
    return views, False


class SimulatedSetupController:
    def __init__(self, anchors):
        self.positions = dict(anchors)

    def command(self, rid, destination):
        self.positions[rid] = destination

    def position(self, rid):
        return self.positions[rid]


class SetupExecutor:
    def __init__(self, deployment, anchors, P, bus, controller=None, clock=monotonic):
        self.deployment, self.P, self.bus = deployment, P, bus
        self.deployment.start_ticks = {}
        self.nbrs = overlap_neighbors(anchors, P.R_s)
        self.controller = controller or SimulatedSetupController(anchors)
        self.tick, self.stage, self.arrived, self.reason = 0, 0, False, ""
        self.positions, self.views = {}, {r: {} for r in anchors}
        self.known_paths = None
        self.commanded = False
        self.clock, self.communication_started, self.timed_out = clock, None, False
        self.last_heard = {r: {} for r in anchors}
        self.last_own_report = {}
        bus._setup_epoch = getattr(bus, "_setup_epoch", 0) + 1
        self.epoch = bus._setup_epoch

    def poll(self):
        if self.arrived or self.reason or not self.deployment.plan_ready:
            return self.arrived
        if self.communication_started is None:
            self.communication_started = self.clock()
        paths = self.deployment.paths
        if self.known_paths is None:
            known, complete = _exchange(self.bus, self.nbrs, "setup_path", paths, self.P)
            if not complete:
                self._timeout("setup path broadcast budget or time limit exhausted")
                return False
            self.known_paths = known
        expected = {r: p.cells[min(self.stage, len(p.cells)-1)] for r, p in paths.items()}
        if self.stage and not self.commanded:
            for r in paths:
                self.controller.command(r, expected[r])
            self.commanded = True
        period = max(1, self.P.setup_broadcast_period)
        own_fresh = {}
        for r, p in paths.items():
            observation = self.controller.position(r)
            own_fresh[r] = observation is not None
            if observation is None:
                continue
            cell = tuple(observation)
            self.last_own_report[r] = self.clock()
            self.positions[r] = cell
            allowed = p.occupied(self.stage-1) if self.stage else {p.cells[0]}
            if cell not in allowed:
                self.reason = f"recharger {r} reported a position outside its reserved setup movement"
                return False
            if self.tick % period == 0:
                self.bus.broadcast(r, "setup_position", (self.epoch, self.stage, self.tick, cell))
        for r in paths:
            for m in self.bus.drain(r, "setup_position"):
                epoch, stage, tick, cell = m.payload
                previous = self.views[r].get(m.src)
                if (m.src in self.nbrs[r] and epoch == self.epoch and stage == self.stage
                        and 0 <= self.tick-tick <= period
                        and (previous is None or tick > previous[0])):
                    self.views[r][m.src] = (tick, cell)
                    self.last_heard[r][m.src] = self.clock()
        now = self.clock()
        for r in paths:
            if now - self.last_own_report.get(r, self.communication_started) >= self.P.setup_timeout_s:
                self._timeout(f"recharger {r} position/heartbeat reports have been silent for {self.P.setup_timeout_s:g}s")
                return False
            for n in self.nbrs[r]:
                if now - self.last_heard[r].get(n, self.communication_started) >= self.P.setup_timeout_s:
                    self._timeout(f"recharger {r} has not heard neighbor {n} for {self.P.setup_timeout_s:g}s")
                    return False
        local = {}
        for r in paths:
            local[r] = own_fresh[r] and self.positions.get(r) == expected[r]
            for n in self.nbrs[r]:
                report = self.views[r].get(n)
                neighbor_path = self.known_paths[r][n]
                target = neighbor_path.cells[min(self.stage, len(neighbor_path.cells)-1)]
                local[r] = local[r] and report is not None and 0 <= self.tick-report[0] <= period and report[1] == target
        if all(local.values()):
            if self.stage >= max((len(p.sweeps) for p in paths.values()), default=0):
                self.arrived = True
                self.deployment.start_ticks = {r: self.tick + 1 for r in paths}
            else:
                self.stage += 1
                self.commanded = False
                self.views = {r: {} for r in paths}
        self.tick += 1
        return self.arrived

    def run(self, max_polls=None):
        budget = max_polls if max_polls is not None else max(1, self.P.K_wait) + self.deployment.steps * max(1, self.P.setup_broadcast_period)
        for _ in range(budget):
            if self.poll() or self.reason:
                break
        # A bounded call can return WAITING. Slow motion with live heartbeats
        # is not a timeout; a caller can keep polling the retained step.
        return self.arrived

    def _timeout(self, message):
        self.timed_out = True
        self.reason = message

    def retry(self, max_polls=None):
        """Explicitly resume the retained step after a communication timeout."""
        if not self.timed_out:
            return self.run(max_polls)
        self.timed_out, self.reason, self.communication_started = False, "", None
        self.last_heard = {r: {} for r in self.nbrs}
        self.last_own_report = {}
        self.views = {r: {} for r in self.nbrs}
        return self.run(max_polls)


def _route(rid, anchor, goal, inst, box, stationary, higher):
    x0, x1, y0, y1 = box
    free = lambda c: x0 <= c[0] <= x1 and y0 <= c[1] <= y1 and inst.free(c) and c not in stationary
    if not free(anchor) or not free(goal):
        return SetupPath(rid, reason="setup start or goal is occupied or outside the region")
    if any(not p.ok for p in higher):
        return SetupPath(rid, reason="higher-priority neighbor has no setup route",
                         pending=any(p.pending for p in higher))
    # After higher neighbors park, a simple route needs at most the region's
    # number of cells. Waiting until then is included in the search horizon.
    horizon = max((len(p.sweeps) for p in higher), default=0) + (x1-x0+1)*(y1-y0+1)
    start = (anchor, 0)
    parent, queue = {start: None}, deque([start])
    while queue:
        cell, tick = state = queue.popleft()
        safe_to_park = not any(goal in p.cells[-1:] or
                               any(goal in s for s in p.sweeps[tick:]) for p in higher)
        if cell == goal and safe_to_park:
            cells, sweeps = [cell], []
            while parent[state] is not None:
                prev, sw = parent[state]
                sweeps.append(sw)
                cells.append(prev[0])
                state = prev
            return SetupPath(rid, list(reversed(cells)), list(reversed(sweeps)))
        if tick >= horizon:
            continue
        blocked = set().union(*(p.occupied(tick) for p in higher)) if higher else set()
        for prim in inst.prims:
            dst = (cell[0] + prim.dx, cell[1] + prim.dy)
            sw = {(cell[0]+x, cell[1]+y) for x, y in prim.swath} | {cell, dst}
            nxt = (dst, tick + 1)
            if nxt not in parent and all(free(c) for c in sw) and not sw & blocked:
                parent[nxt] = (state, sw)
                queue.append(nxt)
    return SetupPath(rid, reason="no collision-free setup route under the neighbor priority policy")


def plan_deployment(inst, plans, anchors, P, bus, controller=None):
    nbrs = overlap_neighbors(anchors, P.R_s)
    paths = {}
    for r in anchors:
        bus.drain(r, "setup_path")
    for rid in sorted(anchors):
        known = {m.src: m.payload for m in bus.drain(rid, "setup_path") if m.src in nbrs[rid]}
        hi = [n for n in nbrs[rid] if n < rid]
        deadline = monotonic() + max(0.001, P.setup_timeout_s)
        for _ in range(max(1, P.K_wait)):
            if all(n in known for n in hi):
                break
            if monotonic() >= deadline:
                break
            # Previously planned neighbors continue publishing instead of
            # declaring a failure after one missing packet.
            for r, path in paths.items():
                bus.broadcast(r, "setup_path", path)
            for m in bus.drain(rid, "setup_path"):
                if m.src in hi:
                    known[m.src] = m.payload
        g = plans[rid]
        box = box_of(inst, anchors[rid], P.R_s)
        x0, x1, y0, y1 = box
        stationary = {w.loop[0] for w in inst.workers
                      if x0 <= w.loop[0][0] <= x1 and y0 <= w.loop[0][1] <= y1}
        stationary |= {anchors[n] for n in nbrs[rid] if n > rid}
        if any(n not in known for n in hi):
            route = SetupPath(rid, reason="neighbor setup path broadcast budget or time limit exhausted",
                              pending=True, timed_out=True)
        elif g.workers and not g.live:
            route = SetupPath(rid, reason="group has no hypercycle plan")
        else:
            goal = g.res.rpos[1][1] if g.live else anchors[rid]
            route = _route(rid, anchors[rid], goal, inst, box, stationary, [known[n] for n in hi])
        paths[rid] = route
        bus.broadcast(rid, "setup_path", route)

    # Neighbor-only AND/MAX propagation over the overlap component. No worker
    # requests or trajectories are relayed; only setup readiness and finish time.
    status = {r: (p.ok, len(p.sweeps)) for r, p in paths.items()}
    for _ in range(max(1, len(anchors))):
        received, complete = _exchange(bus, nbrs, "setup_ready", status, P)
        if not complete:
            return Deployment(paths, {r: s[1] for r, s in status.items()},
                              {r: False for r in anchors}, pending=True, timed_out=True)
        next_status = {}
        for r in anchors:
            ready, end = status[r]
            for n in nbrs[r]:
                ready = ready and received[r][n][0]
                end = max(end, received[r][n][1])
            next_status[r] = (ready, end)
        status = next_status
    result = Deployment(paths, {r: s[1] for r, s in status.items()}, {r: s[0] for r, s in status.items()},
                        pending=any(p.pending for p in paths.values()),
                        timed_out=any(p.pending or p.timed_out for p in paths.values()))
    if result.plan_ready:
        result.execution = SetupExecutor(result, anchors, P, bus, controller)
        result.execution.run()
    return result
