from itertools import combinations
from copy import deepcopy
import re


def cell(value):
    if len(value) != 2 or any(type(v) is not int for v in value):
        raise ValueError('Grid cells must contain two integers')
    return tuple(value)


class Schedule:
    def __init__(self, data):
        if data.get('status') != 'OK' or data.get('schema') != 2:
            raise ValueError('An executable schema-2 OK plan is required')
        self.session = data['session']
        self.disabled = {}
        self.world = data['map']
        self.obstacles = {cell(c) for c in self.world['obstacles']}
        self.tracks = deepcopy(data['tracks'])
        self.setup = {k: [cell(c) for c in v] for k, v in data['setup'].items()}
        if not self.tracks or not self.setup:
            raise ValueError('Empty plan')
        if set(self.setup) != {k for k in self.tracks if k.startswith('r')}:
            raise ValueError('Setup must cover exactly the recharger tracks')
        for key, track in self.tracks.items():
            if not re.fullmatch(r'[rw][1-9][0-9]*',key):
                raise ValueError('Invalid actor identifier')
            path = [cell(c) for c in track['path']]
            track['path'] = path
            if len(path) < 2 or path[0] != path[-1]:
                raise ValueError(f'{key}: hypercycle must close and have at least one step')
            track['sweeps'] = [{cell(c) for c in s} for s in track['sweeps']]
            if len(track['sweeps']) != len(path)-1:
                raise ValueError(f'{key}: missing swept-cell reservations')
            for i, sweep in enumerate(track['sweeps']):
                if not {path[i], path[i+1]} <= sweep:
                    raise ValueError(f'{key}: sweep omits an endpoint')
            if key.startswith('r'):
                if not self.setup.get(key) or self.setup[key][-1] != path[0]:
                    raise ValueError(f'{key}: setup does not reach cycle start')
        self.setup_sweeps = {k: [{cell(c) for c in s} for s in v]
                             for k, v in data['setup_sweeps'].items()}
        for k, path in self.setup.items():
            if len(self.setup_sweeps[k]) != len(path)-1:
                raise ValueError(f'{k}: missing setup reservations')
            for i, sweep in enumerate(self.setup_sweeps[k]):
                if not {path[i], path[i+1]} <= sweep:
                    raise ValueError(f'{k}: setup sweep omits endpoint')
        self.setup_steps = max(len(p)-1 for p in self.setup.values())
        self.frame(0)

    def frame(self, tick):
        result = {}
        for key, track in self.tracks.items():
            if key in self.disabled:
                continue
            if tick <= self.setup_steps:
                path = self.setup.get(key, [track['path'][0]])
                result[key] = path[min(tick, len(path)-1)]
            else:
                result[key] = track['path'][(tick-self.setup_steps) % (len(track['path'])-1)]
        self._check({k: {p} for k, p in result.items()})
        return result

    def transition(self, tick):
        """Check the next full primitive sweep before dispatch, including wrap."""
        reservations = {}
        for key, track in self.tracks.items():
            if key in self.disabled:
                continue
            if tick < self.setup_steps:
                path = self.setup.get(key, [track['path'][0]])
                sweeps = self.setup_sweeps.get(key, [])
                reservations[key] = sweeps[tick] if tick < len(sweeps) else {path[-1]}
            else:
                reservations[key] = track['sweeps'][(tick-self.setup_steps) % len(track['sweeps'])]
        self._check(reservations)
        return self.frame(tick+1)

    def _check(self, reservations):
        for key, sweep in reservations.items():
            if any(c in (self.obstacles | set(self.disabled.values())) or not (0 <= c[0] <= self.world['xmax'] and
                                               0 <= c[1] <= self.world['ymax']) for c in sweep):
                raise ValueError(f'{key}: obstacle or boundary violation')
        for (a, sa), (b, sb) in combinations(reservations.items(), 2):
            # Same semantics as the source runtime.verify: workers may share cells.
            if (a.startswith('r') or b.startswith('r')) and sa & sb:
                raise ValueError(f'Swept-cell collision between {a} and {b}')
