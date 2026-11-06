param(
    [ValidateSet('Start', 'Stop')]
    [string]$Action = 'Start',
    [switch]$NoBrowser,
    [ValidateRange(1024,65535)]
    [int]$Port = 3000
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$pythonExe = Join-Path $projectRoot '.venv\Scripts\python.exe'
$dataDir = Join-Path $projectRoot 'data'
$stateFile = Join-Path $dataDir 'local-server.json'
$stdoutLog = Join-Path $dataDir 'local-server.stdout.log'
$stderrLog = Join-Path $dataDir 'local-server.stderr.log'
$baseUrl = "http://127.0.0.1:$Port"

function Get-LocalServer {
    if (-not (Test-Path -LiteralPath $stateFile)) { return $null }
    $state = Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json
    $process = Get-CimInstance Win32_Process -Filter "ProcessId=$([int]$state.processId)"
    if ($null -eq $process) { return $null }
    $expectedStart = [datetime]::Parse($state.startedAt).ToUniversalTime()
    $actualStart = $process.CreationDate.ToUniversalTime()
    if ([math]::Abs(($actualStart - $expectedStart).TotalSeconds) -gt 1) { return $null }
    if (-not $process.CommandLine.Contains('uvicorn') -or
        -not $process.CommandLine.Contains($projectRoot)) {
        throw 'The saved PID belongs to another process. No process was stopped.'
    }
    return $process
}

$server = Get-LocalServer
if ($Action -eq 'Stop') {
    if ($null -ne $server) {
        Stop-Process -Id $server.ProcessId -ErrorAction SilentlyContinue
        Write-Host 'ChatGPT2API stopped.'
    } else {
        Write-Host 'ChatGPT2API is already stopped.'
    }
    exit 0
}

if ($null -ne $server) {
    Write-Host "ChatGPT2API is already running: $baseUrl"
    $savedUrl = (Get-Content -Raw -LiteralPath $stateFile | ConvertFrom-Json).url
    if (-not $NoBrowser) { Start-Process $savedUrl }
    exit 0
}

if (-not (Test-Path -LiteralPath $pythonExe)) {
    throw 'Python environment is missing. Run uv sync --frozen --no-dev in the project directory.'
}
if (-not (Test-Path -LiteralPath (Join-Path $projectRoot 'web_dist\login\index.html'))) {
    throw 'The built web panel is missing. See the Windows run guide.'
}
$listener = Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue
if ($listener) { throw "Port $Port is already in use by another process." }

New-Item -ItemType Directory -Path $dataDir -Force | Out-Null
$env:PYTHONUTF8 = '1'
$arguments = @('-u', '-m', 'uvicorn', 'main:app', '--app-dir', "`"$projectRoot`"", '--host', '127.0.0.1', '--port', [string]$Port)
$launcher = Start-Process -FilePath $pythonExe -ArgumentList $arguments -WorkingDirectory $projectRoot `
    -WindowStyle Hidden -RedirectStandardOutput $stdoutLog -RedirectStandardError $stderrLog -PassThru

$deadline = [datetime]::UtcNow.AddSeconds(30)
$ready = $false
Add-Type -AssemblyName System.Net.Http
$localHandler = New-Object System.Net.Http.HttpClientHandler
$localHandler.UseProxy = $false
$localClient = New-Object System.Net.Http.HttpClient($localHandler)
$localClient.Timeout = [timespan]::FromSeconds(2)
while ([datetime]::UtcNow -lt $deadline) {
    try {
        $version = $localClient.GetStringAsync("$baseUrl/version").GetAwaiter().GetResult() | ConvertFrom-Json
        if ($version.version) { $ready = $true; break }
    } catch {}
    $launcher.Refresh()
    if ($launcher.HasExited) { break }
    Start-Sleep -Milliseconds 300
}
$localClient.Dispose()
if (-not $ready) {
    throw "Startup failed. Check $stderrLog"
}

$listener = Get-NetTCPConnection -State Listen -LocalAddress '127.0.0.1' -LocalPort $Port | Select-Object -First 1
$server = Get-CimInstance Win32_Process -Filter "ProcessId=$($listener.OwningProcess)"
if (-not $server.CommandLine.Contains('uvicorn') -or -not $server.CommandLine.Contains($projectRoot)) {
    throw 'The listening process could not be verified.'
}
@{
    processId = $server.ProcessId
    startedAt = $server.CreationDate.ToUniversalTime().ToString('o')
    url = $baseUrl
} | ConvertTo-Json | Set-Content -LiteralPath $stateFile -Encoding UTF8

Write-Host "ChatGPT2API v$($version.version) is running: $baseUrl"
Write-Host 'Login with the auth-key value from config.json.'
if (-not $NoBrowser) { Start-Process $baseUrl }
