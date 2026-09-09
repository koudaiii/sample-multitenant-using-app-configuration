# Called by bootstrap only, after deployment and the temporary writer grant.
source "$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)/_common.bash"
load_run "$1"
SUBSCRIPTION=$(state_get subscription)
OUTPUTS="$RUN_DIR/outputs.json"
SHARED_STORE=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sharedStoreName"]["value"])' "$OUTPUTS")

# Generate the three layouts from the same canonical data used in Python tests.
python3 - "$SAMPLE" "$OUTPUTS" > "$RUN_DIR/seed.tsv" <<'PY'
import json, sys
from mtappconfig.sampledata import SHARED_SETTINGS, TENANT_SETTINGS
sample, filename = sys.argv[1:]
outputs = json.load(open(filename))
shared = outputs['sharedStoreName']['value']
tenants = {item['tenantId']: item['name'] for item in outputs.get('tenantStoreNames', {}).get('value', [])}
for tenant, settings in [(None, SHARED_SETTINGS), *TENANT_SETTINGS.items()]:
    for key, value in settings.items():
        store, label = shared, '-'
        if sample in ('01', '04'):
            key = f'{tenant or "_shared"}/{key}'
        elif sample == '02':
            label = tenant or '-'
        elif tenant:
            store = tenants[tenant]
        print(store, key, label, value, sep='\t')
PY
while IFS=$'\t' read -r store key label value; do
    arguments=(appconfig kv set --name "$store" --auth-mode login --yes --key "$key" --value "$value" -o none)
    if [[ "$label" != - ]]; then arguments+=(--label "$label"); fi
    retry_data azure "${arguments[@]}"
done < "$RUN_DIR/seed.tsv"

if [[ "$SAMPLE" == 04 ]]; then
    # Use the same partial snapshot contents as the fake, with real raw key prefixes.
    azure appconfig snapshot create --name "$SHARED_STORE" --auth-mode login \
        --snapshot-name tenant-a-2026-08-01 \
        --filters '{"key":"tenant-a/LogLevel"}' '{"key":"tenant-a/Features:BetaDashboard"}' -o none
    for pair in 'LogLevel|Debug' 'Features:BetaDashboard|true' 'DisplayName|Tenant A (rollout)'; do
        retry_data azure appconfig kv set --name "$SHARED_STORE" --auth-mode login --yes \
            --key "tenant-a/${pair%%|*}" --value "${pair#*|}" -o none
    done
    azure appconfig snapshot create --name "$SHARED_STORE" --auth-mode login \
        --snapshot-name tenant-a-2026-09-01 \
        --filters '{"key":"tenant-a/LogLevel"}' '{"key":"tenant-a/Features:BetaDashboard"}' '{"key":"tenant-a/DisplayName"}' -o none
    for pair in 'LogLevel|Warning' 'Features:BetaDashboard|false' 'DisplayName|Tenant A'; do
        retry_data azure appconfig kv set --name "$SHARED_STORE" --auth-mode login --yes \
            --key "tenant-a/${pair%%|*}" --value "${pair#*|}" -o none
    done
    for pair in 'tenant-a|tenant-a-2026-09-01' 'tenant-b|tenant-b-missing'; do
        retry_data azure appconfig kv set --name "$SHARED_STORE" --auth-mode login --yes \
            --key "${pair%%|*}/RolloutSnapshot" \
            --content-type 'application/json; profile="https://azconfig.io/mime-profiles/snapshot-ref"; charset=utf-8' \
            --value "{\"snapshot_name\":\"${pair#*|}\"}" -o none
    done
fi
