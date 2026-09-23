#!/usr/bin/env bash
set -euo pipefail

ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
PX4_DIR=${PX4_DIR:-"$ROOT/.deps/PX4-Autopilot"}
PX4_COMMIT=6ea3539157ca358c70a515878b77077af7d4611d
ISAAC_VERSION=6.1.0.0
PYMAVLINK_VERSION=2.4.49
INSTALL_PX4=1
INSTALL_ISAAC=1
SYSTEM_DEPS=0

fail() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

for arg in "$@"; do
    case "$arg" in
        --px4-only) INSTALL_ISAAC=0 ;;
        --isaac-only) INSTALL_PX4=0 ;;
        --system-deps) SYSTEM_DEPS=1 ;;
        -h|--help)
            printf '%s\n' \
                'Usage: ./install.sh [--system-deps] [--px4-only | --isaac-only]' \
                'Default: PX4 v1.16.0 SITL and Isaac Sim 6.1.0.0 on Ubuntu 24.04 x86_64.' \
                '--system-deps: install missing Ubuntu packages with sudo apt-get.' \
                'PX4_DIR: reuse a clean checkout at v1.16.0; default .deps/PX4-Autopilot.' \
                'ISAAC_PYTHON: validate an existing Isaac Python/python.sh without modifying it.' \
                'JOBS: parallel build jobs; default 4.'
            exit 0
            ;;
        *) fail "Unknown option: $arg. Run ./install.sh --help." ;;
    esac
done

(( INSTALL_PX4 || INSTALL_ISAAC )) || fail '--px4-only and --isaac-only cannot be combined.'
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] || fail 'This installer requires Linux x86_64.'
[[ -r /etc/os-release ]] || fail 'Cannot identify the operating system.'
source /etc/os-release
[[ ${ID:-} == ubuntu && ${VERSION_ID:-} == 24.04 ]] || fail 'This installer targets Ubuntu 24.04.'
[[ $EUID -ne 0 ]] || fail 'Run as your normal user; --system-deps uses sudo only for apt.'
[[ ${JOBS:-4} =~ ^[1-9][0-9]*$ ]] || fail 'JOBS must be a positive integer.'

if (( INSTALL_ISAAC )); then
    command -v nvidia-smi >/dev/null || fail 'Install a supported NVIDIA RTX driver, reboot, and confirm nvidia-smi works before installing Isaac Sim.'
    nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || fail 'NVIDIA driver is not working; resolve nvidia-smi before continuing.'
    printf '%s\n' 'Isaac Sim 6.1 requirements: https://docs.isaacsim.omniverse.nvidia.com/6.1.0/installation/requirements.html'
fi

PACKAGES=(ca-certificates python3.12 python3.12-venv)
if (( INSTALL_PX4 )); then
    PACKAGES+=(build-essential ccache cmake file git libssl-dev libxml2-dev libxml2-utils ninja-build pkg-config python3-dev rsync unzip zip)
fi
if (( INSTALL_ISAAC )); then
    PACKAGES+=(libegl1 libgl1 libvulkan1 libxt6 libxrandr2 libxi6 libxrender1 libxkbcommon0 libnss3 libasound2t64)
fi
MISSING=()
for package in "${PACKAGES[@]}"; do
    if [[ $(dpkg-query -W -f='${Status}' "$package" 2>/dev/null || true) != 'install ok installed' ]]; then
        MISSING+=("$package")
    fi
done
if (( ${#MISSING[@]} )); then
    if (( ! SYSTEM_DEPS )); then
        printf 'Missing Ubuntu packages: %s\n' "${MISSING[*]}" >&2
        fail 'Rerun with --system-deps (and the same component options) or install those packages first.'
    fi
    command -v sudo >/dev/null || fail 'Install sudo or ask the system administrator to install the listed packages.'
    sudo apt-get update
    sudo apt-get install --no-install-recommends -y "${MISSING[@]}"
fi
python3.12 -c 'import ensurepip, venv' || fail 'Python 3.12 venv support is missing; install python3.12-venv.'

if (( INSTALL_PX4 )); then
    if [[ ! -e $PX4_DIR ]]; then
        mkdir -p -- "$(dirname -- "$PX4_DIR")"
        git clone --branch v1.16.0 --depth 1 https://github.com/PX4/PX4-Autopilot.git "$PX4_DIR"
    fi
    [[ -e $PX4_DIR/.git ]] || fail "PX4_DIR is not a Git checkout: $PX4_DIR"
    [[ $(git -C "$PX4_DIR" rev-parse HEAD) == "$PX4_COMMIT" ]] || fail "PX4_DIR must be exactly PX4 v1.16.0: $PX4_DIR"
    [[ -z $(git -C "$PX4_DIR" status --porcelain --untracked-files=all) ]] || fail "PX4_DIR has local changes; use a separate clean checkout: $PX4_DIR"
    git -C "$PX4_DIR" submodule update --init --recursive --jobs "${JOBS:-4}"
    if [[ ! -e $ROOT/.venv-px4 ]]; then
        python3.12 -m venv "$ROOT/.venv-px4"
    fi
    PX4_PYTHON="$ROOT/.venv-px4/bin/python"
    [[ -x $PX4_PYTHON ]] || fail 'Incomplete .venv-px4; move it aside and rerun the installer.'
    "$PX4_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "PX4 venv must use Python 3.12"'
    "$PX4_PYTHON" -m pip install --upgrade pip
    "$PX4_PYTHON" -m pip install -r "$PX4_DIR/Tools/setup/requirements.txt" "pymavlink==$PYMAVLINK_VERSION" 'numpy<2'
    PATH="$ROOT/.venv-px4/bin:$PATH" make -C "$PX4_DIR" -j "${JOBS:-4}" px4_sitl_default
    [[ -x $PX4_DIR/build/px4_sitl_default/bin/px4 ]] || fail 'PX4 build did not produce its SITL executable.'
    printf 'PX4 ready: %s\n' "$PX4_DIR/build/px4_sitl_default/bin/px4"
fi

if (( INSTALL_ISAAC )); then
    if [[ -n ${ISAAC_PYTHON:-} ]]; then
        [[ $ISAAC_PYTHON == /* && -x $ISAAC_PYTHON ]] || fail 'ISAAC_PYTHON must be an executable absolute path to Python or python.sh.'
        "$ISAAC_PYTHON" -c 'import importlib.util, sys; assert sys.version_info[:2] == (3, 12), "Expected Python 3.12"; assert importlib.util.find_spec("isaacsim"), "Isaac Sim is missing"; import pymavlink' || fail 'Existing Isaac Python must already contain Isaac Sim and pymavlink and use Python 3.12. Unset ISAAC_PYTHON to create the isolated installation.'
        printf 'Using existing Isaac Python without package changes: %s\n' "$ISAAC_PYTHON"
    else
        if [[ ! -e $ROOT/.venv-isaac ]]; then
            python3.12 -m venv "$ROOT/.venv-isaac"
        fi
        ISAAC_PYTHON="$ROOT/.venv-isaac/bin/python"
        [[ -x $ISAAC_PYTHON ]] || fail 'Incomplete .venv-isaac; move it aside and rerun the installer.'
        "$ISAAC_PYTHON" -c 'import sys; assert sys.version_info[:2] == (3, 12), "Isaac venv must use Python 3.12"'
        "$ISAAC_PYTHON" -m pip install --upgrade pip
        "$ISAAC_PYTHON" -m pip install 'torch==2.11.0' --index-url https://download.pytorch.org/whl/cu128
        "$ISAAC_PYTHON" -m pip install "isaacsim[all,extscache]==$ISAAC_VERSION" "pymavlink==$PYMAVLINK_VERSION" --extra-index-url https://pypi.nvidia.com
        "$ISAAC_PYTHON" -c 'from importlib.metadata import version; import pymavlink; assert version("isaacsim") == "6.1.0.0"'
        printf 'Isaac Python ready: %s\n' "$ISAAC_PYTHON"
    fi
    printf '%s\n' 'The first Isaac launch presents NVIDIA license acceptance if it has not already been accepted.'
fi
