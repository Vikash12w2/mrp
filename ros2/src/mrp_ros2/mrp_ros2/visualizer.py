import math
import json
import os
import fcntl
import time
from .motion import interpolate, validate_segments
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from geometry_msgs.msg import Pose2D
from visualization_msgs.msg import Marker, MarkerArray
from std_msgs.msg import ColorRGBA
from std_msgs.msg import String
from std_msgs.msg import Float32

class MRPVisualizer(Node):
    def __init__(self):
        super().__init__('mrp_visualizer')
        domain=os.environ.get('ROS_DOMAIN_ID','0')
        self._publisher_lock=open(f'/tmp/mrp_ros2_visualizer_domain_{domain}.lock','w')
        try:
            fcntl.flock(self._publisher_lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError('Another MRP visualizer is already running in this ROS domain. Stop the other launch with Ctrl-C to avoid competing marker publishers.') from exc
        for name, default in [('xmax', 16), ('ymax', 12), ('cell_size', 1.0), ('robot_ids', [1, 2]), ('worker_positions', '3,3;12,8;8,5'), ('obstacles', '6,3;6,4;6,5;10,7;10,8')]:
            self.declare_parameter(name, default)
        self.xmax=int(self.get_parameter('xmax').value); self.ymax=int(self.get_parameter('ymax').value)
        self.size=float(self.get_parameter('cell_size').value)
        self.ids=[int(i) for i in self.get_parameter('robot_ids').value]
        self.robots={i:[1.0+2*(i-1),1.0,0.] for i in self.ids}
        self.targets={i:None for i in self.ids}
        self.workers=self._parse_cells(self.get_parameter('worker_positions').value)
        self.worker_paths={}; self.worker_positions={}; self.worker_indices={}; self.worker_pos_pubs={}; self.worker_tick=0
        self.obstacles=self._parse_cells(self.get_parameter('obstacles').value)
        self.pub=self.create_publisher(MarkerArray,'/mrp/visualization',10)
        plan_qos=QoSProfile(history=HistoryPolicy.KEEP_LAST,depth=1,
                            reliability=ReliabilityPolicy.RELIABLE,
                            durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.plan_sub=self.create_subscription(String,'/mrp/planner/result',self._plan_received,plan_qos)
        self.plan_paths=[]
        self.schedule=None
        self.execution_tick=-1
        self.motion=None
        self.declare_parameter('failure_kind','')
        self.declare_parameter('failure_id',1)
        self.declare_parameter('failure_after_cycles',2)
        self.injected=False
        if self.get_parameter('failure_kind').value not in ('','worker','recharger'):
            raise ValueError('failure_kind must be worker or recharger')
        self.staged={}
        self.failed_actors={}
        self.failure_queue=set()
        self.battery={}
        self.battery_specs={}
        self.battery_pubs={}
        self.health=self.create_publisher(String,'/mrp/simulation/heartbeat',10)
        self.create_subscription(String,'/mrp/simulation/fail',self._failure,10)
        self.feedback=self.create_publisher(String,'/mrp/execution/feedback',10)
        self.create_subscription(String,'/mrp/execution/command',self._execute_frame,10)
        self.subs=[]
        for rid in self.ids:
            self.subs.append(self.create_subscription(Pose2D,f'/mrp/robots/robot_{rid}/cmd_position',lambda m,r=rid:self._command(r,m),10))
            self.subs.append(self.create_subscription(Pose2D,f'/mrp/robots/robot_{rid}/goal',lambda m,r=rid:self._command(r,m),10))
        self.pos_pubs={i:self.create_publisher(Pose2D,f'/mrp/robots/robot_{i}/position',10) for i in self.ids}
        self.create_timer(.03,self._tick)
    def _command(self,rid,msg):
        if self.schedule is None:
            self.targets[rid]=(round(msg.x),round(msg.y))
        else:
            self.get_logger().warning('Manual goals are disabled during scheduled execution')

    def _failure(self,msg):
        try:
            data=json.loads(msg.data)
            if data['kind'] not in ('worker','recharger'):
                raise ValueError('kind must be worker or recharger')
            key=('w' if data['kind']=='worker' else 'r')+str(int(data['id']))
            if not self.schedule or key not in self.schedule.tracks or key in self.failed_actors:
                raise ValueError('Actor is not active')
            if self.execution_tick<self.schedule.setup_steps:
                raise ValueError('Failure injection is enabled after setup')
            self.failure_queue.add(key)
        except (ValueError,KeyError,TypeError) as exc:
            self.get_logger().error(f'Failure injection rejected: {exc}')

    def _positions(self):
        return {**{f'r{k}':tuple(v[:2]) for k,v in self.robots.items()},
                **{f'w{k}':tuple(v) for k,v in self.worker_positions.items()}}

    def _set_positions(self,positions):
        for key,(x,y) in positions.items():
            ident=int(key[1:])
            if key.startswith('r'):
                old=self.robots[ident]
                theta=math.atan2(y-old[1],x-old[0]) if (x,y)!=tuple(old[:2]) else old[2]
                self.robots[ident]=[float(x),float(y),theta]
            else:self.worker_positions[ident]=(float(x),float(y))

    def _ack(self):
        positions={k:tuple(round(v) for v in p) for k,p in self._positions().items() if k not in self.schedule.disabled}
        self.feedback.publish(String(data=json.dumps(dict(session=self.schedule.session,
            tick=self.execution_tick,positions=positions,
            failed={k:v for k,v in self.failed_actors.items() if k in self.schedule.tracks}))))

    def _execute_frame(self,msg):
        if self.schedule is None:return
        try:
            data=json.loads(msg.data)
            if data.get('session')!=self.schedule.session:
                new=self.staged.get(data.get('session'))
                if not new or data.get('tick')!=0 or self.motion:return
                self._install_plan(new)
            tick=data['tick']
            if self.motion and tick==self.motion['tick']:
                return  # retry never resets animation progress
            if type(tick) is not int or tick not in (self.execution_tick,self.execution_tick+1):
                raise ValueError('Out-of-order execution frame')
            if tick==self.execution_tick:
                self._ack();return
            expected=self.schedule.frame(tick) if self.execution_tick<0 else self.schedule.transition(self.execution_tick)
            if {k:tuple(v) for k,v in data['positions'].items()}!=expected:
                raise ValueError('Command disagrees with validated plan')
            start={k:self._positions()[k] for k in expected}
            validate_segments(start,expected,self.schedule.obstacles|set(self.schedule.disabled.values()))
            duration=max(.05,float(data.get('duration',1.0)))
            self.motion=dict(tick=tick,start=start,end=expected,began=time.monotonic(),duration=duration)
        except (ValueError,KeyError,TypeError) as exc:
            self.get_logger().error(f'Execution command rejected: {exc}')
            if 'data' in locals():
                self.feedback.publish(String(data=json.dumps(dict(session=data.get('session'),tick=data.get('tick'),error=str(exc)))))

    def _plan_received(self,msg):
        try:
            data=json.loads(msg.data)
            if data.get('status')!='OK':return
            if self.schedule:
                if data.get('parent_session')==self.schedule.session:
                    self.staged[data['session']]=data
                return
            self._install_plan(data)
        except (ValueError,KeyError,TypeError) as exc:
            self.get_logger().error(f'Invalid planner result: {exc}')

    def _install_plan(self,data):
        from .schedule import Schedule
        schedule=Schedule(data)
        first=schedule.frame(0)
        if self.schedule:
            current=self._positions()
            if any(k not in current or any(abs(a-b)>1e-6 for a,b in zip(current[k],p)) for k,p in first.items()):
                raise ValueError('Recovery activation would teleport an actor')
        self.plan_paths=data.get('paths',[])
        world=data['map']
        self.xmax=int(world['xmax']);self.ymax=int(world['ymax'])
        self.obstacles=world['obstacles'];self.workers=world['workers']
        self.ids=sorted(int(k[1:]) for k in schedule.tracks if k.startswith('r'))
        self.robots={rid:[float(v) for v in first[f'r{rid}']]+[0.] for rid in self.ids}
        self.targets={rid:None for rid in self.ids}
        for rid in self.ids:
            if rid not in self.pos_pubs:
                self.pos_pubs[rid]=self.create_publisher(Pose2D,f'/mrp/robots/robot_{rid}/position',10)
        self.worker_positions={int(k[1:]):p for k,p in first.items() if k.startswith('w')}
        for wid in self.worker_positions:
            if wid not in self.worker_pos_pubs:
                self.worker_pos_pubs[wid]=self.create_publisher(Pose2D,f'/mrp/workers/worker_{wid}/position',10)
        self.battery_specs={int(k):v for k,v in data.get('battery',{}).items()}
        self.battery={wid:float(spec['capacity']) for wid,spec in self.battery_specs.items()}
        for wid in self.battery:
            if wid not in self.battery_pubs:
                self.battery_pubs[wid]=self.create_publisher(Float32,f'/mrp/workers/worker_{wid}/battery',10)
        self.schedule=schedule
        self.execution_tick=-1
        self.motion=None
        self.staged={}
        self.failed_actors.update(data.get('failed',{}))
        clear=Marker();clear.action=Marker.DELETEALL
        self.pub.publish(MarkerArray(markers=[clear]))
    @staticmethod
    def _parse_cells(value):
        if not value: return []
        return [[float(part) for part in pair.split(',')] for pair in value.split(';') if pair]
    def _tick(self):
        if self.motion:
            move=self.motion
            fraction=(time.monotonic()-move['began'])/move['duration']
            self._set_positions(interpolate(move['start'],move['end'],fraction))
            if fraction>=1.0:
                old_tick=self.execution_tick
                self.execution_tick=move['tick']
                self.motion=None
                self._update_batteries(old_tick,move)
                kind=self.get_parameter('failure_kind').value
                cycles=max(1,int(self.get_parameter('failure_after_cycles').value))
                period=math.lcm(*(len(t['path'])-1 for t in self.schedule.tracks.values()))
                if kind and not self.injected and self.execution_tick>=self.schedule.setup_steps+period*cycles:
                    key=('w' if kind=='worker' else 'r')+str(self.get_parameter('failure_id').value)
                    if key in self.schedule.tracks:self.failure_queue.add(key)
                    else:self.get_logger().error('Automatic failure actor is absent from this scenario')
                    self.injected=True
                for key in self.failure_queue:
                    stop=tuple(int(v) for v in move['end'][key])
                    self.failed_actors[key]=dict(tick=self.execution_tick,cell=stop)
                    self.schedule.disabled[key]=stop
                self.failure_queue.clear()
                self._ack()
        if self.schedule:
            self.health.publish(String(data=json.dumps(dict(session=self.schedule.session,
                tick=self.execution_tick,actors=[k for k in self.schedule.tracks if k not in self.failed_actors]))))
        if self.worker_positions:
            self.workers=[list(c) for wid,c in sorted(self.worker_positions.items()) if f'w{wid}' not in self.failed_actors]
            for wid,(wx,wy) in self.worker_positions.items():
                if f'w{wid}' in self.failed_actors:continue
                p=Pose2D();p.x=float(wx);p.y=float(wy);self.worker_pos_pubs[wid].publish(p)
        for wid,charge in self.battery.items():
            msg=Float32();msg.data=float(charge);self.battery_pubs[wid].publish(msg)
        for rid in self.ids:
            t=self.targets[rid]
            if t:
                x,y,_=self.robots[rid]; dx=t[0]-x; dy=t[1]-y
                if abs(dx)+abs(dy)<.05: self.targets[rid]=None
                else:
                    d=math.hypot(dx,dy); step=min(.08,d); nx=x+dx/d*step; ny=y+dy/d*step
                    blocked=(nx<0 or ny<0 or nx>self.xmax or ny>self.ymax or
                             any(math.hypot(nx-float(o[0]),ny-float(o[1]))<.65 for o in self.obstacles) or
                             any(math.hypot(nx-float(w[0]),ny-float(w[1]))<.45 for w in self.workers) or
                             any(other!=rid and math.hypot(nx-self.robots[other][0],ny-self.robots[other][1])<.7 for other in self.ids))
                    if not blocked: self.robots[rid]=[nx,ny,math.atan2(dy,dx)]
            if f'r{rid}' in self.failed_actors:continue
            p=Pose2D(); p.x=self.robots[rid][0]; p.y=self.robots[rid][1]; p.theta=self.robots[rid][2]; self.pos_pubs[rid].publish(p)
        a=MarkerArray(); stamp=self.get_clock().now().to_msg()
        def marker(ns,i,typ,pos,scale,color):
            m=Marker(); m.header.frame_id='map'; m.header.stamp=stamp; m.ns=ns; m.id=i; m.type=typ; m.action=Marker.ADD
            m.pose.position.x,m.pose.position.y,m.pose.position.z=pos
            m.pose.orientation.w=1.; m.scale.x,m.scale.y,m.scale.z=scale
            m.color=ColorRGBA(r=color[0],g=color[1],b=color[2],a=color[3]); m.lifetime.sec=0
            return m
        a.markers.append(marker('ground',0,Marker.CUBE,(self.xmax*self.size/2,self.ymax*self.size/2,-.12),((self.xmax+1)*self.size,(self.ymax+1)*self.size,.1),(.38,.41,.45,1.)))
        from geometry_msgs.msg import Point
        lines=marker('map_grid',0,Marker.LINE_LIST,(0.,0.,-.055),(.018,0.,0.),(.20,.23,.27,1.))
        x0,y0=-.5*self.size,-.5*self.size
        for i in range(self.xmax+2):
            x=x0+i*self.size
            for xpt,ypt in ((x,y0),(x,(self.ymax+.5)*self.size)):
                p=Point();p.x=float(xpt);p.y=float(ypt);p.z=0.;lines.points.append(p)
        for i in range(self.ymax+2):
            y=y0+i*self.size
            for xpt,ypt in ((x0,y),( (self.xmax+.5)*self.size,y)):
                p=Point();p.x=float(xpt);p.y=float(ypt);p.z=0.;lines.points.append(p)
        a.markers.append(lines)
        for i,o in enumerate(self.obstacles): a.markers.append(marker('obstacle',i,Marker.CUBE,(float(o[0])*self.size,float(o[1])*self.size,.05),(.9*self.size,.9*self.size,.2),(.2,.2,.25,1.)))
        for i,w in enumerate(self.workers): a.markers.append(marker('worker',i,Marker.CYLINDER,(float(w[0])*self.size,float(w[1])*self.size,.45),(.48*self.size,.48*self.size,.75),(.15,.75,.25,1.)))
        for wid,cell in self.worker_positions.items():
            if f'w{wid}' in self.failed_actors or wid not in self.battery_specs:continue
            capacity=float(self.battery_specs[wid]['capacity'])
            percent=100.*self.battery[wid]/capacity if capacity else 0.
            color=(1.,.12,.08,1.) if percent<=20 else (.95,.72,.1,1.)
            label=marker('worker_battery',wid,Marker.TEXT_VIEW_FACING,
                (cell[0]*self.size,cell[1]*self.size,1.05),(0.,0.,.2),color)
            label.text=f'Worker {wid}: {percent:.0f}%';a.markers.append(label)
        colors=[(.1,.45,.95,1.),(.95,.25,.12,1.),(.7,.2,.85,1.),(.95,.65,.1,1.)]
        for j,rid in enumerate(self.ids):
            if f'r{rid}' in self.failed_actors:continue
            x,y,theta=self.robots[rid]; m=marker('recharger',rid,Marker.CUBE,(x*self.size,y*self.size,.4),(.65*self.size,.55*self.size,.55),colors[j%len(colors)])
            m.pose.orientation.z=math.sin(theta/2); m.pose.orientation.w=math.cos(theta/2); a.markers.append(m)
            label=marker('robot_label',rid,Marker.TEXT_VIEW_FACING,(x*self.size,y*self.size,.9),(0.,0.,.25),(.95,.95,.95,1.)); label.text=f'Recharger {rid}'; a.markers.append(label)
        for i,path in enumerate(self.plan_paths):
            m=marker('planned_path',i,Marker.LINE_STRIP,(0.,0.,.12),(.06,0.,0.),(.95,.8,.1,1.))
            for cell in path:
                p=Point(); p.x=float(cell[0])*self.size; p.y=float(cell[1])*self.size; p.z=.12; m.points.append(p)
            a.markers.append(m)
        if self.schedule:
            for key,track in self.schedule.tracks.items():
                if not key.startswith('w'):continue
                m=marker('worker_path',int(key[1:]),Marker.LINE_STRIP,(0.,0.,.10),(.045,0.,0.),(.1,.95,.8,1.))
                for cell in track['path']:
                    p=Point();p.x=float(cell[0])*self.size;p.y=float(cell[1])*self.size;p.z=0.;m.points.append(p)
                a.markers.append(m)
        for i,(key,event) in enumerate(sorted(self.failed_actors.items())):
            x,y=event['cell']
            a.markers.append(marker('failed_actor',i,Marker.CUBE,(x*self.size,y*self.size,.4),(.7,.7,.7),(.65,.05,.05,1.)))
            label=marker('failed_label',i,Marker.TEXT_VIEW_FACING,(x*self.size,y*self.size,1.),(0.,0.,.25),(1.,.2,.2,1.))
            label.text=f'{key} FAILED';a.markers.append(label)
        self.pub.publish(a)

    def _update_batteries(self,old_tick,move):
        if old_tick<0:return
        end=move['end']
        for wid,spec in self.battery_specs.items():
            key=f'w{wid}'
            if key not in end or key in self.failed_actors:continue
            period=int(spec['period'])
            t=((old_tick-self.schedule.setup_steps)%period)+1
            drain=int(spec['drains'][t]) if t<len(spec['drains']) else 0
            charge=self.battery[wid]-drain
            assigned=spec.get('charges',{}).get(str(t))
            worker_cell=end[key]
            available=[]
            for robot_key,robot_cell in end.items():
                if robot_key.startswith('r') and max(abs(worker_cell[0]-robot_cell[0]),abs(worker_cell[1]-robot_cell[1]))==1:
                    available.append(int(robot_key[1:]))
            if assigned is not None and (assigned==0 or assigned in available):
                charge=min(float(spec['capacity']),charge+int(spec['delta_max']))
            self.battery[wid]=max(0.,charge)
            if self.battery[wid]<=0 and not available:
                self.failure_queue.add(key)
def main(args=None):
    rclpy.init(args=args); n=MRPVisualizer()
    try:rclpy.spin(n)
    except KeyboardInterrupt:pass
    finally:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        n.destroy_node()
        if rclpy.ok():rclpy.shutdown()
if __name__=='__main__':main()
