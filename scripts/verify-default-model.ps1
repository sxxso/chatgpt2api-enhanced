param([string]$Model = 'gpt-image-2.6-sunburst')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run the first-install CMD first.' }
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'data\accounts.json'))) { throw 'Start the app and import your own account first.' }
Write-Host ('This sends ONE real image request for ' + $Model + ' using your account and may consume its quota.')
if ((Read-Host 'Type YES to run the probe') -cne 'YES') { Write-Host 'Cancelled.'; exit 0 }
& $taskPython -B scripts/probe_pool_models.py web-image --model $Model --timeout 180
if ($LASTEXITCODE -ne 0) { throw 'Model was not verified. Read the reported error or model mismatch above; no model was advertised as usable.' }
Write-Host 'Model verified. Refresh the account page model checks, then the image page.'
