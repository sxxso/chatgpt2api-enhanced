param([switch]$Preview)

$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot)).TrimEnd('\')
if (-not (Test-Path -LiteralPath (Join-Path $taskRoot 'main.py')) -or
    -not (Test-Path -LiteralPath (Join-Path $taskRoot 'web_dist\login\index.html'))) {
    throw 'Project files could not be verified. Nothing was removed.'
}

# Only disposable build products. Runtime data, installed
# dependencies, source, final web_dist, image files and backups are retained.
$taskAllowlist = @(
    'web\.next', 'web\out', 'web\tsconfig.tsbuildinfo',
    '__pycache__',
    'api\__pycache__', 'services\storage\__pycache__',
    'services\protocol\__pycache__', 'services\__pycache__',
    'utils\__pycache__', 'scripts\__pycache__', 'test\__pycache__'
)

function Assert-TaskPath([string]$taskRelative) {
    $taskFullPath = [IO.Path]::GetFullPath((Join-Path $taskRoot $taskRelative))
    if (-not $taskFullPath.StartsWith($taskRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Cleanup target is outside this project.'
    }
    $taskCurrent = $taskFullPath
    while ($taskCurrent.Length -ge $taskRoot.Length) {
        $taskItem = Get-Item -LiteralPath $taskCurrent -Force
        if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'Linked paths are not cleanup targets.'
        }
        if ($taskCurrent -eq $taskRoot) { break }
        $taskCurrent = [IO.Path]::GetDirectoryName($taskCurrent)
    }
    return $taskFullPath
}

function Get-TaskTarget([string]$taskRelative) {
    $taskFullPath = Assert-TaskPath $taskRelative
    $taskStack = [Collections.Generic.Stack[string]]::new()
    $taskStack.Push($taskFullPath)
    [long]$taskBytes = 0
    [long]$taskFiles = 0
    while ($taskStack.Count -gt 0) {
        $taskItem = Get-Item -LiteralPath $taskStack.Pop() -Force
        if ($taskItem.Attributes -band [IO.FileAttributes]::ReparsePoint) {
            throw 'A cleanup directory contains a linked file or directory.'
        }
        if ($taskItem.PSIsContainer) {
            foreach ($taskChild in (Get-ChildItem -LiteralPath $taskItem.FullName -Force)) {
                $taskStack.Push($taskChild.FullName)
            }
        } else {
            $taskHandle = [IO.File]::Open($taskItem.FullName, [IO.FileMode]::Open,
                [IO.FileAccess]::Read, [IO.FileShare]::None)
            $taskHandle.Dispose()
            $taskBytes += $taskItem.Length
            $taskFiles++
        }
    }
    return [pscustomobject]@{ Path = $taskRelative; FullPath = $taskFullPath; Bytes = $taskBytes; Files = $taskFiles }
}

$taskBusy = @(Get-CimInstance Win32_Process -Filter "Name = 'node.exe' OR Name = 'python.exe'" |
    Where-Object {
        $_.CommandLine -and $_.CommandLine.Contains($taskRoot) -and
        ($_.CommandLine -match 'check_recovery_ui|check_reliability|next[\\/].*|next (dev|start|build)')
    })
if ($taskBusy.Count) { throw 'A project build, development server or fixture is running. Stop it before cleanup.' }

$taskTargets = @(foreach ($taskRelative in $taskAllowlist) {
    if (Test-Path -LiteralPath (Join-Path $taskRoot $taskRelative)) {
        Get-TaskTarget $taskRelative
    }
})
$taskTargets | Select-Object Path, Files, @{Name = 'MiB'; Expression = { [math]::Round($_.Bytes / 1MB, 2) }} | Format-Table -AutoSize
$taskTotal = [long](($taskTargets | Measure-Object Bytes -Sum).Sum)
Write-Host ('Disposable files: {0:N2} MiB' -f ($taskTotal / 1MB))
if ($Preview) {
    Write-Host 'Preview only. No files were removed.'
    exit 0
}

$taskResults = @(foreach ($taskTarget in $taskTargets) {
    # Recheck containment, links and exclusive file access immediately before deleting.
    $taskFresh = Get-TaskTarget $taskTarget.Path
    if ($taskFresh.Bytes -ne $taskTarget.Bytes -or $taskFresh.Files -ne $taskTarget.Files) {
        throw 'Cleanup target changed during validation. Run cleanup again when idle.'
    }
    Remove-Item -LiteralPath $taskFresh.FullPath -Recurse -Force
    [pscustomobject]@{ Path = $taskTarget.Path; Removed = $true; Bytes = $taskTarget.Bytes; Files = $taskTarget.Files }
})
$taskReportDir = Join-Path $taskRoot 'data\maintenance'
New-Item -ItemType Directory -Path $taskReportDir -Force | Out-Null
$taskReport = Join-Path $taskReportDir ('cleanup-executed-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')
[pscustomobject]@{
    CompletedAtUtc = [datetime]::UtcNow.ToString('o')
    Root = $taskRoot
    RemovedBytes = [long](($taskResults | Where-Object Removed | Measure-Object Bytes -Sum).Sum)
    Targets = $taskResults
} | ConvertTo-Json -Depth 4 | Set-Content -LiteralPath $taskReport -Encoding UTF8
Write-Host ('Cleanup completed. Removed {0:N2} MiB.' -f ($taskTotal / 1MB))
Write-Host 'Source, accounts, settings, images, backups, dependencies and web_dist were retained.'
