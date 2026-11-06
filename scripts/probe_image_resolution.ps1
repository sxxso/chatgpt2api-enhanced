$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$config = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'config.json') | ConvertFrom-Json
$state = Get-Content -Raw -LiteralPath (Join-Path $projectRoot 'data\local-server.json') | ConvertFrom-Json
$serverProcessId = [int]$state.processId
$baseUrl = 'http://127.0.0.1:3000'
$reportPath = Join-Path $projectRoot 'data\model-checks\image25-resolution-smoke.json'
$report = [ordered]@{ checkedAt = (Get-Date -Format o); mode = 'resolution-smoke'; results = @() }

Add-Type -AssemblyName System.Net.Http
$handler = New-Object System.Net.Http.HttpClientHandler
$handler.UseProxy = $false
$client = New-Object System.Net.Http.HttpClient($handler)
$client.Timeout = [timespan]::FromSeconds(180)
$client.DefaultRequestHeaders.Authorization = New-Object System.Net.Http.Headers.AuthenticationHeaderValue('Bearer', [string]$config.'auth-key')

$proxyListener = Get-NetTCPConnection -State Listen -LocalPort 7897 -ErrorAction SilentlyContinue | Select-Object -First 1
if ($proxyListener) {
    $proxyProcess = Get-Process -Id $proxyListener.OwningProcess -ErrorAction SilentlyContinue
    $report.proxyListenerProcess = $proxyProcess.ProcessName
    Write-Output ('Local proxy listener: ' + $proxyProcess.ProcessName + ' on 127.0.0.1:7897')
}

try {
    $cases = @(
        @{ model = 'gpt-image-2.5-flare'; size = '2048x2048'; prompt = '生成一张图片：晴天下的山谷木屋与溪流，写实风格，细节清晰，无文字。正方形画幅，请输出原生2048x2048像素图片。' },
        @{ model = 'gpt-image-2.5-sunburst'; size = '3840x2160'; prompt = '生成一张图片：晴天下的山谷木屋与溪流，写实风格，细节清晰，无文字。16比9横向画幅，请输出原生3840x2160像素图片。' }
    )
    foreach ($case in $cases) {
        $body = @{ model = $case.model; size = $case.size; prompt = $case.prompt; quality = 'high'; n = 1; response_format = 'url' } | ConvertTo-Json -Compress
        $content = New-Object System.Net.Http.StringContent($body, [Text.Encoding]::UTF8, 'application/json')
        $timer = [Diagnostics.Stopwatch]::StartNew()
        $responseTask = $client.PostAsync("$baseUrl/v1/images/generations", $content)
        $observed = @{}
        while (-not $responseTask.IsCompleted) {
            $connections = Get-NetTCPConnection -OwningProcess $serverProcessId -State Established -ErrorAction SilentlyContinue
            foreach ($connection in $connections) {
                if ($connection.RemotePort -in @(7897, 443)) {
                    $endpoint = '{0}:{1}' -f $connection.RemoteAddress,$connection.RemotePort
                    $observed[$endpoint] = $true
                }
            }
            Start-Sleep -Milliseconds 500
        }
        $response = $responseTask.GetAwaiter().GetResult()
        $responseBody = $response.Content.ReadAsStringAsync().GetAwaiter().GetResult() | ConvertFrom-Json
        $item = [ordered]@{ model = $case.model; requestedSize = $case.size; httpStatus = [int]$response.StatusCode; observedServerConnections = @($observed.Keys); seconds = [math]::Round($timer.Elapsed.TotalSeconds,2) }
        if (-not $response.IsSuccessStatusCode) {
            $item.error = $responseBody.error.message
        } else {
            $imageUrl = [string]$responseBody.data[0].url
            $imageUri = [uri]$imageUrl
            $png = $client.GetByteArrayAsync("$baseUrl$($imageUri.AbsolutePath)").GetAwaiter().GetResult()
            if ($png.Length -lt 24 -or $png[0] -ne 137 -or $png[1] -ne 80) { throw 'The result is not a PNG.' }
            $width = (([int]$png[16]) -shl 24) -bor (([int]$png[17]) -shl 16) -bor (([int]$png[18]) -shl 8) -bor ([int]$png[19])
            $height = (([int]$png[20]) -shl 24) -bor (([int]$png[21]) -shl 16) -bor (([int]$png[22]) -shl 8) -bor ([int]$png[23])
            $item.actualSize = '{0}x{1}' -f $width,$height
            $item.matchesRequestedSize = $item.actualSize -eq $case.size
            $item.bytes = $png.Length
            $item.url = $imageUrl
        }
        $report.results += [pscustomobject]$item
        $report | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $reportPath -Encoding UTF8
        Write-Output ($item | ConvertTo-Json -Depth 4)
        $content.Dispose()
    }
} finally {
    $client.Dispose()
    $handler.Dispose()
}
