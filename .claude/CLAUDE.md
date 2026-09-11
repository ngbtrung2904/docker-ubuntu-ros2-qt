# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repository is

An environment definition, not an application. It builds a Docker image
(`rqt-based-env:latest`) for ROS 2 and Qt robotics development, and ships the scripts that launch
it. There is no test framework and no linter for the repository itself.

Every tracked file sits at the repository root. There is no `.devcontainer/` directory; if a
document or a comment refers to one, it is stale.

Image contents: Ubuntu 24.04, ROS 2 Rolling, Qt with Qt Creator, Qwt 6.3.0, Boost 1.83.0, plus
X11 and NVIDIA passthrough, host audio, and host desktop theming. `systemd` runs as PID 1.

## Layout

| Path | Role |
| --- | --- |
| `dockerfile` | Image recipe. Numbered steps, lowercase filename on purpose. |
| `docker-compose.yml` | Host-network variant. ROS 2 reaches host nodes. |
| `docker-compose-portmap.yml` | Published ports 61001/61002. ROS 2 does **not** reach host nodes. |
| `start-qt-container.sh` | Host-network launcher, plus all host theme and audio logic. |
| `start-qt-container-portmap.sh` | Port-mapped launcher. Sources the above for that logic. |
| `start-terminal-session.sh` | Throwaway shell, no systemd, no mounts. |
| `ros2-listener-host.sh` | Host-side listener with the Fast DDS profile applied. |
| `fastdds-profile.xml` | UDPv4-only transport, shared memory disabled. |
| `devcontainer.json` | VS Code Dev Containers entry point. |
| `mlib3rd/` | Build inputs. Gitignored, local only. |
| `images/` | Exported image tarball. Gitignored, local only. |
| `projects/` | Your project sources, mounted at `/workspace`. Gitignored, local only. |

`mlib3rd/`, `images/`, and `projects/` are deliberately outside git. Do not propose committing
them. They are also in `.dockerignore` except `mlib3rd/`, which the build needs.

## Build and run

```bash
xhost +local:docker            # required before any GUI run
docker compose build
docker compose up -d
docker compose exec rqt-based-env bash
docker compose down
```

Or via the launchers, which additionally apply host theme and audio:

```bash
./start-qt-container.sh            # --net=host; use when host <-> container ROS 2 is needed
./start-qt-container-portmap.sh    # ports 61001/61002; ROS 2 discovery to host will NOT work
docker exec -it rqt-based-env bash
docker stop rqt-based-env
```

Loading the prebuilt image instead of building: `docker load -i images/rqt-based-env.tar`.

## Build inputs and the one current blocker

The dockerfile copies from `mlib3rd/`: `rolling.tar.xz`, `python3_pkgs.txt`,
`boost_1_83_0.tar.gz`, `JetBrains_Mono-and-Roboto_Condensed.zip`, `snap7-full-1.4.2.7z`,
`rtl-sdr.tar.xz`, `digital-map.zip`, `qwt-6.3.0.tar.bz2`, `Qt.tar.gz`. Check they are all there
before proposing a build, and name any that are missing rather than working around them.

Snap7 is built from upstream source rather than shipped as a prebuilt `libsnap7.so`. The archive
is `.7z`, so step 6 installs the `7zip` package to unpack it and purges it again in the same `RUN`.
In Ubuntu 24.04 that package is `7zip`; `p7zip-full` is an empty transitional stub.

`rolling.tar.xz` is a whole `/opt/ros/rolling` tree unpacked at `/`, not an apt install. It carries
no package metadata, which is why `python3_pkgs.txt` exists to pull the Python dependencies ROS 2
expects. ROS tooling that lives only in the ROS apt repository was deliberately dropped from that
list, because one unresolvable name aborts the whole apt run.

## Qt is pinned to 6.7.3

`Qt.tar.gz` must have a bare `Qt/` at its root, so that `ADD mlib3rd/Qt.tar.gz /opt/` produces
`/opt/Qt/6.7.3`. Pack it with the parent as the working directory and a relative name:

```bash
tar -C "$HOME" -czf mlib3rd/Qt.tar.gz Qt
```

Passing an absolute path instead stores `home/<user>/` inside every entry, and the tree then lands
at `/opt/home/<user>/Qt`. A `RUN test -x` guard right after the `ADD` catches that immediately
rather than letting the qwt build fail much later.

The version appears in four places: that guard, the two `ENV` lines, and the qwt qmake call.
Changing Qt means editing those four and nothing else.

A full Qt install is around 23 GB, most of it separate `.debug` symbol files, the `Src` tree, and
Android kits that this Linux container cannot use. Excluding those brings it to roughly 4 GB.

## Fast DDS transport: the central runtime constraint

Shared memory cannot cross the container boundary, so `fastdds-profile.xml` forces UDPv4 and sets
`useBuiltinTransports=false`.

**Both sides must load the profile or discovery fails silently.** The container gets it through
`FASTDDS_DEFAULT_PROFILES_FILE`. On the host, export it yourself or run `./ros2-listener-host.sh`.
`ROS_DOMAIN_ID=0` on both sides. When a node runs but sees no topics, check this first.

## Host theme and audio are one script, and three pieces are load-bearing

`start-qt-container.sh` carries the launch, the host appearance setup, and the host audio setup.
Running it with `--apply-only [container]` skips the launch and re-applies just the post-start
steps, which is how a container started by `docker compose up -d` gets them. Sourcing the file
returns early, giving the caller `HOST_THEME_ARGS`, `HOST_AUDIO_ARGS`, `apply_host_theme`, and
`apply_host_audio` without launching anything; the port-mapped launcher relies on that.

Three things look removable and are not:

- **`dbus-x11`** in dockerfile step 10 provides the `dbus-daemon` that the gtk3 platform theme
  needs. The launcher starts a session bus on `/run/dbus-session-bus`.
- **`fonts-ubuntu`** in step 10 ships `/etc/fonts/conf.d/71-ubuntulegacy.conf`. The launcher
  replays its rejectfont rules against the mounted host fonts. Without the package, "Ubuntu"
  resolves to the legacy bold face and every label renders bold.
- **`libasound2-plugins`** in step 11 is the ALSA to PulseAudio bridge. Without it `default`
  falls back to raw card 0, which is why `aplay -L` would disagree with the host.

`QT_QPA_PLATFORM=xcb` and `QT_QPA_PLATFORMTHEME=gtk3` look alike but are different settings. The
first picks the windowing backend, the second the palette source. Both are needed.

## Why the container is privileged

`systemd` as PID 1 requires `privileged`, host cgroups, the `/sys/fs/cgroup` bind mount, and tmpfs
on `/run`, `/run/lock`, `/tmp`. These are one unit. Do not remove one while hardening. The
launchers then poll `systemctl is-system-running` for up to 60 seconds and copy the host timezone in.

## Paths inside the image

| Thing | Location |
| --- | --- |
| ROS 2 Rolling | `/opt/ros/rolling`, sourced from `/root/.bashrc` |
| Qt kit | `/opt/Qt/current`, a symlink to the installed version's `gcc_64` |
| Qt Creator | `/opt/Qt/Tools/QtCreator/bin` |
| Qwt 6.3.0 | `/usr/local/qwt-6.3.0` |
| Digital map data | `/opt/digital-map` |
| Snap7 | `/usr/lib/libsnap7.so` |
| Your projects | `/workspace` |

Qt Creator settings persist on the host under `~/.docker-qtcreator/`. Build tooling inside:
cmake, ninja, mold, gcc, clang, ccache, colcon.

## Conventions

- Keep the paired files in sync: the two compose files, and the two launcher scripts. A fix applied
  to one usually belongs in the other.
- Scripts resolve their own directory into `SCRIPT_DIR` and mount relative to that. Never use
  `$(pwd)`, and never hardcode a home directory.
- Dockerfile steps are numbered with a comment header, and each large `COPY` is deleted inside the
  same `RUN` so the layer stays small.
- Commit messages are short and imperative, Vietnamese or English. Default branch is `master`.

## Known drift

- `README.md` and `docker-install-guide.md` describe a `qt-based-img/` layout, a
  `build-docker-img.sh` that does not exist, a `docker-img/` folder now called `images/`, and Qt
  6.7.2. Treat both documents as out of date until they are rewritten.
- `README.md` still documents `host-theme.sh` and `host-audio.sh` as separate files with
  their own usage examples. They no longer exist.
