#!/bin/bash
#
# One-time setup of a radar deployment machine. Run as the operator
# user (a member of the docker group), from a desktop session:
#
#   scripts/install-autostart.sh <lcms|rdp|rdd|dvaz|rcms|rrdd|rdvaz> <asr|par>
#
# local machines
# lcms   ASR: rmm at boot. Both: rdd --lcms when the operator session logs in
# rdp    rdp at boot
# rdd    PAR: rmm at boot. Both: rdd --rdd from a desktop icon
# dvaz   dvaz at boot
# remote machines
# rcms   rdd --rcms when the operator session logs in
# rrdd   rdd --rrdd from a desktop icon
# rdvaz  dvaz at boot
#
# Every app is restarted when it crashes, and the whole container is relaunched
# if it dies. See README.md, "Deployment".

ROLE="$1"
VARIANT="$2"

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
LAUNCHER="$SCRIPT_DIR/start-qt-container.sh"

case "$ROLE" in lcms|rdp|rdd|dvaz|rcms|rrdd|rdvaz) ;; *) ROLE="" ;; esac
case "$VARIANT" in asr|par) ;; *) VARIANT="" ;; esac
if [ -z "$ROLE" ] || [ -z "$VARIANT" ]; then
    echo "Usage: $0 <lcms|rdp|rdd|dvaz|rcms|rrdd|rdvaz> <asr|par>" >&2
    exit 1
fi

# Start the user's systemd, and the unit below, at boot without a login.
loginctl enable-linger "$USER" || exit 1

# Host unit: launches the container and the machine's apps.
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"
cat > "$UNIT_DIR/radar-container.service" <<EOF
[Unit]
Description=Radar container ($ROLE, $VARIANT)
# The host audio socket must exist before the container mounts it.
Wants=pipewire-pulse.socket
After=pipewire-pulse.socket
StartLimitIntervalSec=0

[Service]
ExecStart="$LAUNCHER" --service $ROLE $VARIANT
ExecStop=/usr/bin/docker stop rqt-based-env
Restart=always
RestartSec=5
TimeoutStopSec=60

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable radar-container.service
echo "Installed $UNIT_DIR/radar-container.service (starts at next boot)"

# The console needs the operator's X session, so it starts from there.
_gui_entry() {
    cat > "$1" <<EOF
[Desktop Entry]
Type=Application
Name=$2
Comment=Radar console
Exec="$LAUNCHER" --start-gui
Icon=video-display
Terminal=false
EOF
}

case "$ROLE" in
    lcms|rcms)
        NAME="${ROLE^^}"
        mkdir -p "$HOME/.config/autostart"
        _gui_entry "$HOME/.config/autostart/radar-$ROLE.desktop" "Radar $NAME"
        echo "Installed $HOME/.config/autostart/radar-$ROLE.desktop"
        ;;
    rdd|rrdd)
        NAME="${ROLE^^}"
        DESKTOP="$(xdg-user-dir DESKTOP 2>/dev/null)";  : "${DESKTOP:=$HOME/Desktop}"
        mkdir -p "$DESKTOP"
        _gui_entry "$DESKTOP/radar-$ROLE.desktop" "Radar $NAME"
        chmod +x "$DESKTOP/radar-$ROLE.desktop"
        # GNOME refuses to launch an untrusted desktop file.
        gio set "$DESKTOP/radar-$ROLE.desktop" metadata::trusted true 2>/dev/null || true
        echo "Installed $DESKTOP/radar-$ROLE.desktop"
        ;;
esac
