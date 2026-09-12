#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

mode="${1:-replay}"
export SERVER_ASSET_ROOT="${SERVER_ASSET_ROOT:-/home/xnchen/gnss_public_data/chasing_lightning}"
export SERVER_RUNTIME_ROOT="${SERVER_RUNTIME_ROOT:-/home/xnchen/gnss_public_data/early_warning_runtime}"
export REPLAY_ASSET_ROOT="$SERVER_ASSET_ROOT"
export ACQUISITION_ENABLED=true
export ACQUISITION_AUTOCLEAN="${ACQUISITION_AUTOCLEAN:-false}"
export PIPELINE_ENABLED=true
export MODEL_BASE_URL="${MODEL_BASE_URL:-http://127.0.0.1:8000/v1}"
export MODEL_NAME="${MODEL_NAME:-qwen3-4b}"
export APP_HOST="${APP_HOST:-10.20.14.16}"
if [[ "$mode" == replay ]]; then
    export PIPELINE_MODE=case_replay
    export DATA_DIR="${DATA_DIR:-$SERVER_RUNTIME_ROOT/replays/round4}"
    export APP_PORT="${APP_PORT:-8081}"
    export REPLAY_AUTOSTART=true
    export PIPELINE_ANALYSIS_VERSION="${PIPELINE_ANALYSIS_VERSION:-gnss_replay_v2}"
elif [[ "$mode" == online ]]; then
    export PIPELINE_MODE=online
    # The existing deployment already stores its database here; keep it in place.
    export DATA_DIR="${DATA_DIR:-$PWD/runtime}"
    export APP_PORT="${APP_PORT:-8080}"
else
    echo 'Usage: bash scripts/start_server_data.sh [replay|online]' >&2
    exit 2
fi
exec "${WORKBENCH_PYTHON:-$PWD/.venv-workbench/bin/python}" -m app
