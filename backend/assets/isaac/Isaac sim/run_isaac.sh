#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ISAAC_ROOT="${ISAAC_ROOT:-$SHARED_ROOT/.venv-isaac/lib/python3.12/site-packages/isaacsim}"
NATIVE_SDK="${NATIVE_SDK:-$SHARED_ROOT/.deps/native-sdk}"
export PX4_ISAAC_LOCK_DIR="$SHARED_ROOT/.runtime"
export PX4_INSTANCE="${PX4_INSTANCE:-0}"
WORLD_FILE="$SCRIPT_DIR/worlds/runway.usda"
AIRCRAFT_FILE="$SCRIPT_DIR/aircraft.json"
KIT_OPTIONS=()
while (( $# )); do
    case "$1" in
        --headless) KIT_OPTIONS+=(--no-window --/app/window/hideUi=true); shift ;;
        --physics-check) KIT_OPTIONS+=(--/px4/flight/physicsCheck=true); shift ;;
        --no-camera) KIT_OPTIONS+=(--/px4/camera/enabled=false); shift ;;
        --no-terrain) KIT_OPTIONS+=(--/px4/terrain/enabled=false); shift ;;
        --world|--config|--duration|--report|--camera-port|--camera-host)
            if (( $# < 2 )); then printf 'Missing value for %s\n' "$1" >&2; exit 1; fi
            case "$1" in
                --world) WORLD_FILE="$(realpath -e -- "$2")" ;;
                --config) AIRCRAFT_FILE="$(realpath -e -- "$2")" ;;
                --duration) KIT_OPTIONS+=("--/px4/flight/duration=$2") ;;
                --report) KIT_OPTIONS+=("--/px4/flight/report=$2") ;;
                --camera-port) KIT_OPTIONS+=("--/px4/camera/port=$2") ;;
                --camera-host) KIT_OPTIONS+=("--/px4/camera/host=$2") ;;
            esac
            shift 2
            ;;
        -h|--help)
            printf '%s\n' 'Usage: ./run_isaac.sh [--headless] [--world FILE] [--config FILE]' \
                '  --no-camera --no-terrain --camera-port PORT --camera-host HOST' \
                '  --physics-check --duration SECONDS --report FILE' \
                '  PX4_INSTANCE selects the SITL instance; SIM_LAT/SIM_LON/SIM_ALT set the geographic origin.' \
                '  Additional Kit --/setting/path=value options are forwarded unchanged.'
            exit 0
            ;;
        *) KIT_OPTIONS+=("$1"); shift ;;
    esac
done
if [[ ! "$PX4_INSTANCE" =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( PX4_INSTANCE > 254 )); then
    printf '%s\n' 'PX4_INSTANCE must be an integer from 0 to 254.' >&2
    exit 1
fi
if [[ ! -x "$SCRIPT_DIR/bin/isaac_native" || ! -d "$NATIVE_SDK/python/lib/python3.12" ]]; then
    printf '%s\n' 'Native simulator is missing. Run ./build_native.sh or ./install.sh first.' >&2
    exit 1
fi
export CARB_APP_PATH="$ISAAC_ROOT/kit"
export ISAAC_PATH="$ISAAC_ROOT"
export EXP_PATH="$ISAAC_ROOT/apps"
export LD_LIBRARY_PATH="$ISAAC_ROOT/kit${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export PYTHONPATH="$ISAAC_ROOT/kit/kernel/py:$SHARED_ROOT/.venv-isaac/lib/python3.12/site-packages${PYTHONPATH:+:$PYTHONPATH}"
exec "$SCRIPT_DIR/bin/isaac_native" "$SCRIPT_DIR/native.kit" \
    --ext-folder "$ISAAC_ROOT/kit/exts" \
    --ext-folder "$ISAAC_ROOT/extscache" \
    --ext-folder "$ISAAC_ROOT/exts" \
    --ext-folder "$ISAAC_ROOT/extsDeprecated" \
    --ext-folder "$SCRIPT_DIR/extensions" \
    --/plugins/carb.scripting-python.plugin/pythonHome="$NATIVE_SDK/python" \
    --/app/python/extraPaths="[\"$SHARED_ROOT/.venv-isaac/lib/python3.12/site-packages\"]" \
    --/px4/flight/config="$AIRCRAFT_FILE" \
    --/px4/flight/world="$WORLD_FILE" \
    --/px4/flight/instance="$PX4_INSTANCE" \
    --/px4/flight/latitude="${SIM_LAT:-47.397742}" \
    --/px4/flight/longitude="${SIM_LON:-8.545594}" \
    --/px4/flight/altitude="${SIM_ALT:-489.4}" \
    --/px4/terrain/originLatitude="${SIM_LAT:-47.397742}" \
    --/px4/terrain/originLongitude="${SIM_LON:-8.545594}" \
    --/px4/terrain/originAltitude="${SIM_ALT:-489.4}" "${KIT_OPTIONS[@]}"
