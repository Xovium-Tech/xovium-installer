#!/usr/bin/env bash
set -euo pipefail

if (( $# < 2 )); then
    printf 'Usage: flightgear.sh AIRCRAFT_PX4_LAUNCHER PX4_SOURCE [PX4_ARGUMENTS...]\n' >&2
    exit 2
fi
aircraft_px4=$1
px4_source=$2
shift 2

# Camera selection belongs to FlightGear's view manager, not PX4's launcher.
# The shared model descriptor resolves this stable name after initialization.
px4_arguments=()
while (( $# )); do
    case "$1" in
        --camera)
            (( $# >= 2 )) || { printf 'Missing value for --camera (chase, front, bottom).\n' >&2; exit 2; }
            case "$2" in
                chase|front|bottom|onboard) ;;
                *) printf 'Unknown FlightGear camera: %s (choose chase, front, bottom).\n' "$2" >&2; exit 2 ;;
            esac
            export FG_ARGS_ADD="${FG_ARGS_ADD:-} --prop:/sim/xovium/camera=$2"
            shift 2
            ;;
        *) px4_arguments+=("$1"); shift ;;
    esac
done
set -- "${px4_arguments[@]}"

# The session-model launcher has already applied the chosen model's settings.
# A saved installation may use either family, so its build cannot be reused
# blindly when the next session chooses the other family.
export XOVIUM_VEHICLE=${XOVIUM_VEHICLE:-rc_cessna}
case "$XOVIUM_VEHICLE" in
    r1_rover)
        export PX4_BUILD="$px4_source/build/px4_sitl_default"
        : "${XOVIUM_ROVER_BRIDGE:?Missing native rover bridge path}"
        exec python3 "$XOVIUM_ROVER_BRIDGE/run_rover.py" --flightgear -- "$@"
        ;;
    rc_cessna)
        export PX4_BUILD="$px4_source/build/px4_sitl_nolockstep"
        : "${XOVIUM_FG_BRIDGE:?Missing FlightGear bridge path}"
        exec bash "$XOVIUM_FG_BRIDGE/sitl_run.sh" "$aircraft_px4" "${XOVIUM_FG_MODEL:-rascal-electric}" \
            "$px4_source" "$PX4_BUILD" "$@"
        ;;
    *)
        printf 'Unsupported FlightGear vehicle adapter: %s\n' "$XOVIUM_VEHICLE" >&2
        exit 2
        ;;
esac
