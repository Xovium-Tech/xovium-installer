#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/generate_mavlink.sh [--generate]
Install PX4's generated MAVLink 2 C headers into ThirdParty/mavlink/include.
By default reuse headers from PX4_ROOT/build/px4_sitl_default/mavlink.
--generate (or absent build headers): generate from the pinned PX4 MAVLink XML,
using pymavlink from PX4_VENV. No network request or system Python install.
EOF
}
force_generate=0
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --generate) force_generate=1 ;;
        *) die "Unknown option: $arg" ;;
    esac
done
load_config
destination="$PROJECT_ROOT/ThirdParty/mavlink/include"
generated="$PX4_ROOT/build/px4_sitl_default/mavlink"
mkdir -p -- "$(dirname -- "$destination")"
staging=$(mktemp -d "$(dirname -- "$destination")/.mavlink.XXXXXX")
trap 'rm -rf -- "$staging"' EXIT
if (( ! force_generate )) && [[ -f $generated/common/mavlink.h ]]; then
    cp -a -- "$generated/." "$staging/"
else
    [[ -x $PX4_VENV/bin/python ]] || die 'PX4 virtual environment missing; run ./scripts/install_px4.sh.'
    xml="$PX4_ROOT/src/modules/mavlink/mavlink/message_definitions/v1.0/common.xml"
    [[ -f $xml ]] || die "PX4 MAVLink definitions missing: $xml"
    "$PX4_VENV/bin/python" - "$xml" "$staging" <<'PY'
import sys
from pymavlink.generator import mavgen
opts = mavgen.Opts(output=sys.argv[2], wire_protocol='2.0', language='C')
if not mavgen.mavgen(opts, [sys.argv[1]]):
    raise SystemExit('MAVLink header generation failed')
PY
fi
[[ -f $staging/common/mavlink.h ]] || die 'MAVLink common headers were not generated.'
mkdir -p -- "$destination"
cp -a -- "$staging/." "$destination/"
note "MAVLink 2 headers ready in $destination"
