param([switch]$RebuildWeb)

$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $taskRoot
$taskUv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $taskUv) {
    $taskUvPath = Join-Path $env:USERPROFILE '.local\bin\uv.exe'
    if (Test-Path -LiteralPath $taskUvPath) { $taskUv = Get-Item -LiteralPath $taskUvPath }
}
if (-not $taskUv) { throw 'Install uv first: https://docs.astral.sh/uv/getting-started/installation/ . Reopen this window afterwards.' }
$taskUvExe = if ($taskUv.Path) { $taskUv.Path } else { $taskUv.FullName }
$taskPanelReady = Test-Path -LiteralPath (Join-Path $taskRoot 'web_dist\login\index.html')
$taskBuildWeb = $RebuildWeb -or -not $taskPanelReady
if ($taskBuildWeb) {
    if (-not (Get-Command node -ErrorAction SilentlyContinue) -or -not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
        throw 'Source checkout needs Node.js (tested with 22.x). Install it or use the Windows ZIP from Releases, which includes the built panel.'
    }
}
$taskRunning = @(Get-CimInstance Win32_Process -Filter "Name = 'python.exe' OR Name = 'node.exe'" | Where-Object {
    $_.CommandLine -and $_.CommandLine.Contains($taskRoot) -and
    ($_.CommandLine.Contains('uvicorn') -or $_.CommandLine -match 'next (dev|start|build)')
})
if ($taskRunning.Count) { throw 'Stop this project before installing or updating dependencies.' }

Write-Host '[1/3] Installing Python 3.13 and locked backend dependencies...'
& $taskUvExe python install 3.13
if ($LASTEXITCODE -ne 0) { throw 'Python installation failed. Check network access and retry.' }
& $taskUvExe sync --frozen --no-dev
if ($LASTEXITCODE -ne 0) { throw 'Backend installation failed. Check network access and retry.' }
$taskPython = Join-Path $taskRoot '.venv\Scripts\python.exe'
& $taskPython -B scripts/init_local_config.py
if ($LASTEXITCODE -ne 0) { throw 'Configuration setup failed. The existing config was preserved.' }

if ($taskBuildWeb) {
    Write-Host '[2/3] Installing locked frontend dependencies and building the panel...'
    Push-Location -LiteralPath (Join-Path $taskRoot 'web')
    try {
        & npm.cmd ci --no-audit --no-fund --cache (Join-Path $taskRoot '.local\npm-cache')
        if ($LASTEXITCODE -ne 0) { throw 'Frontend installation failed.' }
        & npm.cmd run build
        if ($LASTEXITCODE -ne 0) { throw 'Frontend build failed.' }
    } finally { Pop-Location }
    $taskOutput = Join-Path $taskRoot 'web_dist'
    New-Item -ItemType Directory -Path $taskOutput -Force | Out-Null
    Copy-Item -Path (Join-Path $taskRoot 'web\out\*') -Destination $taskOutput -Recurse -Force
} else {
    Write-Host '[2/3] Using the included panel. Node.js is not required for this ZIP.'
}
Write-Host '[3/3] Ready. Double-click the start CMD to open http://127.0.0.1:3000 .'
Write-Host 'Log in with auth-key from config.json, then import your own account.'
