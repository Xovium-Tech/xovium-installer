#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SHARED_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd)"
ISAAC_PYTHON="${ISAAC_PYTHON:-$SHARED_ROOT/.venv-isaac/bin/python}"
PX4_INSTANCE="${PX4_INSTANCE:-0}"
if [[ ! "$PX4_INSTANCE" =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( PX4_INSTANCE > 254 )); then
    printf '%s\n' 'PX4_INSTANCE must be an integer from 0 to 254.' >&2
    exit 1
fi
if [[ ! -x "$ISAAC_PYTHON" ]]; then
    printf '%s\n' 'Isaac Sim Python is missing. Run ./install.sh --isaac-only first.' >&2
    exit 1
fi
exec "$ISAAC_PYTHON" -u "$SCRIPT_DIR/isaac_view.py" \
    --endpoint "udpin:127.0.0.1:$((19410 + PX4_INSTANCE))" \
    --system-id "$((PX4_INSTANCE + 1))" \
    --origin "${SIM_LAT:-47.397742}" "${SIM_LON:-8.545594}" "${SIM_ALT:-489.4}" "$@"
