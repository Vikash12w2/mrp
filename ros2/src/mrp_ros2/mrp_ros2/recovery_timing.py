def declaration_tick(stop_tick, setup_steps, period, missed):
    if period < 1 or missed < 1 or stop_tick < setup_steps:
        raise ValueError('Invalid runtime failure timing')
    t = stop_tick - setup_steps + 1
    # Source failure.declared_at uses a 1-based runtime clock. A failure on
    # a heartbeat slot first misses the following slot, not the current one.
    first = ((t + period - 1) // period + int(t % period == 0)) * period
    return setup_steps + first + (missed - 1) * period - 1
