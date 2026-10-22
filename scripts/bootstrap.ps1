param([string]$CondaEnv = '', [switch]$WithModels, [switch]$WithEvaluation)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
# Reuse a caller-selected environment, otherwise use the repository-local prefix.
$condaArgs = if ($CondaEnv) { @('-n', $CondaEnv) } else { @('-p', (Join-Path $projectRoot '.conda-env')) }
if (-not (Test-Path -LiteralPath '.env')) { Copy-Item -LiteralPath '.env.example' -Destination '.env' }
$extras = @('dev')
if ($WithModels) { $extras += 'ml' }
if ($WithEvaluation) { $extras += 'evaluation' }
$dependency = ".[$($extras -join ',')]"
Set-Location (Join-Path $projectRoot 'backend')
& conda run @condaArgs python -m pip install -e $dependency
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
Set-Location (Join-Path $projectRoot 'frontend')
& conda run @condaArgs npm.cmd ci
exit $LASTEXITCODE
