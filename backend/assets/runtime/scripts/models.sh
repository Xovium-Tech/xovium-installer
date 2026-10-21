#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
usage() { printf '%s\n' 'Usage: scripts/models.sh [shared|gazebo-jetty|gazebo-harmonic|flightgear|isaac|sih|unreal-engine|px4] [--open]' 'Shared models: Cessna, rover, custom SDF. Shared worlds: Baylands, dynamic terrain.' 'Without a selection, lists shared sources and simulator exports. --open opens its model folder.' 'px4 opens the unchanged upstream simulator assets for reference.'; }
selected=all open=0
for arg in "$@"; do
    case "$arg" in
        -h|--help) usage; exit 0 ;;
        --open) open=1 ;;
        shared|gazebo-jetty|gazebo-harmonic|flightgear|isaac|sih|unreal-engine|px4) selected=$arg ;;
        *) xovium_fail "Unknown model selection: $arg" ;;
    esac
done
[[ $selected != all || $open == 0 ]] || xovium_fail 'Choose shared, one simulator or px4 with --open.'
if [[ $selected == px4 ]]; then
    upstream="$XOVIUM_PREFIX/px4/source/Tools/simulation"
    printf 'Upstream PX4 simulator assets (reference)\n  %s\n' "$upstream"
    printf '  Shared selections apply to the managed simulator launchers.\n'
    if (( open )); then
        [[ -d $upstream ]] || xovium_fail "PX4 simulator assets are not installed: $upstream"
        command -v xdg-open >/dev/null || xovium_fail 'xdg-open is unavailable; open the printed path manually.'
        exec xdg-open "$upstream"
    fi
    exit 0
fi
if [[ $selected == all || $selected == shared ]]; then
    printf 'Shared library\n  models: Cessna, rover, custom SDF\n  worlds: Baylands, dynamic terrain\n'
    printf '  model sources: %s/shared/models\n  world sources: %s/shared/worlds\n  transform: %s/shared/transform\n' "$XOVIUM_PREFIX" "$XOVIUM_PREFIX" "$XOVIUM_PREFIX"
    printf '  Regenerate exports from the installer: ./install.sh --refresh-assets\n'
    printf '  Upstream simulator assets: scripts/models.sh px4\n'
    if (( open )); then
        [[ -d $XOVIUM_PREFIX/shared/models ]] || xovium_fail 'Shared model sources are not installed yet.'
        command -v xdg-open >/dev/null || xovium_fail 'xdg-open is unavailable; open the printed path manually.'
        exec xdg-open "$XOVIUM_PREFIX/shared/models"
    fi
fi
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
