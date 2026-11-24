#!/usr/bin/env bash
# Run the card templates against the model replicas on this machine.
#
# The vLLM replicas listen on 127.0.0.1, so the benchmark runs next to them and no
# SSH forward is needed. Five jobs run side by side: the two outbound-call templates
# with a scripted and with a simulated customer, and the loan template once. Each job
# makes a second pass over its output directory, which only fills in runs that failed.
#
#   PYTHON=/path/to/python3.12 PORTS="8101 8102 8103 8104" \
#   WORKERS=1 SEEDS=10 TAG=20261124 bash deploy/run_templates.sh
#
# WORKERS is the runs in flight per replica for each job, so with five jobs a
# replica serves about five times WORKERS requests at once.
set -u
cd "$(dirname "$0")/.."

PYTHON=${PYTHON:-python3}
PORTS=${PORTS:-"8101 8102 8103 8104"}
WORKERS=${WORKERS:-1}
SEEDS=${SEEDS:-10}
START=${START:-300}
MODEL=${MODEL:-qwen3.6-35b-a3b}
TAG=${TAG:-$(date +%Y%m%d-%H%M)}
OUT=runs/server-$TAG
mkdir -p "$OUT"

bases=()
for port in $PORTS; do bases+=("http://127.0.0.1:$port/v1"); done
export NO_PROXY=127.0.0.1,localhost

job() {  # job <name> <template> [extra template_run options]
    local name=$1 template=$2
    shift 2
    local dir=$OUT/$name
    mkdir -p "$dir"
    for pass in 1 2; do
        "$PYTHON" -m case_lifecycle_bench.template_run \
            --template "$template" --start-seed "$START" --seeds "$SEEDS" \
            --model "$MODEL" --api-bases "${bases[@]}" \
            --workers-per-replica "$WORKERS" --output "$dir" --disable-thinking "$@" \
            > "$dir/pass$pass.summary.json" 2> "$dir/pass$pass.err.txt"
        local done
        done=$(find "$dir" -maxdepth 1 -name '*--*--*--*.json' | wc -l)
        echo "$(date -Is) $name pass $pass: $done/$SEEDS result files" >> "$OUT/progress.log"
        [ "$done" -ge "$SEEDS" ] && break
    done
}

echo "$(date -Is) start tag=$TAG workers=$WORKERS seeds=$SEEDS model=$MODEL" >> "$OUT/progress.log"
job l1-script credit-delinquency &
job l1-llm credit-delinquency --llm-customer &
job h1-script field-service-repair &
job h1-llm field-service-repair --llm-customer &
job l2 credit-application &
wait
echo "$(date -Is) all jobs finished" >> "$OUT/progress.log"
for dir in "$OUT"/*/; do
    "$PYTHON" -m case_lifecycle_bench.template_summary "$dir" > "$dir/summary.json" 2>> "$OUT/progress.log"
done
echo "$(date -Is) summaries written" >> "$OUT/progress.log"
