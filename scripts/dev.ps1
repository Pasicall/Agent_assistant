param(
    [ValidateSet('backend', 'frontend', 'worker', 'test')]
    [string]$Service = 'backend',
    [string]$CondaEnv = ''
)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$condaArgs = if ($CondaEnv) { @('-n', $CondaEnv) } else { @('-p', (Join-Path $projectRoot '.conda-env')) }
if ($Service -eq 'frontend') {
    Set-Location (Join-Path $projectRoot 'frontend')
    & conda run --no-capture-output @condaArgs npm.cmd run dev
} else {
    Set-Location (Join-Path $projectRoot 'backend')
    if ($Service -eq 'worker') { & conda run --no-capture-output @condaArgs python -m app.worker }
    elseif ($Service -eq 'test') { & conda run --no-capture-output @condaArgs python -m pytest -q }
    else { & conda run --no-capture-output @condaArgs python -m uvicorn app.main:app --host 127.0.0.1 --port 8010 }
}
exit $LASTEXITCODE
