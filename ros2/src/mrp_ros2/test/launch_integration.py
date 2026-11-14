"""Exercise installed launch files as separate processes in an unused DDS domain."""
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile,DurabilityPolicy
from std_msgs.msg import String
from geometry_msgs.msg import Pose2D
from visualization_msgs.msg import MarkerArray


def main():
    name=sys.argv[1] if len(sys.argv)>1 else 'mrp_4w2r.launch.py'
    rclpy.init(args=[]);node=Node('mrp_launch_regression')
    states=[];plans=[];samples=[];markers=set()
    qos=QoSProfile(depth=1,durability=DurabilityPolicy.TRANSIENT_LOCAL)
    node.create_subscription(String,'/mrp/execution/status',lambda m:states.append(json.loads(m.data)),qos)
    node.create_subscription(String,'/mrp/planner/result',lambda m:plans.append(json.loads(m.data)),qos)
    node.create_subscription(Pose2D,'/mrp/workers/worker_1/position',lambda m:samples.append((m.x,m.y)),10)
    node.create_subscription(MarkerArray,'/mrp/visualization',lambda m:markers.update((x.ns,x.id) for x in m.markers),10)
    log=tempfile.NamedTemporaryFile(prefix='mrp_launch_',suffix='.log',delete=False)
    child=subprocess.Popen(['ros2','launch','mrp_ros2',name,'rviz:=false','step_period:=0.12']+sys.argv[2:],stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    try:
        deadline=time.monotonic()+300
        while time.monotonic()<deadline and child.poll() is None:
            rclpy.spin_once(node,timeout_sec=.05)
            if states and states[-1]['state']=='STOPPED':raise AssertionError(states[-1])
            recovering=any(arg.startswith('failure_kind:=') for arg in sys.argv[2:])
            if states and states[-1]['tick']>=70 and (not recovering or len({p.get('session') for p in plans if p.get('session')})>=2):break
        assert plans and plans[-1]['status']=='OK',plans
        assert states and states[-1]['tick']>=70,states[-2:]
        assert plans[-1]['map']['xmax']==18 and plans[-1]['map']['ymax']==18
        workers=sum(k.startswith('w') for k in plans[-1]['tracks'])
        assert len({i for ns,i in markers if ns=='worker_path'})==workers
        assert any(abs(x-round(x))>1e-4 or abs(y-round(y))>1e-4 for x,y in samples)
        print(f'PASS installed {name}: 19x19 cells, {workers} worker paths, smooth motion, tick={states[-1]["tick"]}; log={log.name}')
    finally:
        # Signal launch only; it forwards SIGINT to its children once.
        if child.poll() is None:child.send_signal(signal.SIGINT)
        try:child.wait(timeout=15)
        except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGTERM);child.wait(timeout=5)
        node.destroy_node();rclpy.shutdown();log.close()

if __name__=='__main__':main()
