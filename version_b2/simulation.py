from collections import Counter, deque
from dataclasses import asdict

from ..model import cheb, worker_edge_cells
from ..version_b1.geometry import neighbors
from ..version_b1.grouping import group_workers
from .controller import RechargerController, WorkerBroadcast


def next_move(inst, start, goal, blocked):
    """First primitive of a shortest currently clear route."""
    if start != goal:
        queue = deque([start])
        first = {start: (start, {start})}
        while queue:
            cell = queue.popleft()
            for dest, sweep in neighbors(inst, cell):
                if dest in first or blocked & sweep:
                    continue
                first[dest] = (dest, sweep) if cell == start else first[cell]
                if dest == goal:
                    return first[dest]
                queue.append(dest)
    return start, {start}


def run_b2(inst, anchors, steps=None, params=None, *, trace=False):
    steps = inst.T if steps is None else steps
    if steps < 1:
        raise ValueError('steps must be positive')
    groups = group_workers(inst, anchors, params)
    report = dict(grouping_status=groups.status, groups=groups.grouping.groups,
                  unassigned=groups.grouping.unclaimed, ticks=0)
    if groups.status != 'OPTIMAL':
        return dict(report, reason=groups.reason)
    controller = RechargerController(inst, anchors, groups.grouping.groups, params)
    report.update(simulate_online(inst, anchors, steps, controller, trace))
    return report


def simulate_online(inst, anchors, steps, controller, trace=False):
    report = {}
    positions = dict(anchors)
    phase = {w.id: 0 for w in inst.workers}
    charge = {w.id: w.fch for w in inst.workers}
    starts = [w.loop[0] for w in inst.workers] + list(anchors.values())
    if len(set(starts)) != len(starts):
        raise ValueError('worker and recharger starting cells must be distinct')
    metrics = {w.id: Counter(moving=0, waiting=0, charging=0, energy_received=0)
               for w in inst.workers}
    history = []
    travel = Counter(dict.fromkeys(anchors, 0))
    for tick in range(steps):
        live = {w.id: WorkerBroadcast(tick, w.id, w.loop[phase[w.id]], phase[w.id],
                                      charge[w.id], w.fch, w.req[phase[w.id]]) for w in inst.workers}
        targets = controller.decide(tick, positions, live.values())
        charging = {b.worker: r for r, b in targets.items()
                    if cheb(positions[r], live[b.worker].position) == 1}
        occupied = set(positions.values()) | {s.position for s in live.values()}
        desired = set()
        for w in sorted(inst.workers, key=lambda w: w.id):
            s = live[w.id]
            stats = metrics[w.id]
            if w.id in charging:
                energy = min(inst.delta_max, w.fch - s.charge)
                charge[w.id] += energy
                stats['energy_received'] += energy
                stats['charging'] += 1
                continue
            sweep = worker_edge_cells(inst, w, s.loop_index)
            if s.charge >= s.next_step_cost and all(inst.free(c) for c in sweep):
                desired.update(sweep)
                if not sweep.intersection(occupied - {s.position}):
                    occupied.update(sweep)
                    charge[w.id] -= s.next_step_cost
                    phase[w.id] = (s.loop_index + 1) % len(w.loop)
                    stats['moving'] += 1
                    continue
            stats['waiting'] += 1
        priority = sorted(positions, key=lambda r: (
            controller.locks[r].score if r in controller.locks else float('inf'), r))
        for r in priority:
            start = positions[r]
            blocked = occupied - {start}
            dest, sweep = start, {start}
            if r in targets and targets[r].worker not in charging:
                dest, sweep = next_move(inst, start, targets[r].meeting_cell, blocked)
            if dest == start and start in desired and r not in charging.values():
                for cell, cells in neighbors(inst, start):
                    if cell not in desired and not cells.intersection(blocked):
                        dest, sweep = cell, cells
                        break
            positions[r] = dest
            occupied.update(sweep)
            travel[r] += int(dest != start)
        if trace:
            history.append(dict(tick=tick, broadcasts=[asdict(s) for s in live.values()],
                                targets={r: asdict(b) for r, b in targets.items()},
                                charging=charging, end_positions=dict(positions)))
    report.update(ticks=steps, workers=metrics, final_charge=charge, travel_steps=travel)
    if trace:
        report['trace'] = history
    return report
