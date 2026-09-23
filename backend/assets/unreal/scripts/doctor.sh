#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/doctor.sh [--headless]
Read-only checks for the engine, project build, PX4, Python and Vulkan runtime.
--headless skips the Vulkan/display checks for physics-only operation.
Missing dependencies produce exit status 1. Nothing is installed or modified.
EOF
}
headless=0
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --headless) headless=1 ;;
        *) die "Unknown option: $arg" ;;
    esac
done
load_config
failed=0
check_file() {
    if [[ -f $2 ]]; then printf 'OK    %s: %s\n' "$1" "$2"
    else printf 'MISS  %s: %s\n' "$1" "$2"; failed=1; fi
}
printf 'Platform: %s\n' "$(uname -sm)"
printf 'Memory: '; awk '/MemTotal/ { printf "%.1f GiB\n", $2 / 1048576 }' /proc/meminfo
df -h -- "$PROJECT_ROOT"
for command in git python3 cmake ninja make setsid flock bc; do
    if command -v "$command" >/dev/null 2>&1; then printf 'OK    command: %s\n' "$command"
    else printf 'MISS  command: %s\n' "$command"; failed=1; fi
done
check_file UnrealEditor "$UE_ROOT/Engine/Binaries/Linux/UnrealEditor"
check_file 'Unreal build script' "$UE_ROOT/Engine/Build/BatchFiles/Linux/Build.sh"
check_file 'Project module' "$PROJECT_ROOT/Unreal/Binaries/Linux/libUnrealEditor-PX4Unreal.so"
check_file 'PX4 SITL' "${PX4_BUILD:-$PX4_ROOT/build/px4_sitl_default}/bin/px4"
check_file 'MAVLink headers' "$PROJECT_ROOT/ThirdParty/mavlink/include/common/mavlink.h"
if [[ -x $PX4_VENV/bin/python ]] && "$PX4_VENV/bin/python" -c 'import jinja2, kconfiglib, pymavlink, em' 2>/dev/null; then
    printf 'OK    PX4 Python dependencies\n'
else
    printf 'MISS  PX4 Python environment: %s\n' "$PX4_VENV"; failed=1
fi
if (( ! headless )); then
    if command -v vulkaninfo >/dev/null 2>&1 && vulkaninfo --summary >/dev/null 2>&1; then
        printf 'OK    Vulkan loader found a device (use vulkaninfo --summary to verify its driver)\n'
    else
        printf 'MISS  Working Vulkan device; install GPU drivers or use --headless\n'; failed=1
    fi
    printf 'Display: DISPLAY=%s WAYLAND_DISPLAY=%s (offscreen mode does not need a window)\n' "${DISPLAY:-unset}" "${WAYLAND_DISPLAY:-unset}"
fi
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
fi
printf 'CUDA toolkit is not needed by this simulator. Physics runs on CPU; graphics use Vulkan.\n'
exit "$failed"
