#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./run_editor.sh [--map /Game/Maps/MyMap] [-- UE_EDITOR_ARGUMENTS...]
Open PX4Unreal.uproject in the interactive Unreal Editor.

Uses the project's EditorStartupMap unless --map selects another map.
No PX4 process is needed to edit the project. Log: .runtime/editor.log.
The supplied runway and airplane spawn at runtime, so the default editor map
looks mostly empty. Save your own maps under the project's Content directory.
PX4 flight currently uses ./run_sim.sh; Play in Editor is disabled.
UE_ROOT and RUNTIME_DIR can be set in config/local.env or the environment.
EOF
}
map=''
extra=()
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --map)
            require_value "$@"
            case "$2" in
                /Game/*|/Engine/*) map=$2 ;;
                *) die 'Use an Unreal map asset path, e.g. /Game/Maps/MyMap.' ;;
            esac
            shift 2
            ;;
        --) shift; extra=("$@"); break ;;
        *) die "Unknown option: $1. Use --help." ;;
    esac
done
load_config
check_ue
[[ -f $UE_PROJECT ]] || die "Unreal project missing: $UE_PROJECT"
[[ -f $PROJECT_ROOT/Unreal/Binaries/Linux/libUnrealEditor-PX4Unreal.so ]] || die 'Build the project first: ./scripts/build_unreal.sh'
mkdir -p -- "$RUNTIME_DIR"
args=("$UE_PROJECT")
[[ -z $map ]] || args+=("$map")
args+=(-vulkan -log "-abslog=$RUNTIME_DIR/editor.log")
note "Opening Unreal Editor: $UE_PROJECT"
exec "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor" "${args[@]}" "${extra[@]}"
