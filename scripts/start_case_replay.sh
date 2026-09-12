#!/usr/bin/env bash
set -eu
if [ "$#" -lt 3 ]; then
    echo 'Usage: bash scripts/start_case_replay.sh ASSET_ROOT DATA_DIR MODEL_BASE_URL [PORT]'
    exit 2
fi
export REPLAY_ASSET_ROOT="$1"
export DATA_DIR="$2"
export MODEL_BASE_URL="$3"
export APP_PORT="${4:-8081}"
export APP_HOST=127.0.0.1
export PIPELINE_MODE=case_replay
export PIPELINE_ENABLED=true
export PIPELINE_ANALYSIS_VERSION=gnss_replay_v1
export REPLAY_AUTOSTART=true
export REPLAY_MANIFESTS='cases/replay/kharkiv/manifest.json;cases/replay/hormuz/manifest.json'
cd "$(dirname "$0")/.."
exec python -m app
