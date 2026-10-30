#!/usr/bin/env bash
# Install Docker Engine (docker-ce, from Docker's own repository) at pinned
# versions on an Ubuntu 24.04 machine with no internet access.
#
#   scripts/install-docker.sh --download [dir]    online machine with Docker: fetch the
#                                                 .debs into dir (default docker-package/ above scripts/)
#   sudo scripts/install-docker.sh [dir]          offline target: install from dir
#
# Versions are the ones on the reference machine (2026-10-29). Override any of
# them through the environment when downloading.
#
# The download runs in a clean ubuntu:24.04 container, so apt fetches every
# dependency a fresh install needs, not only the ones this machine is missing.
# It empties dir first: a docker.io .deb left there would conflict with docker-ce.
#
# The install removes Ubuntu's docker.io packages if present, installs the
# folder without touching the network, holds the versions so apt upgrade keeps
# them, and adds the sudo user to the docker group.
set -euo pipefail

: "${DOCKER_VERSION:=5:29.7.2-1~ubuntu.24.04~noble}"
: "${CONTAINERD_VERSION:=2.3.4-1~ubuntu.24.04~noble}"
: "${BUILDX_VERSION:=0.36.1-1~ubuntu.24.04~noble}"
: "${COMPOSE_VERSION:=5.5.0-1~ubuntu.24.04~noble}"

PINNED=(docker-ce docker-ce-cli docker-ce-rootless-extras containerd.io
        docker-buildx-plugin docker-compose-plugin)

# Ubuntu's own packaging of the same tools. They conflict with docker-ce.
CONFLICTS=(docker.io docker-doc docker-compose docker-compose-v2 podman-docker
           containerd runc)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

download() {
    local out
    out="$(realpath -m "${1:-$ROOT_DIR/docker-package}")"
    local packages=(
        "docker-ce=$DOCKER_VERSION"
        "docker-ce-cli=$DOCKER_VERSION"
        "docker-ce-rootless-extras=$DOCKER_VERSION"
        "containerd.io=$CONTAINERD_VERSION"
        "docker-buildx-plugin=$BUILDX_VERSION"
        "docker-compose-plugin=$COMPOSE_VERSION"
    )

    mkdir -p "$out"
    rm -f "$out"/*.deb
    echo "Downloading into $out:"
    printf '  %s\n' "${packages[@]}"

    # The container runs as root, so hand the files back to the caller afterwards.
    docker run --rm \
        -e DEBIAN_FRONTEND=noninteractive \
        -e HOST_UID="$(id -u)" -e HOST_GID="$(id -g)" \
        -v "$out":/out \
        ubuntu:24.04 bash -euo pipefail -c '
            # Resolve against the pristine package list: curl below pulls in
            # libraries that would otherwise count as installed and be skipped.
            cp /var/lib/dpkg/status /tmp/status.clean
            apt-get update
            apt-get install -y --no-install-recommends ca-certificates curl
            install -m 0755 -d /etc/apt/keyrings
            curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
            echo "deb [arch=amd64 signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu noble stable" \
                > /etc/apt/sources.list.d/docker.list
            apt-get update
            mkdir -p /out/partial
            apt-get install -y --download-only --no-install-recommends \
                -o Dir::State::status=/tmp/status.clean \
                -o Dir::Cache::archives=/out "$@"
            rm -rf /out/partial /out/lock
            chown -R "$HOST_UID:$HOST_GID" /out
        ' _ "${packages[@]}"

    echo
    echo "Done: $(ls "$out"/*.deb | wc -l) packages, $(du -sh "$out" | cut -f1) in $out"
}

# Print, as name=version, the packages in a folder that need installing. A pinned Docker package is
# installed when missing or older (an older .deb would downgrade it). Any other
# package is a dependency and is installed only when missing: upgrading one
# already on the host can drag in a sibling that is not in the folder, because
# the folder was resolved on a clean system (libapparmor1 needs apparmor at the
# same exact version, libudev1 needs udev, and so on).
debs_to_install() {
    local deb name pkg arch ver installed
    for deb in "$1"/*.deb; do
        name="$(dpkg-deb -f "$deb" Package)"
        arch="$(dpkg-deb -f "$deb" Architecture)"
        ver="$(dpkg-deb -f "$deb" Version)"
        pkg="$name"
        [[ "$arch" == all ]] || pkg="$pkg:$arch"
        installed="$(dpkg-query -W -f='${db:Status-Abbrev} ${Version}' "$pkg" 2>/dev/null || true)"
        if [[ "$installed" == ii* ]]; then
            [[ " ${PINNED[*]} " != *" $name "* ]] && continue
            dpkg --compare-versions "${installed#* }" ge "$ver" && continue
        fi
        echo "$pkg=$ver"
    done
}

# Run apt-get with a folder of .debs as its only source, through a throwaway
# flat repository: no network, no configured sources, and the host's package
# lists left alone. Passing .deb paths to apt directly is not reliable offline:
# apt prefers an archive copy of the same version and then cannot fetch it.
apt_local() {
    local dir="$1"; shift
    local repo deb i=0
    APT_LOCAL_TMP="$(mktemp -d)"
    trap 'rm -rf "$APT_LOCAL_TMP"' EXIT
    repo="$APT_LOCAL_TMP/repo"
    mkdir -p "$repo" "$APT_LOCAL_TMP/lists/partial" "$APT_LOCAL_TMP/sources.list.d"
    # Plain link names: apt decodes %3a in a Filename, which epoch versions carry.
    for deb in "$dir"/*.deb; do
        i=$((i + 1))
        ln -s "$(realpath "$deb")" "$repo/$i.deb"
        dpkg-deb -f "$deb"
        echo "Filename: ./$i.deb"
        echo "Size: $(stat -c %s "$deb")"
        echo "SHA256: $(sha256sum "$deb" | cut -d' ' -f1)"
        echo
    done > "$repo/Packages"
    echo "deb [trusted=yes] file:$repo ./" > "$APT_LOCAL_TMP/sources.list"

    local -a opts=(
        -o Dir::Etc::SourceList="$APT_LOCAL_TMP/sources.list"
        -o Dir::Etc::SourceParts="$APT_LOCAL_TMP/sources.list.d"
        -o Dir::State::Lists="$APT_LOCAL_TMP/lists"
        -o Dir::Cache::pkgcache= -o Dir::Cache::srcpkgcache=
    )
    apt-get "${opts[@]}" update -qq
    apt-get "${opts[@]}" "$@"
}

install_offline() {
    local dir="${1:-$ROOT_DIR/docker-package}"
    local -a debs remove=()
    local p

    if [[ $EUID -ne 0 ]]; then
        echo "Run as root: sudo $0 $*" >&2
        exit 1
    fi
    if ! compgen -G "$dir/*.deb" >/dev/null; then
        echo "No .deb files in $dir. Run $0 --download on an online machine first." >&2
        exit 1
    fi
    if compgen -G "$dir/docker.io_*.deb" >/dev/null; then
        echo "$dir still holds Ubuntu's docker.io. Download again with $0 --download." >&2
        exit 1
    fi
    export DEBIAN_FRONTEND=noninteractive

    for p in "${CONFLICTS[@]}"; do
        dpkg-query -W -f='${db:Status-Abbrev}' "$p" 2>/dev/null | grep -q '^ii' && remove+=("$p")
    done
    if [[ ${#remove[@]} -gt 0 ]]; then
        echo "== Removing conflicting packages: ${remove[*]}"
        apt-get remove -y "${remove[@]}"
    fi

    # A missing dependency fails loudly: the folder is the only source apt sees.
    mapfile -t debs < <(debs_to_install "$dir")
    if [[ ${#debs[@]} -eq 0 ]]; then
        echo "== Docker: everything already installed"
    else
        echo "== Docker: installing ${#debs[@]} packages from $dir"
        apt_local "$dir" install -y --no-install-recommends \
            -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            "${debs[@]}"
    fi
    apt-mark hold "${PINNED[@]}" >/dev/null

    systemctl enable --now containerd docker
    if [[ -n "${SUDO_USER:-}" ]]; then
        usermod -aG docker "$SUDO_USER"
        echo "Added $SUDO_USER to the docker group (log out and back in to apply)"
    fi

    echo
    echo "Installed (held at these versions):"
    dpkg-query -W -f='  ${Package} ${Version}\n' "${PINNED[@]}" 2>/dev/null || true
    docker --version
}

if [[ "${1:-}" == --download ]]; then
    download "${2:-}"
else
    install_offline "${1:-}"
fi
