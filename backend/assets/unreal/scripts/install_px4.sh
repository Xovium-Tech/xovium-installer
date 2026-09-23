#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/install_px4.sh [--reference /path/to/existing/PX4-Autopilot] [--skip-build]
Clone PX4 v1.16.0 into the isolated PX4_ROOT, install Python dependencies in
PX4_VENV, and build px4_sitl_default. The launcher prepares the Unreal airframe
in its isolated runtime directory.
--reference borrows Git objects during cloning only, then dissociates; it never
modifies the reference checkout. For example, the existing Isaac checkout can
be supplied here. JOBS controls compilation (default 4).
EOF
}
reference='' skip_build=0
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --reference) require_value "$@"; reference=$2; shift 2 ;;
        --skip-build) skip_build=1; shift ;;
        *) die "Unknown option: $1" ;;
    esac
done
load_config
validate_jobs
need_command git
need_command python3
need_command cmake
need_command ninja
need_command make
if [[ ! -e $PX4_ROOT ]]; then
    clone_options=(--branch v1.16.0 --single-branch)
    if [[ -n $reference ]]; then
        [[ -d $reference/.git ]] || die "Reference is not a Git checkout: $reference"
        clone_options+=(--reference-if-able "$reference" --dissociate)
    fi
    mkdir -p -- "$(dirname -- "$PX4_ROOT")"
    git clone "${clone_options[@]}" https://github.com/PX4/PX4-Autopilot.git "$PX4_ROOT"
fi
[[ -d $PX4_ROOT/.git ]] || die "PX4_ROOT is not a Git checkout: $PX4_ROOT"
expected_commit=$(git -C "$PX4_ROOT" rev-parse 'v1.16.0^{commit}') || die 'The PX4 checkout has no v1.16.0 tag.'
[[ $(git -C "$PX4_ROOT" rev-parse HEAD) == "$expected_commit" ]] || die 'PX4_ROOT must be at v1.16.0; choose an isolated path to avoid changing existing work.'
git -C "$PX4_ROOT" submodule update --init --recursive --jobs "$JOBS"
if [[ ! -x $PX4_VENV/bin/python ]]; then python3 -m venv "$PX4_VENV"; fi
"$PX4_VENV/bin/python" -m pip install --upgrade pip
"$PX4_VENV/bin/python" -m pip install -r "$PX4_ROOT/Tools/setup/requirements.txt"
if (( ! skip_build )); then
    (
        export PATH="$PX4_VENV/bin:$PATH"
        export VIRTUAL_ENV="$PX4_VENV"
        export PYTHON_EXECUTABLE="$PX4_VENV/bin/python"
        cd -- "$PX4_ROOT"
        make -j"$JOBS" px4_sitl_default
    )
    "$PROJECT_ROOT/scripts/generate_mavlink.sh"
fi
note "PX4 v1.16.0 ready in $PX4_ROOT"
