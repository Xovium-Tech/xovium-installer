#!/usr/bin/env bash
set -euo pipefail
source "$(dirname -- "${BASH_SOURCE[0]}")/common.sh"
usage() {
    cat <<'EOF'
Usage: ./scripts/install_unreal.sh --archive /path/Linux_Unreal_Engine_5.6.1.zip
       ./scripts/install_unreal.sh --source [--ref 5.6.1-release]
       ./scripts/install_unreal.sh --check
Archive: download the official Linux Unreal Engine 5.6.1 archive with your Epic account:
  https://www.unrealengine.com/en-US/linux
Source: link your Epic and GitHub accounts, then authenticate Git for EpicGames/UnrealEngine.
  UE_GIT_URL may select SSH instead of HTTPS; no credentials belong in this repo.
Installs to UE_ROOT (default .deps/UnrealEngine). JOBS defaults to 4.
Existing engine paths are never deleted or switched to another revision.
EOF
}
mode='' archive='' ref=5.6.1-release
while (( $# )); do
    case "$1" in
        -h|--help) usage; exit 0 ;;
        --archive) require_value "$@"; [[ -z $mode ]] || die 'Choose one installation mode.'; mode=archive; archive=$2; shift 2 ;;
        --source) [[ -z $mode ]] || die 'Choose one installation mode.'; mode=source; shift ;;
        --check) [[ -z $mode ]] || die 'Choose one installation mode.'; mode=check; shift ;;
        --ref) require_value "$@"; ref=$2; shift 2 ;;
        *) die "Unknown option: $1" ;;
    esac
done
[[ -n $mode ]] || { usage; exit 2; }
load_config
validate_jobs
if [[ $mode == archive ]]; then
    need_command unzip
    need_command python3
    [[ -f $archive ]] || die "Archive not found: $archive"
    [[ ! -e $UE_ROOT ]] || die "UE_ROOT already exists: $UE_ROOT. Use --check or choose an empty path."
    archive_root=$(python3 - "$archive" <<'PY'
import pathlib, sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    names = archive.namelist()
    for name in names:
        path = pathlib.PurePosixPath(name)
        if path.is_absolute() or '..' in path.parts:
            raise SystemExit('Unsafe path in engine archive')
    suffix = 'Engine/Binaries/Linux/UnrealEditor'
    roots = [name[:-len(suffix)] for name in names if name.endswith(suffix)]
    if len(roots) != 1:
        raise SystemExit('Expected one Linux UnrealEditor in the archive')
    print(roots[0])
PY
    )
    mkdir -p -- "$(dirname -- "$UE_ROOT")"
    extraction_dir=$(mktemp -d "$(dirname -- "$UE_ROOT")/.unreal-extract.XXXXXX")
    trap 'note "Extraction staging directory retained at $extraction_dir if installation failed."' ERR
    note "Extracting official archive into $UE_ROOT (this can take several minutes)."
    unzip -q "$archive" -d "$extraction_dir"
    if [[ -n $archive_root ]]; then
        mv -- "$extraction_dir/$archive_root" "$UE_ROOT"
        rmdir -- "$extraction_dir" 2>/dev/null || true
    else
        mv -- "$extraction_dir" "$UE_ROOT"
    fi
    trap - ERR
    if [[ -f $UE_ROOT/Engine/Build/BatchFiles/Linux/SetupToolchain.sh ]]; then
        bash "$UE_ROOT/Engine/Build/BatchFiles/Linux/SetupToolchain.sh"
    fi
elif [[ $mode == source ]]; then
    need_command git
    need_command make
    if [[ ! -e $UE_ROOT ]]; then
        mkdir -p -- "$(dirname -- "$UE_ROOT")"
        git clone --branch "$ref" --single-branch --depth 1 \
            "${UE_GIT_URL:-https://github.com/EpicGames/UnrealEngine.git}" "$UE_ROOT"
    else
        [[ -d $UE_ROOT/.git ]] || die "Existing UE_ROOT is not a source checkout: $UE_ROOT"
        expected_commit=$(git -C "$UE_ROOT" rev-parse "$ref^{commit}") || die "Requested ref $ref is absent in existing engine."
        [[ $(git -C "$UE_ROOT" rev-parse HEAD) == "$expected_commit" ]] || die "Existing engine HEAD does not match $ref. Choose a separate UE_ROOT."
    fi
    (
        cd -- "$UE_ROOT"
        ./Setup.sh
        ./GenerateProjectFiles.sh
        for target in UnrealEditor ShaderCompileWorker UnrealPak; do
            bash Engine/Build/BatchFiles/Linux/Build.sh "$target" Linux Development "-MaxParallelActions=$JOBS"
        done
    )
fi
check_ue
if [[ -f $UE_ROOT/Engine/Build/Build.version ]]; then
    python3 - "$UE_ROOT/Engine/Build/Build.version" <<'PY'
import json, sys
v = json.load(open(sys.argv[1]))
version = '.'.join(str(v[k]) for k in ('MajorVersion', 'MinorVersion', 'PatchVersion'))
print('Unreal Engine ' + version)
if (v['MajorVersion'], v['MinorVersion']) != (5, 6):
    print('Warning: this project targets UE 5.6; another version is unverified.', file=sys.stderr)
PY
fi
note "Engine ready: $UE_ROOT"
