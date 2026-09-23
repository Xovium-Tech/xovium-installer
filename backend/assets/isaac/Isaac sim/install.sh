#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ISAAC_ROOT="${ISAAC_ROOT:-$SHARED_ROOT/.venv-isaac/lib/python3.12/site-packages/isaacsim}"
PX4_ROOT="${PX4_ROOT:-${PX4_DIR:-$SHARED_ROOT/.deps/PX4-Autopilot}}"
BUILD_OPTIONS=()
PX4_ONLY=0
ISAAC_ONLY=0

for arg in "$@"; do
    case "$arg" in
        --system-deps) BUILD_OPTIONS+=(--system-deps) ;;
        --px4-only) PX4_ONLY=1 ;;
        --isaac-only) ISAAC_ONLY=1 ;;
        -h|--help)
            printf '%s\n' \
                'Usage: ./install.sh [--system-deps] [--px4-only | --isaac-only]' \
                'Install PX4 v1.16.0, Isaac Sim 6.1.0.0, and the native C++ simulator.' \
                '--system-deps: install missing Ubuntu packages with sudo apt-get.' \
                '--px4-only or --isaac-only: install that shared component; build native code once both exist.' \
                'JOBS controls parallel compilation; default 3.' \
                'ISAAC_ROOT, PX4_ROOT/PX4_DIR and NATIVE_SDK override installation paths.' \
                'Use ./build_native.sh to rebuild C++ code without reinstalling shared components.'
            exit 0
            ;;
        *) printf 'Error: unknown option %s. Run ./install.sh --help.\n' "$arg" >&2; exit 1 ;;
    esac
done
if (( PX4_ONLY && ISAAC_ONLY )); then
    printf '%s\n' 'Error: --px4-only and --isaac-only cannot be combined.' >&2
    exit 1
fi

JOBS="${JOBS:-3}" PX4_DIR="$PX4_ROOT" "$SHARED_ROOT/SIH/install.sh" "$@"
if [[ -d $ISAAC_ROOT/kit/dev/include && -x $PX4_ROOT/build/px4_sitl_default/bin/px4 ]]; then
    ISAAC_ROOT="$ISAAC_ROOT" PX4_ROOT="$PX4_ROOT" "$SCRIPT_DIR/build_native.sh" "${BUILD_OPTIONS[@]}"
else
    printf '%s\n' 'Shared component installed. Native build will run after both Isaac Sim and PX4 SITL are present.'
fi
