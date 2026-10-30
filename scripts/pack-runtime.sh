#!/bin/bash
#
# Pack the apps built in projects/ into projects.zip, without their source:
# only what run.sh needs to start each app. projects/ itself is not touched.
#
#   scripts/pack-runtime.sh [--strip] [output.zip]   (default: projects.zip above scripts/)
#
# Build first, inside the container, with Qt Creator's default kit. Each of the
# eight apps (asr|par x rdd|rmm|rdp|dvaz) that is missing or not built is
# skipped with a warning, and the rest are packed anyway.
#
# Each app keeps the paths it was built at, because the binaries find their
# protocol libraries through absolute /workspace/... RUNPATHs. So on the target
# the zip is unpacked in the folder above scripts/, where projects/ was:
#
#   unzip projects.zip
#
# Kept per repository: run.sh, protocol/install without its headers (run.sh
# sources it), and the build folder minus compiler output (objects, generated
# sources, CMake files).
#
# --strip removes debug symbols from the binaries. They get much smaller, but
# the crash logs then show addresses instead of function names and lines.

SCRIPT_DIR="$(dirname "$(readlink -f "$0")")"
ROOT_DIR="$(dirname "$SCRIPT_DIR")"
PROJECTS="$ROOT_DIR/projects"
: "${BUILD_KIT:=Desktop_Qt_6_7_3-Debug}"

STRIP=0
if [ "$1" = "--strip" ]; then
    STRIP=1
    shift
fi
OUT="$(realpath -m "${1:-$ROOT_DIR/projects.zip}")"

if ! command -v zip >/dev/null; then
    echo "zip not found: sudo apt install zip" >&2
    exit 1
fi

# Compiler output and build-system files: none of it is read at run time.
EXCLUDES=(
    --exclude=CMakeFiles --exclude='*_autogen' --exclude='rosidl_*' --exclude='ament_*'
    --exclude=.qt --exclude=.rcc --exclude=.lupdate --exclude=meta_types --exclude=qmltypes
    --exclude='*.o' --exclude='*.a' --exclude='*.c' --exclude='*.cpp' --exclude='*.h' --exclude='*.hpp'
    --exclude='*.cmake' --exclude=Makefile --exclude=CMakeCache.txt --exclude='CTest*'
    --exclude=compile_commands.json --exclude='*.ninja' --exclude='.ninja_*' --exclude='*.depends'
    --exclude=AMENT_IGNORE
)

STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/projects"

packed=()
skipped=()
for variant in asr par; do
    for app in rdd rmm rdp dvaz; do
        # Repository folder as unzipped: <prefix>_<variant>_<app><suffix>.
        repo=""
        for d in "$PROJECTS/"*_"$variant"_"$app"*/; do
            [ -d "$d" ] && { repo="$(basename "$d")"; break; }
        done
        if [ -z "$repo" ]; then
            echo "skip $variant $app: not in projects/" >&2
            skipped+=("$variant-$app")
            continue
        fi

        # rdd builds at the repository root, every other app under src/.
        base="$repo"
        [ "$app" = rdd ] || base="$repo/src"
        if [ ! -f "$PROJECTS/$base/run.sh" ]; then
            echo "skip $repo: no $base/run.sh" >&2
            skipped+=("$variant-$app")
            continue
        fi
        if [ ! -d "$PROJECTS/$base/build/$BUILD_KIT/app" ]; then
            echo "skip $repo: not built ($base/build/$BUILD_KIT missing)" >&2
            skipped+=("$variant-$app")
            continue
        fi

        tar -C "$PROJECTS" "${EXCLUDES[@]}" -cf - "$base/run.sh" "$base/build/$BUILD_KIT" |
            tar -C "$STAGE/projects" -xf - || exit 1
        # All but the headers: ROS 2 finds the typesupport libraries through its ament index.
        if [ -d "$PROJECTS/$base/protocol/install" ]; then
            tar -C "$PROJECTS" --exclude="$base/protocol/install/protocol/include" \
                -cf - "$base/protocol/install" | tar -C "$STAGE/projects" -xf - || exit 1
        fi
        packed+=("$repo")
    done
done

if [ ${#packed[@]} -eq 0 ]; then
    echo "Nothing built to pack in $PROJECTS." >&2
    exit 1
fi

# Empty folders left behind by the excludes.
find "$STAGE/projects" -mindepth 1 -type d -empty -delete

if [ "$STRIP" = 1 ]; then
    find "$STAGE/projects" -type f -executable -path "*/build/$BUILD_KIT/app/*" -print0 |
        while IFS= read -r -d '' f; do
            file -b "$f" | grep -q '^ELF' && strip --strip-debug "$f"
        done
fi

# -y keeps the library symlinks as links; unzip on Linux restores the modes.
mkdir -p "$(dirname "$OUT")"
rm -f "$OUT"
(cd "$STAGE" && zip -qry "$OUT" projects) || exit 1
(cd "$(dirname "$OUT")" && sha256sum "$(basename "$OUT")" > "$(basename "$OUT").sha256")

echo "Packed:  ${packed[*]}"
[ ${#skipped[@]} -gt 0 ] && echo "Skipped: ${skipped[*]}"
echo "$OUT ($(du -h "$OUT" | cut -f1))"
