#!/usr/bin/env bash
set -euo pipefail
if [[ -n ${XOVIUM_PX4_LOG:-} ]]; then
    exec >>"$XOVIUM_PX4_LOG" 2>&1
fi
binary=$1
shift
args=()
[[ -t 0 && -t 1 ]] || args+=(-d)
if [[ ${PX4_MAVLINK_ROUTER:-0} == 1 ]]; then
    build=$(dirname -- "$(dirname -- "$binary")")
    startup="$build/etc/init.d-posix"
    runtime="$build/rootfs/xovium-router"
    mkdir -p -- "$runtime"
    export XOVIUM_MAVLINK_STARTUP="$runtime/px4-rc.mavlink"
    python3 - "$startup" "$runtime" <<'PY'
from pathlib import Path
import sys
source, target = map(Path, sys.argv[1:])
rcs = (source / 'rcS').read_text()
mavlink = (source / 'px4-rc.mavlink').read_text()
source_line = '. px4-rc.mavlink'
offboard = 'mavlink start -x -u $udp_offboard_port_local -r 4000000 -f -m onboard -o $udp_offboard_port_remote'
if rcs.splitlines().count(source_line) != 1 or mavlink.splitlines().count(offboard) != 1:
    sys.exit('Unsupported PX4 startup layout for router mode.')
(target / 'rcS').write_text(rcs.replace(source_line, '. "$XOVIUM_MAVLINK_STARTUP"'))
gcs = 'mavlink start -x -u $udp_gcs_port_local -r 4000000 -f'
if mavlink.splitlines().count(gcs) != 1: sys.exit('Unsupported PX4 GCS startup layout.')
mavlink = mavlink.replace(gcs, gcs + ' -o ${XOVIUM_ROUTER_PX4_PORT:-14550}')
(target / 'px4-rc.mavlink').write_text(mavlink.replace(offboard, '# Offboard commands use the existing MAVLink router.'))
PY
    args+=(-s "$runtime/rcS")
fi
exec "$binary" "${args[@]}" "$@"
