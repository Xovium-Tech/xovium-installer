#!/usr/bin/env bash
set -euo pipefail
if [[ ${QGC_COMPAT_GLIB:-0} == 1 && ${QGC_SYSTEM_GLIB:-1} == 1 ]]; then
    glib=/usr/lib/x86_64-linux-gnu/libglib-2.0.so.0
    if [[ -r $glib ]]; then export LD_PRELOAD="$glib${LD_PRELOAD:+:$LD_PRELOAD}"; fi
fi
if [[ -n ${XDG_CONFIG_HOME:-} && -n ${XOVIUM_QGC_ROUTER_DEFAULT:-} ]]; then
    python3 -B "$(dirname -- "${BASH_SOURCE[0]}")/qgc-link.py" \
        "$XDG_CONFIG_HOME/QGroundControl/QGroundControl.ini" \
        --router "${PX4_MAVLINK_ROUTER:-$XOVIUM_QGC_ROUTER_DEFAULT}" --port "${XOVIUM_ROUTER_QGC_PORT:-14552}"
fi
exec "$@"
