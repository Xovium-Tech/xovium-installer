#!/usr/bin/env bash
XOVIUM_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)
XOVIUM_PREFIX=$XOVIUM_ROOT
readonly XOVIUM_ROOT XOVIUM_PREFIX

xovium_fail() { printf 'Error: %s\n' "$*" >&2; exit 1; }
[[ -f "$XOVIUM_PREFIX/state.json" ]] || xovium_fail "These are launcher templates. Install a section, then run scripts from the installed xovium/scripts folder."
xovium_require() {
    [[ -x "$XOVIUM_PREFIX/scripts/.commands/$1" ]] || xovium_fail "Missing $1. Install its section with install.sh in the installer kit first."
}
xovium_configure() {
    if [[ -f "$XOVIUM_ROOT/scripts/network.env" ]]; then
        source "$XOVIUM_ROOT/scripts/network.env"
    fi
    if [[ -f "$XOVIUM_ROOT/scripts/wizard.env" ]]; then
        source "$XOVIUM_ROOT/scripts/wizard.env"
    fi
    if [[ -f "$XOVIUM_ROOT/scripts/local.env" ]]; then
        set -a
        source "$XOVIUM_ROOT/scripts/local.env"
        set +a
    fi
    if [[ ${PX4_MAVLINK_ROUTER:-0} == 1 ]]; then
        local port=${XOVIUM_ROUTER_PX4_PORT:-14550}
        [[ $port =~ ^[1-9][0-9]{3,4}$ ]] && (( port >= 1024 && port <= 65535 )) || xovium_fail 'Router PX4 input port must be 1024..65535.'
        export XOVIUM_ROUTER_PX4_PORT=$port
    fi
    export PX4_INSTANCE="${PX4_INSTANCE:-0}"
    if [[ ! $PX4_INSTANCE =~ ^(0|[1-9][0-9]{0,2})$ ]] || (( PX4_INSTANCE > 254 )); then
        xovium_fail 'PX4_INSTANCE must be 0..254.'
    fi
    export XDG_CACHE_HOME="$XOVIUM_PREFIX/cache"
    export XDG_CONFIG_HOME="$XOVIUM_PREFIX/config"
    export XDG_DATA_HOME="$XOVIUM_PREFIX/data"
}
xovium_check_gazebo() {
    local selected=$1
    [[ -r "$XOVIUM_PREFIX/state.json" ]] || xovium_fail 'No managed installation. Run install.sh in the installer kit first.'
    local built
    built=$(python3 -B -c 'import json,sys; s=json.load(open(sys.argv[1])); print(s.get("components",{}).get("px4",{}).get("backend",""))' "$XOVIUM_PREFIX/state.json")
    [[ "$built" == "$selected" ]] || xovium_fail "PX4 is built for ${built:-no Gazebo backend}. Switch with install.sh --gazebo-backend $selected in the installer kit first."
}
