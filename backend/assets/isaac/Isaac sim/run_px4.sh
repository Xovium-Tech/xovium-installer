#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
PX4_DIR="${PX4_DIR:-$SHARED_ROOT/.deps/PX4-Autopilot}"
PX4_BUILD="${PX4_BUILD:-$PX4_DIR/build/px4_sitl_default}"
PX4_INSTANCE="${PX4_INSTANCE:-0}"
PX4_MAVLINK_ROUTER="${PX4_MAVLINK_ROUTER:-0}"
if [[ ! "$PX4_INSTANCE" =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( PX4_INSTANCE > 254 )); then
    printf '%s\n' 'PX4_INSTANCE must be an integer from 0 to 254.' >&2
    exit 1
fi
if [[ "$PX4_MAVLINK_ROUTER" != 0 && "$PX4_MAVLINK_ROUTER" != 1 ]]; then
    printf '%s\n' 'PX4_MAVLINK_ROUTER must be 0 or 1.' >&2
    exit 1
fi
if [[ ! -x "$PX4_BUILD/bin/px4" || ! -f "$PX4_BUILD/etc/init.d-posix/airframes/1030_gazebo-classic_plane" ]]; then
    printf '%s\n' 'PX4 v1.16 SITL build is missing. Run ./install.sh --px4-only first.' >&2
    exit 1
fi
PX4_BUILD="$(realpath -e -- "$PX4_BUILD")"
PX4_RUNTIME="${PX4_RUNTIME:-$SHARED_ROOT/.runtime/isaac-px4-$PX4_INSTANCE}"
mkdir -p -- "$PX4_RUNTIME"
PX4_RUNTIME="$(realpath -e -- "$PX4_RUNTIME")"
export PX4_RUNTIME_RCS="$PX4_RUNTIME/rcS-isaac"
export PX4_ISAAC_AIRFRAME="$SCRIPT_DIR/px4_airframe.sh"
export PX4_MAVLINK_STARTUP="$PX4_RUNTIME/px4-rc.mavlink"
PX4_LAUNCHER="$SCRIPT_DIR/bin/px4_launcher"
if [[ ! -x "$PX4_LAUNCHER" ]]; then
    printf '%s\n' 'Native PX4 launcher is missing. Run ./build_native.sh first.' >&2
    exit 1
fi
"$PX4_LAUNCHER" prepare "$PX4_BUILD/etc/init.d-posix" "$PX4_RUNTIME_RCS" "$PX4_MAVLINK_STARTUP" "$PX4_INSTANCE" "$PX4_MAVLINK_ROUTER"
export PX4_SIM_MODEL=gazebo-classic_plane
export PX4_SIMULATOR=isaac
export PX4_SYS_AUTOSTART=1030
export PX4_SIM_SPEED_FACTOR=1
unset PX4_SIM_HOSTNAME PX4_SIM_HOST_ADDR
printf 'Isaac physics/sensors: TCP %s; actuator channels: 0 roll, 1 pitch, 2 yaw, 3 throttle\n' "$((4560 + PX4_INSTANCE))"
if [[ "$PX4_MAVLINK_ROUTER" == 1 ]]; then
    printf '%s\n' 'PX4 -> MAVLink router: UDP 14550; QGC/app: use router endpoints.'
else
    printf 'QGC: UDP 14550; MAVLink app: UDP %s\n' "$((PX4_INSTANCE > 9 ? 14549 : 14540 + PX4_INSTANCE))"
fi
exec "$PX4_LAUNCHER" run "$PX4_INSTANCE" "$PX4_BUILD/bin/px4" "$PX4_BUILD/etc" -i "$PX4_INSTANCE" -w "$PX4_RUNTIME" -s rcS-isaac "$@"
