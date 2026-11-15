# MRP ROS 2 integration (Jazzy)

All integration source is under `paper/ros2`; the original Version A source files are imported without editing them.

## Build and launch

Stop previous launches with Ctrl-C, then:

```bash
cd ~/Downloads/paper/ros2
source /opt/ros/jazzy/setup.bash
colcon build --symlink-install
source install/setup.bash
ros2 launch mrp_ros2 mrp_4w2r.launch.py
```

The new scenarios use **19 × 19 cells**, with coordinates **0–18** in both directions. Their workers travel around four-waypoint square loops. Obstacles are provided between work areas in the fleet scenarios.

| Launch | Workers | Rechargers | Scenario |
|---|---:|---:|---|
| `mrp_4w2r.launch.py` | 4 | 2 | Two work areas |
| `mrp_6w3r.launch.py` | 6 | 3 | Three work areas |
| `mrp_8w4r.launch.py` | 8 | 4 | Four work areas |
| `mrp_recovery_demo.launch.py` | 3 | 2 | Overlapping coverage for failure recovery |
| `mrp_full.launch.py` | 2 | 1 | Original small bundled sample, 9 × 7 cells |

For example:

```bash
ros2 launch mrp_ros2 mrp_6w3r.launch.py step_period:=1.5
ros2 launch mrp_ros2 mrp_8w4r.launch.py
```

Run one at a time. The equivalent scenario selector is:

```bash
ros2 launch mrp_ros2 mrp_scenarios.launch.py scenario:=four_workers
```

Other selector values are `six_workers`, `eight_workers`, and `recovery`. `rviz:=false` runs headlessly. `step_period` is seconds per logical waypoint transition; larger values mean slower movement. The supplied scenarios took roughly 27–80 seconds to solve during testing; motion starts after SMT planning and setup are ready. Solver duration depends on the machine and scenario.

No dataset download is required. For custom inputs, use `mrp_full.launch.py input_dir:=/absolute/input/directory`, containing `workspace.txt`, `obstacle.txt`, `2d_template.txt`, and `robN_traj.txt`. Supply `anchors` as JSON when needed. `source_root` locates the original planner if the ROS package is installed elsewhere. System Python needs `python3-z3`; the optional dependency tool is `python3-rosdep`.

## Smooth movement and RViz paths

Both workers and rechargers interpolate smoothly between consecutive scheduled waypoints. Their movements start together and the next step is issued only after arrival feedback. Repeated waypoints consume a full step, preserving planned waits and charging intervals. Both fleets repeat their hypercycles indefinitely, wrapping at `M-1` without duplicating the closing wait.

* **Yellow lines:** recharger planned paths, including initial setup.
* **Cyan/teal lines:** worker planned paths, including their closed work loops.
* **Green cylinders:** workers; colored blocks: rechargers.
* **Dark red blocks/labels:** failed actors, retained as obstacles.

Workers also have a simulated battery initialized from `fch` in their input files. Movement uses the worker's configured per-step energy cost; a scheduled charge event restores charge only when its recharger is actually adjacent. Battery percentage is shown above each worker and published on `/mrp/workers/worker_<id>/battery` (`std_msgs/msg/Float32`). If charge reaches zero away from a recharger, that worker stops at the completed waypoint, is marked failed, and remains an obstacle. Version A recovery excludes that worker from future plans; it does not restart or recharge the failed worker.

The launch loads RViz settings. For manual setup, set **Fixed Frame** to `map`, add **MarkerArray**, and choose `/mrp/visualization`. The floor/grid covers the complete map, and the camera is configured for the 19 × 19 scenarios.

This remains a simulation of the source grid model, not a hardware controller. Straight interpolation is checked for obstacle proximity and simultaneous crossings involving a recharger. A custom abstract waypoint jump that cannot be drawn safely is rejected rather than animated through an obstacle. Curved motion primitives would need their actual geometry; endpoint-only trajectories do not define a curve.

## Version A runtime failure recovery

Recovery now calls the original `failure.recover`, including local orphan-worker reassignment, neighbor replan notices and SMT replanning. It runs in a background job so heartbeats and existing safe motion remain responsive.

To inject a fail-stop event after setup, use a second sourced terminal:

```bash
ros2 topic pub --once /mrp/simulation/fail std_msgs/msg/String \
  '{data: "{\"kind\":\"worker\",\"id\":1}"}'
```

For a recharger, use `kind: recharger` inside the JSON. Use a scenario with surviving rechargers, such as `mrp_recovery_demo.launch.py`; the one-recharger sample cannot recover from losing its only recharger.

A launch can inject a failure automatically at a completed common-cycle boundary:

```bash
ros2 launch mrp_ros2 mrp_recovery_demo.launch.py \
  failure_kind:=recharger failure_id:=2 failure_after_cycles:=2
```

`failure_kind:=worker` also works. Without `failure_kind`, no failure is injected automatically. Failure injection is a simulation control: a requested failure is applied at the next completed waypoint so its stop cell is exact, not somewhere between grid cells.

The failed actor stops, ceases heartbeats, and its cell becomes an obstacle immediately. After the source algorithm’s missed-heartbeat threshold (default three logical heartbeat slots; rendering speed does not change the count), the executor requests Version A recovery. Healthy actors follow their old schedules while the solver works, provided the old transitions remain safe. At the next common hypercycle boundary they wait for the replacement plan. Surviving rechargers then follow checked setup routes from their actual boundary positions to their new cycle starts; no actor teleports. Workers are at their loop starts. This retains **Version A's full-charge restart assumption**; it does not simulate measured partial battery charge.

Recovery is supported for worker and recharger failures, one recovery transaction at a time. A new failure after activation can be recovered again. Concurrent failures arriving during an existing replan may require stopping/restarting; they are not silently applied to a stale replacement plan.

If the failed cell blocks the old route before its boundary, no feasible recovery/setup route exists, workers cannot be reassigned within capacity, or no recharger survives, execution stops with a reason. This is not a promise that every failure is recoverable. Loss of the entire simulator/feedback channel also stops execution: without a confirmed stop cell, the wrapper does not invent physical robot failures or obstacle positions. Hardware control remains outside this implementation.

## ROS topics

| Topic | Message | Purpose |
|---|---|---|
| `/mrp/planner/result` | `std_msgs/msg/String` | Transient-local plan, tracks, swept cells, setup and recovery reports |
| `/mrp/execution/command` | `std_msgs/msg/String` | Session, tick, duration and joint target cells |
| `/mrp/execution/feedback` | `std_msgs/msg/String` | Arrival acknowledgement and confirmed simulated failures |
| `/mrp/execution/status` | `std_msgs/msg/String` | SETUP, RUNNING, REPLANNING, RECOVERY_WAIT or STOPPED |
| `/mrp/recovery/request` | `std_msgs/msg/String` | Version A failure events and boundary handover state |
| `/mrp/simulation/fail` | `std_msgs/msg/String` | Inject a worker/recharger fail-stop event |
| `/mrp/simulation/heartbeat` | `std_msgs/msg/String` | Live actor IDs and current session |
| `/mrp/robots/robot_<id>/position` | `geometry_msgs/msg/Pose2D` | Smooth recharger telemetry in grid coordinates |
| `/mrp/workers/worker_<id>/position` | `geometry_msgs/msg/Pose2D` | Smooth worker telemetry in grid coordinates |
| `/mrp/workers/worker_<id>/battery` | `std_msgs/msg/Float32` | Simulated remaining worker charge |
| `/mrp/negotiation/robot_<id>/inbox` and `/ack` | `std_msgs/msg/String` | Typed negotiation JSON and matching ACKs |
| `/mrp/visualization` | `visualization_msgs/msg/MarkerArray` | RViz scene and path lines |

Monitor execution with `ros2 topic echo /mrp/execution/status`. Negotiation has a ROS endpoint node per recharger, session IDs, retries, duplicate suppression and source-compatible periodic-message behavior. The original synchronous planning algorithms remain orchestrated by the planner process; per-recharger ROS endpoints do not mean independent planner processes. This is not a Nav2 integration.

`mrp_demo.launch.py` is only a manually commanded placeholder scene. Use one of the full/scenario launches above for planning and execution. Manual goals are disabled during scheduled operation. Stop old launches; a local lock prevents a second current-version visualizer in the same domain.

## Regression checks

From `paper/ros2`, after sourcing ROS and the workspace:

```bash
PYTHONPATH=src/mrp_ros2 /usr/bin/python3 -m unittest discover -s src/mrp_ros2/test -v
ROS_DOMAIN_ID=108 PYTHONPATH=src/mrp_ros2 /usr/bin/python3 src/mrp_ros2/test/ros_integration.py --recover-worker
ROS_DOMAIN_ID=108 PYTHONPATH=src/mrp_ros2 /usr/bin/python3 src/mrp_ros2/test/ros_integration.py --recover-recharger
ROS_DOMAIN_ID=109 /usr/bin/python3 src/mrp_ros2/test/launch_integration.py mrp_4w2r.launch.py
```

Use unused DDS domains and run these sequentially. Tests cover repeated cycles, interpolated telemetry, both path-marker types, source recovery and activation, transport acknowledgements/deduplication, collision checks and feedback-loss stopping. The feedback-loss test deliberately logs one STOPPED error.
