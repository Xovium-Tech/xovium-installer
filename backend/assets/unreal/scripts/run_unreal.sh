#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/run_unreal.sh [--headless | --offscreen] [--speed N] [--hz N]
                               [--instance N] [--editor] [-- UE_ARGUMENTS...]
Run the compiled fixed-wing simulator; start ./run_px4.sh in another terminal.
Visible simulation starts in a 1280x720 window.
--headless   Disable rendering with -nullrhi (no camera images).
--offscreen  Vulkan rendering without a visible window.
--speed N    Wall-clock target; 1 real time, 0 unlimited, e.g. 5 five times real time.
--hz N       Physics/sensor rate; currently must be 250 for PX4 lockstep.
R1 rover runs at real time with cached 100 Hz controls and 250 Hz sensors.
--editor     Open the interactive editor (alias for ./run_editor.sh).
TCP simulator port is 4560 + instance. Extra engine options go after --.
EOF
}
for arg in "$@"; do [[ $arg != -h && $arg != --help ]] || { usage; exit 0; }; done
load_config
render_mode=normal editor=0
extra=()
while (( $# )); do
    case "$1" in
        --headless|--offscreen) [[ $render_mode == normal ]] || die 'Choose one rendering mode.'; render_mode=${1#--}; shift ;;
        --speed) require_value "$@"; SIM_SPEED=$2; shift 2 ;;
        --hz) require_value "$@"; SIM_HZ=$2; shift 2 ;;
        --instance) require_value "$@"; PX4_INSTANCE=$2; shift 2 ;;
        --editor) editor=1; shift ;;
        --) shift; extra=("$@"); break ;;
        *) die "Unknown option: $1" ;;
    esac
done
if (( editor )); then
    [[ $render_mode == normal ]] || die '--editor requires a visible window; use ./run_editor.sh.'
    exec "$PROJECT_ROOT/scripts/run_editor.sh" -- "${extra[@]}"
fi
validate_sim_settings
[[ "$XOVIUM_VEHICLE" != r1_rover ]] || SIM_SPEED=1
check_ue
[[ -f $UE_PROJECT ]] || die "Unreal project missing: $UE_PROJECT"
[[ -f $PROJECT_ROOT/Unreal/Binaries/Linux/libUnrealEditor-PX4Unreal.so ]] || die 'Build the project first: ./scripts/build_unreal.sh'
mkdir -p -- "$RUNTIME_DIR"
args=("$UE_PROJECT" "${XOVIUM_UE_WORLD:-/Engine/Maps/Entry}?game=/Script/PX4Unreal.PX4GameMode"
    -unattended -log -nosound "-abslog=$RUNTIME_DIR/unreal-$PX4_INSTANCE.log"
    "-PX4Port=$((4560 + PX4_INSTANCE))" "-SimHz=$SIM_HZ" "-SimSpeed=$SIM_SPEED"
    "-PX4Timeout=$PX4_TIMEOUT" "-PX4StartupTimeout=$PX4_STARTUP_TIMEOUT")
args+=(-game)
[[ -z ${XOVIUM_UE_WORLD:-} ]] || args+=(-PX4CustomWorld)
[[ -z ${XOVIUM_UE_MODEL:-} ]] || args+=("-PX4Model=$XOVIUM_UE_MODEL")
case "$render_mode" in
    headless) args+=(-nullrhi) ;;
    offscreen) args+=(-RenderOffscreen -vulkan) ;;
    normal) args+=(-vulkan -windowed -ResX=1280 -ResY=720) ;;
esac
args+=("-PX4Vehicle=$XOVIUM_VEHICLE")
[[ -z ${XOVIUM_TERRAIN_CONFIG:-} ]] || args+=("-PX4TerrainConfig=$XOVIUM_TERRAIN_CONFIG")
note "Starting Unreal: instance $PX4_INSTANCE, TCP $((4560 + PX4_INSTANCE)), ${SIM_HZ} Hz, speed $SIM_SPEED"
exec "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor" "${args[@]}" "${extra[@]}"
