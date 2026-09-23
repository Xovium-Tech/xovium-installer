#!/usr/bin/env bash
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ISAAC_PYTHON=${ISAAC_PYTHON:-"$SCRIPT_DIR/../../isaac/runtime/bin/python"}
PX4_INSTANCE=${PX4_INSTANCE:-0}
if [[ ! $PX4_INSTANCE =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( PX4_INSTANCE > 254 )); then
    printf 'PX4_INSTANCE must be 0..254.\n' >&2
    exit 1
fi
[[ -x $ISAAC_PYTHON ]] || { printf 'Isaac Python is missing; install -SIH first.\n' >&2; exit 1; }
exec "$ISAAC_PYTHON" -B -u "$SCRIPT_DIR/isaac_view.py" \
    --endpoint "udpin:127.0.0.1:$((19410 + PX4_INSTANCE))" \
    --system-id "$((PX4_INSTANCE + 1))" \
    --origin "${SIM_LAT:-47.397742}" "${SIM_LON:-8.545594}" "${SIM_ALT:-489.4}" "$@"
