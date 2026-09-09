#!/bin/bash
#
# host-theme.sh - make GUI apps inside the container look like the host desktop.
#
# Sourced by start-qt-container*.sh. It provides:
#   HOST_THEME_ARGS            array of extra `docker run` arguments
#   apply_host_theme <name>    post-start tweaks that need a running container
#
# How it works
# ------------
# The container already shares the host X display, so the host's XSETTINGS
# manager (gsd-xsettings) tells GTK apps inside the container which theme, icon
# set, cursor and fonts to use - `gtk-query-settings` in the container already
# reports the host values. They just cannot find those files, so the theme,
# icon and font directories are bind-mounted in read-only, additively, under
# /usr/local/share (which comes before /usr/share in XDG_DATA_DIRS).
#
# Qt apps are pointed at the gtk3 platform theme, which the Qt in /opt ships,
# so they derive their palette and fonts from the very same GTK theme.

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
    # Theme/icon/font assets, read-only and additive: /usr/local/share is
    # searched before /usr/share, so the host copies win but nothing in the
    # image is hidden.
    -v /usr/share/themes:/usr/local/share/themes:ro
    -v /usr/share/icons:/usr/local/share/icons:ro
    -v /usr/share/fonts:/usr/local/share/fonts:ro
    -e XDG_DATA_DIRS=/usr/local/share:/usr/share
    # Xcursor does not look in /usr/local/share by default, so spell the path out.
    -e XCURSOR_PATH=/root/.icons:/usr/local/share/icons:/usr/share/icons:/usr/share/pixmaps
    -e XCURSOR_THEME="$HOST_CURSOR_THEME"
    -e XCURSOR_SIZE="$HOST_CURSOR_SIZE"
    # Qt (including Qt Creator) takes its palette and fonts from the GTK theme.
    -e QT_QPA_PLATFORMTHEME=gtk3
    -e GDK_BACKEND=x11
    # The GTK platform theme wants a session bus; apply_host_theme starts one
    # here. (The host's own bus cannot be shared: it authenticates by uid and
    # the container runs as root.)
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

# Loose font files sitting directly in the host's /usr/share/fonts, mounted onto
# the identical path. Code that opens a .ttf by absolute path never goes through
# fontconfig, so the rules above cannot help it - and the image ships a
# different font under the very same name (see the RobotoCondensed-Regular.ttf
# that GLFontManager.cpp loads).
while IFS= read -r _host_font; do
    [ -n "$_host_font" ] && HOST_THEME_ARGS+=( -v "$_host_font:$_host_font:ro" )
done < <(find /usr/share/fonts -maxdepth 1 -type f \
             \( -name '*.ttf' -o -name '*.otf' -o -name '*.ttc' \) 2>/dev/null | sort)

# Steps that need the container to be up.
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

    # Font packages ship fontconfig rules that reject superseded font files -
    # fonts-ubuntu, for instance, rejects the legacy static Ubuntu-*.ttf so the
    # variable Ubuntu[wdth,wght].ttf wins. Without them "Ubuntu" resolves to
    # Ubuntu-B.ttf and every label renders bold. Those rules live in /etc/fonts
    # (not mounted) and their globs name /usr/share/fonts, so replay them here
    # against both that path and the /usr/local/share/fonts mount point.
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
    print("<!-- generated by host-theme.sh from the host's fontconfig rules -->")
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

    # The image dumps extra font files flat into /usr/share/fonts (Dockerfile
    # step 5). When one of those duplicates a family the host also ships, the
    # flat copy wins the tie and the app renders in a different cut of the font
    # than it does on the host - e.g. three different RobotoCondensed-Regular.ttf
    # exist, and the container picked the zip's redesign while the host uses the
    # classic fonts-roboto one. Hide the duplicates so the host's choice wins.
    # Families the host does not have (JetBrains Mono) are left alone, and a file
    # is only hidden when every family it provides remains available elsewhere.
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
        fh.write("<!-- generated by host-theme.sh: host fonts win over image duplicates -->\n")
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

# Executed rather than sourced: apply the runtime part to a container that is
# already up, e.g. one started with `docker compose up`.
#
#   ./host-theme.sh [container-name]
#
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    apply_host_theme "${1:-rqt-based-env}"
fi
