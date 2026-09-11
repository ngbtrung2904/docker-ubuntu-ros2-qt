# ROS 2 & Qt Development Docker Environment (`rqt-based-env`)

A Docker environment based on **Ubuntu 24.04 LTS** for robotics software development, computer vision, and GUI applications. It integrates **ROS 2 Rolling**, **Qt 6.7.3**, **Boost 1.83.0**, hardware interfacing tools, and full X11/NVIDIA GPU acceleration, and it matches the host desktop's appearance and audio routing.

---

## 📑 Table of Contents

- [Features](#-features)
- [Project Structure](#-project-structure)
- [Build Inputs & Downloads](#-build-inputs--downloads)
- [Prerequisites](#-prerequisites)
- [Quick Start](#-quick-start)
- [ROS 2 & Fast DDS Communication](#-ros-2--fast-dds-communication)
- [Mounted Volumes & Persistent Data](#-mounted-volumes--persistent-data)
- [Appearance: Matching the Host Desktop](#-appearance-matching-the-host-desktop)
- [Audio: Sound Devices and the Host Audio Server](#-audio-sound-devices-and-the-host-audio-server)
- [Documentation & External Links](#-documentation--external-links)

---

## ✨ Features

- **Base OS**: Ubuntu 24.04 LTS (Noble Numbat)
- **Robotics Middleware**: ROS 2 Rolling, with Fast DDS configured for UDP-based host-container communication.
- **GUI & IDE**: Qt 6.7.3 and Qt Creator with X11 forwarding and NVIDIA GPU acceleration (`--gpus all`).
- **Host-matched Appearance**: GUI apps pick up the host desktop's GTK theme, icon set, cursor and fonts, dark mode included. See [Appearance](#-appearance-matching-the-host-desktop).
- **Host-matched Audio**: ALSA `default` routes to the host audio server, so recording and playback behave as they do on the host. See [Audio](#-audio-sound-devices-and-the-host-audio-server).
- **System Service Management**: Runs `systemd` as PID 1 (`/sbin/init`), so `systemctl` and `timedatectl` work inside.
- **Development & Build Tools**: CMake, Ninja, Mold linker, GCC, Clang, ccache, colcon, gedit, Terminator.
- **Libraries & Hardware Interfacing**:
  - Boost 1.83.0 and Qwt 6.3.0, both built from source
  - Snap7 for Siemens S7 PLC communication, built from upstream source
  - RTL-SDR for software-defined radio development
  - Google Log (`glog`), Protobuf, ZeroMQ (`cppzmq`), SQLite3, GDAL, OpenGL/FreeGLUT
  - JetBrains Mono & Roboto Condensed developer fonts
  - Digital map datasets pre-extracted to `/opt/digital-map`

---

## 📁 Project Structure

Everything tracked in git sits at the repository root.

```
docker-ubuntu-ros2-qt/
├── dockerfile                       # Image recipe (Ubuntu 24.04 + ROS 2 Rolling + Qt 6.7.3)
├── docker-compose.yml               # Host-network Compose config (GPU, GUI, audio, FastDDS, systemd)
├── docker-compose-portmap.yml       # Same, but with published ports instead of host networking
├── devcontainer.json                # VS Code Dev Containers entry point
├── start-qt-container.sh            # Main launcher: X11 + GPU + systemd + FastDDS + host theme + host audio
├── start-qt-container-portmap.sh    # Port-mapped launcher; sources the above for theme/audio
├── start-terminal-session.sh        # Minimal disposable bash session, no systemd, no mounts
├── ros2-listener-host.sh            # Run a ROS 2 listener on the host with the UDP FastDDS profile
├── fastdds-profile.xml              # Fast DDS: UDPv4 only, shared memory disabled
├── docker-install-guide.md          # Detailed setup & troubleshooting documentation
├── .dockerignore / .gitignore
│
├── mlib3rd/                         # ⚠️ Build inputs (git-ignored, see Build Inputs below)
├── images/                          # ⚠️ Pre-built image archive (git-ignored)
│   └── rqt-based-env.tar            #   Exported image (~3.9 GB), load with: docker load -i
└── projects/                        # ⚠️ Your project sources (git-ignored), mounted at /workspace
```

> **Note:** `mlib3rd/`, `images/` and `projects/` are **git-ignored**. The first two must be present before building.

---

## 📦 Build Inputs & Downloads

The `dockerfile` copies everything it needs from `mlib3rd/`:

| File | Approx. size | Becomes |
| :--- | :--- | :--- |
| `Qt.tar.gz` | 7 GB | `/opt/Qt` (Qt 6.7.3 + Qt Creator) |
| `boost_1_83_0.tar.gz` | 138 MB | Built and installed to `/usr/local` |
| `digital-map.zip` | 128 MB | `/opt/digital-map` |
| `rolling.tar.xz` | 34 MB | `/opt/ros/rolling` (unpacked at `/`, not an apt install) |
| `JetBrains_Mono-and-Roboto_Condensed.zip` | 5 MB | `/usr/share/fonts` |
| `qwt-6.3.0.tar.bz2` | 5 MB | Built and installed to `/usr/local/qwt-6.3.0` |
| `snap7-full-1.4.2.7z` | 3 MB | Built; `libsnap7.so` installed to `/usr/lib` |
| `rtl-sdr.tar.xz` | 600 KB | Built and installed system-wide |
| `python3_pkgs.txt` | 4 KB | apt package list ROS 2 needs (the tarball carries no apt metadata) |

Large binaries are hosted on Google Drive:

| Resource | Link |
| :--- | :--- |
| **Build inputs** (`mlib3rd/` contents) | [📥 Download](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link) |
| **Pre-built Docker image** (`images/rqt-based-env.tar`) | [📥 Download](https://drive.google.com/drive/folders/1LTTGTxSGOOlnBSf-TOyOVT7iUzzliVHR?usp=drive_link) |

### Packing `Qt.tar.gz` yourself

The archive must have a bare `Qt/` at its root, so that `ADD` lands it at `/opt/Qt`. Pack from the parent directory with a **relative** name:

```bash
tar -C "$HOME" -czf mlib3rd/Qt.tar.gz Qt
```

Passing an absolute path instead (`tar czf ... /home/you/Qt`) stores `home/you/` inside every entry, and the tree ends up at `/opt/home/you/Qt`. The dockerfile checks for `/opt/Qt/6.7.3/gcc_64/bin/qmake` right after the `ADD` and fails immediately if so.

A full Qt install is around 23 GB, mostly debug symbols, the `Src` tree and Android kits this container cannot use. Excluding them brings it to roughly 4 GB:

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

`-I pigz` replaces `-z` with parallel gzip. Drop the `*.debug` and `Src` exclusions if you need to step into Qt's own source.

---

## 🛠️ Prerequisites

1. **Docker & Docker Compose**, installed and running on Linux.
2. **NVIDIA Container Toolkit** for GPU acceleration. See [`docker-install-guide.md`](docker-install-guide.md) for setup.
3. **X11 display** on the host for GUI applications.
4. **Build inputs** in `mlib3rd/`, or the pre-built image in `images/`.

---

## 🚀 Quick Start

### 1. Build the image

```bash
docker compose build
```

The build context is the repository root, because the dockerfile copies from `mlib3rd/`. `.dockerignore` keeps `images/` and `projects/` out of the upload.

Or load the pre-built image instead:

```bash
docker load -i images/rqt-based-env.tar
```

### 2. Run with GUI support

**Option A: the launcher script (recommended).** It applies the host theme and audio, which Compose alone does not do.

```bash
./start-qt-container.sh
```

It grants local X access, starts the container with `--net=host`, `--gpus all` and the Fast DDS profile, boots `systemd` as PID 1, syncs the host timezone, applies the host appearance and audio, then attaches an interactive shell.

**Option B: Docker Compose.**

```bash
xhost +local:docker
docker compose up -d
./start-qt-container.sh --apply-only    # theme + audio post-start steps
docker compose exec rqt-based-env bash
```

**Option C: port-mapped mode**, when other devices must connect in on ports 61001/61002:

```bash
./start-qt-container-portmap.sh
```

> ⚠️ Without `--net=host`, ROS 2 discovery between the container and the host does **not** work. Use Option A or B if you need that.

Additional shells and shutdown:

```bash
docker exec -it rqt-based-env bash
docker stop rqt-based-env
```

### 3. Quick throwaway shell

```bash
./start-terminal-session.sh
```

No systemd, no mounts, no GUI.

### 4. Remove containers and images

```bash
docker ps -aq | xargs -r docker rm -f          # remove all containers
docker images -q | xargs -r docker rmi -f      # remove all images
docker system prune -a --volumes -f            # clean caches, networks, volumes
```

---

## 📡 ROS 2 & Fast DDS Communication

The container runs in an isolated IPC space, so **Fast DDS shared memory transport cannot reach host ROS 2 nodes**. `fastdds-profile.xml` forces UDPv4 and disables the built-in transports.

**Both sides must load the profile, or discovery fails silently.** Inside the container the launchers and Compose files set `FASTDDS_DEFAULT_PROFILES_FILE=/opt/ros/fastdds-profile.xml`. On the host:

```bash
export FASTDDS_DEFAULT_PROFILES_FILE=$(pwd)/fastdds-profile.xml
export ROS_DOMAIN_ID=0
```

Or run the provided script, which does both:

```bash
./ros2-listener-host.sh
```

If a node runs but sees no topics, check the profile on both sides before anything else.

---

## 💾 Mounted Volumes & Persistent Data

- `projects/` → `/workspace` in the container
- `fastdds-profile.xml` → `/opt/ros/fastdds-profile.xml` (read-only)
- Qt Creator settings persist on the host under `~/.docker-qtcreator/` (`config`, `share`, `cache`)
- Host theme, icon and font directories, read-only under `/usr/local/share`
- Host sound devices (`/dev/snd`) and the PulseAudio/PipeWire socket

---

## 🎨 Appearance: Matching the Host Desktop

`start-qt-container.sh` makes GUI apps inside the container look like the ones on the host: same GTK theme, icon set, cursor, fonts and dark/light mode. `start-qt-container-portmap.sh` sources it for the same behaviour.

**How it works**

The container shares the host X display, so the host's XSETTINGS manager (`gsd-xsettings`) already *tells* GTK apps in the container which theme to use. Running `gtk-query-settings` inside the container reports the host values. What was missing were the files themselves. The launcher therefore:

1. Bind-mounts the host's `/usr/share/{themes,icons,fonts}` read-only under `/usr/local/share/`, plus any per-user `~/.themes`, `~/.icons`, `~/.fonts`. This is *additive*: `/usr/local/share` is searched before `/usr/share`, so host copies win but nothing shipped in the image is hidden.
2. Relies on `QT_QPA_PLATFORMTHEME=gtk3`, set in the image, so Qt apps including Qt Creator build their palette and fonts from the same GTK theme. The Qt in `/opt` ships the required `libqgtk3.so`, so no distro Qt is pulled in.
3. Points `XCURSOR_PATH` and `XCURSOR_THEME` at the host cursor theme, since Xcursor does not search `/usr/local/share` by default.
4. Writes `~/.config/gtk-{3,4}.0/settings.ini` in the container as a fallback for hosts with no XSETTINGS manager, and starts a container-local session D-Bus that the GTK platform theme expects.
5. Sets Qt Creator's own theme (`flat-dark` / `flat-light`) to match the host's `color-scheme`, in the persisted `~/.docker-qtcreator/config/QtCreator.ini`. Qt Creator paints its own UI, so this is separate from the GTK theme.
6. Replays the host's fontconfig *reject* rules into `/etc/fonts/conf.d/71-host-legacy-reject.conf`, rewritten for the `/usr/local/share/fonts` mount point. Font packages use these to hide superseded font files: `fonts-ubuntu` rejects the legacy static `Ubuntu-*.ttf` so the variable `Ubuntu[wdth,wght].ttf` wins. Those rules live in `/etc/fonts`, which is not mounted, so without this step `Ubuntu` resolves to `Ubuntu-B.ttf` and all text renders **bold**.
7. Writes `/etc/fonts/conf.d/72-host-font-priority.conf`, hiding font files that dockerfile step 5 dumps flat into `/usr/share/fonts/` when they compete for a family the host also ships. Three different files named `RobotoCondensed-Regular.ttf` exist between the two machines; without this the container picked the zip's redesign while the host uses the classic `fonts-roboto` cut, so any app calling `QFont("Roboto Condensed")` rendered in a visibly different, heavier face. Families the host does not have (JetBrains Mono) are left alone, and hidden files stay on disk.
8. Bind-mounts loose font files from the host's `/usr/share/fonts` root onto the identical path in the container. Code that opens a `.ttf` by absolute path never consults fontconfig, so rule 7 cannot reach it, and the image ships a *different* font under the very same name. `GLFontManager.cpp` hardcodes `/usr/share/fonts/RobotoCondensed-Regular.ttf`, which is one file on the host and another in the image; without this mount, OpenGL-rendered text would still differ once the Qt widgets matched.

Everything is read from the host at launch, so changing the host theme and re-running the launcher is enough. No image rebuild.

**Verifying it worked**

```bash
# inside the container - should print the host's theme, not "Adwaita"
gtk-query-settings | grep -E "theme-name|font-name"
```

**Two image packages are load-bearing here, despite looking like fallbacks.** `dbus-x11` provides the `dbus-daemon` the gtk3 platform theme needs, and `fonts-ubuntu` ships the reject rules that step 6 replays. The `yaru-theme-*` and `gnome-themes-extra` packages are genuine fallbacks, for running the image without the launcher.

If you started the container with Compose, apply the post-start part once it is up:

```bash
./start-qt-container.sh --apply-only
```

> **Note:** Qt Creator's theme is re-applied on every launch. To pick a theme by hand inside Qt Creator instead, drop the `apply_host_theme` call from the launcher.

---

## 🔊 Audio: Sound Devices and the Host Audio Server

`start-qt-container.sh` also fixes two distinct problems for code that opens ALSA directly, for example `snd_pcm_open("default", ...)`.

1. **`/dev/snd` is a snapshot.** It is populated when the container is created, so a card plugged in later, a USB capture device for instance, has no device nodes inside even though `/proc/asound` (shared with the host) lists it. Bind-mounting `/dev/snd` keeps the nodes in sync.
2. **`"default"` means something different inside.** On the host, `pipewire-alsa` redefines the ALSA `default` PCM to follow the desktop's current default source/sink. The container has no such config, so `default` falls back to raw card 0, usually the unconnected onboard input. This is also why `aplay -L` output differs between host and container. An app that records fine from `"default"` on the host therefore captures the wrong device inside. The launcher mounts the host's PulseAudio/PipeWire socket, plus the auth cookie since the container runs as root and the socket authenticates by uid, and writes an `/etc/asound.conf` routing `default` through it.

Verify with:

```bash
arecord -D default -d 1 -f cd -v /tmp/t.wav   # should print "ALSA <-> PulseAudio PCM I/O Plugin"
aplay -L                                       # should match the host's output
ls /dev/snd                                    # should list every card the host has
```

The image installs `libasound2-plugins`, which carries the ALSA to PulseAudio bridge and is required for the above, plus `alsa-utils` for `aplay` and `arecord`.

With Compose, apply the post-start part once the container is up:

```bash
./start-qt-container.sh --apply-only
```

---

## 📚 Documentation & External Links

For host NVIDIA setup, Fast DDS configuration rationale and troubleshooting, see [`docker-install-guide.md`](docker-install-guide.md).

- [Build inputs on Google Drive](https://drive.google.com/drive/folders/14BGOLk7sR8P1wWYu3HLT-rhYEA8ev69-?usp=drive_link)
- [Pre-built image archive on Google Drive](https://drive.google.com/drive/folders/1LTTGTxSGOOlnBSf-TOyOVT7iUzzliVHR?usp=drive_link)
