#!/usr/bin/env bash
# Install the NVIDIA driver and the NVIDIA Container Toolkit at pinned versions
# on an Ubuntu 24.04 machine with no internet access.
#
#   scripts/install-nvidia.sh --download [dir]    online machine with Docker: fetch the
#                                                 .debs into dir (default nvidia-package/ above scripts/)
#   sudo scripts/install-nvidia.sh [dir]          offline target: install from dir
#
# Versions are the ones on the reference machine (2026-10-29, RTX A2000 12GB):
#   driver  nvidia-driver-595-open 595.99.02-0ubuntu0.24.04.1
#   toolkit nvidia-container-toolkit 1.20.1-1
#   kernel  7.0.0-38-generic (headers are needed to build the DKMS module)
#
# The download runs in a clean ubuntu:24.04 container, so apt fetches every
# dependency a fresh install needs, not only the ones this machine is missing.
#
# The install holds the versions so apt upgrade keeps them, and registers the
# toolkit with Docker when Docker is installed (run install-docker.sh first).
# When nouveau is loaded, the NVIDIA services and udev rules are masked under
# /run until the reboot, so they do not retry a driver that cannot load yet.
set -euo pipefail

: "${DRIVER_BRANCH:=595}"
: "${DRIVER_VERSION:=595.99.02-0ubuntu0.24.04.1}"
: "${TOOLKIT_VERSION:=1.20.1-1}"
: "${KERNEL:=7.0.0-38-generic}"

PINNED=("nvidia-driver-${DRIVER_BRANCH}-open" "nvidia-dkms-${DRIVER_BRANCH}-open"
        "nvidia-utils-${DRIVER_BRANCH}" nvidia-container-toolkit
        nvidia-container-toolkit-base libnvidia-container-tools libnvidia-container1)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"

download() {
    local out
    out="$(realpath -m "${1:-$ROOT_DIR/nvidia-package}")"
    local packages=(
        "nvidia-driver-${DRIVER_BRANCH}-open=${DRIVER_VERSION}"
        "nvidia-dkms-${DRIVER_BRANCH}-open=${DRIVER_VERSION}"
        "nvidia-utils-${DRIVER_BRANCH}=${DRIVER_VERSION}"
        nvidia-settings
        nvidia-prime
        "linux-headers-${KERNEL}"
        "nvidia-container-toolkit=${TOOLKIT_VERSION}"
        "nvidia-container-toolkit-base=${TOOLKIT_VERSION}"
        "libnvidia-container-tools=${TOOLKIT_VERSION}"
        "libnvidia-container1=${TOOLKIT_VERSION}"
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
            # Resolve against the pristine package list: curl and gpg below pull in
            # libraries that would otherwise count as installed and be skipped.
            cp /var/lib/dpkg/status /tmp/status.clean
            apt-get update
            apt-get install -y --no-install-recommends ca-certificates curl gpg
            curl -fsSL https://nvidia.github.io/libnvidia-container/gpgkey \
                | gpg --dearmor -o /usr/share/keyrings/nvidia-container-toolkit-keyring.gpg
            curl -fsSL https://nvidia.github.io/libnvidia-container/stable/deb/nvidia-container-toolkit.list \
                | sed "s#deb https://#deb [signed-by=/usr/share/keyrings/nvidia-container-toolkit-keyring.gpg] https://#g" \
                > /etc/apt/sources.list.d/nvidia-container-toolkit.list
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

# Print, as name=version, the packages in a folder that need installing. A driver or toolkit package
# (pinned, or at the pinned driver or toolkit version, since the driver's own
# libraries depend on it at that exact version) is installed when missing or
# older (an older .deb would downgrade it). Any other package is a dependency
# and is installed only when missing: upgrading one already on the host can drag
# in a sibling that is not in the folder, because the folder was resolved on a
# clean system (libapparmor1 needs apparmor at the same exact version, the Mesa
# and X server libraries need theirs, and so on).
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
            if [[ " ${PINNED[*]} " != *" $name "* && "$ver" != "$DRIVER_VERSION" \
                  && "$ver" != "$TOOLKIT_VERSION" ]]; then
                continue
            fi
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
    local dir="${1:-$ROOT_DIR/nvidia-package}"
    local -a debs

    if [[ $EUID -ne 0 ]]; then
        echo "Run as root: sudo $0 $*" >&2
        exit 1
    fi
    if ! compgen -G "$dir/*.deb" >/dev/null; then
        echo "No .deb files in $dir. Run $0 --download on an online machine first." >&2
        exit 1
    fi
    export DEBIAN_FRONTEND=noninteractive

    if [[ "$(uname -r)" != "$KERNEL" ]]; then
        echo "WARNING: running kernel is $(uname -r), but $dir has headers for $KERNEL."
        echo "         The DKMS module is built only for kernels whose headers are installed."
        echo "         Boot $KERNEL, or download again with KERNEL=$(uname -r)."
    fi
    if command -v mokutil >/dev/null && mokutil --sb-state 2>/dev/null | grep -q enabled; then
        echo "WARNING: Secure Boot is enabled. The DKMS module must be signed with an"
        echo "         enrolled MOK key, or disable Secure Boot in the BIOS."
    fi

    # With nouveau bound to the GPUs, every load of nvidia.ko until the reboot
    # fails with "NVRM: GPU ... is already bound to nouveau", and three things
    # keep retrying it: nvidia-persistenced restarts, nvidia-cdi-refresh reruns,
    # and 71-nvidia.rules modprobes nvidia-modeset/-drm/-uvm on each load. Mask
    # them under /run only, so the reboot that loads the driver drops the masks.
    local nouveau_loaded=0
    if grep -q '^nouveau ' /proc/modules; then
        nouveau_loaded=1
        echo "== nouveau is driving the GPU: the NVIDIA driver loads only after a reboot."
        echo "   Masking its services and udev rules until then (they live in /run)."
        systemctl mask --runtime nvidia-persistenced.service \
            nvidia-cdi-refresh.service nvidia-cdi-refresh.path >/dev/null
        mkdir -p /run/udev/rules.d
        : > /run/udev/rules.d/71-nvidia.rules
        : > /run/udev/rules.d/99-nvidia-cdi-refresh.rules
        udevadm control --reload
    fi

    # A missing dependency fails loudly: the folder is the only source apt sees.
    mapfile -t debs < <(debs_to_install "$dir")
    if [[ ${#debs[@]} -eq 0 ]]; then
        echo "== NVIDIA: everything already installed"
    else
        echo "== NVIDIA: installing ${#debs[@]} packages from $dir"
        apt_local "$dir" install -y --no-install-recommends \
            -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold \
            "${debs[@]}"
    fi
    apt-mark hold "${PINNED[@]}" >/dev/null

    if command -v docker >/dev/null; then
        nvidia-ctk runtime configure --runtime=docker
        systemctl restart docker
    else
        echo "Docker not installed: after install-docker.sh, run"
        echo "  sudo nvidia-ctk runtime configure --runtime=docker && sudo systemctl restart docker"
    fi

    echo
    echo "Installed (held at these versions):"
    dpkg-query -W -f='  ${Package} ${Version}\n' "${PINNED[@]}" 2>/dev/null || true
    echo
    if [[ $nouveau_loaded -eq 1 ]]; then
        echo "nouveau still holds the GPU, so a few \"already bound to nouveau\" lines in"
        echo "dmesg are expected until the reboot. They do not come back afterwards."
    fi
    echo "Reboot now to load the driver, then check:"
    echo "  nvidia-smi"
    echo "  docker run --rm --gpus all rqt-based-env:latest nvidia-smi"
}

if [[ "${1:-}" == --download ]]; then
    download "${2:-}"
else
    install_offline "${1:-}"
fi
