import json
import os
from launch import LaunchDescription
from launch.actions import DeclareLaunchArgument, IncludeLaunchDescription, OpaqueFunction
from launch.launch_description_sources import PythonLaunchDescriptionSource
from launch.substitutions import LaunchConfiguration
from ament_index_python.packages import get_package_share_directory


def include(context):
    share=get_package_share_directory('mrp_ros2')
    name=LaunchConfiguration('scenario').perform(context)
    with open(os.path.join(share,'config','scenarios','scenarios.json')) as source:
        scenarios=json.load(source)
    if name not in scenarios:
        raise ValueError('scenario must be one of '+', '.join(scenarios))
    settings=scenarios[name]
    args=dict(input_dir=os.path.join(share,'config','scenarios',name),
              anchors=json.dumps(settings['anchors']),R_s=str(settings['R_s']),
              cap_util=str(settings['cap_util']),
              rviz=LaunchConfiguration('rviz').perform(context),
              step_period=LaunchConfiguration('step_period').perform(context))
    for key in ('failure_kind','failure_id','failure_after_cycles'):
        args[key]=LaunchConfiguration(key).perform(context)
    return [IncludeLaunchDescription(PythonLaunchDescriptionSource(os.path.join(share,'launch','mrp_full.launch.py')),launch_arguments=args.items())]


def generate_launch_description():
    return LaunchDescription([
        DeclareLaunchArgument('failure_kind',default_value=''),
        DeclareLaunchArgument('failure_id',default_value='1'),
        DeclareLaunchArgument('failure_after_cycles',default_value='2'),
        DeclareLaunchArgument('scenario',default_value='four_workers'),
        DeclareLaunchArgument('rviz',default_value='true'),
        DeclareLaunchArgument('step_period',default_value='1.0'),
        OpaqueFunction(function=include),
    ])
