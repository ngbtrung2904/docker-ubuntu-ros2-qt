#!/bin/bash

# Setup ROS2 environment
source /opt/ros/rolling/setup.bash

# Configure FASTDDS
export FASTDDS_DEFAULT_PROFILES_FILE=./fastdds-profile.xml
export ROS_DOMAIN_ID=0

# Start listener node
ros2 run demo_nodes_cpp listener