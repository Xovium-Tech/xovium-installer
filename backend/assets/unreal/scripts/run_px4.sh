#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/common.sh"

if [[ "${1:-}" == --help || "${1:-}" == -h ]]; then
    cat <<'HELP'
Usage: ./run_px4.sh [PX4 arguments, e.g. -d]
Start the Unreal project as a standalone game, then start PX4 (either order works).

Configuration: config/local.env or environment variables.
  PX4_ROOT                PX4 v1.16 checkout
  PX4_BUILD               Optional build directory (default: $PX4_ROOT/build/px4_sitl_default)
  PX4_INSTANCE            0..254, same value for Unreal and PX4 (default 0)
  SIM_SPEED               Unreal wall-clock speed target; 0 = unlimited (default 1)
  PX4_SIM_SPEED_FACTOR    Optional PX4 failsafe scaling (defaults to speed, or 1 for unlimited)
  PX4_SIM_HOST_ADDR       Simulator IPv4 address (default 127.0.0.1)
  PX4_MAVLINK_ROUTER      1 only when an external router handles offboard traffic
  PX4_RUNTIME            Optional isolated runtime directory for this instance

Unreal listens on TCP 4560 + instance. QGroundControl listens on UDP 14550.
MAVSDK listens on UDP 14540 + instance (14549 for instance > 9).
HELP
    exit 0
fi

load_config
if [[ "$SIM_HZ" != 250 ]]; then
    printf '%s\n' 'SIM_HZ must be 250 for the validated PX4 v1.16 lockstep integration.' >&2
    exit 1
fi
PX4_INSTANCE="${PX4_INSTANCE:-0}"
PX4_MAVLINK_ROUTER="${PX4_MAVLINK_ROUTER:-0}"
default_speed_factor="$SIM_SPEED"
[[ "$default_speed_factor" =~ ^0+([.]0+)?$ ]] && default_speed_factor=1
export PX4_SIM_SPEED_FACTOR="${PX4_SIM_SPEED_FACTOR:-$default_speed_factor}"
if [[ ! "$PX4_INSTANCE" =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( PX4_INSTANCE > 254 )); then
    printf '%s\n' 'PX4_INSTANCE must be an integer from 0 to 254.' >&2
    exit 1
fi
if [[ "$PX4_MAVLINK_ROUTER" != 0 && "$PX4_MAVLINK_ROUTER" != 1 ]]; then
    printf '%s\n' 'PX4_MAVLINK_ROUTER must be 0 or 1.' >&2
    exit 1
fi
if ! python3 - "$PX4_SIM_SPEED_FACTOR" <<'PY'
import math, sys
try:
    value = float(sys.argv[1])
    valid = math.isfinite(value) and 0 < value <= 1000 and all(c in "0123456789." for c in sys.argv[1])
except ValueError:
    valid = False
sys.exit(0 if valid else 1)
PY
then
    printf '%s\n' 'PX4_SIM_SPEED_FACTOR must be a positive decimal <= 1000 (no exponent notation).' >&2
    exit 1
fi

PX4_BUILD="${PX4_BUILD:-$PX4_ROOT/build/px4_sitl_default}"
if [[ ! -x "$PX4_BUILD/bin/px4" || ! -f "$PX4_BUILD/etc/init.d-posix/airframes/1030_gazebo-classic_plane" ]]; then
    printf 'PX4 v1.16 SITL build is missing at %s. Run ./install.sh --px4-only.\n' "$PX4_BUILD" >&2
    exit 1
fi
PX4_BUILD="$(realpath -e -- "$PX4_BUILD")"
if [[ -d "$PX4_ROOT/.git" || -f "$PX4_ROOT/.git" ]]; then
    version="$(git -C "$PX4_ROOT" describe --tags --always 2>/dev/null || true)"
    if [[ ! "$version" =~ ^v1\.16(\.|$) ]]; then
        printf 'Expected PX4 v1.16 source; found %s in %s.\n' "$version" "$PX4_ROOT" >&2
        exit 1
    fi
fi

PX4_RUNTIME="${PX4_RUNTIME:-$RUNTIME_DIR/px4-$PX4_INSTANCE}"
mkdir -p -- "$PX4_RUNTIME"
PX4_RUNTIME="$(realpath -e -- "$PX4_RUNTIME")"
exec 9>"$PX4_RUNTIME/.launcher.lock"
if ! flock -n 9; then
    printf 'A PX4 launcher already owns %s.\n' "$PX4_RUNTIME" >&2
    exit 1
fi

prepare_args=(--source "$PX4_BUILD/etc/init.d-posix" --runtime "$PX4_RUNTIME" --instance "$PX4_INSTANCE")
[[ "$PX4_MAVLINK_ROUTER" == 1 ]] && prepare_args+=(--router)
python3 "$PROJECT_ROOT/tools/prepare_px4.py" "${prepare_args[@]}"
export PX4_UNREAL_AIRFRAME="$PROJECT_ROOT/config/px4_airframe.sh"
export PX4_MAVLINK_STARTUP="$PX4_RUNTIME/px4-rc.mavlink"
export PX4_SIM_MODEL=gazebo-classic_plane
export PX4_SIMULATOR=unreal
export PX4_SYS_AUTOSTART=1030
if [[ -z "${PX4_SIM_HOSTNAME:-}" ]]; then
    export PX4_SIM_HOST_ADDR="${PX4_SIM_HOST_ADDR:-127.0.0.1}"
fi
printf 'PX4 v1.16: Unreal physics/sensors at TCP %s:%s; runtime %s\n' \
    "${PX4_SIM_HOSTNAME:-${PX4_SIM_HOST_ADDR:-127.0.0.1}}" "$((4560 + PX4_INSTANCE))" "$PX4_RUNTIME"
printf '%s\n' 'Actuator channels: 0 aileron, 1 elevator, 2 rudder, 3 throttle. QGroundControl: UDP 14550.'
if [[ "$PX4_MAVLINK_ROUTER" == 1 ]]; then
    printf '%s\n' 'Offboard connection is delegated to your MAVLink router.'
else
    printf 'MAVSDK/offboard: listen on UDP %s.\n' "$((PX4_INSTANCE > 9 ? 14549 : 14540 + PX4_INSTANCE))"
fi
printf '%s\n' 'Startup waits for HIL_SENSOR; run Unreal as a standalone game to continue.'
exec "$PX4_BUILD/bin/px4" "$PX4_BUILD/etc" -i "$PX4_INSTANCE" -w "$PX4_RUNTIME" -s "$PX4_RUNTIME/rcS-unreal" "$@"
