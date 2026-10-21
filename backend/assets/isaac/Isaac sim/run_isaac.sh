#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ISAAC_ROOT="${ISAAC_ROOT:-$SHARED_ROOT/.venv-isaac/lib/python3.12/site-packages/isaacsim}"
NATIVE_SDK="${NATIVE_SDK:-$SHARED_ROOT/.deps/native-sdk}"
export PX4_ISAAC_LOCK_DIR="$SHARED_ROOT/.runtime"
export PX4_INSTANCE="${PX4_INSTANCE:-0}"
WORLD_FILE="$SCRIPT_DIR/worlds/baylands.usda"
AIRCRAFT_FILE="$SCRIPT_DIR/aircraft.json"
MODEL_FILE="$SCRIPT_DIR/models/rc_cessna.usda"
KIT_OPTIONS=()
XOVIUM_VEHICLE="${XOVIUM_VEHICLE:-rc_cessna}"
XOVIUM_WORLD="${XOVIUM_WORLD:-baylands}"
SIM_LAT="${SIM_LAT:-${PX4_HOME_LAT:-37.412173071650805}}"
SIM_LON="${SIM_LON:-${PX4_HOME_LON:--121.998878727967}}"
SIM_ALT="${SIM_ALT:-${PX4_HOME_ALT:-38}}"
case "$XOVIUM_VEHICLE" in
    rc_cessna) ;;
    r1_rover)
        AIRCRAFT_FILE="$SCRIPT_DIR/rover.json"
        MODEL_FILE="$SCRIPT_DIR/models/r1_rover.usda"
        ;;
    *) printf 'Unsupported vehicle: %s\n' "$XOVIUM_VEHICLE" >&2; exit 1 ;;
esac
KIT_OPTIONS+=("--/px4/flight/vehicle=$XOVIUM_VEHICLE" "--/px4/flight/model=$MODEL_FILE")
case "$XOVIUM_WORLD" in
    baylands) WORLD_FILE="$SCRIPT_DIR/worlds/baylands.usda"; KIT_OPTIONS+=(--/px4/terrain/enabled=false) ;;
    dynamic)
        WORLD_FILE="$SCRIPT_DIR/worlds/dynamic.usda"
        terrain_options=$(python3 -B "$SCRIPT_DIR/terrain_options.py" "${XOVIUM_TERRAIN_CONFIG:?Set XOVIUM_TERRAIN_CONFIG for dynamic terrain}")
        mapfile -t terrain_args <<<"$terrain_options"
        KIT_OPTIONS+=("${terrain_args[@]}")
        ;;
    *) printf 'Unsupported world: %s\n' "$XOVIUM_WORLD" >&2; exit 1 ;;
esac
PHYSICS_CHECK=false
EXPLICIT_WORLD=false
for option in "$@"; do
    case "$option" in
        --physics-check) PHYSICS_CHECK=true ;;
        --world) EXPLICIT_WORLD=true ;;
    esac
done
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
                '  Airplane --physics-check requires --world with an explicit flat-ground/obstacle test fixture.' \
                '  Additional Kit --/setting/path=value options are forwarded unchanged.'
            exit 0
            ;;
        *) KIT_OPTIONS+=("$1"); shift ;;
    esac
done
if $PHYSICS_CHECK && [[ $XOVIUM_VEHICLE == rc_cessna ]] && ! $EXPLICIT_WORLD; then
    printf '%s\n' 'Airplane --physics-check requires --world with an explicit flat-ground/obstacle test fixture.' >&2
    exit 1
fi
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
ISAAC_SITE_PACKAGES="$(dirname -- "$ISAAC_ROOT")"
export PYTHONPATH="$ISAAC_ROOT/kit/kernel/py:$ISAAC_SITE_PACKAGES${PYTHONPATH:+:$PYTHONPATH}"
exec "$SCRIPT_DIR/bin/isaac_native" "$SCRIPT_DIR/native.kit" \
    --ext-folder "$ISAAC_ROOT/kit/exts" \
    --ext-folder "$ISAAC_ROOT/extscache" \
    --ext-folder "$ISAAC_ROOT/exts" \
    --ext-folder "$ISAAC_ROOT/extsDeprecated" \
    --ext-folder "$SCRIPT_DIR/extensions" \
    --/plugins/carb.scripting-python.plugin/pythonHome="$NATIVE_SDK/python" \
    --/app/python/extraPaths="[\"$ISAAC_SITE_PACKAGES\"]" \
    --/px4/flight/config="$AIRCRAFT_FILE" \
    --/px4/flight/world="$WORLD_FILE" \
    --/px4/flight/instance="$PX4_INSTANCE" \
    --/px4/flight/latitude="${SIM_LAT:-47.397742}" \
    --/px4/flight/longitude="${SIM_LON:-8.545594}" \
    --/px4/flight/altitude="${SIM_ALT:-489.4}" \
    --/px4/terrain/originLatitude="${SIM_LAT:-47.397742}" \
    --/px4/terrain/originLongitude="${SIM_LON:-8.545594}" \
    --/px4/terrain/originAltitude="${SIM_ALT:-489.4}" "${KIT_OPTIONS[@]}"
