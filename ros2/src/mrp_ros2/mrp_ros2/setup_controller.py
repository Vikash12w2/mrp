from __future__ import annotations

try:
    import rclpy
    from rclpy.node import Node
    from geometry_msgs.msg import Pose2D
except ImportError as exc:  # allow importing package outside a ROS environment
    raise ImportError("mrp_ros2.setup_controller requires ROS 2 rclpy and geometry_msgs") from exc


class Ros2SetupNode(Node):
    def __init__(self):
        super().__init__("mrp_setup_controller")
        self.declare_parameter("robot_ids", [1])
        self.declare_parameter("telemetry_timeout", 2.0)
        self._positions = {}
        self._received_at = {}
        self._publishers = {}
        for rid in self.get_parameter("robot_ids").value:
            rid = int(rid)
            topic = f"/mrp/robots/robot_{rid}"
            self._publishers[rid] = self.create_publisher(Pose2D, topic + "/cmd_position", 10)
            self.create_subscription(Pose2D, topic + "/position", self._callback(rid), 10)

    def _callback(self, rid):
        def receive(msg):
            self._positions[rid] = (round(msg.x), round(msg.y))
            self._received_at[rid] = self.get_clock().now()
        return receive

    def command(self, rid, destination):
        msg = Pose2D()
        msg.x, msg.y = float(destination[0]), float(destination[1])
        self._publishers[int(rid)].publish(msg)

    def position(self, rid):
        rid = int(rid)
        if rid not in self._positions:
            return None
        age = (self.get_clock().now() - self._received_at[rid]).nanoseconds / 1e9
        return self._positions[rid] if age <= float(self.get_parameter("telemetry_timeout").value) else None


def main(args=None):
    rclpy.init(args=args)
    node = Ros2SetupNode()
    node.controller = node
    try:
        rclpy.spin(node)
    except KeyboardInterrupt:
        pass
    finally:
        import signal
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        node.destroy_node()
        if rclpy.ok():
            rclpy.shutdown()


if __name__ == "__main__":
    main()
