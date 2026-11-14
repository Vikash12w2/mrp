import importlib
import json
import sys
import uuid
import threading
from pathlib import Path
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, ReliabilityPolicy, DurabilityPolicy, HistoryPolicy
from std_msgs.msg import String

class PlannerNode(Node):
    def __init__(self):
        super().__init__('mrp_planner')
        self.declare_parameter('source_root', '')
        self.declare_parameter('input_dir', '')
        self.declare_parameter('anchors', '') # JSON object, e.g. {"1":[2,2],"2":[8,8]}
        self.declare_parameter('workers', 0)
        self.declare_parameter('rechargers', 0)
        self.declare_parameter('T', 0)
        self.declare_parameter('R_s', 6)
        self.declare_parameter('cap_util', 1.5)
        plan_qos=QoSProfile(history=HistoryPolicy.KEEP_LAST,depth=1,
                            reliability=ReliabilityPolicy.RELIABLE,
                            durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.pub=self.create_publisher(String,'/mrp/planner/result',plan_qos)
        self.last_result=None
        self.result_successful=False
        self.started=False
        self.result=None
        self.recovery_thread=None
        self.create_subscription(String,'/mrp/recovery/request',self._recover,10)
    def _start(self):
        if self.started:return
        self.started=True
        originals=[]
        try:
            try:
                import z3  # noqa: F401
            except ImportError as exc:
                raise RuntimeError('Z3 is missing from the ROS Python environment. Install it with: sudo apt install python3-z3') from exc
            configured_root=self.get_parameter('source_root').value
            if configured_root:
                root=Path(configured_root).expanduser().resolve()
            else:
                root=next((p for p in Path(__file__).resolve().parents
                           if (p/'decentralized.py').is_file() and (p/'model.py').is_file()),None)
                if root is None:
                    raise ValueError('Set source_root to the original planner directory')
            sys.path.insert(0,str(root.parent))
            self.mrp_module=root.name
            importlib.import_module(root.name)
            model=importlib.import_module(root.name+'.model')
            params=importlib.import_module(root.name+'.params')
            from .ros_bus import RosBus
            from functools import partial
            dec=importlib.import_module(root.name+'.decentralized')
            originals.append((dec,dec.Bus))
            dec.Bus=partial(RosBus,node=self)
            failure=importlib.import_module(root.name+'.failure')
            originals.append((failure,failure.Bus))
            failure.Bus=partial(RosBus,node=self)
            moddeploy=importlib.import_module(root.name+'.deploy')
            input_dir=self.get_parameter('input_dir').value
            if not input_dir: raise ValueError('Set input_dir to a directory containing workspace.txt, obstacle.txt, 2d_template.txt and robN_traj.txt')
            inst=model.load_instance(input_dir,int(self.get_parameter('workers').value) or None,int(self.get_parameter('rechargers').value) or None,int(self.get_parameter('T').value) or None)
            raw=self.get_parameter('anchors').value
            anchors={int(k):tuple(v) for k,v in json.loads(raw).items()} if raw else moddeploy.pick_anchors(inst,inst.n_rech,int(self.get_parameter('R_s').value))
            if set(anchors) != set(range(1, inst.n_rech+1)):
                raise ValueError('anchors must contain exactly one entry for each configured recharger')
            if len(set(anchors.values())) != len(anchors):
                raise ValueError('Recharger anchors must be distinct')
            P=params.Params(R_s=int(self.get_parameter('R_s').value),
                            cap_util=float(self.get_parameter('cap_util').value),
                            phase2_optimize_cost=True)
            result=dec.run_decentralized(inst,anchors,P)
            self._publish_result(result)
        except Exception as e:
            self.get_logger().error(f'Planner failed: {e}')
            self.last_result=json.dumps({'status':'ERROR','error':str(e),'paths':[]})
            self.pub.publish(String(data=self.last_result))
        finally:
            for module, original in reversed(originals):
                module.Bus=original


    def _publish_result(self,result,**extra):
        inst=result.inst; anchors=result.anchors
        model=importlib.import_module(self.mrp_module+'.model')
        if result.grouping.unclaimed or result.conflicts:
            raise ValueError(f'Unsafe/incomplete plan: unclaimed={result.grouping.unclaimed}, conflicts={result.conflicts[:5]}')
        if not result.deployment or not result.deployment.plan_ready:
            raise ValueError('No safe setup route exists for every recharger')
        if any(g.workers and not g.live for g in result.plans.values()):
            raise ValueError('One or more worker groups have no feasible hypercycle')
        paths=[]
        tracks={}
        setup={}
        setup_sweeps={}
        for rid, g in sorted(result.plans.items()):
            key=f'r{rid}'
            route=result.deployment.paths[rid]
            if not route.ok:
                raise ValueError(f'Recharger {rid}: setup is blocked')
            setup[key]=route.cells
            setup_sweeps[key]=[sorted(s) for s in route.sweeps]
            if g.live:
                tracks[key]={'path':g.res.rpos[1][1:], 'sweeps':[sorted(s) for s in g.res.rsweep[1][1:]]}
                for wid, positions in g.res.wpos.items():
                    sweeps=g.res.wsweep.get(wid)
                    if sweeps is None:
                        worker=next(w for w in inst.workers if w.id==wid)
                        sweeps=[None]+[model.worker_transition_cells(inst,worker,a,b) for a,b in zip(positions[1:-1],positions[2:])]
                    tracks[f'w{wid}']={'path':positions[1:], 'sweeps':[sorted(s) for s in sweeps[1:]]}
            else:
                tracks[key]={'path':[anchors[rid],anchors[rid]], 'sweeps':[[anchors[rid]]]}
        if {int(k[1:]) for k in tracks if k.startswith('w')} != {w.id for w in inst.workers}:
            raise ValueError('Plan does not cover all workers')
        for rid,g in sorted(result.plans.items()):
            setup_route=result.deployment.paths[rid]
            setup_cells=setup_route.cells
            if g.live: cells=list(setup_cells)+list(g.res.rpos[1][2:])
            else: cells=list(setup_cells)
            paths.append([[int(c[0]),int(c[1])] for c in cells])
        worker_paths={str(wid):[[int(c[0]),int(c[1])] for c in positions[1:]]
                      for g in result.plans.values() if g.live
                      for wid,positions in g.res.wpos.items()}
        payload={'status':'OK' if all((not g.workers) or g.live for g in result.plans.values()) else 'PARTIAL','anchors':{str(k):list(v) for k,v in anchors.items()},'paths':paths,'worker_paths':worker_paths,'groups':{str(k):list(v) for k,v in result.grouping.groups.items()},'conflicts':result.conflicts,'metrics':result.metrics(),'map':{'xmax':inst.xmax,'ymax':inst.ymax,'obstacles':[list(c) for c in sorted(inst.obstacles)],'workers':[list(w.loop[0]) for w in inst.workers]}}
        payload['metrics']['deployment_ready']=False
        payload['metrics']['deployment_status']='PLANNED_AWAITING_ROS_EXECUTION'
        payload['heartbeat_policy']={'period':result.P.hb_period,'missed':result.P.k_missed}
        payload.update(schema=2, session=uuid.uuid4().hex, tracks=tracks,
                       setup=setup, setup_sweeps=setup_sweeps,
                       setup_confirmed=False)
        # Validate the executable contract before publishing. Physical setup is
        # confirmed later by the ROS executor; planner deployment is a projection.
        from .schedule import Schedule
        Schedule(payload)
        payload.update(extra)
        self.result=result
        self.last_result=json.dumps(payload,default=str)
        self.result_successful=True
        self.pub.publish(String(data=self.last_result))
        self.get_logger().info(f"Planner finished: {payload['status']}; published {len(paths)} recharger paths on /mrp/planner/result")

    def _recover(self,msg):
        if self.recovery_thread and self.recovery_thread.is_alive():
            return
        self.recovery_thread=threading.Thread(target=self._recover_job,args=(msg,),daemon=True)
        self.recovery_thread.start()

    def _recover_job(self,msg):
        """Invoke Version A recovery at a requested old-plan cycle boundary."""
        from functools import partial
        from dataclasses import asdict
        from .ros_bus import RosBus
        try:
            request=json.loads(msg.data)
            if self.result is None or request['session']!=json.loads(self.last_result).get('session'):
                return
            self.get_logger().info('Version A recovery started; existing safe motion continues to the boundary')
            failure=importlib.import_module(self.mrp_module+'.failure')
            deploy=importlib.import_module(self.mrp_module+'.deployment')
            planner=importlib.import_module(self.mrp_module+'.planner')
            original=failure.Bus
            try:
                failure.Bus=partial(RosBus,node=self)
                events=[failure.Failure(e['kind'],int(e['id']),int(e['t'])) for e in request['failures']]
                recovered,reports=failure.recover(self.result,events)
            finally:
                failure.Bus=original
            if not recovered.anchors:
                raise ValueError('No surviving recharger remains')
            # The source assumes fresh worker start states at the boundary. Do
            # not invent battery state or teleport robots to newly selected starts.
            # Route surviving rechargers from their OLD cycle start to the NEW
            # one with the original primitive setup router in their fixed regions.
            paths={}
            starts={int(k[1:]):tuple(v) for k,v in request['boundary_positions'].items() if k.startswith('r') and int(k[1:]) in recovered.anchors}
            for rid,g in sorted(recovered.plans.items()):
                static={w.loop[0] for w in recovered.inst.workers}
                static.update(starts[n] for n in starts if n>rid)
                goal=g.res.rpos[1][1] if g.live else recovered.anchors[rid]
                route=deploy._route(rid,starts[rid],goal,recovered.inst,
                    planner.box_of(recovered.inst,recovered.anchors[rid],recovered.P.R_s),static,list(paths.values()))
                if not route.ok:
                    raise ValueError(f'Recovery setup blocked for recharger {rid}: {route.reason}')
                paths[rid]=route
            recovered.deployment=deploy.Deployment(paths,{}, {r:True for r in paths})
            self._publish_result(recovered,parent_session=request['session'],
                boundary_tick=request['boundary_tick'],recovery_reports=[asdict(r) for r in reports],
                failed=request['failed'])
        except Exception as exc:
            self.get_logger().error(f'Recovery failed: {exc}')
            self.pub.publish(String(data=json.dumps(dict(status='ERROR',error=str(exc),
                parent_session=request.get('session') if 'request' in locals() else None))))


def main(args=None):
    rclpy.init(args=args); n=PlannerNode()
    try:n._start(); rclpy.spin(n)
    except KeyboardInterrupt: pass
    finally:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        if n.recovery_thread:
            n.recovery_thread.join(timeout=2.)
        for bus in getattr(n,'transport_buses',[]):
            bus.close()
        n.destroy_node()
        if rclpy.ok():rclpy.shutdown()
if __name__=='__main__':main()
