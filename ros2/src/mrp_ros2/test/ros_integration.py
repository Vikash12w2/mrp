"""Headless Jazzy integration check; run in an unused ROS_DOMAIN_ID."""
import json
import os
from pathlib import Path
import sys
import time
import shutil
import tempfile
import rclpy
from rclpy.executors import SingleThreadedExecutor
from rclpy.parameter import Parameter
from mrp_ros2.planner_node import PlannerNode
from mrp_ros2.executor import PlanExecutor
from mrp_ros2.visualizer import MRPVisualizer
from mrp_ros2.ros_bus import RosBus


def main():
    root=Path(__file__).resolve().parents[4]
    rclpy.init()
    planner=PlannerNode(); runner=PlanExecutor(); view=MRPVisualizer()
    executor=SingleThreadedExecutor()
    for node in (planner,runner,view):executor.add_node(node)
    fixture=tempfile.TemporaryDirectory(prefix='mrp_ros_fixture_')
    input_dir=root/'ros2/src/mrp_ros2/config/sample_input'
    two='--two' in sys.argv or '--recover-recharger' in sys.argv
    if two:
        shutil.copytree(input_dir,fixture.name,dirs_exist_ok=True)
        input_dir=Path(fixture.name)
        workspace=(input_dir/'workspace.txt').read_text().splitlines()
        workspace[5]='2'
        (input_dir/'workspace.txt').write_text('\n'.join(workspace)+'\n')
    try:
        if two:
            planner.set_parameters([Parameter('anchors',value=('{"1":[4,0],"2":[4,6]}' if '--recover-recharger' in sys.argv else '{"1":[0,0],"2":[8,0]}'))])
        planner.set_parameters([Parameter('input_dir',value=str(input_dir)),
            Parameter('source_root',value=str(root)), Parameter('R_s',value=4),
            Parameter('cap_util',value=4.0)])
        runner.set_parameters([Parameter('step_period',value=.01)])
        planner._start()
        payload=json.loads(planner.last_result)
        assert payload['status']=='OK',payload
        samples=[]
        from geometry_msgs.msg import Pose2D
        from visualization_msgs.msg import MarkerArray
        planner.create_subscription(Pose2D,'/mrp/workers/worker_1/position',lambda m:samples.append((m.x,m.y)),10)
        marker_paths=set()
        planner.create_subscription(MarkerArray,'/mrp/visualization',lambda m:marker_paths.update(x.ns for x in m.markers),10)
        max_period=max(len(t['path'])-1 for t in payload['tracks'].values())
        target=3*max_period+max(len(p)-1 for p in payload['setup'].values())
        deadline=time.monotonic()+40
        while runner.tick<target and time.monotonic()<deadline and not runner.failed:
            executor.spin_once(timeout_sec=.05)
        assert not runner.failed,'execution fault'
        assert runner.tick>=target,(runner.tick,target)
        assert len(view.worker_positions)==2
        assert len(view.ids)==(2 if two else 1)
        print(f'PASS real ROS planner/executor/simulator: {runner.tick} steps, >=3 cycles, workers={len(view.worker_positions)}')
        assert any(abs(x-round(x))>1e-4 or abs(y-round(y))>1e-4 for x,y in samples), 'no interpolated worker telemetry'
        assert 'worker_path' in marker_paths and 'planned_path' in marker_paths
        print('PASS interpolated worker telemetry and distinct worker/recharger path markers')
        if '--recover-worker' in sys.argv or '--recover-recharger' in sys.argv:
            from std_msgs.msg import String
            old_session=runner.schedule.session
            trigger=planner.create_publisher(String,'/mrp/simulation/fail',10)
            kind='recharger' if '--recover-recharger' in sys.argv else 'worker'
            victim=max((int(k) for k,v in payload['groups'].items() if v),default=1) if kind=='recharger' else 1
            key=('r' if kind=='recharger' else 'w')+str(victim)
            trigger.publish(String(data=json.dumps(dict(kind=kind,id=victim))))
            deadline=time.monotonic()+100
            while time.monotonic()<deadline and not runner.failed:
                executor.spin_once(timeout_sec=.03)
                if runner.schedule.session!=old_session and runner.tick>runner.schedule.setup_steps+5:
                    break
            assert not runner.failed, 'recovery execution stopped'
            assert runner.schedule.session!=old_session,'recovery never activated'
            assert key not in runner.schedule.tracks
            assert key in view.failed_actors
            assert json.loads(planner.last_result)['recovery_reports']
            print(f'PASS Version A {kind} failure: missed heartbeats, source recover(), boundary handover, continued movement')
        # A second recharger endpoint exercises actual DDS delivery and ACK matching,
        # even though the bundled planning scenario contains only one recharger.
        bus=RosBus({1:(0,0),2:(2,0)},4,node=planner)
        assert bus.send(1,2,'claim',{'tuple':(1,2),'set':{3,4}})
        received=bus.drain(2,'claim');assert len(received)==1
        assert received[0].payload=={'tuple':(1,2),'set':{3,4}}
        assert bus.stats['ack']==1
        # Duplicate network delivery must not enqueue twice.
        from std_msgs.msg import String
        envelope=dict(session=bus.session,src=1,dst=2,kind='claim',seq=1,payload=bus.codec.encode('duplicate'))
        bus._receive(2,String(data=json.dumps(envelope)))
        assert not bus.drain(2,'claim')
        bus.loss=lambda *args:True
        assert not bus.send(1,2,'beacon',{})
        assert not bus.unreachable
        assert bus.stats['periodic_drop']==1
        print('PASS ROS transport ACK, typed JSON, deduplication, periodic-loss semantics')
        # No simulator callback => timeout stops the entire schedule.
        executor.remove_node(view)
        runner.set_parameters([Parameter('feedback_timeout',value=.15)])
        deadline=time.monotonic()+2
        while not runner.failed and time.monotonic()<deadline:
            executor.spin_once(timeout_sec=.05)
        assert runner.failed
        stopped=runner.tick
        for _ in range(5):executor.spin_once(timeout_sec=.05)
        assert runner.tick==stopped
        print('PASS feedback loss freezes execution')
    finally:
        if planner.recovery_thread:
            planner.recovery_thread.join(timeout=5.)
        executor.shutdown()
        for bus in getattr(planner,'transport_buses',[]):bus.close()
        for node in (planner,runner,view):node.destroy_node()
        rclpy.shutdown()
        fixture.cleanup()

if __name__=='__main__':main()
