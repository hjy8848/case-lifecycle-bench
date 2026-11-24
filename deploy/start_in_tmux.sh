#!/usr/bin/env bash
# Start run_templates.sh in a detached tmux session so it outlives the login shell.
#
#   PYTHON=/path/to/python3.12 bash deploy/start_in_tmux.sh [session-name]
#
# Watch it with:  tmux attach -t <session>   or   tail -f runs/server-*/progress.log
set -eu
cd "$(dirname "$0")/.."
session=${1:-clb}
if tmux has-session -t "$session" 2>/dev/null; then
    echo "tmux session '$session' already exists; attach to it or pick another name" >&2
    exit 1
fi
# A running tmux server may not inherit this shell's environment, so pass it in.
settings="PYTHON='${PYTHON:-python3}' PORTS='${PORTS:-8101 8102 8103 8104}'"
settings="$settings WORKERS='${WORKERS:-1}' SEEDS='${SEEDS:-10}' START='${START:-300}'"
settings="$settings MODEL='${MODEL:-qwen3.6-35b-a3b}' TAG='${TAG:-}'"
tmux new-session -d -s "$session" "env $settings bash deploy/run_templates.sh; echo finished; sleep 3600"
echo "started tmux session '$session' in $(pwd)"
