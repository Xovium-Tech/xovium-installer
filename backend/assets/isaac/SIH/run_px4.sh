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
if [[ ! -x "$PX4_BUILD/bin/px4" || ! -f "$PX4_BUILD/etc/init.d-posix/airframes/10041_sihsim_airplane" ]]; then
    printf '%s\n' 'PX4 v1.16 SITL build is missing. Run ./install.sh --px4-only first.' >&2
    exit 1
fi
PX4_BUILD="$(realpath -e -- "$PX4_BUILD")"
PX4_RUNTIME="${PX4_RUNTIME:-$SHARED_ROOT/.runtime/px4-$PX4_INSTANCE}"
mkdir -p -- "$PX4_RUNTIME"
PX4_RUNTIME="$(realpath -e -- "$PX4_RUNTIME")"
export PX4_RUNTIME_RCS="$PX4_RUNTIME/rcS-mavlink-only"
export PX4_MAVLINK_STARTUP="$PX4_RUNTIME/px4-rc.mavlink"
python3 - "$PX4_BUILD/etc/init.d-posix" "$PX4_RUNTIME_RCS" "$PX4_MAVLINK_STARTUP" "$PX4_INSTANCE" "$PX4_MAVLINK_ROUTER" <<'PY'
import errno
from pathlib import Path
import socket
import sys

source, startup_path, mavlink_path, instance, router = sys.argv[1:]
instance = int(instance)
router = router == "1"
try:
    startup = (Path(source) / "rcS").read_text()
    mavlink = (Path(source) / "px4-rc.mavlink").read_text()
except OSError as exc:
    sys.exit(f"Cannot read PX4 startup files: {exc}")
dds_start = "uxrce_dds_client start -t udp -h 127.0.0.1 -p $uxrce_dds_port $uxrce_dds_ns"
mavlink_source = ". px4-rc.mavlink"
offboard_start = "mavlink start -x -u $udp_offboard_port_local -r 4000000 -f -m onboard -o $udp_offboard_port_remote"
for contents, expected in ((startup, dds_start), (startup, mavlink_source), (mavlink, offboard_start)):
    if contents.splitlines().count(expected) != 1:
        sys.exit(f"Unsupported PX4 startup layout; expected exactly one line: {expected}")
ports = [("QGC/router", 18570), ("payload", 14280), ("gimbal", 13030), ("Isaac", 19450)]
if not router:
    ports.append(("MAVLink application", 14580))
conflicts = []
for name, base_port in ports:
    port = base_port + instance
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as connection:
            connection.bind(("0.0.0.0", port))
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            sys.exit(f"Cannot check UDP {port}: {exc}")
        conflicts.append(f"{name}: UDP {port}")
if conflicts:
    message = "PX4 cannot start because these local ports are already in use: " + ", ".join(conflicts) + "."
    message += " Close any previous PX4 instance before restarting."
    if not router:
        message += " If a MAVLink router owns the application port and forwards PX4 UDP 14550, launch with PX4_MAVLINK_ROUTER=1 ./run_px4.sh."
    sys.exit(message)
startup_lines = []
for line in startup.splitlines():
    if line.lstrip().startswith("#") or line == dds_start:
        continue
    startup_lines.append('. "$PX4_MAVLINK_STARTUP"' if line == mavlink_source else line)
mavlink_lines = [line for line in mavlink.splitlines() if not line.lstrip().startswith("#") and not (router and line == offboard_start)]
Path(startup_path).write_text("\n".join(startup_lines) + "\n")
Path(mavlink_path).write_text("\n".join(mavlink_lines) + "\n")
PY
export PX4_SIM_MODEL=sihsim_airplane
export PX4_SIMULATOR=sihsim
export PX4_SYS_AUTOSTART=10041
export PX4_SIM_SPEED_FACTOR=1
export PX4_PARAM_SIH_LOC_LAT0="${SIM_LAT:-47.397742}"
export PX4_PARAM_SIH_LOC_LON0="${SIM_LON:-8.545594}"
export PX4_PARAM_SIH_LOC_H0="${SIM_ALT:-489.4}"
if [[ "$PX4_MAVLINK_ROUTER" == 1 ]]; then
    printf 'PX4 -> MAVLink router: UDP 14550; QGC/app: use router endpoints; Isaac Sim: UDP %s\n' "$((19410 + PX4_INSTANCE))"
else
    printf 'QGC: UDP 14550; MAVLink app: UDP %s; Isaac Sim: UDP %s\n' "$((PX4_INSTANCE > 9 ? 14549 : 14540 + PX4_INSTANCE))" "$((19410 + PX4_INSTANCE))"
fi
exec "$PX4_BUILD/bin/px4" "$PX4_BUILD/etc" -i "$PX4_INSTANCE" -w "$PX4_RUNTIME" -s "$SCRIPT_DIR/px4_startup.sh" "$@"
