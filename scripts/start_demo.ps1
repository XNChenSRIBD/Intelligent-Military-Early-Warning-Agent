param([string]$Python = 'python')
Set-Location -LiteralPath (Split-Path -Parent $PSScriptRoot)
& $Python -m app
exit $LASTEXITCODE
