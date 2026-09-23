#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
[[ $# -gt 0 ]] || xovium_fail 'Usage: scripts/app.sh APP [ARGUMENTS...]'
app=$1
shift
case "$app" in
    qgc|mavlink-router|isaac|isaac-sim|isaac-px4|sih-view|sih-px4|unreal|unreal-editor|unreal-px4|gazebo-jetty|gazebo-harmonic) ;;
    *) xovium_fail "Unknown managed application: $app" ;;
esac
xovium_configure
xovium_require "$app"
exec "$XOVIUM_PREFIX/scripts/.commands/$app" "$@"
