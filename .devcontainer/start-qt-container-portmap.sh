#!/bin/bash

# Container name so we can exec into the running systemd container
CONTAINER_NAME="rqt-based-env"

# Grant local X server access
xhost +local:docker

# Remove any stale container with the same name (fresh start every run)
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

# Port mappings derived from the device configuration:
#
#   SyncMCmdPort=61001     -> other devices connect IN on 61001
#   FwdFlashQtrmPort=61002 -> other devices connect IN on 61002
#
# The "IP;port" entries (SPC, RCU, RDP, PLC, IOServer1/2) are connections the
# app makes OUT to remote devices, so they do not need a host->container map.
#
# NOTE: without --net=host, ROS 2 discovery between the container and the host
# does NOT work over multicast. Use start-qt-container.sh (--net=host) if you
# need container<->host ROS 2 communication.

# Start container with systemd as PID 1 so systemd tools
# (timedatectl, systemctl, ...) work inside the container.
docker run -d --rm \
  --name "$CONTAINER_NAME" \
  -p 61001:61001 \
  -p 61002:61002 \
  --ipc=host \
  --privileged \
  --cgroupns=host \
  --tmpfs /run \
  --tmpfs /run/lock \
  --tmpfs /tmp \
  -v /sys/fs/cgroup:/sys/fs/cgroup:rw \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  --gpus all \
  --device=/dev/dri \
  -e NVIDIA_DRIVER_CAPABILITIES=all \
  -v "$(pwd)/fastdds-profile.xml:/opt/ros/fastdds-profile.xml:ro" \
  -e FASTDDS_DEFAULT_PROFILES_FILE=/opt/ros/fastdds-profile.xml \
  -e ROS_DOMAIN_ID=0 \
  -v "$(pwd)":/workspace \
  -v ~/.docker-qtcreator/config:/root/.config/QtProject \
  -v ~/.docker-qtcreator/share:/root/.local/share/QtProject \
  -v ~/.docker-qtcreator/cache:/root/.cache/QtProject \
  rqt-based-env:latest \
  /sbin/init

if [ $? -ne 0 ]; then
  echo "Failed to start container '$CONTAINER_NAME'." >&2
  exit 1
fi

# Wait for systemd to finish booting
echo "Waiting for systemd to boot..."
for i in $(seq 1 60); do
  state=$(docker exec "$CONTAINER_NAME" systemctl is-system-running 2>/dev/null || true)
  if [ "$state" = "running" ] || [ "$state" = "degraded" ]; then
    echo "systemd is running ($state)"
    break
  fi
  sleep 1
done

# Sync the host timezone so `date` and `timedatectl` report the right zone
HOST_TZ=$(cat /etc/timezone 2>/dev/null || readlink /etc/localtime | sed 's#.*/zoneinfo/##')
if [ -n "$HOST_TZ" ]; then
  docker exec "$CONTAINER_NAME" timedatectl set-timezone "$HOST_TZ" >/dev/null 2>&1
fi

echo ""
echo "Container '$CONTAINER_NAME' is running (port-mapped, no --net=host)."
echo "Opening an interactive shell (exit to leave; the container keeps running)."
docker exec -it "$CONTAINER_NAME" bash

echo ""
echo "Useful commands:"
echo "  docker exec -it $CONTAINER_NAME bash   # open another shell"
echo "  docker stop $CONTAINER_NAME            # stop the container"