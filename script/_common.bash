# Shared by the executable Bash entrypoints. Requires Bash 3.2 or later.
set -euo pipefail
SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
RUNS="$ROOT/.runs"
export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"

die() { printf 'Error: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || die "Install $1 first (see README)."; }
value_required() { [[ $# -ge 2 && -n "$2" && "$2" != --* ]] || die "$1 requires a value."; }
sample_directory() {
    case "$1" in
        01) SAMPLE_DIR="$ROOT/samples/01-shared-store-key-prefix" ;;
        02) SAMPLE_DIR="$ROOT/samples/02-shared-store-label" ;;
        03) SAMPLE_DIR="$ROOT/samples/03-store-per-tenant" ;;
        04) SAMPLE_DIR="$ROOT/samples/04-snapshot-references" ;;
        *) die 'Sample must be 01, 02, 03 or 04.' ;;
    esac
}
state_get() { python3 "$SCRIPT_DIR/_state.py" get "$STATE" "$1"; }
state_set() { python3 "$SCRIPT_DIR/_state.py" set "$STATE" "$1" "$2"; }
load_run() {
    [[ "$1" =~ ^[a-f0-9]{32}$ ]] || die 'Invalid run ID; use the ID printed by bootstrap.'
    RUN_ID=$1
    RUN_DIR="$RUNS/$RUN_ID"
    STATE="$RUN_DIR/state.json"
    [[ -f "$STATE" ]] || die "Run $RUN_ID not found in $RUNS."
    python3 "$SCRIPT_DIR/_state.py" validate "$STATE" "$RUN_ID"
    SAMPLE=$(state_get sample)
    MODE=$(state_get mode)
    STATUS=$(state_get status)
    sample_directory "$SAMPLE"
}
azure() { az "$@" --subscription "$SUBSCRIPTION" --only-show-errors; }

# Retry data-plane operations while a newly assigned RBAC role propagates.
# At most 10 minutes of delay; every sleep is <= 10 seconds.
retry_data() {
    local attempt
    for ((attempt=1; attempt<=61; attempt++)); do
        if "$@"; then return 0; fi
        [[ $attempt -lt 61 ]] || return 1
        printf 'Data-plane operation not ready; retry %s/60 in 10s...\n' "$attempt" >&2
        sleep 10
    done
}
