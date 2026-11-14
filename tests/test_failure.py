from mrp.bus import Bus
from mrp.failure import Failure, declared_at, failed_by_unreachable, RecoveryActor, propagate_replans
from mrp.grouping import Agent
from mrp.model import Instance, Prim
from mrp.params import Params
from mrp.planner import GroupPlan


def main():
    # heartbeat rule: stops at t, first missed slot is the next one, declared after k misses
    assert declared_at(Failure("worker", 1, 5), 1, 3) == 8
    assert declared_at(Failure("worker", 1, 5), 4, 3) == 16
    assert declared_at(Failure("worker", 1, 8), 4, 3) == 20
    # deterministic: same inputs, same answer
    assert declared_at(Failure("recharger", 2, 9), 2, 2) == declared_at(Failure("recharger", 2, 9), 2, 2)
    # message loss -> unreachable after exactly `retries` extra attempts -> treated as failed
    anchors = {1: (2, 2), 2: (8, 2), 3: (30, 30)}
    bus = Bus(anchors, 12, retries=3, loss=lambda s, d, k, q, a: d == 2)       # everything to 2 is lost
    assert bus.send(1, 2, "plan", {}) is False
    assert bus.stats["retry"] == 4 and failed_by_unreachable(bus) == {2}
    assert bus.send(2, 1, "plan", {}) is True                                  # 2 can still talk
    assert bus.send(1, 3, "plan", {}) is False                                 # out of range is not a failure of 3 by loss
    # periodic kinds are never retried
    bus2 = Bus(anchors, 12, retries=3, loss=lambda *a: True)
    bus2.send(1, 2, "beacon", {})
    assert bus2.stats["retry"] == 1
    # dependents cascade through the priority chain only
    nbrs = {1: [2], 2: [1, 3], 3: [2], 4: []}
    anchors = {1: (0, 0), 2: (2, 0), 3: (4, 0), 4: (20, 0)}
    inst = Instance(20, 2, frozenset(), (Prim(0, 0, 0, 0, ((0, 0),)),), (), 4, 1, 4)
    for source, expected in ((1, {1, 2, 3}), (3, {3}), (4, {4})):
        bus = Bus(anchors, 2)
        actors = []
        for rid, anchor in anchors.items():
            agent = Agent(rid, anchor, inst, Params(R_s=1))
            agent.neighbors = nbrs[rid]
            actor = RecoveryActor(agent, set(), GroupPlan(rid, [], anchor), bus)
            actor.dirty = rid == source
            actors.append(actor)
        propagate_replans(actors, bus)
        assert {a.rid for a in actors if a.dirty} == expected
        assert not bus.unreachable
    print("failure unit tests passed")


if __name__ == "__main__":
    main()
