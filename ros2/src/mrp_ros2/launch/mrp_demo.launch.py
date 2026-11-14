from launch import LaunchDescription
from launch_ros.actions import Node
from ament_index_python.packages import get_package_share_directory
import os

def generate_launch_description():
    share=get_package_share_directory('mrp_ros2')
    return LaunchDescription([
        Node(package='tf2_ros', executable='static_transform_publisher', name='map_frame', arguments=['--x','0','--y','0','--z','0','--yaw','0','--pitch','0','--roll','0','--frame-id','map','--child-frame-id','mrp_world'], output='screen'),
        Node(package='mrp_ros2',executable='visualizer',name='mrp_visualizer',output='screen',parameters=[os.path.join(share,'config','demo.yaml')]),
        Node(package='rviz2',executable='rviz2',name='rviz2',arguments=['-d',os.path.join(share,'rviz','mrp.rviz')],output='screen'),
    ])
