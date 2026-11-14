from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument
from launch.conditions import IfCondition
from launch.substitutions import LaunchConfiguration
from launch_ros.actions import Node
from launch_ros.parameter_descriptions import ParameterValue
from ament_index_python.packages import get_package_share_directory
import os

def generate_launch_description():
    share=get_package_share_directory('mrp_ros2')
    return LaunchDescription([
        DeclareLaunchArgument('failure_kind',default_value='',description='Optional automatic simulated failure: worker or recharger'),
        DeclareLaunchArgument('failure_id',default_value='1'),
        DeclareLaunchArgument('failure_after_cycles',default_value='2'),
        DeclareLaunchArgument('rviz',default_value='true',description='Open RViz; false runs headless'),
        DeclareLaunchArgument('input_dir',default_value=os.path.join(share,'config','sample_input'),description='MRP input directory (defaults to the bundled small sample)'),
        DeclareLaunchArgument('source_root',default_value='',description='Path to the existing Python planner repository root'),
        DeclareLaunchArgument('R_s',default_value='4',description='Sensing/planning radius (sample default: 4)'),
        DeclareLaunchArgument('cap_util',default_value='4.0',description='Worker utilization capacity (sample default: 4.0)'),
        DeclareLaunchArgument('anchors',default_value='',description='Physical anchors as JSON; empty selects anchors using the original deployment algorithm'),
        DeclareLaunchArgument('step_period',default_value='1.0',description='Seconds per logical simulation step'),
        Node(package='tf2_ros',executable='static_transform_publisher',name='map_frame',arguments=['--x','0','--y','0','--z','0','--yaw','0','--pitch','0','--roll','0','--frame-id','map','--child-frame-id','mrp_world'],output='screen'),
        Node(package='mrp_ros2',executable='visualizer',name='mrp_visualizer',parameters=[os.path.join(share,'config','demo.yaml'),{'robot_ids':[1], 'failure_kind':ParameterValue(LaunchConfiguration('failure_kind'),value_type=str), 'failure_id':ParameterValue(LaunchConfiguration('failure_id'),value_type=int), 'failure_after_cycles':ParameterValue(LaunchConfiguration('failure_after_cycles'),value_type=int)}],output='screen'),
        Node(package='mrp_ros2',executable='plan_executor',name='mrp_plan_executor',parameters=[{'step_period':ParameterValue(LaunchConfiguration('step_period'),value_type=float)}],output='screen'),
        Node(package='mrp_ros2',executable='mrp_planner',name='mrp_planner',parameters=[{'input_dir':LaunchConfiguration('input_dir')},{'source_root':LaunchConfiguration('source_root')},{'R_s':LaunchConfiguration('R_s')},{'cap_util':LaunchConfiguration('cap_util')},{'anchors':ParameterValue(LaunchConfiguration('anchors'),value_type=str)}],output='screen'),
        Node(package='rviz2',executable='rviz2',name='rviz2',condition=IfCondition(LaunchConfiguration('rviz')),arguments=['-d',os.path.join(share,'rviz','mrp.rviz')],output='screen'),
    ])
