import json
import math
import time
import rclpy
from rclpy.node import Node
from rclpy.qos import QoSProfile, DurabilityPolicy
from std_msgs.msg import String
from .schedule import Schedule
from .recovery_timing import declaration_tick


class PlanExecutor(Node):
    def __init__(self):
        super().__init__('mrp_plan_executor')
        self.declare_parameter('step_period', 1.0)
        self.declare_parameter('feedback_timeout', 5.0)
        self.schedule = None
        self.tick = 0
        self.pending = None
        self.sent_at = self.last_send = self.ready_at = 0.0
        self.failed = False
        self.failures = {}
        self.heartbeat = {}
        self.recovery = None
        self.replacement = None
        self.hb_period = 1
        self.k_missed = 3
        self.commands = self.create_publisher(String, '/mrp/execution/command', 10)
        self.recovery_pub = self.create_publisher(String, '/mrp/recovery/request', 10)
        qos = QoSProfile(depth=1, durability=DurabilityPolicy.TRANSIENT_LOCAL)
        self.status = self.create_publisher(String, '/mrp/execution/status', qos)
        self.create_subscription(String, '/mrp/planner/result', self._result, qos)
        self.create_subscription(String, '/mrp/execution/feedback', self._feedback, 10)
        self.create_subscription(String, '/mrp/simulation/heartbeat', self._heartbeat, 10)
        self.create_timer(.03, self._tick)

    def report(self, state, reason=''):
        self.status.publish(String(data=json.dumps(dict(state=state, reason=reason,
            session=self.schedule.session if self.schedule else None, tick=self.tick))))

    def fail(self, reason):
        self.failed = True
        self.report('STOPPED', reason)
        self.get_logger().error(reason)

    def _result(self, msg):
        try:
            data = json.loads(msg.data)
            if self.schedule:
                if data.get('session') == self.schedule.session:
                    return
                if data.get('status') == 'ERROR':
                    raise ValueError(data.get('error','Planner failed'))
                if not self.recovery or data.get('parent_session') != self.schedule.session:
                    raise ValueError('Unsolicited plan replacement')
                Schedule(data)
                self.replacement = data
                return
            self.install(data)
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(f'Plan rejected: {exc}')

    def install(self, data):
        new = Schedule(data)
        if self.schedule:
            current = self.schedule.frame(self.tick)
            if new.frame(0) != {k:v for k,v in current.items() if k in new.tracks}:
                raise ValueError('Recovery setup would teleport an actor')
        policy=data.get('heartbeat_policy',{'period':1,'missed':3})
        self.hb_period=int(policy['period']);self.k_missed=int(policy['missed'])
        if min(self.hb_period,self.k_missed)<1:raise ValueError('Invalid heartbeat policy')
        self.schedule = new
        self.tick = 0
        self.failed = False
        self.failures = {}
        self.heartbeat = {k:-1 for k in new.tracks}
        self.recovery = self.replacement = None
        self.dispatch(0, new.frame(0))

    def dispatch(self, tick, positions):
        duration = max(.05,float(self.get_parameter('step_period').value))
        self.pending = dict(session=self.schedule.session, tick=tick,
                            positions=positions, duration=duration)
        self.sent_at = self.last_send = time.monotonic()
        self.commands.publish(String(data=json.dumps(self.pending)))
        self.report('SETUP' if tick <= self.schedule.setup_steps else 'RUNNING')

    def _heartbeat(self, msg):
        try:
            data=json.loads(msg.data)
            if not self.schedule or data.get('session')!=self.schedule.session:
                return
            for key in data['actors']:
                if key in self.schedule.tracks:
                    self.heartbeat[key]=max(self.heartbeat.get(key,-1),int(data['tick']))
        except (ValueError,KeyError,TypeError):
            pass

    def _feedback(self, msg):
        if self.failed or not self.pending:
            return
        try:
            data = json.loads(msg.data)
            if (data.get('session'), data.get('tick')) != (self.pending['session'], self.pending['tick']):
                return
            if data.get('error'):
                raise ValueError(data['error'])
            for key,event in data.get('failed',{}).items():
                if key in self.schedule.tracks and key not in self.failures:
                    # Fail-stop simulation applies failures at completed waypoints.
                    # The failed actor remains a stationary obstacle from now on.
                    if event['tick'] < self.schedule.setup_steps:
                        raise ValueError('A failure during setup requires a new initial deployment')
                    if tuple(event['cell']) != self.schedule.frame(event['tick']).get(key):
                        raise ValueError('Failure stop cell disagrees with the source schedule')
                    self.failures[key]=event
                    self.schedule.disabled[key]=tuple(event['cell'])
            measured = {k:tuple(v) for k,v in data['positions'].items() if k not in self.failures}
            expected = {k:tuple(v) for k,v in self.pending['positions'].items() if k not in self.failures}
            if measured != expected:
                raise ValueError('Feedback disagrees with the commanded frame')
            self.tick = self.pending['tick']
            self.pending = None
            self.ready_at = time.monotonic()
            self.report('SETUP' if self.tick < self.schedule.setup_steps else 'RUNNING')
        except (ValueError, KeyError, TypeError) as exc:
            self.fail(f'Execution feedback rejected: {exc}')

    def declared_tick(self, stop_tick):
        return declaration_tick(stop_tick,self.schedule.setup_steps,self.hb_period,self.k_missed)

    def request_recovery(self):
        per=math.lcm(*(len(t['path'])-1 for t in self.schedule.tracks.values()))
        elapsed=max(0,self.tick-self.schedule.setup_steps)
        boundary=self.schedule.setup_steps+((elapsed+per-1)//per)*per
        if boundary==self.tick and self.pending:
            boundary+=per
        self.recovery=dict(session=self.schedule.session,boundary_tick=boundary,
            boundary_positions=self.schedule.frame(boundary),
            failures=[dict(kind='worker' if k.startswith('w') else 'recharger',
                           id=int(k[1:]),t=e['tick']-self.schedule.setup_steps+1)
                      for k,e in self.failures.items()],failed=self.failures)
        self.recovery_pub.publish(String(data=json.dumps(self.recovery)))
        self.report('REPLANNING','Version A failure.recover requested; activation at the common cycle boundary')

    def _tick(self):
        if self.failed or not self.schedule:
            return
        now = time.monotonic()
        if self.failures and not self.recovery and not self.pending:
            if all(self.tick>=self.declared_tick(e['tick']) and self.heartbeat.get(k,-1)<=e['tick'] for k,e in self.failures.items()):
                try:self.request_recovery()
                except ValueError as exc:self.fail(str(exc))
        if self.pending:
            # Long waypoint moves are not failures. Silence budget begins after
            # the commanded travel duration; health messages continue during motion.
            timeout=max(float(self.get_parameter('feedback_timeout').value),self.pending['duration']+1.)
            if now-self.sent_at > timeout:
                self.fail('Simulator feedback lost: no confirmed fail-stop cell; refusing to invent a robot failure')
            elif now-self.last_send > .5:
                self.commands.publish(String(data=json.dumps(self.pending)))
                self.last_send = now
        elif self.recovery and self.tick>=self.recovery['boundary_tick']:
            if self.replacement:
                try:self.install(self.replacement)
                except ValueError as exc:self.fail(str(exc))
            else:self.report('RECOVERY_WAIT','At cycle boundary; waiting for SMT recovery')
        elif now >= self.ready_at:
            try:
                self.dispatch(self.tick+1, self.schedule.transition(self.tick))
            except ValueError as exc:
                if self.failures:
                    # A fail-stop cell can obstruct the old route. Never drive
                    # through it just to reach the source model's restart boundary.
                    self.fail(f'Cannot safely reach recovery boundary: {exc}')
                else:self.fail(str(exc))


def main(args=None):
    rclpy.init(args=args)
    node = PlanExecutor()
    try:rclpy.spin(node)
    except KeyboardInterrupt:pass
    finally:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.destroy_node()
        if rclpy.ok():rclpy.shutdown()
