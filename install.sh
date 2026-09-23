#!/usr/bin/env bash
set -euo pipefail
KIT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
export XOVIUM_KIT_ROOT="$KIT" PYTHONDONTWRITEBYTECODE=1
case "${1:-}" in
  --cli) shift; exec python3 -B "$KIT/backend/xovium_installer.py" "$@" ;;
  --uninstall) shift; exec python3 -B "$KIT/backend/xovium_installer.py" uninstall "$@" ;;
  --help|-h)
    cat <<'HELP'
Xovium Installer 0.2.0 — Ubuntu 24.04 x86-64
  ./install.sh                          Build/open the C++ installation wizard
  ./install.sh --build-only             Build the wizard only
  ./install.sh --cli --help             Scriptable installer commands
  ./install.sh --cli --status           Show managed installation
  ./install.sh --uninstall --dry-run    Preview removal
  ./install.sh --uninstall              Remove managed software; retain this kit
First GUI build needs git, cmake, g++, Python 3.12, X11/OpenGL development packages.
GUI libraries use pinned upstream versions; source archives are cached locally.
HELP
    exit ;;
  --all|-PX4|--px4|-QGC|--qgc|--gazebo-*|--prefix|--status|--dry-run|--relocate|--refresh-launchers|--print-prefix|-unreal-engine|--unreal-engine|--flightgear|--isaac|--sih|--mavlink-router|--jobs)
    exec python3 -B "$KIT/backend/xovium_installer.py" "$@" ;;
esac
command -v python3 >/dev/null || { printf 'Python 3 is required. Install python3 first.\n' >&2; exit 1; }
python3 -B "$KIT/app/system_bootstrap.py"
mkdir -p "$KIT/.build"
exec 9>"$KIT/.build/bootstrap.lock"
flock 9
python3 -B "$KIT/app/bootstrap.py"
cmake -S "$KIT" -B "$KIT/.build/cmake" -DCMAKE_BUILD_TYPE=Release >"$KIT/.build/configure.log" 2>&1 || { cat "$KIT/.build/configure.log" >&2; exit 1; }
cmake --build "$KIT/.build/cmake" --parallel "${XOVIUM_BUILD_JOBS:-4}" >"$KIT/.build/build.log" 2>&1 || { cat "$KIT/.build/build.log" >&2; exit 1; }
flock -u 9
exec 9>&-
[[ ${1:-} != --build-only ]] || { printf 'Built: %s/.build/cmake/xovium-installer\n' "$KIT"; exit; }
exec "$KIT/.build/cmake/xovium-installer" --kit "$KIT" "$@"
