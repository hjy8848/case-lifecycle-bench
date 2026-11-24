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
tmux new-session -d -s "$session" "bash deploy/run_templates.sh; echo finished; sleep 3600"
echo "started tmux session '$session' in $(pwd)"
