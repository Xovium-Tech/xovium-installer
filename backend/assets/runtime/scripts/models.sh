#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
usage() { printf '%s\n' 'Usage: scripts/models.sh [gazebo-jetty|gazebo-harmonic|flightgear|isaac|sih|unreal-engine] [--open]' 'Without a simulator, lists all model and world folders. --open opens its model folder.'; }
selected=all open=0
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --open) open=1 ;;
        gazebo-jetty|gazebo-harmonic|flightgear|isaac|sih|unreal-engine) selected=$arg ;;
        *) xovium_fail "Unknown model selection: $arg" ;;
    esac
done
[[ $selected != all || $open == 0 ]] || xovium_fail 'Choose one simulator with --open.'
for kind in gazebo-jetty gazebo-harmonic flightgear isaac sih unreal-engine; do
    [[ $selected == all || $selected == "$kind" ]] || continue
    models="$XOVIUM_PREFIX/simulation/$kind/models"
    worlds="$XOVIUM_PREFIX/simulation/$kind/worlds"
    printf '%s\n  models: %s\n' "$kind" "$models"
    printf '  worlds: %s\n' "$worlds"
    [[ -d $models ]] || printf '  (not installed yet; install this section from the installer kit)\n'
    if (( open )); then
        [[ -d $models ]] || xovium_fail "Model folder is not installed: $models"
        command -v xdg-open >/dev/null || xovium_fail 'xdg-open is unavailable; open the printed path manually.'
        exec xdg-open "$models"
    fi
done
