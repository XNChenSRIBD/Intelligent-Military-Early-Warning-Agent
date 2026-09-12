param(
    [Parameter(Mandatory=$true)][string]$AssetRoot,
    [Parameter(Mandatory=$true)][string]$DataDir,
    [Parameter(Mandatory=$true)][string]$ModelBaseUrl,
    [int]$Port = 8081
)

$ErrorActionPreference = 'Stop'
$replayProject = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $replayProject
$env:PIPELINE_MODE = 'case_replay'
$env:PIPELINE_ENABLED = 'true'
$env:PIPELINE_ANALYSIS_VERSION = 'gnss_replay_v1'
$env:REPLAY_AUTOSTART = 'true'
$env:REPLAY_MANIFESTS = 'cases/replay/kharkiv/manifest.json;cases/replay/hormuz/manifest.json'
$env:REPLAY_ASSET_ROOT = [System.IO.Path]::GetFullPath($AssetRoot)
$env:DATA_DIR = [System.IO.Path]::GetFullPath($DataDir)
$env:MODEL_BASE_URL = $ModelBaseUrl
$env:APP_HOST = '127.0.0.1'
$env:APP_PORT = [string]$Port
python -m app
exit $LASTEXITCODE
