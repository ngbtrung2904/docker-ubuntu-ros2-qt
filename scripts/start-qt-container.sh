#!/bin/bash

# Container name so we can exec into the running systemd container
CONTAINER_NAME="rqt-based-env"

# Everything this script mounts lives in the folder above scripts/, so resolve
# that once rather than depending on the directory the user happens to be in.
SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

APPLY_ONLY=0
START_GUI=0
SERVICE_ROLE=""
case "$1" in
    --apply-only)
        APPLY_ONLY=1
        [ -n "$2" ] && CONTAINER_NAME="$2"
        ;;
    # Used by the host unit that install-autostart.sh writes.
    --service)
        SERVICE_ROLE="$2"
        SERVICE_VARIANT="$3"
        ;;
    # Used by the RDD desktop icon and the LCMS login autostart.
    --start-gui)
        START_GUI=1
        ;;
esac

# ----------------------------------------------------------------------------
# Host appearance: theme, icons, cursor and fonts
# ----------------------------------------------------------------------------
# Read a value from the host's GNOME interface settings, without quotes/types.
_host_gs() {
    gsettings get org.gnome.desktop.interface "$1" 2>/dev/null \
        | sed "s/^[a-z0-9]* //; s/^'//; s/'$//"
}

HOST_GTK_THEME="$(_host_gs gtk-theme)";              : "${HOST_GTK_THEME:=Adwaita}"
HOST_ICON_THEME="$(_host_gs icon-theme)";            : "${HOST_ICON_THEME:=Adwaita}"
HOST_CURSOR_THEME="$(_host_gs cursor-theme)";        : "${HOST_CURSOR_THEME:=Adwaita}"
HOST_CURSOR_SIZE="$(_host_gs cursor-size)";          : "${HOST_CURSOR_SIZE:=24}"
HOST_FONT="$(_host_gs font-name)";                   : "${HOST_FONT:=Sans 10}"
HOST_COLOR_SCHEME="$(_host_gs color-scheme)";        : "${HOST_COLOR_SCHEME:=default}"
HOST_CURSOR_SIZE="${HOST_CURSOR_SIZE//[!0-9]/}";     : "${HOST_CURSOR_SIZE:=24}"

# Dark if the host asked for dark, or if the theme name says so.
HOST_PREFER_DARK=0
[ "$HOST_COLOR_SCHEME" = "prefer-dark" ] && HOST_PREFER_DARK=1
case "$HOST_GTK_THEME" in *-dark|*-Dark) HOST_PREFER_DARK=1 ;; esac

HOST_THEME_ARGS=(
    -v /usr/share/themes:/usr/local/share/themes:ro
    -v /usr/share/icons:/usr/local/share/icons:ro
    -v /usr/share/fonts:/usr/local/share/fonts:ro
    -e XDG_DATA_DIRS=/usr/local/share:/usr/share
    # Xcursor does not look in /usr/local/share by default, so spell the path out.
    -e XCURSOR_PATH=/root/.icons:/usr/local/share/icons:/usr/share/icons:/usr/share/pixmaps
    -e XCURSOR_THEME="$HOST_CURSOR_THEME"
    -e XCURSOR_SIZE="$HOST_CURSOR_SIZE"
    -e GDK_BACKEND=x11
    -e DBUS_SESSION_BUS_ADDRESS=unix:path=/run/dbus-session-bus
    # No accessibility bus in the container - skip it instead of warning.
    -e NO_AT_BRIDGE=1
)

# Per-user themes/fonts installed on the host, if any.
_host_theme_add_user_dir() {
    [ -d "$1" ] && HOST_THEME_ARGS+=( -v "$1:$2:ro" )
}
_host_theme_add_user_dir "$HOME/.themes"              /root/.themes
_host_theme_add_user_dir "$HOME/.icons"               /root/.icons
_host_theme_add_user_dir "$HOME/.fonts"               /root/.fonts
_host_theme_add_user_dir "$HOME/.local/share/themes"  /root/.local/share/themes
_host_theme_add_user_dir "$HOME/.local/share/icons"   /root/.local/share/icons
_host_theme_add_user_dir "$HOME/.local/share/fonts"   /root/.local/share/fonts

while IFS= read -r _host_font; do
    [ -n "$_host_font" ] && HOST_THEME_ARGS+=( -v "$_host_font:$_host_font:ro" )
done < <(find /usr/share/fonts -maxdepth 1 -type f \
             \( -name '*.ttf' -o -name '*.otf' -o -name '*.ttc' \) 2>/dev/null | sort)

# ----------------------------------------------------------------------------
# Host audio: sound device nodes and the host's audio server
# ----------------------------------------------------------------------------
_host_audio_runtime="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
_host_audio_plugin=/usr/lib/x86_64-linux-gnu/alsa-lib/libasound_module_pcm_pulse.so

HOST_AUDIO_ARGS=()

# Live sound device nodes rather than a start-time snapshot.
[ -d /dev/snd ] && HOST_AUDIO_ARGS+=( -v /dev/snd:/dev/snd )

# The host's audio server. Its socket authenticates by uid, and the container
# runs as root, so the cookie has to come along too.
if [ -S "$_host_audio_runtime/pulse/native" ]; then
    HOST_AUDIO_ARGS+=(
        -v "$_host_audio_runtime/pulse/native:/run/host-pulse"
        -e PULSE_SERVER=unix:/run/host-pulse
    )
    if [ -f "$HOME/.config/pulse/cookie" ]; then
        HOST_AUDIO_ARGS+=(
            -v "$HOME/.config/pulse/cookie:/root/.config/pulse/cookie:ro"
            -e PULSE_COOKIE=/root/.config/pulse/cookie
        )
    fi
fi

# ----------------------------------------------------------------------------
# App data kept on the host: system logs, recorded sessions, exported videos
# ----------------------------------------------------------------------------
# The apps write these below $HOME, which is /root in the container and gone
# with it. Mounting the host user's folders keeps the bare-metal paths.
HOST_DATA_ARGS=()
for _host_data_dir in .app rrecord recordings; do
    # Created here, or Docker would create them root-owned.
    mkdir -p "$HOME/$_host_data_dir"
    HOST_DATA_ARGS+=( -v "$HOME/$_host_data_dir:/root/$_host_data_dir" )
done

# ----------------------------------------------------------------------------
# Host SSH keys: the console's power-off scripts ssh to the station machines
# ----------------------------------------------------------------------------
# The apps run as root in the container, so root needs the host user's key and
# known_hosts (install-ssh-target.sh sets them up). Mounted read-only aside and
# copied in by apply_host_ssh: ssh rejects a ~/.ssh/config that root does not own.
HOST_SSH_ARGS=()
[ -d "$HOME/.ssh" ] && HOST_SSH_ARGS+=( -v "$HOME/.ssh:/run/host-ssh:ro" )

# ----------------------------------------------------------------------------
# Post-start steps, both needing a running container
# ----------------------------------------------------------------------------
apply_host_theme() {
    local container="$1"

    echo "Applying host appearance: $HOST_GTK_THEME / $HOST_ICON_THEME / $HOST_FONT"

    # Session bus for the GTK platform theme and GTK apps.
    docker exec "$container" bash -c '
        [ -S /run/dbus-session-bus ] ||
            dbus-daemon --session --address=unix:path=/run/dbus-session-bus --fork
    ' >/dev/null 2>&1 || true

    # A settings.ini fallback for the values XSETTINGS normally supplies (and
    # the source GTK4 apps read most reliably).
    docker exec -i "$container" bash -c '
        mkdir -p /root/.config/gtk-3.0 /root/.config/gtk-4.0
        cat > /root/.config/gtk-3.0/settings.ini
        cp /root/.config/gtk-3.0/settings.ini /root/.config/gtk-4.0/settings.ini
    ' <<EOF
[Settings]
gtk-theme-name=$HOST_GTK_THEME
gtk-icon-theme-name=$HOST_ICON_THEME
gtk-cursor-theme-name=$HOST_CURSOR_THEME
gtk-cursor-theme-size=$HOST_CURSOR_SIZE
gtk-font-name=$HOST_FONT
gtk-application-prefer-dark-theme=$HOST_PREFER_DARK
EOF

    # Qt Creator paints its own UI, so match it to the host's light/dark mode.
    local creator_theme=flat-light
    [ "$HOST_PREFER_DARK" = 1 ] && creator_theme=flat-dark
    docker exec -i "$container" python3 - "$creator_theme" <<'PY'
import os, re, sys

path, theme = "/root/.config/QtProject/QtCreator.ini", sys.argv[1]
os.makedirs(os.path.dirname(path), exist_ok=True)
try:
    lines = open(path).read().splitlines()
except FileNotFoundError:
    lines = []

out, in_core, done = [], False, False
for line in lines:
    if line.startswith("["):
        if in_core and not done:
            out.append("Theme=" + theme)
            done = True
        in_core = line.strip() == "[Core]"
    elif in_core and re.match(r"\s*Theme\s*=", line):
        out.append("Theme=" + theme)
        done = True
        continue
    out.append(line)
if not done:
    if not in_core:
        out.append("[Core]")
    out.append("Theme=" + theme)

open(path, "w").write("\n".join(out) + "\n")
PY
    local reject_conf
    reject_conf=$(python3 - <<'PY'
import glob, re

globs = []
for path in sorted(glob.glob("/etc/fonts/conf.d/*.conf")):
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        continue
    if "rejectfont" not in text:
        continue
    for g in re.findall(r"<glob>([^<]+)</glob>", text):
        globs.append(g)
        if g.startswith("/usr/share/fonts"):
            globs.append(g.replace("/usr/share/fonts", "/usr/local/share/fonts", 1))

if globs:
    print("<?xml version='1.0'?>")
    print("<!DOCTYPE fontconfig SYSTEM 'fonts.dtd'>")
    print("<!-- generated by start-qt-container.sh from the host's fontconfig rules -->")
    print("<selectfont><rejectfont>")
    for g in dict.fromkeys(globs):
        print("    <glob>%s</glob>" % g)
    print("</rejectfont></selectfont>")
PY
)
    if [ -n "$reject_conf" ]; then
        printf '%s\n' "$reject_conf" | docker exec -i "$container" \
            bash -c 'cat > /etc/fonts/conf.d/71-host-legacy-reject.conf'
    fi

    docker exec -i "$container" python3 - <<'PY' >/dev/null 2>&1 || true
import glob, subprocess

CONF = "/etc/fonts/conf.d/72-host-font-priority.conf"
HOST_DIRS = ("/usr/local/share/fonts/", "/root/.fonts/", "/root/.local/share/fonts/")

flat = sorted(glob.glob("/usr/share/fonts/*.ttf") +
              glob.glob("/usr/share/fonts/*.otf") +
              glob.glob("/usr/share/fonts/*.ttc"))

def families(text):
    # A variable font reports one line per named instance, and a single line
    # may list several comma-separated names.
    return {f.strip() for f in text.replace("\n", ",").split(",") if f.strip()}

# Query the files directly, so this does not depend on the rules we wrote before.
flat_families = {}
for path in flat:
    out = subprocess.run(["fc-query", "-f", "%{family}\n", path],
                         capture_output=True, text=True).stdout
    fams = families(out)
    if fams:
        flat_families[path] = fams

host_families, other_families = set(), set()
listing = subprocess.run(["fc-list", "-f", "%{file}\t%{family}\n"],
                         capture_output=True, text=True).stdout
for line in listing.splitlines():
    path, _, fams = line.partition("\t")
    if path in flat_families:
        continue
    other_families |= families(fams)
    if path.startswith(HOST_DIRS):
        host_families |= families(fams)

# Hide a flat file when it competes for a family the host also provides and
# that family survives elsewhere. Weights only the image had (SemiBold, Black,
# and the variable font's extra instances) go too - the host has none of them
# either, which is the point.
hide = []
for p, fams in flat_families.items():
    shared = fams & host_families
    if shared and shared <= other_families:
        hide.append(p)
hide.sort()

if hide:
    with open(CONF, "w") as fh:
        fh.write("<?xml version='1.0'?>\n")
        fh.write("<!DOCTYPE fontconfig SYSTEM 'fonts.dtd'>\n")
        fh.write("<!-- generated by start-qt-container.sh: host fonts win over image duplicates -->\n")
        fh.write("<selectfont><rejectfont>\n")
        for p in hide:
            fh.write("    <glob>%s</glob>\n" % p)
        fh.write("</rejectfont></selectfont>\n")
else:
    try:
        import os
        os.remove(CONF)
    except OSError:
        pass
PY

    # Pick up the newly mounted host fonts and the rules above.
    docker exec "$container" fc-cache -f >/dev/null 2>&1 || true
}

apply_host_audio() {
    local container="$1"

    docker exec "$container" test -S /run/host-pulse 2>/dev/null || {
        echo "Host audio server not shared - ALSA 'default' stays on raw card 0."
        return 0
    }

    # ALSA reaches PulseAudio/PipeWire through this plugin, which step 11 of the
    # dockerfile installs. Absent means the image predates that step.
    if ! docker exec "$container" test -e "$_host_audio_plugin" 2>/dev/null; then
        echo "  libasound2-plugins missing from the image - ALSA 'default' stays on raw card 0."
        return 0
    fi

    # What pipewire-alsa does on the host, minus the running PipeWire daemon.
    docker exec -i "$container" bash -c 'cat > /etc/asound.conf' <<'EOF'
# generated by start-qt-container.sh - route ALSA through the host's audio server
pcm.!default {
    type pulse
    hint.description "Default (host audio server)"
}
ctl.!default {
    type pulse
}
EOF
    echo "Audio: ALSA 'default' routed to the host audio server."
}

apply_host_ssh() {
    local container="$1"
    docker exec "$container" bash -c '
        [ -d /run/host-ssh ] || exit 0
        rm -rf /root/.ssh
        cp -r /run/host-ssh /root/.ssh
        chown -R root:root /root/.ssh
        find /root/.ssh -type d -exec chmod 700 {} +
        find /root/.ssh -type f -exec chmod 600 {} +
        chmod 644 /root/.ssh/*.pub 2>/dev/null
    ' && echo "SSH: host keys copied to /root/.ssh."
}

# ----------------------------------------------------------------------------
# PAR console: runwayconfig.ini and glidepathconfig.ini linked onto the host desktop
# ----------------------------------------------------------------------------
# Qt Creator's build folder for every app.
: "${BUILD_KIT:=Desktop_Qt_6_7_3-Debug}"

# _repo_dir <variant> <app>: the repository folder in projects/ for that app,
# whatever prefix or suffix it was unzipped with (<prefix>_par_rdd<suffix>).
_repo_dir() {
    local d
    for d in "$ROOT_DIR/projects/"*_"$1"_"$2"*/; do
        [ -d "$d" ] && { basename "$d"; return 0; }
    done
    return 1
}

# _par_config_link <container> <file name> <override>: link one of the PAR
# console's settings files onto the host desktop, once it has been built.
_par_config_link() {
    local container="$1" name="$2" rel="$3"
    local desktop
    desktop="$(xdg-user-dir DESKTOP 2>/dev/null)";      : "${desktop:=$HOME/Desktop}"

    # Path below projects/ (host) and /workspace (container).
    if [ -z "$rel" ]; then
        rel="$(_repo_dir par rdd)" || {
            echo "PAR rdd not found in projects/ - no desktop link for $name."
            return 0
        }
        rel="$rel/build/$BUILD_KIT/app/settings/$name"
    fi
    local host_file="$ROOT_DIR/projects/$rel"

    if [ ! -f "$host_file" ]; then
        echo "PAR $name not built yet - no desktop link ($host_file)."
        return 0
    fi

    # The build runs as root in the container, so the host user could not save
    # the file. Qt keeps these modes when the console rewrites the file.
    docker exec "$container" bash -c '
        chmod a+rw "/workspace/$1" && chmod a+rwx "$(dirname "/workspace/$1")"
    ' _ "$rel" >/dev/null 2>&1 || true

    # The link lives on the host, where the operator's desktop is.
    mkdir -p "$desktop"
    ln -sf "$host_file" "$desktop/$name"
    echo "PAR: $desktop/$name -> $host_file"
}

# Set PAR_RDD_RUNWAY_CONFIG or PAR_RDD_GLIDEPATH_CONFIG (a path below projects/)
# when the build folder is laid out differently.
apply_par_config_links() {
    _par_config_link "$1" runwayconfig.ini "$PAR_RDD_RUNWAY_CONFIG"
    _par_config_link "$1" glidepathconfig.ini "$PAR_RDD_GLIDEPATH_CONFIG"
}

# ----------------------------------------------------------------------------
# Radar apps run by the container's systemd, restarted when they crash
# ----------------------------------------------------------------------------
# _app_unit <container> <variant> <app> [run.sh args]
_app_unit() {
    local repo dir
    repo="$(_repo_dir "$2" "$3")" || {
        echo "No $2 $3 repository in $ROOT_DIR/projects." >&2
        return 1
    }
    # rdd runs from the repository root, every other app from src/.
    dir="$repo"
    [ "$3" = rdd ] || dir="$repo/src"

    docker exec -i "$1" bash -c "cat > /etc/systemd/system/radar-$3.service" <<EOF
[Unit]
Description=Radar $3 ($2)
# Restart after a crash or a close, however often it happens.
StartLimitIntervalSec=0

[Service]
WorkingDirectory=/workspace/$dir
EnvironmentFile=/run/radar.env
ExecStart=/bin/bash -c 'source /opt/ros/rolling/setup.bash && exec ./run.sh $4'
Restart=always
RestartSec=3
EOF
}

apply_radar_apps() {
    local container="$1" role="$2" v="$3"

    # Services do not inherit the container's docker -e variables, so hand them
    # over. DISPLAY comes from the logged-in session instead (--start-gui).
    docker exec "$container" bash -c '
        tr "\0" "\n" < /proc/1/environ | grep -v -e "^HOME=" -e "^DISPLAY=" > /run/radar.env
        echo HOME=/root >> /run/radar.env
    '

    # rmm runs on the ASR LCMS machine, but on the PAR RDD machine.
    local rmm_role=lcms
    [ "$v" = par ] && rmm_role=rdd

    local backends=()
    case "$role" in
        lcms|rdd|rcms|rrdd)
            _app_unit "$container" "$v" rdd "--$role" || return 1 ;;
        rdp)
            _app_unit "$container" "$v" rdp || return 1
            backends=(rdp) ;;
        dvaz|rdvaz)
            _app_unit "$container" "$v" dvaz || return 1
            backends=(dvaz) ;;
        *)
            echo "Unknown role '$role' (lcms|rdp|rdd|dvaz|rcms|rrdd|rdvaz)." >&2
            return 1 ;;
    esac
    if [ "$role" = "$rmm_role" ]; then
        _app_unit "$container" "$v" rmm || return 1
        backends+=(rmm)
    fi

    docker exec "$container" systemctl daemon-reload
    for app in "${backends[@]}"; do
        docker exec "$container" systemctl start "radar-$app.service"
        echo "Started radar-$app"
    done
}

if [ "${BASH_SOURCE[0]}" != "$0" ]; then
    return 0
fi

if [ "$APPLY_ONLY" = 1 ]; then
    apply_host_theme "$CONTAINER_NAME"
    apply_host_audio "$CONTAINER_NAME"
    apply_host_ssh "$CONTAINER_NAME"
    apply_par_config_links "$CONTAINER_NAME"
    exit 0
fi

if [ "$START_GUI" = 1 ]; then
    xhost +local:docker

    # At login the container may still be booting.
    for i in $(seq 1 300); do
        docker exec "$CONTAINER_NAME" test -e /etc/systemd/system/radar-rdd.service 2>/dev/null && break
        sleep 1
    done

    docker exec "$CONTAINER_NAME" systemctl set-environment DISPLAY="$DISPLAY"
    exec docker exec "$CONTAINER_NAME" systemctl start radar-rdd.service
fi

# The host unit starts before the docker daemon is guaranteed to be up.
if [ -n "$SERVICE_ROLE" ]; then
    for i in $(seq 1 120); do
        docker info >/dev/null 2>&1 && break
        sleep 1
    done
fi

# ----------------------------------------------------------------------------
# Launch
# ----------------------------------------------------------------------------

# Grant local X server access
xhost +local:docker

# Remove any stale container with the same name (fresh start every run)
docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true

# Start container with systemd as PID 1 so systemd tools
# (timedatectl, systemctl, ...) work inside the container.
docker run -d --rm \
        --name "$CONTAINER_NAME" \
        --net=host \
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
        -v "$ROOT_DIR/fastdds-profile.xml:/opt/ros/fastdds-profile.xml:ro" \
        -e FASTDDS_DEFAULT_PROFILES_FILE=/opt/ros/fastdds-profile.xml \
        -e ROS_DOMAIN_ID=0 \
        -v "$ROOT_DIR/projects":/workspace \
        -v ~/.docker-qtcreator/config:/root/.config/QtProject \
        -v ~/.docker-qtcreator/share:/root/.local/share/QtProject \
        -v ~/.docker-qtcreator/cache:/root/.cache/QtProject \
        "${HOST_DATA_ARGS[@]}" \
        "${HOST_SSH_ARGS[@]}" \
        "${HOST_THEME_ARGS[@]}" \
        "${HOST_AUDIO_ARGS[@]}" \
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

# Match the host GTK/Qt theme, fonts and Qt Creator light/dark mode
apply_host_theme "$CONTAINER_NAME"

# Route ALSA through the host audio server
apply_host_audio "$CONTAINER_NAME"

# The host user's ssh keys, for the console's power-off scripts
apply_host_ssh "$CONTAINER_NAME"

# Desktop links to the PAR console's runwayconfig.ini and glidepathconfig.ini, once built
apply_par_config_links "$CONTAINER_NAME"

# Started by the host unit: run this machine's apps, then stay in the
# foreground so the unit relaunches everything if the container dies.
if [ -n "$SERVICE_ROLE" ]; then
    apply_radar_apps "$CONTAINER_NAME" "$SERVICE_ROLE" "$SERVICE_VARIANT" || exit 1
    exec docker wait "$CONTAINER_NAME"
fi

echo ""
echo "Container '$CONTAINER_NAME' is running with systemd as init."
echo "Opening an interactive shell (exit to leave; the container keeps running)."
docker exec -it "$CONTAINER_NAME" bash

echo ""
echo "Useful commands:"
echo "  docker exec -it $CONTAINER_NAME bash   # open another shell"
echo "  docker stop $CONTAINER_NAME            # stop the container"
