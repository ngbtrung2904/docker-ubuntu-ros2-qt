# ROS 2 & Qt Development Docker Environment (`rqt-based-env`)

A comprehensive Docker environment based on **Ubuntu 24.04 LTS** pre-configured for robotics software development, computer vision, and GUI applications. It integrates **ROS 2 Rolling**, **Qt 6.7.2**, **Boost 1.83.0**, hardware interfacing tools, and full X11/NVIDIA GPU acceleration.

---

## 📑 Table of Contents

- [Features](#-features)
- [Project Structure](#-project-structure)
- [LFS & Pre-built Image Downloads](#-lfs--pre-built-image-downloads)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
  - [1. Build the Docker Image](#1-build-the-docker-image)
  - [2. Run the Container with GUI Support](#2-run-the-container-with-gui-support)
  - [3. Run a Quick Terminal Session](#3-run-a-quick-terminal-session)
- [ROS 2 & Fast DDS Communication](#-ros-2--fast-dds-communication)
- [Mounted Volumes & Persistent Data](#-mounted-volumes--persistent-data)
- [Appearance: Matching the Host Desktop](#-appearance-matching-the-host-desktop)
- [Audio: Sound Devices and the Host Audio Server](#-audio-sound-devices-and-the-host-audio-server)
- [Documentation & External Links](#-documentation--external-links)

---

## ✨ Features

- **Base OS**: Ubuntu 24.04 LTS (Noble Numbat)
- **Robotics Middleware**: ROS 2 Rolling (`ros-rolling-desktop`) with Fast DDS configured for UDP-based host-container communication.
- **GUI & IDE**: Qt 6.7.2 and Qt Creator with X11 forwarding and NVIDIA GPU acceleration (`--gpus all`).
- **Host-matched Appearance**: GUI apps pick up the host desktop's GTK theme, icon set, cursor and fonts (dark mode included) - see [Appearance](#-appearance-matching-the-host-desktop).
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

## 📁 Project Structure

```
qt-based-img/
├── dockerfile                          # Image build recipe (Ubuntu 24.04 + ROS 2 Rolling + Qt 6.7.2)
├── docker-compose.yml                  # Declarative Compose config (GPU, GUI, FastDDS, systemd)
├── build-docker-img.sh                 # Packages $HOME/Qt → mlib3rd/Qt.tar.gz, then runs docker build
├── start-qt-container.sh              # Full GUI launcher (X11 + GPU + systemd + FastDDS + volumes)
├── host-theme.sh                      # Copies the host desktop theme/icons/cursor/fonts into the container
├── host-audio.sh                      # Shares host sound devices + audio server (ALSA "default")
├── start-terminal-session.sh           # Minimal disposable bash session
├── ros2-listener-host.sh              # Run a ROS 2 listener on the host with UDP FastDDS profile
├── fastdds-profile.xml                # Fast DDS: UDPv4 only, shared-memory disabled
├── docker-install-guide.md            # Detailed setup & troubleshooting documentation
├── .gitignore
│
├── mlib3rd/                           # ⚠️ Third-party archives (Git-ignored, download from LFS)
│   ├── Qt.tar.gz                      #   Qt 6.7.2 (~2.3 GB) — extracted to /opt/Qt
│   ├── boost_1_83_0.tar.gz            #   Boost 1.83.0 (~145 MB) — built & installed to /usr/local
│   ├── digital-map.zip                #   Digital map data (~135 MB) — extracted to /opt/digital-map
│   ├── rtl-sdr.tar.xz                 #   RTL-SDR source — built & installed system-wide
│   ├── JetBrains_Mono-and-Roboto_Condensed.zip  # Developer fonts
│   └── snap7/                         #   Siemens S7 PLC communication library
│       ├── libsnap7.so                #     Pre-built shared library → /usr/bin/
│       ├── snap7.cpp                  #     C++ source
│       ├── snap7.h                    #     Header file
│       └── How_To_Install_snap7.md    #     Installation notes
│
└── docker-img/                        # ⚠️ Pre-built image archive (Git-ignored, download from Drive)
    └── rqt-based-env.tar              #   Exported image (~4.5 GB), load with: docker load -i
```

> **Note:** The `mlib3rd/` and `docker-img/` directories are **Git-ignored**. You must download them before building.
> See [LFS & Pre-built Image Downloads](#-lfs--pre-built-image-downloads) below.

---

## 📦 LFS & Pre-built Image Downloads

The large binary dependencies and pre-built image are hosted on Google Drive:

| Resource | Size | Link |
| :--- | :--- | :--- |
| **LFS Dependencies** (`mlib3rd/` contents) | ~2.6 GB | [📥 Download](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link) |
| **Pre-built Docker Image** (`docker-img/rqt-based-env.tar`) | ~4.5 GB | [📥 Download](https://drive.google.com/drive/folders/1LTTGTxSGOOlnBSf-TOyOVT7iUzzliVHR?usp=drive_link) |

**Option 1 — Build from scratch:** Download the LFS dependencies into `mlib3rd/`, then run `./build-docker-img.sh`.

**Option 2 — Load pre-built image:** Download `rqt-based-env.tar` into `docker-img/`, then load directly:
```bash
docker load -i docker-img/rqt-based-env.tar
```

---

## 🛠️ Prerequisites

Before building or running the container, ensure your host system satisfies the following requirements:

1. **Docker & Docker Compose**: Installed and running on Linux.
2. **NVIDIA Container Toolkit**: Required for GPU acceleration (see [`docker-install-guide.md`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/docker-install-guide.md#2-prerequisites) for step-by-step setup).
3. **X11 Display**: A running X server on the host for GUI applications.
4. **Qt 6.7.2 on Host**: Local installation at `$HOME/Qt` if building from scratch via `build-docker-img.sh`.
5. **LFS Files**: Download `mlib3rd/` contents from [Google Drive](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link) (or use the pre-built image instead).

---

## 🚀 Quick Start

### 1. Build the Docker Image

To archive local Qt dependencies (if not already archived in `mlib3rd/Qt.tar.gz`) and build the image:

```bash
./build-docker-img.sh
```

Or build using Docker Compose:
```bash
docker compose build
```

### 2. Run the Container with GUI Support

#### Option A: Using Docker Compose (Recommended)
Make sure local X server access is granted, then start the container:

```bash
xhost +local:docker
docker compose up -d
```

To enter an interactive shell:
```bash
docker compose exec rqt-based-env bash
```

To stop the container:
```bash
docker compose down
```

#### Option B: Using the Launcher Script
Alternatively, run the launcher script:

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

## 🎨 Appearance: Matching the Host Desktop

`start-qt-container.sh` and `start-qt-container-portmap.sh` source
[`host-theme.sh`](host-theme.sh), which makes GUI apps inside the container look
like the ones on the host - same GTK theme, icon set, cursor, fonts and dark/light
mode.

**How it works**

The container already shares the host X display, so the host's XSETTINGS manager
(`gsd-xsettings`) *tells* GTK apps in the container which theme to use - running
`gtk-query-settings` inside the container already reports the host values. What
was missing were the files themselves. `host-theme.sh` therefore:

1. Bind-mounts the host's `/usr/share/{themes,icons,fonts}` read-only under
   `/usr/local/share/`, plus any per-user `~/.themes`, `~/.icons`, `~/.fonts`.
   This is *additive* - `/usr/local/share` is searched before `/usr/share`, so
   the host copies win but nothing shipped in the image is hidden.
2. Sets `QT_QPA_PLATFORMTHEME=gtk3`, so Qt apps (Qt Creator included) build their
   palette and fonts from the same GTK theme. The Qt in `/opt` ships the required
   `libqgtk3.so` plugin, so no distro Qt is pulled in.
3. Points `XCURSOR_PATH`/`XCURSOR_THEME` at the host cursor theme, since Xcursor
   does not search `/usr/local/share` by default.
4. Writes `~/.config/gtk-{3,4}.0/settings.ini` in the container as a fallback for
   hosts with no XSETTINGS manager, and starts a container-local session D-Bus
   that the GTK platform theme expects.
5. Sets Qt Creator's own theme (`flat-dark` / `flat-light`) to match the host's
   `color-scheme`, in the persisted `~/.docker-qtcreator/config/QtCreator.ini`.
6. Replays the host's fontconfig *reject* rules into
   `/etc/fonts/conf.d/71-host-legacy-reject.conf`, rewritten for the
   `/usr/local/share/fonts` mount point. Font packages use these to hide
   superseded font files - `fonts-ubuntu` rejects the legacy static
   `Ubuntu-*.ttf` so the variable `Ubuntu[wdth,wght].ttf` wins. Those rules live
   in `/etc/fonts`, which is not mounted, so without this step `Ubuntu` resolves
   to `Ubuntu-B.ttf` and all text renders **bold**.
7. Writes `/etc/fonts/conf.d/72-host-font-priority.conf`, hiding font files that
   Dockerfile step 5 dumps flat into `/usr/share/fonts/` when they compete for a
   family the host also ships. Three different files named
   `RobotoCondensed-Regular.ttf` exist between the two machines; without this the
   container picked the zip's redesign while the host uses the classic
   `fonts-roboto` cut, so any app calling `QFont("Roboto Condensed")` rendered in
   a visibly different, heavier face. Families the host does not have (JetBrains
   Mono) are left alone, and hidden files stay on disk.
8. Bind-mounts loose font files from the host's `/usr/share/fonts` root onto the
   identical path in the container. Code that opens a `.ttf` by absolute path
   never consults fontconfig, so rule 7 cannot reach it - and the image ships a
   *different* font under the very same name. `GLFontManager.cpp` hardcodes
   `/usr/share/fonts/RobotoCondensed-Regular.ttf`, which is one file on the host
   and another in the image; without this mount, OpenGL-rendered text would
   still differ from the host even once the Qt widgets matched.

Everything is read from the host at launch, so changing the host theme and
re-running the launcher is enough - no image rebuild.

**Verifying it worked**

```bash
# inside the container - should print the host's theme, not "Adwaita"
gtk-query-settings | grep -E "theme-name|font-name"
```

The image also installs `yaru-theme-gtk`, `yaru-theme-icon` and `fonts-ubuntu`
as a fallback, so it still looks reasonable when run without the launcher (for
example via `start-terminal-session.sh` or on a non-GNOME host). Rebuild the
image to pick these up; the launcher's bind-mounts work without a rebuild.

If you start the container with Docker Compose instead, the mounts and
environment are already in `docker-compose.yml`; run the post-start part once the
container is up:

```bash
./host-theme.sh rqt-based-env
```

> **Note:** Qt Creator's theme is re-applied on every launch. If you prefer to
> pick a theme by hand inside Qt Creator, drop the `apply_host_theme` call from
> the launcher script.

---

## 🔊 Audio: Sound Devices and the Host Audio Server

`start-qt-container.sh` also sources [`host-audio.sh`](host-audio.sh), which fixes
two distinct problems for code that opens ALSA directly (for example
`snd_pcm_open("default", ...)`).

1. **`/dev/snd` is a snapshot.** It is populated when the container is created,
   so a card plugged in later - a USB capture device, say - has no device nodes
   inside, even though `/proc/asound` (shared with the host) lists it.
   Bind-mounting `/dev/snd` keeps the nodes in sync.
2. **`"default"` means something different inside.** On the host, `pipewire-alsa`
   redefines the ALSA `default` PCM to follow the desktop's current default
   source/sink. The container has no such config, so `default` falls back to raw
   card 0 - usually the unconnected onboard input. An app that records fine from
   `"default"` on the host therefore captures the wrong device in the container.
   `host-audio.sh` mounts the host's PulseAudio/PipeWire socket (plus the auth
   cookie, since the container runs as root and the socket authenticates by uid)
   and writes an `/etc/asound.conf` routing `default` through it.

Verify with:

```bash
arecord -D default -d 1 -f cd -v /tmp/t.wav   # should print "ALSA <-> PulseAudio PCM I/O Plugin"
ls /dev/snd                                    # should list every card the host has
```

The image installs `libasound2-plugins` (the ALSA→PulseAudio bridge) and
`alsa-utils`; on an image built before that, `host-audio.sh` installs the bridge
at startup. With Docker Compose the mounts are already in the compose file - run
the post-start part once the container is up:

```bash
./host-audio.sh rqt-based-env
```

---

## 📚 Documentation & External Links

For comprehensive details on host NVIDIA setup, Fast DDS configuration rationale, and troubleshooting, consult [`docker-install-guide.md`](file:///home/trungnb/workspace/my-work/docker-ws/qt-based-img/docker-install-guide.md).

- **Google Drive LFS Files & Pre-built Image**:
  - [LFS Dependencies Folder](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link)
  - [Pre-built Docker Image Archive](https://drive.google.com/drive/folders/1LTTGTxSGOOlnBSf-TOyOVT7iUzzliVHR?usp=drive_link)

- **Working with docker compose**:
```bash
# 1. Allow X server access on the host (for GUI apps)
xhost +local:docker

# 2. Build the image (optional if already built)
docker compose build

# 3. Start the container in background
docker compose up -d

# 4. Attach an interactive shell
docker compose exec rqt-based-env bash

# 5. Stop the container
docker compose down

```