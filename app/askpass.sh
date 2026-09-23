#!/usr/bin/env bash
set -euo pipefail
KIT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
HELPER="$KIT/.build/cmake/xovium-askpass"
if [[ ! -x "$HELPER" ]]; then
    printf 'Xovium: password helper is missing. Reopen install.sh to rebuild it.\n' >&2
    exit 1
fi
exec "$HELPER" "$@"
