#!/usr/bin/env bash
PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
note() { printf '%s\n' "$*" >&2; }
need_command() { command -v "$1" >/dev/null 2>&1 || die "Missing $1; run ./scripts/install_system.sh first."; }
require_value() { [[ $# -ge 2 && -n $2 ]] || die "$1 needs a value."; }

load_config() {
    local config_file="${PX4_UNREAL_CONFIG:-$PROJECT_ROOT/config/local.env}"
    if [[ -f $config_file ]]; then
        local had_allexport=0
        [[ $- != *a* ]] || had_allexport=1
        set -a
        source "$config_file"
        (( had_allexport )) || set +a
    fi
    UE_ROOT="${UE_ROOT:-$PROJECT_ROOT/.deps/UnrealEngine}"
    PX4_ROOT="${PX4_ROOT:-$PROJECT_ROOT/.deps/PX4-Autopilot}"
    PX4_VENV="${PX4_VENV:-$PROJECT_ROOT/.venv-px4}"
    RUNTIME_DIR="${RUNTIME_DIR:-$PROJECT_ROOT/.runtime}"
    UE_PROJECT="$PROJECT_ROOT/Unreal/PX4Unreal.uproject"
    PX4_INSTANCE="${PX4_INSTANCE:-0}"
    SIM_HZ="${SIM_HZ:-250}"
    SIM_SPEED="${SIM_SPEED:-1}"
    PX4_TIMEOUT="${PX4_TIMEOUT:-30}"
    PX4_STARTUP_TIMEOUT="${PX4_STARTUP_TIMEOUT:-120}"
    JOBS="${JOBS:-4}"
    export PROJECT_ROOT UE_ROOT PX4_ROOT PX4_VENV RUNTIME_DIR UE_PROJECT
    export PX4_INSTANCE SIM_HZ SIM_SPEED PX4_TIMEOUT PX4_STARTUP_TIMEOUT JOBS
}

validate_instance() {
    [[ $PX4_INSTANCE =~ ^(0|[1-9][0-9]{0,2})$ ]] && (( PX4_INSTANCE <= 254 )) || die 'PX4_INSTANCE must be an integer from 0 through 254.'
}

validate_jobs() {
    [[ $JOBS =~ ^[1-9][0-9]*$ ]] || die 'JOBS must be a positive integer.'
}

validate_sim_settings() {
    validate_instance
    [[ $SIM_HZ == 250 ]] || die 'SIM_HZ must be 250 to match the PX4 IMU/control lockstep rate.'
    [[ $SIM_SPEED =~ ^[0-9]+([.][0-9]+)?$ ]] || die 'SIM_SPEED must be nonnegative (0 means unlimited).'
    [[ $PX4_TIMEOUT =~ ^[1-9][0-9]*$ ]] || die 'PX4_TIMEOUT must be a positive integer.'
    [[ $PX4_STARTUP_TIMEOUT =~ ^[1-9][0-9]*$ ]] || die 'PX4_STARTUP_TIMEOUT must be a positive integer.'
}

check_ue() {
    [[ -x $UE_ROOT/Engine/Binaries/Linux/UnrealEditor ]] || die "UnrealEditor missing in $UE_ROOT. Run ./scripts/install_unreal.sh --help."
    [[ -f $UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh ]] || die "Unreal C++ build scripts missing in $UE_ROOT."
}
