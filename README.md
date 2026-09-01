# ROS 2 & Qt Development Docker Environment (`rqt-based-env`)

A comprehensive Docker environment based on **Ubuntu 24.04 LTS** pre-configured for robotics software development, computer vision, and GUI applications. It integrates **ROS 2 Rolling**, **Qt 6.7.2**, **Boost 1.83.0**, hardware interfacing tools, and full X11/NVIDIA GPU acceleration.

---

## 📑 Table of Contents

- [Features](#-features)
- [Repository Structure](#-repository-structure)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
  - [1. Build the Docker Image](#1-build-the-docker-image)
  - [2. Run the Container with GUI Support](#2-run-the-container-with-gui-support)
  - [3. Run a Quick Terminal Session](#3-run-a-quick-terminal-session)
- [ROS 2 & Fast DDS Communication](#-ros-2--fast-dds-communication)
- [Mounted Volumes & Persistent Data](#-mounted-volumes--persistent-data)
- [Documentation & External Links](#-documentation--external-links)

---

## ✨ Features

- **Base OS**: Ubuntu 24.04 LTS (Noble Numbat)
- **Robotics Middleware**: ROS 2 Rolling (`ros-rolling-desktop`) with Fast DDS configured for UDP-based host-container communication.
- **GUI & IDE**: Qt 6.7.2 and Qt Creator with X11 forwarding and NVIDIA GPU acceleration (`--gpus all`).
- **System Service Management**: Runs `systemd` as PID 1 inside the container (`/sbin/init`), allowing standard service management (`systemctl`, `timedatectl`).
- **Development & Build Tools**: CMake, Ninja, Mold linker, GCC, Clang, ccache, colcon, gedit, Terminator.
- **Libraries & Hardware Interfacing**:
  - Boost 1.83.0 (built from source)
  - Snap7 (`libsnap7.so`) for Siemens PLC communication
  - RTL-SDR for software-defined radio development
  - Google Log (`glog`), Protobuf, ZeroMQ (`cppzmq`), SQLite3, GDAL, OpenGL/FreeGLUT
  - JetBrains Mono & Roboto Condensed developer fonts
  - Digital map datasets pre-extracted to `/opt/digital-map`

---

## 📁 Repository Structure

| File / Directory | Description |
| :--- | :--- |
| [`dockerfile`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/dockerfile) | Dockerfile recipe building `rqt-based-env:latest`. |
| [`build-docker-img.sh`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/build-docker-img.sh) | Script to package local Qt environment into `mlib3rd/Qt.tar.gz` and trigger `docker build`. |
| [`start-qt-container.sh`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/start-qt-container.sh) | Main launcher script starting the container with X11, GPU support, systemd init, FastDDS profile, and persistent volume mounts. |
| [`start-terminal-session.sh`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/start-terminal-session.sh) | Launches a temporary, lightweight interactive `bash` shell inside the container. |
| [`ros2-listener-host.sh`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/ros2-listener-host.sh) | Helper script to run a ROS 2 listener on the host using the UDP Fast DDS profile. |
| [`fastdds-profile.xml`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/fastdds-profile.xml) | Fast DDS configuration profile explicitly using UDPv4 transport to bypass shared memory limits. |
| [`docker-install-guide.md`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/docker-install-guide.md) | Detailed documentation on Docker setup, NVIDIA container toolkit installation, systemd init, and Fast DDS rationale. |
| [`mlib3rd/`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/mlib3rd) | Third-party archives (Boost 1.83.0, Qt 6.7.2, RTL-SDR, Snap7, fonts, digital map). |
| [`docker-img/`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/docker-img) | Saved tarball images (e.g. `rqt-based-env.tar`). |

---

## 🛠️ Prerequisites

Before building or running the container, ensure your host system satisfies the following requirements:

1. **Docker**: Installed and running on Linux.
2. **NVIDIA Container Toolkit**: Required for GPU acceleration (see [`docker-install-guide.md`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/docker-install-guide.md#2-prerequisites) for step-by-step setup).
3. **X11 Display**: A running X server on the host for GUI applications.
4. **Qt 6.7.2 on Host**: Local installation at `$HOME/Qt` if building from scratch via `build-docker-img.sh`.

---

## 🚀 Quick Start

### 1. Build the Docker Image

To archive local Qt dependencies (if not already archived in `mlib3rd/Qt.tar.gz`) and build the image:

```bash
./build-docker-img.sh
```

This generates the Docker image tagged as `rqt-based-env:latest`.

### 2. Run the Container with GUI Support

To start the container with full GUI forwarding, systemd init, GPU access, and persistent volume mounts:

```bash
./start-qt-container.sh
```

This script will:
- Enable local Docker connections to the host X server (`xhost +local:docker`).
- Launch `rqt-based-env` with `--net=host`, `--gpus all`, and `fastdds-profile.xml`.
- Boot `systemd` as PID 1 and synchronize timezone settings.
- Automatically attach an interactive `bash` shell.

To open additional terminal instances into the running container:
```bash
docker exec -it rqt-based-env bash
```

To stop the container:
```bash
docker stop rqt-based-env
```

### 3. Run a Quick Terminal Session

To spin up a temporary, disposable container without systemd:

```bash
./start-terminal-session.sh
```

---

## 📡 ROS 2 & Fast DDS Communication

Because the container runs in isolated IPC space, **Fast DDS default shared memory transport will fail to communicate across host/container boundaries**.

To ensure seamless discovery and data transfer between host ROS 2 nodes and container ROS 2 nodes, transport is routed strictly over **UDPv4** using `fastdds-profile.xml`.

- Inside the container, [`start-qt-container.sh`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/start-qt-container.sh) automatically sets `FASTDDS_DEFAULT_PROFILES_FILE=/opt/ros/fastdds-profile.xml`.
- On the host, set the profile before running ROS 2 nodes:
  ```bash
  export FASTDDS_DEFAULT_PROFILES_FILE=$(pwd)/fastdds-profile.xml
  export ROS_DOMAIN_ID=0
  ```
  Or run the provided script:
  ```bash
  ./ros2-listener-host.sh
  ```

---

## 💾 Mounted Volumes & Persistent Data

When running via [`start-qt-container.sh`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/start-qt-container.sh), the following locations are mounted:

- Host workspace `/home/trungnb/workspace/my-work` $\rightarrow$ Container `/workspace`
- Qt Creator settings are persisted on host under `~/.docker-qtcreator/` (`config`, `share`, `cache`).

---

## 📚 Documentation & External Links

For comprehensive details on host NVIDIA setup, Fast DDS configuration rationale, and troubleshooting, consult [`docker-install-guide.md`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/docker-install-guide.md).

- **Google Drive LFS Files & Pre-built Image**:
  - [LFS Dependencies Folder](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link)
  - [Pre-built Docker Image Archive](https://drive.google.com/drive/folders/1LTTGTxSGOOlnBSf-TOyOVT7iUzzliVHR?usp=drive_link)
