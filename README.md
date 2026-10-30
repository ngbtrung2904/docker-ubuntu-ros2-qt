# ROS 2 & Qt Docker Environment (`rqt-based-env`)

A Docker image based on **Ubuntu 24.04 LTS** for building and running ROS 2 + Qt applications, plus the scripts that install Docker and the NVIDIA stack offline, launch the container, and run the radar apps at boot on a deployment machine.

The image carries **ROS 2 Rolling**, **Qt 6.7.3** with Qt Creator, **Boost 1.83.0**, **Qwt 6.3.0**, Snap7 and RTL-SDR, runs `systemd` as PID 1, and passes through X11, the NVIDIA GPU, host audio and the host desktop's appearance.

For the step-by-step deployment procedure in Vietnamese, see [`Triển khai phần mềm bằng Docker.md`](Triển%20khai%20phần%20mềm%20bằng%20Docker.md).

---

## 📑 Table of Contents

- [Features](#-features)
- [Project Structure](#-project-structure)
- [Build Inputs & Downloads](#-build-inputs--downloads)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
- [Export the Image to Another Machine](#-export-the-image-to-another-machine)
- [Deployment: Radar Apps at Boot](#-deployment-radar-apps-at-boot)
- [Remote Power-off over SSH](#-remote-power-off-over-ssh)
- [Logs and Recorded Data](#-logs-and-recorded-data)
- [ROS 2 & Fast DDS Communication](#-ros-2--fast-dds-communication)
- [Mounted Volumes & Persistent Data](#-mounted-volumes--persistent-data)
- [Appearance: Matching the Host Desktop](#-appearance-matching-the-host-desktop)
- [Audio: Sound Devices and the Host Audio Server](#-audio-sound-devices-and-the-host-audio-server)
- [Device Simulators (`tools/`)](#-device-simulators-tools)
- [Removing Docker and NVIDIA](#-removing-docker-and-nvidia)
- [Links](#-links)

---

## ✨ Features

- **Base OS**: Ubuntu 24.04 LTS (Noble Numbat).
- **Robotics middleware**: ROS 2 Rolling, unpacked from a tarball into `/opt/ros/rolling`, with Fast DDS forced onto UDPv4 so host and container nodes talk to each other.
- **GUI & IDE**: Qt 6.7.3 and Qt Creator in `/opt/Qt`, X11 forwarding and NVIDIA GPU acceleration (`--gpus all`).
- **systemd as PID 1** (`/sbin/init`): `systemctl`, `journalctl` and `timedatectl` work inside, and the radar apps run as systemd services.
- **Host-matched appearance**: GTK theme, icons, cursor, fonts and dark mode come from the host. See [Appearance](#-appearance-matching-the-host-desktop).
- **Host-matched audio**: ALSA `default` routes to the host audio server. See [Audio](#-audio-sound-devices-and-the-host-audio-server).
- **Offline installers** for Docker Engine 29.7.2 and the NVIDIA driver 595 + Container Toolkit 1.20.1, with pinned and held versions.
- **Build tools**: CMake, Ninja, mold, GCC, Clang, ccache, colcon, gedit, Terminator, Wireshark, ffmpeg, VLC.
- **Libraries**:
  - Boost 1.83.0 and Qwt 6.3.0 (with Qt SVG), both built from source
  - Snap7 1.4.2 for Siemens S7 PLCs, built from upstream source into `/usr/lib/libsnap7.so`
  - RTL-SDR, with its udev rules and the `dvb_usb_rtl28xxu` blacklist
  - glog, Protobuf, ZeroMQ (`cppzmq`), SQLite3, GDAL, OpenGL/FreeGLUT/GLFW/GLEW/GLM, FTGL, SoundTouch
  - JetBrains Mono and Roboto Condensed fonts
  - Digital map data unpacked into `/opt/digital-map`

---

## 📁 Project Structure

```
docker-ubuntu-ros2-qt/
├── dockerfile                 # Image recipe, numbered steps (lowercase name on purpose)
├── docker-compose.yml         # Compose variant of the launcher (host network, GPU, GUI, audio, systemd)
├── .dockerignore              # Keeps .git, .claude, images/, projects/ and *.md out of the build context
├── fastdds-profile.xml        # Fast DDS: UDPv4 only, shared memory disabled
├── scripts/
│   ├── start-qt-container.sh  # Launcher: container + timezone + theme + audio + SSH keys + PAR config links,
│   │                          #   and the --service / --start-gui / --apply-only modes used at deployment
│   ├── install-docker.sh      # Docker Engine 29.7.2 from docker-package/, offline (or --download)
│   ├── install-nvidia.sh      # NVIDIA driver 595.99.02 + Container Toolkit 1.20.1 from nvidia-package/
│   ├── install-autostart.sh   # One-time setup of a deployment machine (role + variant)
│   ├── install-ssh-target.sh  # One-time setup of a machine the console powers off over SSH
│   ├── pack-runtime.sh        # Built apps without their source -> projects.zip (+ .sha256)
│   └── shutdown-test.sh       # Lab test of the power-off commands (lab IPs hardcoded)
├── tools/                     # Python simulators of the PLC, PSR/SSR status devices and the SPC stream
├── Triển khai phần mềm bằng Docker.md   # Deployment procedure (Vietnamese)
│
├── mlib3rd/                   # ⚠️ git-ignored: build inputs, see below
├── images/                    # ⚠️ git-ignored: rqt-based-env.tar.gz (+ .sha256), the exported image
├── docker-package/            # ⚠️ git-ignored: docker-ce .debs for install-docker.sh
├── nvidia-package/            # ⚠️ git-ignored: NVIDIA .debs for install-nvidia.sh
├── projects/                  # ⚠️ git-ignored: app repositories, mounted at /workspace
└── projects.zip (+ .sha256)   # ⚠️ git-ignored: output of pack-runtime.sh
```

Every script resolves its own directory, so it can be run from anywhere, but everything it mounts or installs from must sit in the folder above `scripts/`.

---

## 📦 Build Inputs & Downloads

The `dockerfile` copies everything it needs from `mlib3rd/`:

| File | Size | Becomes |
| :--- | :--- | :--- |
| `Qt.tar.gz` | 1.1 GB | `/opt/Qt` (Qt 6.7.3 + Qt Creator) |
| `boost_1_83_0.tar.gz` | 138 MB | Built and installed to `/usr/local` |
| `digital-map.zip` | 128 MB | `/opt/digital-map` |
| `rolling.tar.xz` | 34 MB | `/opt/ros/rolling` (unpacked at `/`, not an apt install) |
| `snap7-full-1.4.2.7z` | 20 MB | Built; `libsnap7.so` installed to `/usr/lib` |
| `JetBrains_Mono-and-Roboto_Condensed.zip` | 5 MB | `.ttf` files copied flat into `/usr/share/fonts` |
| `qwt-6.3.0.tar.bz2` | 5 MB | Built and installed to `/usr/local/qwt-6.3.0` |
| `rtl-sdr.tar.xz` | 0.6 MB | Built and installed system-wide |
| `python3_pkgs.txt` | 4 KB | apt package list ROS 2 needs (the tarball carries no apt metadata) |

The large files are on Google Drive, see [Links](#-links).

### Packing `Qt.tar.gz` yourself

The archive must have a bare `Qt/` at its root, so that `ADD mlib3rd/Qt.tar.gz /opt/` produces `/opt/Qt/6.7.3/gcc_64`. Pack from the parent directory with a **relative** name:

```bash
tar -C "$HOME" -czf mlib3rd/Qt.tar.gz Qt
```

With an absolute path (`tar czf ... /home/you/Qt`) every entry carries `home/you/`, the tree lands at `/opt/home/you/Qt`, and the build fails later at the Qwt step, when `/opt/Qt/6.7.3/gcc_64/bin/qmake` is not found.

A full Qt install is around 23 GB, mostly debug symbols, the `Src` tree and Android kits this container cannot use. Excluding them brings it to a few GB before compression:

```bash
tar -C "$HOME" \
    --exclude='Qt/6.7.3/Src' \
    --exclude='Qt/6.7.3/android_*' \
    --exclude='Qt/Docs' \
    --exclude='Qt/Examples' \
    --exclude='Qt/installerResources' \
    --exclude='Qt/Tools/QtInstallerFramework' \
    --exclude='*.debug' \
    -I pigz -cf mlib3rd/Qt.tar.gz Qt
```

`-I pigz` replaces `-z` with parallel gzip. Keep `*.debug` and `Src` if you need to step into Qt's own source.

The Qt version appears in three places in the `dockerfile`: the two `ENV` lines after the `ADD`, and the `qmake` path in step 9. The launcher and `pack-runtime.sh` also assume Qt Creator's build folder `Desktop_Qt_6_7_3-Debug` (override with `BUILD_KIT=...`).

---

## 🛠️ Prerequisites

1. **Ubuntu 24.04** on kernel `7.0.0-38-generic` (the kernel the NVIDIA package folder carries headers for).
2. **Docker Engine**: `sudo scripts/install-docker.sh`.
3. **NVIDIA driver and Container Toolkit**: `sudo scripts/install-nvidia.sh`, then reboot. Both the launcher (`--gpus all`) and Compose (a GPU reservation) require them, so every machine that runs this container needs an NVIDIA GPU with the driver and toolkit installed.
4. **An X11 desktop session** for GUI apps.
5. **Build inputs** in `mlib3rd/`, or the exported image in `images/`.

See [Pinned versions](#pinned-versions-and-the-offline-package-folders) for what the two install scripts install.

---

## 🚀 Quick Start

### 1. Build or load the image

```bash
docker compose build
```

The build context is the repository root, because the dockerfile copies from `mlib3rd/`. Compose builds with `network: host`. `.dockerignore` keeps `images/` and `projects/` out of the upload.

Or load the exported image:

```bash
docker load -i images/rqt-based-env.tar.gz
```

### 2. Run it

**Option A: the launcher (recommended).**

```bash
mkdir -p ~/.docker-qtcreator/{config,share,cache}   # once; otherwise Docker creates them root-owned
scripts/start-qt-container.sh
```

It runs `xhost +local:docker`, removes any old `rqt-based-env` container, starts a new one (`--net=host`, `--ipc=host`, `--privileged`, `--gpus all`, systemd as PID 1), waits up to 60 s for systemd, copies the host timezone in, then applies the host theme, audio, SSH keys and the PAR config links, and opens an interactive shell. Leaving the shell leaves the container running.

**Option B: Docker Compose.**

```bash
xhost +local:docker
docker compose up -d
scripts/start-qt-container.sh --apply-only    # theme, audio, SSH keys, PAR config links
docker compose exec rqt-based-env bash
docker compose down
```

Compose differs from the launcher in a few ways: it mounts `~/.config/pulse/cookie`, `~/.ssh`, `~/.app`, `~/rrecord` and `~/recordings` unconditionally (Docker creates a missing one as an empty root-owned directory), it does not set the cursor theme or the session D-Bus address, and it does not mount per-user theme/font folders or the loose host font files. Prefer the launcher on a deployment machine.

Extra shells and shutdown:

```bash
docker exec -it rqt-based-env bash
docker stop rqt-based-env
```

### 3. Build the apps inside the container

`projects/` is mounted at `/workspace`. In the container run `qtcreator`, open each app's `CMakeLists.txt` (repository root for `rdd`, `src/` for `rmm`, `rdp` and `dvaz`) and build with the default kit, **Desktop Qt 6.7.3**, Debug. The output goes to `build/Desktop_Qt_6_7_3-Debug/`, which is where each `run.sh`, the launcher and `pack-runtime.sh` look for it. If an app's `protocol/install` is missing, build the protocol package first with `protocol/buildpackage.sh` (colcon).

### 4. Paths inside the image

| Thing | Location |
| :--- | :--- |
| ROS 2 Rolling | `/opt/ros/rolling`, sourced from `/root/.bashrc` |
| Qt kit | `/opt/Qt/6.7.3/gcc_64` (`PATH`, `CMAKE_PREFIX_PATH`) |
| Qt Creator | `/opt/Qt/Tools/QtCreator/bin` (`PATH`) |
| Qwt 6.3.0 | `/usr/local/qwt-6.3.0` |
| Boost 1.83.0 | `/usr/local` |
| Snap7 | `/usr/lib/libsnap7.so` |
| Digital map data | `/opt/digital-map` |
| Your projects | `/workspace` |

The image masks `systemd-udevd` and its sockets (dockerfile step 12), so the container's systemd stays out of the host's device management.

### 5. Remove containers and images

```bash
docker ps -aq | xargs -r docker rm -f          # remove all containers
docker images -q | xargs -r docker rmi -f      # remove all images
docker system prune -a --volumes -f            # clean caches, networks, volumes
```

---

## 📤 Export the Image to Another Machine

Check first that the local image matches the `dockerfile`: `docker image inspect rqt-based-env:latest --format '{{.Created}}'`. If the dockerfile changed since then, rebuild (all of `mlib3rd/` must be present).

**On the build machine:**

```bash
mkdir -p images
docker save rqt-based-env:latest | gzip -1 > images/rqt-based-env.tar.gz   # ~3.3 GB
sha256sum images/rqt-based-env.tar.gz > images/rqt-based-env.tar.gz.sha256
scripts/pack-runtime.sh                                                          # projects.zip, see Deployment
```

Copy this folder to the target: the scripts, `dockerfile`, `docker-compose.yml`, `fastdds-profile.xml`, `tools/`, `images/`, `docker-package/`, `nvidia-package/`, `projects.zip` and its `.sha256`. `mlib3rd/` is only needed to rebuild. Put the folder on the target's local disk, not on the USB stick: the deployment unit stores the launcher's absolute path, and `projects/` is mounted from here.

**On the target:**

```bash
sudo scripts/install-docker.sh                                           # docker-ce from docker-package/, offline
sudo scripts/install-nvidia.sh                                           # driver + toolkit from nvidia-package/, offline
sudo reboot                                                        # loads the driver and the docker group

sha256sum -c images/rqt-based-env.tar.gz.sha256
docker load -i images/rqt-based-env.tar.gz
docker run --rm --gpus all rqt-based-env:latest nvidia-smi         # the GPU must show up
mkdir -p ~/.docker-qtcreator/{config,share,cache}
sha256sum -c projects.zip.sha256 && unzip projects.zip
scripts/install-autostart.sh <role> <variant>                            # see Deployment
```

### Pinned versions and the offline package folders

Reference machine (2026-10-29): Ubuntu 24.04, NVIDIA RTX A2000 12GB, kernel `7.0.0-38-generic`.

| Component | Package | Version | Folder |
| :--- | :--- | :--- | :--- |
| Docker Engine | `docker-ce`, `docker-ce-cli`, `docker-ce-rootless-extras` | `5:29.7.2-1~ubuntu.24.04~noble` | `docker-package/` |
| | `containerd.io` | `2.3.4-1~ubuntu.24.04~noble` | |
| | `docker-buildx-plugin` / `docker-compose-plugin` | `0.36.1-1~…` / `5.5.0-1~…` | |
| NVIDIA driver | `nvidia-driver-595-open`, `nvidia-dkms-595-open`, `nvidia-utils-595`, plus `nvidia-settings`, `nvidia-prime`, `linux-headers-7.0.0-38-generic` | `595.99.02-0ubuntu0.24.04.1` | `nvidia-package/` |
| NVIDIA Container Toolkit | `nvidia-container-toolkit`, `nvidia-container-toolkit-base`, `libnvidia-container-tools`, `libnvidia-container1` | `1.20.1-1` | |

What the install scripts do:

- Both refuse to run without root and without `.deb` files in their folder. They install through a throwaway local apt repository built from the folder, so apt never touches the network or the host's package lists. A pinned package is installed when missing or older; any other package only when missing, so nothing already on the host is upgraded. Both then `apt-mark hold` the pinned packages, so `apt upgrade` leaves them alone.
- `install-docker.sh` first removes Ubuntu's conflicting packages (`docker.io`, `docker-doc`, `docker-compose`, `docker-compose-v2`, `podman-docker`, `containerd`, `runc`), enables `containerd` and `docker`, and adds the sudo user to the `docker` group (effective after logging in again).
- `install-nvidia.sh` warns when the running kernel is not `7.0.0-38-generic` (the DKMS module is built only for kernels whose headers are installed) and when Secure Boot is on (the module must then be signed with an enrolled MOK key, or Secure Boot turned off). It registers the toolkit with Docker (`nvidia-ctk runtime configure --runtime=docker`) when Docker is already installed, so install Docker first.
- **While nouveau drives the GPU**, the new `nvidia.ko` cannot load until the reboot, and each attempt logs `NVRM: GPU 0000:xx:00.0 is already bound to nouveau`. `nvidia-persistenced`, `nvidia-cdi-refresh` and the udev rule `71-nvidia.rules` would retry it until shutdown. The script masks them under `/run` only, so the reboot clears the masks. A few `already bound` lines (and unrelated ACPI BIOS errors on some boards) can still appear before the reboot; they are harmless and do not come back.

Refill the folders on a machine with internet and Docker. Each run empties its folder, then downloads every dependency a fresh Ubuntu 24.04 needs inside a clean `ubuntu:24.04` container:

```bash
scripts/install-docker.sh --download          # -> docker-package/
scripts/install-nvidia.sh --download          # -> nvidia-package/
KERNEL=$(uname -r) scripts/install-nvidia.sh --download   # for a target on another kernel
```

Versions can be overridden through the environment when downloading: `DOCKER_VERSION`, `CONTAINERD_VERSION`, `BUILDX_VERSION`, `COMPOSE_VERSION`, `DRIVER_BRANCH`, `DRIVER_VERSION`, `TOOLKIT_VERSION`, `KERNEL`.

---

## 🚦 Deployment: Radar Apps at Boot

On bare metal, Ubuntu's *Startup Applications* called each project's `run.sh`. In Docker the same `run.sh` files run inside the container, started by the container's systemd. That also gives what Startup Applications cannot: **every app is restarted when it crashes or is closed**, and the whole container is relaunched if it dies.

| Role | At boot (no login needed) | From the operator's desktop | ASR address | PAR address |
| :--- | :--- | :--- | :--- | :--- |
| `lcms` | ASR only: rmm | rdd `run.sh --lcms`, started at login | 192.168.5.1 | 192.168.5.10 |
| `rdp` | rdp | | 192.168.5.21 | 192.168.5.22 |
| `rdd` | PAR only: rmm | rdd `run.sh --rdd`, from the **Radar RDD** desktop icon | 192.168.5.4 | 192.168.5.5 |
| `dvaz` | dvaz | | 192.168.5.31 | 192.168.5.32 |
| `rcms` (remote) | | rdd `run.sh --rcms`, started at login | 192.168.5.41 | 192.168.5.42 |
| `rrdd` (remote) | | rdd `run.sh --rrdd`, from the **Radar RRDD** desktop icon | 192.168.5.51 | 192.168.5.52 |
| `rdvaz` (remote) | dvaz | | 192.168.5.33 | 192.168.5.34 |

rmm runs on the LCMS machine in ASR, but on the RDD machine in PAR.

### Shipping the apps without their source

Target machines need neither the source nor a build. Build every app on the dev machine, inside the container (see [Build the apps](#3-build-the-apps-inside-the-container)), then pack only what `run.sh` needs:

```bash
scripts/pack-runtime.sh                      # -> ./projects.zip (+ projects.zip.sha256)
scripts/pack-runtime.sh --strip              # also strip debug symbols (smaller, but crash logs lose names and lines)
scripts/pack-runtime.sh /media/usb/projects.zip
```

The script needs `zip` on the host (`sudo apt install zip`). It does not touch `projects/`. For each of the eight apps (`asr`/`par` x `rdd`/`rmm`/`rdp`/`dvaz`) it finds the folder `projects/*_<variant>_<app>*`, and copies `run.sh`, `protocol/install` without its headers, and `build/Desktop_Qt_6_7_3-Debug` minus objects, generated sources and CMake files into a temporary folder that it zips as `projects/...`. An app that is missing, has no `run.sh` or is not built is listed as skipped and the rest are still packed; only an empty result is an error.

On the target, unzip it next to the scripts:

```bash
sha256sum -c projects.zip.sha256 && unzip projects.zip
```

Folder names must stay as built: each binary finds its protocol libraries through an absolute `/workspace/<folder>/...` RUNPATH. Nothing can be rebuilt on the target; change the source on the dev machine, rebuild, repack. Settings still live in each app's `build/Desktop_Qt_6_7_3-Debug/app/settings/appconfig.ini`, editable on the target. On the dev machine, a rebuild copies the source `appconfig.ini` over that copy whenever they differ.

### Setup, once per machine

1. Install Docker and NVIDIA, load the image, and unzip `projects.zip` (see [Export](#-export-the-image-to-another-machine)). Keep only one folder per app in `projects/`.
2. **Remove the old Startup Applications entries** that call `run.sh`. Otherwise every app runs twice, on the host and in the container, and the two fight over the same ports.
3. On `lcms`, `rdd`, `rcms` and `rrdd`, turn on automatic login for the operator account: *Settings → Users → Automatic Login*, or `AutomaticLoginEnable=true` and `AutomaticLogin=<user>` under `[daemon]` in `/etc/gdm3/custom.conf`. The console needs that user's desktop session.
4. Give the machine its address from the table above by hand (*Settings → Network*, `/24`), and set the apps' `appconfig.ini` addresses to match. No script touches the network.
5. As the operator user, **without `sudo`**, from a desktop session:

   ```bash
   scripts/install-autostart.sh <lcms|rdp|rdd|dvaz|rcms|rrdd|rdvaz> <asr|par>
   # e.g. scripts/install-autostart.sh rdd par
   ```

6. On the console machine that powers the station off, set up the SSH targets, see [Remote Power-off](#-remote-power-off-over-ssh).
7. Reboot.

### How it works

- `install-autostart.sh` enables *linger* for the user, so the user's systemd starts at boot without a login. It writes and enables `~/.config/systemd/user/radar-container.service`, which runs `start-qt-container.sh --service <role> <variant>` (`Restart=always`, 5 s, after `pipewire-pulse.socket`). On `lcms` and `rcms` it also writes `~/.config/autostart/radar-<role>.desktop`; on `rdd` and `rrdd` a trusted `~/Desktop/radar-<role>.desktop` icon. Both run `start-qt-container.sh --start-gui`.
- `--service` waits up to 120 s for the Docker daemon, does a normal launch without the interactive shell, then writes one unit per app into the container's systemd: `radar-rmm`, `radar-rdp`, `radar-dvaz` or `radar-rdd`. Each runs `/workspace/<repo>[/src]/run.sh` with ROS 2 sourced, `Restart=always` after 3 s and no start limit. The container's environment (`ROS_DOMAIN_ID`, Fast DDS profile, audio, theme) is handed to the units through `/run/radar.env`. The backends are started at once; the launcher then blocks on `docker wait`, so if the container dies the host unit relaunches everything.
- The console is not started at boot, because it needs the logged-in X display. `--start-gui` runs `xhost +local:docker`, waits up to 300 s for the container to have `radar-rdd.service`, hands the session's `DISPLAY` to the container's systemd and starts `radar-rdd`. Clicking the icon while the console runs does nothing.
- `<repo>` is the first folder in `projects/` matching `*_<variant>_<app>*`, so any prefix or suffix works.

### Operating it

```bash
systemctl --user status radar-container                      # the container, on the host
docker exec rqt-based-env systemctl status 'radar-*'         # the apps
docker exec rqt-based-env journalctl -u radar-rmm -f         # an app's console output
docker exec rqt-based-env systemctl stop radar-rdd           # stop one app; it stays down until started again
systemctl --user stop radar-container                        # stop everything, e.g. to update
systemctl --user start radar-container
```

`Restart=always` means closing a window brings it straight back. To really stop an app, use `systemctl stop`.

To remove the deployment from a machine:

```bash
systemctl --user disable --now radar-container
rm ~/.config/systemd/user/radar-container.service ~/.config/autostart/radar-*.desktop ~/Desktop/radar-*.desktop
loginctl disable-linger "$USER"
```

Moving this folder elsewhere breaks the unit and the desktop entries, which hold the launcher's absolute path. Run `install-autostart.sh` again from the new place.

### PAR: `runwayconfig.ini` and `glidepathconfig.ini` on the desktop

For the PAR console, every launch (and `--apply-only`) links `~/Desktop/runwayconfig.ini` and `~/Desktop/glidepathconfig.ini` to the files of the same name in `projects/<par rdd repo>/build/Desktop_Qt_6_7_3-Debug/app/settings/`, and makes the files and their folder writable by the host user, because the build in the container runs as root. Set `PAR_RDD_RUNWAY_CONFIG` or `PAR_RDD_GLIDEPATH_CONFIG` to another path below `projects/` if the build folder differs.

- The link and its file are both on the host disk (`projects/` is only bind-mounted), so recreating the container changes nothing.
- The console reads each file at start-up and writes it back when the operator changes a runway value or a glide angle. Qt writes a new file and renames it over the old one, keeping the mode, so the link and the write access both stay.
- The link is recreated (`ln -sf`) at every start, and only once its file exists, that is after the first build or after unzipping `projects.zip`.

> ⚠️ On the dev machine, every build of PAR rdd copies `app/runwayconfig.ini` and `app/glidepathconfig.ini` over the build copies whenever they differ, throwing away the desktop edits. Once the field values are final, copy them back into `app/`. On a target that only received `projects.zip`, nothing is rebuilt, so this cannot happen there.

---

## 🔌 Remote Power-off over SSH

When the station loses power, the ASR console runs `app/settings/scripts/powerAhOff.sh`, which shuts the station machines down over SSH (`ssh <user>@<host> 'sudo shutdown -h now'` for the Ubuntu machines, `sshpass ... "shutdown /s /t 0"` for the Windows machine). In Docker the console runs as root in the container, so root needs the host user's key and `known_hosts`, and every target must let that user run `shutdown` without a password.

On the console's host, as the operator user, once:

```bash
scripts/install-ssh-target.sh <user>@<host> [<user>@<host> ...]
```

For each Linux target it creates `~/.ssh/id_ed25519` if missing, installs the key there (asks the target's login password once), records the host key, adds `/etc/sudoers.d/shutdown-<user>` allowing `shutdown` with no password (asks the sudo password once, checked with `visudo`), and finally checks with no terminal, as the console runs, that `sudo shutdown` needs no password. Nothing is shut down. A Windows target is not set up by this script; connect to it once by hand (`ssh <user>@<host>`) so its host key is in `known_hosts`.

The host's `~/.ssh` is mounted read-only at `/run/host-ssh` and copied into the container's `/root/.ssh` with root ownership at every start (ssh rejects a config root does not own). After adding a target, restart the container or run `scripts/start-qt-container.sh --apply-only`.

`shutdown-test.sh` is a lab test of the same two command forms against two hardcoded lab machines. It really shuts them down. Edit its addresses before using it, then run it in the container: `docker exec -i rqt-based-env bash -s < scripts/shutdown-test.sh`.

---

## 🗂️ Logs and Recorded Data

Every app writes below `$HOME`. On bare metal that is the operator's home; in the container it is `/root`, which disappears with the container (every launch recreates it). The launcher and Compose therefore mount the host user's folders over the container's, so the paths are the same in both setups. The launcher creates them first, so they are owned by the host user.

| Host | Container | Written by |
| :--- | :--- | :--- |
| `~/.app` | `/root/.app` | System logs (glog) of every app |
| `~/rrecord` | `/root/rrecord` | dvaz recorded sessions |
| `~/recordings` | `/root/recordings` | Screen recordings exported by the rdd console (`.mp4`) |

System log folder per app, `$HOME` + the `appconfig.ini` key:

| App | Key | Folder |
| :--- | :--- | :--- |
| ASR rdd | `DirSysLog` | `~/.app/appasr/syslog` |
| PAR rdd | `DirSysLog` | `~/.app/apprddp/syslog` |
| ASR rmm, PAR rmm | `SysLogPath` | `~/.app/a00/par_rmm/syslog` (the ASR ini also names the PAR folder) |
| ASR rdp | `DirSysLog` | `~/.app/a00/asr_rdp/syslog` |
| PAR rdp | `DirSysLog` | `~/.app/a00/par_rdp/syslog` |
| ASR dvaz, PAR dvaz | `DirSysLog` | `~/.app/appasr/syslog` (both use the ASR name) |

Two more outputs are relative to the folder `run.sh` starts in (the repository root for rdd, `src/` for the others), so they land in `projects/<repo>/...` on the host:

- `crashfolder/`: stack dumps after a crash.
- `log/`: the rmm and rdp application log (`AppLogPath="./log"`).

`journalctl -u radar-<app>` inside the container also has each app's console output, but only while the container runs.

The dvaz recorder writes each session to `~/rrecord/<yyyyMMdd_hhmm>/` and deletes those older than `Days2SaveRRecord` days. Exporting a video does not happen in dvaz: dvaz replays the session and streams its audio to the rdd machine (`RddHost`, `StreamPort` 5002), and the **rdd console** records its own screen with ffmpeg into `~/recordings/`. Recordings stay on the dvaz machine, videos on the rdd machine.

Files the container creates in these folders are owned by root on the host. The host user can read them; to delete or move them, use `sudo`, or `sudo chown -R "$USER:" ~/.app ~/rrecord ~/recordings`.

---

## 📡 ROS 2 & Fast DDS Communication

By default Fast DDS adds a shared-memory transport for nodes it believes are on the same machine. With `--net=host` a container node and a host node look local to each other, so Fast DDS picks shared memory, which does not work across the container boundary. Discovery still succeeds over UDP, which gives the typical symptom: **`ros2 topic list` shows the topics but `ros2 topic echo` receives nothing.**

`fastdds-profile.xml` prevents this: `useBuiltinTransports=false` drops the built-in transports (shared memory and the default UDP), and the only transport left is a user-defined UDPv4 one.

**Both sides must load the profile, or no data flows.** The launcher and Compose set `FASTDDS_DEFAULT_PROFILES_FILE=/opt/ros/fastdds-profile.xml` and `ROS_DOMAIN_ID=0` in the container. On the host:

```bash
source /opt/ros/rolling/setup.bash
export FASTDDS_DEFAULT_PROFILES_FILE=/path/to/docker-ubuntu-ros2-qt/fastdds-profile.xml
export ROS_DOMAIN_ID=0
```

Check it with the demo nodes, if the host has them:

```bash
ros2 run demo_nodes_cpp talker      # in the container
ros2 run demo_nodes_cpp listener    # on the host: prints "I heard: 'Hello World: N'"
```

| Check | Command |
| :--- | :--- |
| Profile loaded | `echo $FASTDDS_DEFAULT_PROFILES_FILE` |
| Same domain | `echo $ROS_DOMAIN_ID` |
| Same RMW | `echo $RMW_IMPLEMENTATION` (empty means Fast DDS) |
| Discovery | `ros2 node list`, `ros2 topic list` |
| Data | `ros2 topic echo <topic>` |

---

## 💾 Mounted Volumes & Persistent Data

| Host | Container | Notes |
| :--- | :--- | :--- |
| `projects/` (next to the scripts) | `/workspace` | App repositories |
| `fastdds-profile.xml` | `/opt/ros/fastdds-profile.xml` | Read-only |
| `~/.docker-qtcreator/{config,share,cache}` | `/root/.config/QtProject`, `/root/.local/share/QtProject`, `/root/.cache/QtProject` | Qt Creator settings |
| `~/.app`, `~/rrecord`, `~/recordings` | the same names under `/root` | See [Logs](#-logs-and-recorded-data) |
| `~/.ssh` | `/run/host-ssh` (read-only), copied to `/root/.ssh` | See [Power-off](#-remote-power-off-over-ssh) |
| `/usr/share/{themes,icons,fonts}` | `/usr/local/share/{themes,icons,fonts}` | Read-only |
| `/tmp/.X11-unix`, `/dev/dri`, `/dev/snd` | the same | X11, GPU, sound devices |
| `$XDG_RUNTIME_DIR/pulse/native`, `~/.config/pulse/cookie` | `/run/host-pulse`, `/root/.config/pulse/cookie` | Host audio server |
| `/sys/fs/cgroup` | the same | Needed by systemd |

`/run`, `/run/lock` and `/tmp` are tmpfs in the container. systemd as PID 1 needs `privileged`, the host cgroup namespace, the `/sys/fs/cgroup` mount and these tmpfs together; removing one of them breaks the boot.

---

## 🎨 Appearance: Matching the Host Desktop

`start-qt-container.sh` makes GUI apps in the container look like the host's: same GTK theme, icon set, cursor, fonts and dark/light mode. Everything is read from the host (`gsettings`) at launch, so changing the host theme and relaunching is enough.

The container shares the host X display, so the host's XSETTINGS manager already tells GTK apps which theme to use; what was missing are the files. The launcher:

1. Bind-mounts the host's `/usr/share/{themes,icons,fonts}` read-only under `/usr/local/share/`, plus any per-user `~/.themes`, `~/.icons`, `~/.fonts`, `~/.local/share/{themes,icons,fonts}`. `/usr/local/share` is searched first, so host copies win but nothing in the image is hidden.
2. Relies on `QT_QPA_PLATFORMTHEME=gtk3` (set in the image), so Qt apps, Qt Creator included, take their palette and fonts from the GTK theme. `QT_QPA_PLATFORM=xcb` is a different setting (the windowing backend); both are needed.
3. Sets `XCURSOR_PATH`, `XCURSOR_THEME` and `XCURSOR_SIZE`, since Xcursor does not search `/usr/local/share` by default.
4. Starts a session D-Bus on `/run/dbus-session-bus` for the GTK platform theme, and writes `~/.config/gtk-{3,4}.0/settings.ini` as a fallback for hosts without an XSETTINGS manager.
5. Sets Qt Creator's own theme (`flat-dark` / `flat-light`) from the host's `color-scheme`, in the persisted `QtCreator.ini`. It is re-applied on every launch.
6. Replays the host's fontconfig *reject* rules into `/etc/fonts/conf.d/71-host-legacy-reject.conf`, rewritten for the `/usr/local/share/fonts` mount. `fonts-ubuntu` uses these to hide the legacy static `Ubuntu-*.ttf`; without them `Ubuntu` resolves to `Ubuntu-B.ttf` and every label renders **bold**.
7. Writes `/etc/fonts/conf.d/72-host-font-priority.conf`, hiding the font files dockerfile step 5 put flat into `/usr/share/fonts/` when they compete for a family the host also ships (for example `Roboto Condensed`, which exists in different cuts in the zip and in the host's `fonts-roboto`). Families only the image has (JetBrains Mono) are left alone.
8. Bind-mounts each loose font file at the root of the host's `/usr/share/fonts` onto the same path in the container, because code that opens a `.ttf` by absolute path (the consoles' OpenGL text uses `/usr/share/fonts/RobotoCondensed-Regular.ttf`) never consults fontconfig.

Three image packages look like fallbacks but are required: `dbus-x11` (the `dbus-daemon` for step 4), `fonts-ubuntu` (the reject rules for step 6) and `libasound2-plugins` (see [Audio](#-audio-sound-devices-and-the-host-audio-server)). `yaru-theme-*` and `gnome-themes-extra` are real fallbacks for running without the launcher.

Check it inside the container:

```bash
gtk-query-settings | grep -E "theme-name|font-name"    # the host's theme, not "Adwaita"
```

---

## 🔊 Audio: Sound Devices and the Host Audio Server

The launcher fixes two problems for code that opens ALSA directly, such as `snd_pcm_open("default", ...)`:

1. **`/dev/snd` is a snapshot.** It is populated when the container is created, so a card plugged in later has no device nodes inside. Bind-mounting `/dev/snd` keeps them live.
2. **`default` means something else inside.** On the host, `pipewire-alsa` makes ALSA `default` follow the desktop's default source and sink. The container has no such config, so `default` falls back to raw card 0, usually the unconnected onboard input. The launcher mounts the host's PulseAudio/PipeWire socket and auth cookie (the socket authenticates by uid and the container runs as root), and writes `/etc/asound.conf` routing `default` through it with the `pulse` plugin from `libasound2-plugins`.

```bash
arecord -D default -d 1 -f cd -v /tmp/t.wav   # prints "ALSA <-> PulseAudio PCM I/O Plugin"
aplay -L                                       # matches the host's list
ls /dev/snd                                    # every card the host has
```

If the host has no audio server socket, or the image lacks the plugin, the launcher says so and `default` stays on card 0.

---

## 🧪 Device Simulators (`tools/`)

Python simulators for checking the apps without hardware. They use the Python standard library only, except `sim_spc_rdata.py`, which needs NumPy (in the image as `python3-numpy`).

| Script | Simulates | Point the app at it with |
| :--- | :--- | :--- |
| `monitoring_psr_asr.py` | ASR PSR status devices (TX, RX, SYNC, TEXL, MRLR, TFSL1-4, DSP, SPC), TCP 8888 | ASR rmm `DspDevAddress`, `SpcDevAddress` |
| `monitoring_psr_par.py` | PAR PSR status devices (TX, RX, SYNC, RFMON, DSP, SPC), TCP 8888 | PAR rmm `DspDevAddress`, `SpcDevAddress` |
| `monitoring_plc_asr.py` | ASR Siemens S7 PLC (DB14 status, DB13 commands), port 102 | ASR rmm `PlcDevAddress="127.0.0.1;0;1"` |
| `monitoring_plc_par.py` | PAR Siemens S7 PLC, port 102 | PAR rmm `PlcDevAddress="127.0.0.1;0;1"` |
| `monitoring_ssr_asr.py` | ASR SSR modules (TMOS, MSYN-I, MSYN-U, ISSR) as TCP clients of rdp, default `127.0.0.1:30010` | ASR rdp `SSRServerPort` and the `SSR*Address` keys |
| `sim_spc_rdata.py` | ASR SPC radar stream (encoder, video, hit, meteo), TCP 8889 | ASR rdp `SpcDevAddress` |
| `s7server.py` | Snap7 server through ctypes, shared by the two PLC scripts | (library, uses `/usr/lib/libsnap7.so` or `LIBSNAP7`) |

The PSR and PLC scripts send fixed values by default, or new random values every N seconds with `--random N`. The docstring at the top of each script has the full options and the exact `appconfig.ini` settings. The PLC scripts need `libsnap7.so`, which the image has, so run them in the container.

---

## 🧹 Removing Docker and NVIDIA

Removes everything `install-docker.sh` and `install-nvidia.sh` install, for example to test them again on the same machine. Removing `/var/lib/docker` deletes every image, so `docker load` again afterwards. `~/.app`, `~/rrecord` and `~/recordings` stay.

```bash
# 1. Stop the deployment and the container
systemctl --user disable --now radar-container 2>/dev/null
docker rm -f rqt-based-env 2>/dev/null

# 2. Docker: release the hold, purge every flavour, delete its data
sudo apt-mark unhold docker-ce docker-ce-cli docker-ce-rootless-extras containerd.io \
    docker-buildx-plugin docker-compose-plugin
sudo apt-get purge -y docker-ce docker-ce-cli docker-ce-rootless-extras containerd.io \
    docker-buildx-plugin docker-compose-plugin docker.io docker-compose-v2 containerd runc
sudo rm -rf /var/lib/docker /var/lib/containerd /etc/docker
sudo rm -f /etc/apt/sources.list.d/docker.list /etc/apt/sources.list.d/docker.sources /etc/apt/keyrings/docker.asc
sudo groupdel docker 2>/dev/null

# 3. NVIDIA container toolkit and driver
sudo apt-mark unhold $(apt-mark showhold | grep -E '^(nvidia|libnvidia)')
sudo apt-get purge -y 'nvidia-container-toolkit*' 'libnvidia-container*'
sudo apt-get purge -y '^nvidia-.*' '^libnvidia-.*' '^xserver-xorg-video-nvidia.*'
sudo rm -f /etc/apt/sources.list.d/nvidia-container-toolkit.list \
           /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
sudo rm -rf /etc/nvidia-container-runtime /etc/cdi/nvidia.yaml

# 4. Leftover dependencies, then reboot onto nouveau
sudo apt-get autoremove --purge -y
sudo reboot
```

After the reboot, `dpkg -l | grep -E 'docker|containerd|nvidia'` prints nothing and `nvidia-smi` is not found. Reinstall with `sudo scripts/install-docker.sh && sudo scripts/install-nvidia.sh && sudo reboot`.

> ⚠️ Step 3 removes the desktop's NVIDIA driver. Do it with physical access to the machine: until the reinstall and reboot, the screen runs on nouveau.

---

## 🔗 Links

- [Build inputs (`mlib3rd/` contents) on Google Drive](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link)
- [Exported image (`images/`) on Google Drive](https://drive.google.com/drive/folders/1LTTGTxSGOOlnBSf-TOyOVT7iUzzliVHR?usp=drive_link)
