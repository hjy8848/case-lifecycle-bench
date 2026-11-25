#!/usr/bin/env bash
# Run every card template against the model replicas on this machine, Poll-only, once with
# the scripted customer and once with the simulated one, plus the two P7 instances each.
#
#   PYTHON=/path/to/python3.12 PORTS="8101 8102 8103 8104" JOBS=8 WORKERS=1 TAG=20261125 \
#       bash deploy/run_all_templates.sh
#
# JOBS is how many template runs are in flight at once; WORKERS is the runs in flight per
# replica inside each of them. Each run makes a second pass over its directory that only
# fills in the runs that failed. Result files are reused, so the script can be restarted.
set -u
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
PORTS=${PORTS:-"8101 8102 8103 8104"}
JOBS=${JOBS:-8}
WORKERS=${WORKERS:-1}
SEEDS=${SEEDS:-10}
START=${START:-300}
MODEL=${MODEL:-qwen3.6-35b-a3b}
TAG=${TAG:-$(date +%Y%m%d-%H%M)}
OUT=runs/server-$TAG
EXPECTED=$((SEEDS + 2))   # ten ordinary instances and the two P7 ones
mkdir -p "$OUT"

bases=()
for port in $PORTS; do bases+=("http://127.0.0.1:$port/v1"); done
export NO_PROXY=127.0.0.1,localhost
export PYTHON OUT SEEDS START MODEL WORKERS EXPECTED
export BASES="${bases[*]}"

one() {  # one <template>:<mode>
    local template=${1%%:*} mode=${1##*:}
    local dir=$OUT/$template-$mode extra=()
    [ "$mode" = llm ] && extra=(--llm-customer)
    mkdir -p "$dir"
    for pass in 1 2; do
        "$PYTHON" -m case_lifecycle_bench.template_run \
            --template "$template" --start-seed "$START" --seeds "$SEEDS" --p7 \
            --model "$MODEL" --api-bases $BASES \
            --workers-per-replica "$WORKERS" --output "$dir" --disable-thinking "${extra[@]}" \
            > "$dir/pass$pass.summary.json" 2> "$dir/pass$pass.err.txt"
        local done
        done=$(find "$dir" -maxdepth 1 -name '*--*--*--*.json' | wc -l)
        echo "$(date -Is) $template-$mode pass $pass: $done/$EXPECTED result files" >> "$OUT/progress.log"
        [ "$done" -ge "$EXPECTED" ] && break
    done
}
export -f one

echo "$(date -Is) start tag=$TAG jobs=$JOBS workers=$WORKERS model=$MODEL" >> "$OUT/progress.log"
ONLY=${ONLY:-}   # optional: space-separated template names to run instead of all of them
export ONLY
"$PYTHON" - <<'PY' | xargs -P "$JOBS" -I{} bash -c 'one {}'
import os
from case_lifecycle_bench.industries.service_cases import WORKFLOWS
only = os.environ.get("ONLY", "").split()
for name in WORKFLOWS:
    if only and name not in only:
        continue
    for mode in ("script", "llm"):
        print(f"{name}:{mode}")
PY
echo "$(date -Is) all jobs finished" >> "$OUT/progress.log"
for dir in "$OUT"/*/; do
    "$PYTHON" -m case_lifecycle_bench.template_summary "$dir" > "$dir/summary.json" 2>> "$OUT/progress.log"
done
echo "$(date -Is) summaries written" >> "$OUT/progress.log"
