#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ISAAC_ROOT="${ISAAC_ROOT:-$SHARED_ROOT/.venv-isaac/lib/python3.12/site-packages/isaacsim}"
NATIVE_SDK="${NATIVE_SDK:-$SHARED_ROOT/.deps/native-sdk}"
PX4_ROOT="${PX4_ROOT:-${PX4_DIR:-$SHARED_ROOT/.deps/PX4-Autopilot}}"
BUILD_DIR="${BUILD_DIR:-$SCRIPT_DIR/build}"
BUILD_JOBS="${JOBS:-3}"
SYSTEM_DEPS=0
SDK_ONLY=0
USD_PACKAGE='usd.py312.manylinux_2_35_x86_64.stock.release'
USD_VERSION='0.25.11.kit.5-gl.21175'
USD_SHA256='8109e2c3eb910007d7002358cb0c30ed11e977a02283ce4fc091c9aa87dc4e82'
PYTHON_PACKAGE='3.12.13+nv3-manylinux_2_35_x86_64'
PYTHON_SHA256='65904c324b9c18b6e71c28e4809daee09f6e348d07ff503a08f8f0ea405bf149'
PHYSX_COMMIT='517a0073715120e114ee055b63b26c95e00d9039'
PX4_COMMIT='6ea3539157ca358c70a515878b77077af7d4611d'

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        --system-deps) SYSTEM_DEPS=1 ;;
        --sdk-only) SDK_ONLY=1 ;;
        -h|--help)
            printf '%s\n' \
                'Usage: ./build_native.sh [--system-deps] [--sdk-only]' \
                'Build native C++ launchers and Isaac extensions for Isaac Sim 6.1.0.0.' \
                '--system-deps: install missing Ubuntu build and GStreamer packages.' \
                '--sdk-only: validate the runtime and prepare pinned SDK dependencies.' \
                'ISAAC_ROOT, PX4_ROOT/PX4_DIR, NATIVE_SDK, BUILD_DIR override their paths.' \
                'JOBS controls parallel compilation; default 3.'
            exit 0
            ;;
        *) fail "Unknown option: $arg. Run ./build_native.sh --help." ;;
    esac
done

[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'Native build requires Linux x86_64.'
[[ -r /etc/os-release ]] || fail 'Cannot identify the operating system.'
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || fail 'Native build targets Ubuntu 24.04.'
[[ $EUID -ne 0 ]] || fail 'Run as your normal user; --system-deps uses sudo only for apt.'
if [[ ! $BUILD_JOBS =~ ^[1-9][0-9]{0,2}$ ]] || (( BUILD_JOBS > 128 )); then
    fail 'JOBS must be an integer from 1 to 128.'
fi
ISAAC_ROOT=$(realpath -m -- "$ISAAC_ROOT")
NATIVE_SDK=$(realpath -m -- "$NATIVE_SDK")
PX4_ROOT=$(realpath -m -- "$PX4_ROOT")
BUILD_DIR=$(realpath -m -- "$BUILD_DIR")
[[ -r $ISAAC_ROOT/VERSION && -d $ISAAC_ROOT/kit/dev/include ]] || fail "Isaac runtime is missing at $ISAAC_ROOT; run ./install.sh first or set ISAAC_ROOT."
ISAAC_VERSION=$(<"$ISAAC_ROOT/VERSION")
[[ $ISAAC_VERSION == 6.1.0-* || $ISAAC_VERSION == 6.1.0 || $ISAAC_VERSION == 6.1.0.0 ]] || fail "Isaac 6.1.0.0 is required; found $ISAAC_VERSION."
SDK_MANIFEST="$ISAAC_ROOT/kit/dev/all-deps.packman.xml"
[[ -r $SDK_MANIFEST ]] || fail 'The installed Isaac runtime does not contain the native SDK manifest.'
grep -Fq "name=\"$USD_PACKAGE\" version=\"$USD_VERSION\"" "$SDK_MANIFEST" || fail 'Isaac USD SDK differs from the pinned 0.25.11.kit.5 build; native binaries must be updated for this runtime.'
grep -Fq "name=\"python\" version=\"$PYTHON_PACKAGE\"" "$SDK_MANIFEST" || fail 'Isaac Kit Python runtime differs from the pinned 3.12.13+nv3 build.'
shopt -s nullglob
PHYSX_RUNTIME=("$ISAAC_ROOT"/extscache/omni.physx-110.3.2+*)
shopt -u nullglob
(( ${#PHYSX_RUNTIME[@]} == 1 )) || fail 'Expected exactly one Isaac PhysX 110.3.2 extension; this runtime requires different native interface validation.'

PACKAGES=(build-essential cmake ninja-build pkg-config curl git ca-certificates 7zip libcurl4-openssl-dev libpng-dev python3.12-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev gstreamer1.0-tools gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly)
MISSING=()
NATIVE_ARCH=$(dpkg --print-architecture)
for package in "${PACKAGES[@]}"; do
    PACKAGE_STATUS=$(dpkg-query -W -f='${Status}' "$package:$NATIVE_ARCH" 2>/dev/null || dpkg-query -W -f='${Status}' "$package:all" 2>/dev/null || true)
    if [[ $PACKAGE_STATUS != 'install ok installed' ]]; then
        MISSING+=("$package")
    fi
done
if (( ${#MISSING[@]} )); then
    if (( ! SYSTEM_DEPS )); then
        printf 'Missing Ubuntu packages: %s\n' "${MISSING[*]}" >&2
        fail 'Rerun ./build_native.sh with --system-deps or install the listed packages first.'
    fi
    command -v sudo >/dev/null || fail 'sudo is required to install the missing system packages.'
    sudo apt-get update
    sudo apt-get install --no-install-recommends -y "${MISSING[@]}"
fi
pkg-config --exists libcurl libpng gstreamer-1.0 gstreamer-app-1.0 || fail 'Native library development packages are incomplete.'

download() {
    local url=$1 archive=$2 expected=$3 actual
    if [[ ! -f $archive ]]; then
        printf 'Downloading pinned SDK archive: %s\n' "$(basename -- "$archive")"
        curl --fail --location --retry 3 --retry-delay 2 --connect-timeout 30 \
            --continue-at - --output "$archive.part" "$url"
        actual=$(sha256sum -- "$archive.part")
        [[ ${actual%% *} == "$expected" ]] || fail "Checksum mismatch for $archive.part; the archive was not extracted."
        mv -- "$archive.part" "$archive"
    fi
    actual=$(sha256sum -- "$archive")
    [[ ${actual%% *} == "$expected" ]] || fail "Checksum mismatch for $archive; use the pinned SDK archive."
}

mkdir -p -- "$NATIVE_SDK"
download "https://d4i3qtqj3r0z5.cloudfront.net/$USD_PACKAGE@$USD_VERSION.7z" "$NATIVE_SDK/usd.7z" "$USD_SHA256"
if [[ ! -r $NATIVE_SDK/usd/include/pxr/pxr.h || ! -r $NATIVE_SDK/usd/lib/libtbb.so.12 || ! -r $NATIVE_SDK/usd/PACKAGE-LICENSES/usd-license.txt ]]; then
    7z x -y "-o$NATIVE_SDK/usd" "$NATIVE_SDK/usd.7z" 'include/*' 'lib/libtbb*' 'PACKAGE-LICENSES/*' 'LICENSE*'
fi
grep -Eq '^#define PXR_MINOR_VERSION[[:space:]]+25$' "$NATIVE_SDK/usd/include/pxr/pxr.h" || fail 'Extracted USD headers have the wrong version.'
grep -Eq '^#define PXR_PATCH_VERSION[[:space:]]+11$' "$NATIVE_SDK/usd/include/pxr/pxr.h" || fail 'Extracted USD headers have the wrong patch version.'
download 'https://d4i3qtqj3r0z5.cloudfront.net/python@3.12.13%2Bnv3-manylinux_2_35_x86_64.7z' "$NATIVE_SDK/python.7z" "$PYTHON_SHA256"
if [[ ! -x $NATIVE_SDK/python/bin/python3.12 || ! -r $NATIVE_SDK/python/lib/libpython3.12.so.1.0 || ! -r $NATIVE_SDK/python/lib/python3.12/encodings/__init__.py ]]; then
    7z x -y "-o$NATIVE_SDK/python" "$NATIVE_SDK/python.7z"
fi
grep -Eq '^#define PY_VERSION[[:space:]]+"3\.12\.13"$' "$NATIVE_SDK/python/include/python3.12/patchlevel.h" || fail 'Extracted Kit Python runtime has the wrong version.'

PHYSX_DIR="$NATIVE_SDK/PhysX"
if [[ ! -e $PHYSX_DIR ]]; then
    git init "$PHYSX_DIR"
    git -C "$PHYSX_DIR" remote add origin https://github.com/NVIDIA-Omniverse/PhysX.git
fi
[[ -e $PHYSX_DIR/.git ]] || fail "PhysX SDK path is not a Git checkout: $PHYSX_DIR"
if ! git -C "$PHYSX_DIR" rev-parse --verify HEAD >/dev/null 2>&1; then
    [[ $(git -C "$PHYSX_DIR" remote get-url origin) == https://github.com/NVIDIA-Omniverse/PhysX.git ]] || fail 'Incomplete PhysX SDK checkout has an unexpected origin.'
    git -C "$PHYSX_DIR" sparse-checkout init --cone
    git -C "$PHYSX_DIR" sparse-checkout set omni/ovruntime/include omni/ovruntime/deps omni/schema/source/physicsSchemaTools physx/include
    git -C "$PHYSX_DIR" fetch --depth 1 origin "$PHYSX_COMMIT"
    git -C "$PHYSX_DIR" checkout --detach FETCH_HEAD
fi
[[ $(git -C "$PHYSX_DIR" rev-parse HEAD) == "$PHYSX_COMMIT" ]] || fail 'PhysX SDK checkout differs from the pinned native interface revision.'
git -C "$PHYSX_DIR" diff --quiet HEAD -- omni/ovruntime/include omni/schema/source/physicsSchemaTools physx/include || fail 'PhysX SDK headers have local changes; use an unmodified SDK checkout.'
[[ -r $PHYSX_DIR/omni/ovruntime/include/omni/physx/IPhysx.h && -r $PHYSX_DIR/omni/schema/source/physicsSchemaTools/UsdTools.h ]] || fail 'PhysX SDK sparse checkout is incomplete.'
printf 'Native SDK ready: %s\n' "$NATIVE_SDK"
if (( SDK_ONLY )); then
    exit 0
fi

[[ -x $PX4_ROOT/build/px4_sitl_default/bin/px4 ]] || fail 'PX4 SITL is missing; run ./install.sh --px4-only first.'
[[ $(git -C "$PX4_ROOT" rev-parse HEAD) == "$PX4_COMMIT" ]] || fail 'Native simulator requires the PX4 v1.16.0 checkout.'
[[ -r $PX4_ROOT/build/px4_sitl_default/mavlink/common/mavlink.h ]] || fail 'PX4 generated MAVLink headers are missing; rebuild px4_sitl_default.'
cmake -S "$SCRIPT_DIR" -B "$BUILD_DIR" -G Ninja -DCMAKE_BUILD_TYPE=Release \
    "-DISAAC_ROOT=$ISAAC_ROOT" "-DNATIVE_SDK=$NATIVE_SDK" "-DPX4_ROOT=$PX4_ROOT"
cmake --build "$BUILD_DIR" --parallel "$BUILD_JOBS"
printf 'Native simulator ready: %s\n' "$SCRIPT_DIR/run_isaac.sh"
