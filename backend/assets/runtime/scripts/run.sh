#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/lib/common.sh"
usage() {
    cat <<'EOF'
Usage: scripts/run.sh SIMULATOR_OR_NUMBER [--no-qgc | --qgc] [--headless] [--dry-run]
                      [--router | --no-router] [--output auto|tabs|console|files] [--list] [--models] [-- SIMULATOR_ARGUMENTS...]
Simulators: 1 gazebo-jetty, 2 gazebo-harmonic, 3 flightgear, 4 isaac, 5 sih, 6 unreal-engine
Examples: ./run.sh gazebo-jetty --qgc
          ./qgc/run.sh                 (QGC on its own)
Names and numbers work both as command arguments and in the interactive menu.
--list shows which simulators are installed without starting anything.
Starts the simulator and PX4 together, plus QGC when installed.
Ctrl-C or a simulator/PX4 exit stops this session. Closing QGC leaves it running.
Live output opens in terminal tabs on a desktop, or here when headless/using SSH.
--output console shows all components here; --output files only saves logs.
Closing the QGC output tab stops QGC only. Closing a PX4/simulator tab stops
this session. Ctrl-C here or closing the whole output window stops it too.
Logs: INSTALL_PATH/runtime/sessions/SIMULATOR-INSTANCE/TIMESTAMP/
INSTALL_PATH is the parent of this scripts folder.
--headless disables QGC unless --qgc is explicitly selected.
--models prints this simulator's model/world folders without starting anything.
--router uses an existing MAVLink router for any simulator, including Unreal.
--no-router overrides a router setting from scripts/local.env.
The installed QGC launcher selects its saved router/direct link automatically.
scripts/local.env can supply PX4_INSTANCE, SIM_LAT/LON/ALT and other launch settings.
EOF
}
choices=(gazebo-jetty gazebo-harmonic flightgear isaac sih unreal-engine)
show_menu() {
    local i name command status built
    built=$(python3 -B -c 'import json,sys; s=json.load(open(sys.argv[1])); print(s.get("components",{}).get("px4",{}).get("backend",""))' "$XOVIUM_PREFIX/state.json")
    for i in "${!choices[@]}"; do
        name=${choices[$i]}
        case "$name" in
            gazebo-*) command=$name ;;
            flightgear|isaac) command=$name ;;
            sih) command=sih-px4 ;;
            unreal-engine) command=unreal ;;
        esac
        status='installed'
        if [[ ! -x "$XOVIUM_PREFIX/scripts/.commands/$command" ]]; then
            status='not installed'
        elif [[ $name == gazebo-* && $built != "${name#gazebo-}" ]]; then
            status="installed; switch PX4 backend to ${name#gazebo-}"
        elif [[ $name == gazebo-* && ! -x "$XOVIUM_PREFIX/scripts/.commands/px4-gazebo" ]] ||
             [[ $name == isaac && ! -x "$XOVIUM_PREFIX/scripts/.commands/isaac-px4" ]]; then
            status='incomplete; repair in installer'
        fi
        printf '  %s) %-17s [%s]\n' "$((i + 1))" "$name" "$status"
    done
}
simulator='' qgc=auto headless=0 dry=0 router='' output=''
extra=()
while (( $# )); do
    case "$1" in
        [1-6])
            [[ -z $simulator ]] || xovium_fail 'Choose one simulator per session.'
            simulator=${choices[$(( $1 - 1 ))]}; shift ;;
        gazebo-jetty|gazebo-harmonic|flightgear|isaac|sih|unreal-engine)
            [[ -z $simulator ]] || xovium_fail 'Choose one simulator per session.'
            simulator=$1; shift ;;
        --no-qgc) qgc=no; shift ;;
        --qgc) qgc=yes; shift ;;
        --headless) headless=1; shift ;;
        --dry-run) dry=1; shift ;;
        --output)
            (( $# >= 2 )) || xovium_fail '--output needs auto, tabs, console or files.'
            output=$2; shift 2 ;;
        --router) router=1; shift ;;
        --no-router) router=0; shift ;;
        --list) show_menu; exit 0 ;;
        --models) exec "$XOVIUM_ROOT/scripts/models.sh" ${simulator:+"$simulator"} ;;
        -h|--help) usage; exit 0 ;;
        --) shift; extra=("$@"); break ;;
        *) xovium_fail "Unknown launcher option: $1; pass simulator options after --." ;;
    esac
done
if [[ -z $simulator ]]; then
    if [[ ! -t 0 ]]; then
        usage
        xovium_fail 'Choose a simulator, for example: ./run.sh gazebo-jetty --qgc. QGC alone: ./qgc/run.sh'
    fi
    printf 'Choose a simulator (QGC starts too when installed):\n'
    show_menu
    read -r -p 'Number or simulator name (Enter cancels): ' choice || exit 0
    [[ -n $choice ]] || exit 0
    if [[ $choice =~ ^[1-6]$ ]]; then simulator=${choices[$((choice - 1))]}; else simulator=$choice; fi
    case "$simulator" in
        gazebo-jetty|gazebo-harmonic|flightgear|isaac|sih|unreal-engine) ;;
        *) xovium_fail "Unknown simulator: $simulator" ;;
    esac
fi
xovium_configure
output=${output:-${XOVIUM_OUTPUT:-auto}}
case "$output" in auto|tabs|console|files) ;; *) xovium_fail '--output needs auto, tabs, console or files.' ;; esac
[[ -z $router ]] || export PX4_MAVLINK_ROUTER=$router
case "${PX4_MAVLINK_ROUTER:-0}" in
    0|1) ;;
    *) xovium_fail 'PX4_MAVLINK_ROUTER must be 0 or 1.' ;;
esac
(( ! headless )) || [[ $qgc != auto ]] || qgc=no
case "$simulator" in
    gazebo-*)
        xovium_check_gazebo "${simulator#gazebo-}"
        main=px4-gazebo
        (( ! headless )) || export HEADLESS=1
        [[ $PX4_INSTANCE == 0 ]] || xovium_fail 'The shared Gazebo launcher currently supports PX4_INSTANCE=0.'
        ;;
    flightgear)
        main=flightgear
        (( ! headless )) || xovium_fail 'FlightGear needs a graphical display.'
        [[ $PX4_INSTANCE == 0 ]] || xovium_fail 'The FlightGear bridge currently supports PX4_INSTANCE=0.'
        ;;
    isaac) main=isaac; (( ! headless )) || extra=(--headless "${extra[@]}") ;;
    sih)
        if [[ -x "$XOVIUM_PREFIX/scripts/.commands/sih-view" ]]; then
            main=sih-view; (( ! headless )) || extra=(--headless "${extra[@]}")
        else
            main=sih-px4; extra=(-d "${extra[@]}")
        fi
        ;;
    unreal-engine) main=unreal; (( ! headless )) || extra=(--headless "${extra[@]}") ;;
esac
xovium_require "$main"
case "$simulator" in isaac) xovium_require isaac-px4 ;; sih) xovium_require sih-px4 ;; esac
if [[ $qgc == yes ]]; then xovium_require qgc; fi
if [[ $qgc == auto ]]; then
    if [[ -x "$XOVIUM_PREFIX/scripts/.commands/qgc" ]]; then qgc=yes; else qgc=no; fi
fi
children=() active=() output_pid='' qgc_pid=0
declare -A names=()
cleanup() {
    local status=$?
    trap - EXIT INT TERM HUP
    local pid alive
    for pid in "${children[@]}"; do kill -TERM -- "-$pid" 2>/dev/null || true; done
    for _ in {1..50}; do
        alive=0
        for pid in "${children[@]}"; do kill -0 -- "-$pid" 2>/dev/null && alive=1; done
        (( alive )) || break
        sleep 0.1
    done
    for pid in "${children[@]}"; do kill -KILL -- "-$pid" 2>/dev/null || true; done
    for pid in "${children[@]}"; do wait "$pid" 2>/dev/null || true; done
    printf '%s\n' "$status" >"$log_dir/session.exit"
    if [[ -n $output_pid ]]; then
        for _ in {1..30}; do
            kill -0 -- "-$output_pid" 2>/dev/null || break
            sleep 0.1
        done
        kill -TERM -- "-$output_pid" 2>/dev/null || true
        wait "$output_pid" 2>/dev/null || true
    fi
    exit "$status"
}
start() {
    local name=$1
    shift
    printf 'Start: '; printf '%q ' "$XOVIUM_PREFIX/scripts/.commands/$name" "$@"; printf '\n'
    if (( ! dry )); then
        setsid --wait stdbuf -oL -eL "$XOVIUM_PREFIX/scripts/.commands/$name" "$@" 9>&- >"$log_dir/$name.log" 2>&1 &
        children+=("$!")
        active+=("$!")
        names[$!]=$name
        if [[ $name == qgc ]]; then qgc_pid=$!; fi
    fi
}
if (( ! dry )); then
    command -v stdbuf >/dev/null || xovium_fail 'stdbuf (coreutils) is required.'
    command -v setsid >/dev/null || xovium_fail 'setsid (util-linux) is required.'
    command -v flock >/dev/null || xovium_fail 'flock (util-linux) is required.'
    mkdir -p -- "$XOVIUM_PREFIX/runtime/sessions"
    exec 9>"$XOVIUM_PREFIX/runtime/sessions/instance-$PX4_INSTANCE.lock"
    flock -n 9 || xovium_fail "Another managed simulator session owns instance $PX4_INSTANCE."
    log_dir="$XOVIUM_PREFIX/runtime/sessions/$simulator-$PX4_INSTANCE/$(date +%Y%m%d-%H%M%S)-$$"
    mkdir -p -- "$log_dir"
    printf 'Logs: %s\n' "$log_dir"
    export XOVIUM_SESSION_LOG_DIR=$log_dir PYTHONUNBUFFERED=1
    unset XOVIUM_PX4_LOG XOVIUM_UNREAL_LOG
    case "$simulator" in
        flightgear) export XOVIUM_PX4_LOG="$log_dir/flightgear-px4.log" ;;
        unreal-engine)
            export XOVIUM_PX4_LOG="$log_dir/unreal-px4.log"
            export XOVIUM_UNREAL_LOG="$log_dir/unreal-engine.log" ;;
    esac
    trap cleanup EXIT
    trap 'exit 130' INT
    trap 'exit 143' TERM HUP
    case "$simulator" in
        isaac|sih) [[ $main == sih-px4 ]] || printf 'Isaac first startup can take several minutes while the GPU compiles shaders. Logs: %s\n' "$log_dir" ;;
    esac
    if [[ $simulator == isaac ]]; then
        extra+=("--/log/file=$log_dir/isaac-kit.log")
        printf 'Isaac engine/shader log: %s/isaac-kit.log\n' "$log_dir"
    fi
fi
if [[ ${PX4_MAVLINK_ROUTER:-0} == 1 ]]; then
    printf 'Router mode: PX4 sends to UDP %s; QGC uses its saved automatic router link.\n' "${XOVIUM_ROUTER_PX4_PORT:-14550}"
fi
[[ $qgc != yes ]] || start qgc
case "$simulator" in isaac) start isaac-px4 -d ;; sih) [[ $main == sih-px4 ]] || start sih-px4 -d ;; esac
start "$main" "${extra[@]}"
if (( ! dry )) && [[ $output != files ]]; then
    setsid --wait python3 -u "$XOVIUM_ROOT/scripts/lib/session-output.py" start \
        "$log_dir" "$output" "$simulator" "$main" "$qgc" "$headless" "$$" "$qgc_pid" 9>&- &
    output_pid=$!
fi
if (( ! dry )); then
    printf 'Session running. Press Ctrl-C to stop.\n'
    while (( ${#active[@]} )); do
        ended='' status=0
        wait -n -p ended "${active[@]}" || status=$?
        [[ -n $ended ]] || exit "$status"
        name=${names[$ended]}
        printf '\n%s exited (status %s).\n' "$name" "$status" >>"$log_dir/$name.log"
        printf '%s\n' "$status" >"$log_dir/$name.exit"
        printf '%s exited (status %s). Log: %s/%s.log\n' "$name" "$status" "$log_dir" "$name"
        if (( status )); then
            tail -c 4000 -- "$log_dir/$name.log" | tail -n 20 >&2
            if [[ $name != qgc && -n ${XOVIUM_PX4_LOG:-} && -s $XOVIUM_PX4_LOG ]]; then
                printf 'PX4 output:\n' >&2
                tail -c 4000 -- "$XOVIUM_PX4_LOG" | tail -n 20 >&2
            fi
        fi
        if [[ $name != qgc ]]; then exit "$status"; fi
        printf 'Simulator is still running. Restart QGC with ./qgc/run.sh, or Ctrl-C to stop.\n'
        remaining=()
        for pid in "${active[@]}"; do [[ $pid == "$ended" ]] || remaining+=("$pid"); done
        active=("${remaining[@]}")
    done
fi
