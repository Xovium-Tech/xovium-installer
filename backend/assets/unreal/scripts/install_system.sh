#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/install_system.sh [--nvidia-driver]
Install Ubuntu 24.04 build, Python, Vulkan and Unreal runtime packages using apt.
--nvidia-driver  Also let ubuntu-drivers install its recommended NVIDIA driver.
                This may require a reboot and Secure Boot enrollment.
CUDA is not required: rendering uses Vulkan and aircraft dynamics run on the CPU.
EOF
}
nvidia_driver=0
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --nvidia-driver) nvidia_driver=1 ;;
        *) die "Unknown option: $arg" ;;
    esac
done
[[ $(uname -m) == x86_64 ]] || die 'The supported Unreal Linux package requires x86_64.'
[[ -r /etc/os-release ]] || die 'Cannot identify this operating system.'
source /etc/os-release
[[ $ID == ubuntu && $VERSION_ID == 24.04 ]] || die 'This installer targets Ubuntu 24.04.'
sudo_cmd=()
if (( EUID != 0 )); then need_command sudo; sudo_cmd=(sudo); fi
"${sudo_cmd[@]}" apt-get update
"${sudo_cmd[@]}" apt-get install -y --no-install-recommends \
    build-essential bc ca-certificates ccache cmake curl file git git-lfs \
    ninja-build pkg-config python3 python3-dev python3-pip python3-venv \
    rsync unzip zip xz-utils shellcheck astyle cppcheck gdb lcov \
    libssl-dev libxml2-dev libxml2-utils libeigen3-dev \
    libvulkan1 vulkan-tools mesa-vulkan-drivers libgl1 libegl1 \
    libsdl2-2.0-0 libnss3 libnspr4 libgbm1 libasound2t64 libpulse0 \
    libx11-6 libxrandr2 libxinerama1 libxcursor1 libxi6 libxss1 \
    libxcomposite1 libxdamage1 libxfixes3 libxkbcommon0 libxkbcommon-x11-0 \
    libgtk-3-0t64 libfreetype6 libfontconfig1 libatk1.0-0t64 \
    libatk-bridge2.0-0t64 libdrm2 libglu1-mesa util-linux
if (( nvidia_driver )); then
    "${sudo_cmd[@]}" apt-get install -y ubuntu-drivers-common
    "${sudo_cmd[@]}" ubuntu-drivers install
    note 'NVIDIA installation requested. Reboot before running Unreal; enroll the module key if Secure Boot asks.'
fi
note 'System dependencies installed. Epic Setup.sh supplies the matching compiler for source engine builds.'
