#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./run_sim.sh [--headless | --offscreen] [--speed N] [--hz N]
                    [--instance N] [-- UE_ARGUMENTS...]
Run Unreal and PX4 together; stop both when either exits or on Ctrl-C.
Logs: .runtime/unreal-INSTANCE.log and .runtime/px4-INSTANCE.log.
Headless (-nullrhi) gives the best throughput when camera images are unnecessary.
Use matching --speed/--hz/--instance values when starting the programs separately.
EOF
}
for arg in "$@"; do [[ $arg != -h && $arg != --help ]] || { usage; exit 0; }; done
load_config
unreal_args=()
render_mode=normal
while (( $# )); do
    case "$1" in
        --headless|--offscreen) [[ $render_mode == normal ]] || die 'Choose one rendering mode.'; render_mode=${1#--}; unreal_args+=("$1"); shift ;;
        --speed) require_value "$@"; SIM_SPEED=$2; shift 2 ;;
        --hz) require_value "$@"; SIM_HZ=$2; shift 2 ;;
        --instance) require_value "$@"; PX4_INSTANCE=$2; shift 2 ;;
        --) shift; unreal_args+=(-- "$@"); break ;;
        *) die "Unknown option: $1" ;;
    esac
done
validate_sim_settings
check_ue
need_command setsid
need_command flock
[[ -x ${PX4_BUILD:-$PX4_ROOT/build/px4_sitl_default}/bin/px4 ]] || die 'PX4 build missing; run ./scripts/install_px4.sh.'
mkdir -p -- "$RUNTIME_DIR"
exec 8>"$RUNTIME_DIR/sim-$PX4_INSTANCE.lock"
flock -n 8 || die "A simulator supervisor already owns instance $PX4_INSTANCE."
export PX4_UNREAL_CONFIG=/dev/null
export SIM_SPEED SIM_HZ PX4_INSTANCE
children=()
cleanup() {
    local status=$?
    trap - EXIT INT TERM
    if (( ${#children[@]} )); then
        for pid in "${children[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
        for _ in {1..50}; do
            local alive=0
            for pid in "${children[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=1; done
            (( alive )) || break
            sleep 0.1
        done
        for pid in "${children[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
        for pid in "${children[@]}"; do wait "$pid" 2>/dev/null || true; done
    fi
    exit "$status"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
note "Launching instance $PX4_INSTANCE; log directory: $RUNTIME_DIR"
setsid "$PROJECT_ROOT/scripts/run_unreal.sh" "${unreal_args[@]}" 8>&- &
children+=("$!")
setsid "$PROJECT_ROOT/scripts/run_px4.sh" -d >"$RUNTIME_DIR/px4-$PX4_INSTANCE.log" 2>&1 8>&- &
children+=("$!")
set +e
wait -n "${children[@]}"
status=$?
set -e
note "A simulator process exited (status $status); stopping the pair. PX4 log: $RUNTIME_DIR/px4-$PX4_INSTANCE.log"
exit "$status"
