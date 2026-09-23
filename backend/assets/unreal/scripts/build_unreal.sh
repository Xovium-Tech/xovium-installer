#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/build_unreal.sh [--generate-project]
Build the PX4UnrealEditor C++ module with the engine's bundled Linux toolchain.
Install Unreal and PX4 first. Headers are generated automatically when missing.
JOBS caps concurrent compiler actions (default 4).
EOF
}
generate_project=0
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --generate-project) generate_project=1; shift ;;
        *) die "Unknown option: $1" ;;
    esac
done
load_config
validate_jobs
check_ue
[[ -f $UE_PROJECT ]] || die "Unreal project missing: $UE_PROJECT"
if [[ ! -f $PROJECT_ROOT/ThirdParty/mavlink/include/common/mavlink.h ]]; then
    "$PROJECT_ROOT/scripts/generate_mavlink.sh"
fi
if (( generate_project )); then
    bash "$UE_ROOT/Engine/Build/BatchFiles/Linux/GenerateProjectFiles.sh" -project="$UE_PROJECT" -game -engine
fi
exec bash "$UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh" \
    PX4UnrealEditor Linux Development "$UE_PROJECT" \
    -WaitMutex -NoHotReloadFromIDE "-MaxParallelActions=$JOBS"
