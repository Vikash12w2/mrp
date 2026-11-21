# B2 online recharger simulation

Run from the directory containing the `mrp` package.

```console
python -m mrp.version_b2 --steps 24
python -m mrp.version_b2 --input mrp/examples --steps 20 --trace
```

B1's G2 optimizer supplies initial ownership. Each tick a worker broadcasts its
timestamp, ID, position, loop phase, remaining charge, capacity and next-edge
cost. The controller knows the fixed loops and uses the reported phase and
energy to predict where each worker can be intercepted or will stop. Runtime
energy consumption follows loop edges. Waiting uses zero energy.

Each recharger scores reachable meeting cells by predicted waiting, primitive
travel steps and earliness using the existing score weights. A recharger bids
on its own workers and on boundary workers currently inside its B1 planning
box. A synchronous exchange orders bids by score, recharger ID and worker ID.
Each available recharger locks one available worker. The recharger keeps the
lock through service until a full-charge report, recharger removal or worker
heartbeat expiry. Fresh telemetry updates the meeting point each tick. Workers
only publish state and execute movement or charging.

`RechargerController.decide(tick, positions, broadcasts)` accepts externally
supplied tick snapshots. `run_b2` supplies an in-process simulation with
reliable delivery and fixed membership. Its one-step movement reservations
cover primitive and worker swept cells. A blocked robot may wait. The JSON
reports moving, waiting and charging ticks separately, plus energy transferred
and recharger travel. A completed run records elapsed simulation ticks and
does not certify a repeatable schedule or eventual service.

This implementation uses the single-target planning option. Multi-target
bundles, two-step reservations, runtime G2 regrouping and ROS transport are
outside this implementation.
