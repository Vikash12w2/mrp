"""Synchronous radio transport, local sensors, and simulated C1 physics."""
from collections import Counter
from dataclasses import asdict
from ..model import cheb, worker_edge_cells
from ..params import Params
from ..version_b2.controller import WorkerBroadcast
from .agent import LocalGrid, Recharger


def listeners(source, workers, radios, radius, hops):
    frontier = {source}
    seen = set(frontier)
    for _ in range(hops):
        frontier = {cell for cell in workers if cell not in seen
                    and any(cheb(cell, p) <= radius for p in frontier)}
        seen.update(frontier)
    return [r for r, pos in radios.items() if any(cheb(pos, c) <= radius for c in seen)]


def received_cells(receiver, messages, radio_range):
    cells = set()
    for sender, payload in messages:
        if cheb(receiver, sender) <= radio_range:
            cells.update(payload)
    return cells


def run_c1(inst, anchors, steps=None, params=None, *, trace=False, relay_hops=2):
    params = params or Params()
    steps = inst.T if steps is None else steps
    radius = params.R_s
    if steps < 1 or inst.delta_max <= 0 or radius < 1 or relay_hops < 0:
        raise ValueError('require positive steps, sensing radius and charging rate, and nonnegative relay hops')
    positions = dict(anchors)
    starts = list(positions.values()) + [w.loop[0] for w in inst.workers]
    if (len(anchors) != inst.n_rech or len(starts) != len(set(starts))
            or any(not inst.free(c) for c in starts)):
        raise ValueError('provide distinct free starting cells for every robot')
    agents = {r: Recharger(r, params) for r in positions}
    phase = {w.id: 0 for w in inst.workers}
    energy = {w.id: w.fch for w in inst.workers}
    stats = {w.id: Counter(moving=0, charging=0, waiting=0, energy_received=0) for w in inst.workers}
    travel = Counter(dict.fromkeys(positions, 0))
    events, history = [], []
    for tick in range(steps):
        states = {w.id: WorkerBroadcast(tick, w.id, w.loop[phase[w.id]], phase[w.id],
                  energy[w.id], w.fch, w.req[phase[w.id]]) for w in inst.workers}
        cells = {s.position for s in states.values()}
        inbox = {r: {} for r in agents}
        for w in inst.workers:
            for r in listeners(states[w.id].position, cells, positions, radius, relay_hops):
                inbox[r][w.id] = w, states[w.id]
        grids, bids, packets = {}, {}, {}
        before = {r: set(a.members) for r, a in agents.items()}
        for r, agent in agents.items():
            x, y = positions[r]
            free = frozenset((i,j) for i in range(x-radius,x+radius+1)
                             for j in range(y-radius,y+radius+1) if inst.free((i,j)))
            grids[r] = LocalGrid(inst.xmax, inst.ymax, inst.prims, free)
            lock = agent.lock
            finished = lock and lock.worker in inbox[r] and inbox[r][lock.worker][1].charge == inbox[r][lock.worker][1].capacity
            bids[r] = agent.offers(grids[r], positions[r], inbox[r])
            for wid, bid in bids[r].items():
                priority = 0 if agent.lock and agent.lock.worker == wid else 1 if wid in agent.members and not finished else 2
                packets.setdefault(wid, []).append((priority, bid.score, r))
        # Model a passive echo of bid packets along the same bounded radio
        # paths. Only bidders on that broadcast receive its packet list.
        targets = {}
        for r, agent in agents.items():
            replies = {wid: tuple(packets[wid]) for wid in bids[r]}
            target = agent.select(bids[r], replies, inbox[r])
            if target:
                targets[r] = target
            for wid in sorted(before[r] - agent.members):
                events.append((tick, 'release', wid, r))
            for wid in sorted(agent.members - before[r]):
                events.append((tick, 'join', wid, r))
        charging = {b.worker:r for r,b in targets.items() if cheb(positions[r], states[b.worker].position)==1}
        # Workers announce their next swept cells. Nearby robots reserve in
        # deterministic ID slots. Each robot reads only its local cells.
        # Starts and subsequent reservations are radio messages from robot
        # slots. A pair of sweeps of radius R can intersect only when their
        # origins are within 2R, the reservation radio range.
        reservations = [(c, {c}) for c in cells | set(positions.values())]
        intents = []
        for w in sorted(inst.workers, key=lambda w:w.id):
            s = states[w.id]
            if w.id in charging:
                amount = min(inst.delta_max, w.fch-energy[w.id])
                energy[w.id] += amount
                stats[w.id]['charging'] += 1
                stats[w.id]['energy_received'] += amount
                continue
            sweep = worker_edge_cells(inst, w, phase[w.id])
            if energy[w.id] >= s.next_step_cost and all(inst.free(c) and cheb(c, s.position) <= radius for c in sweep):
                intents.append((s.position, sweep))
                blocked = received_cells(s.position, reservations, params.R_c) - {s.position}
                if not sweep & blocked:
                    reservations.append((s.position, sweep))
                    phase[w.id] = (phase[w.id]+1) % len(w.loop)
                    energy[w.id] -= s.next_step_cost
                    stats[w.id]['moving'] += 1
                    continue
            stats[w.id]['waiting'] += 1
        for r, agent in sorted(agents.items()):
            pos = positions[r]
            visible = grids[r].visible_free
            occupied = received_cells(pos, reservations, params.R_c)
            desired = received_cells(pos, intents, params.R_c)
            dest, sweep = agent.move(grids[r], pos, targets.get(r), r in charging.values(),
                                     (visible & occupied) - {pos}, visible & desired)
            reservations.append((pos, sweep))
            positions[r] = dest
            travel[r] += int(dest != pos)
        if trace:
            history.append(dict(tick=tick, received={r:sorted(m) for r,m in inbox.items()},
                                targets={r:asdict(b) for r,b in targets.items()},
                                charging=charging, end_positions=dict(positions)))
    groups = {r:sorted(a.members) for r,a in agents.items()}
    total = steps * len(stats)
    moving = sum(w['moving'] for w in stats.values())
    charged = sum(w['charging'] for w in stats.values())
    result = dict(version='C1', ticks=steps, workers=stats, final_charge=energy, travel_steps=travel,
                  groups=groups, unassigned=sorted(set(stats).difference(*groups.values())),
                  membership_events=events, hard_eff=100*moving/total if total else None,
                  soft_eff=100*(moving+charged)/total if total else None)
    if trace:
        result['trace'] = history
    return result
