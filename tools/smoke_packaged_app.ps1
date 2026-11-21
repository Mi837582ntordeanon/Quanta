$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$Exe = Join-Path $Root "dist\Quanta\Quanta.exe"
if (-not (Test-Path -LiteralPath $Exe)) { throw "Packaged executable not found: $Exe" }

$DataRoot = Join-Path $env:TEMP ("quanta-packaged-smoke-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $DataRoot | Out-Null
$DataRoot = (Resolve-Path -LiteralPath $DataRoot).Path
$Settings = '{"global_hotkey_enabled":false,"model_warmup_on_start":false,"voice_enabled":false}'
[IO.File]::WriteAllText((Join-Path $DataRoot "settings.json"), $Settings, [Text.UTF8Encoding]::new($false))

$Listener = [System.Net.Sockets.TcpListener]::new([Net.IPAddress]::Loopback, 0)
$Listener.Start()
$Port = ([Net.IPEndPoint]$Listener.LocalEndpoint).Port
$Listener.Stop()

$OldEnvironment = @{
    QUANTA_DATA_DIR = $env:QUANTA_DATA_DIR
    QUANTA_PORT = $env:QUANTA_PORT
    QUANTA_SKIP_MODEL_WARMUP = $env:QUANTA_SKIP_MODEL_WARMUP
}
$env:QUANTA_DATA_DIR = $DataRoot
$env:QUANTA_PORT = [string]$Port
$env:QUANTA_SKIP_MODEL_WARMUP = "1"
$Process = $null
try {
    $Process = Start-Process -FilePath $Exe -ArgumentList "--quanta-server" -WorkingDirectory (Split-Path $Exe) -PassThru -WindowStyle Hidden
    $Ready = $false
    for ($Attempt = 0; $Attempt -lt 80; $Attempt++) {
        if ($Process.HasExited) { break }
        try {
            $Page = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/" -TimeoutSec 2 -UseBasicParsing
            if ($Page.StatusCode -eq 200) { $Ready = $true; break }
        } catch { }
        Start-Sleep -Milliseconds 250
    }
    if (-not $Ready) {
        $Log = Join-Path $DataRoot "quanta.log"
        if (Test-Path -LiteralPath $Log) { Get-Content -LiteralPath $Log -Tail 30 }
        throw "Packaged server failed to start (process exited: $($Process.HasExited))."
    }
    $Match = [regex]::Match($Page.Content, '<meta name="quanta-session-token" content="([^"]+)"')
    if (-not $Match.Success) { throw "Packaged interface did not receive a session token." }
    $Headers = @{ "X-Quanta-Session" = $Match.Groups[1].Value }
    $Api = Invoke-WebRequest -Uri "http://127.0.0.1:$Port/api/bootstrap" -Headers $Headers -TimeoutSec 8 -UseBasicParsing
    $Bootstrap = $Api.Content | ConvertFrom-Json
    $ExpectedLeaf = Split-Path -Leaf $DataRoot
    $ActualLeaf = Split-Path -Leaf ([string]$Bootstrap.data_dir)
    if ($Api.StatusCode -ne 200 -or -not $Bootstrap.ok -or $ActualLeaf -ne $ExpectedLeaf) {
        throw "Packaged API validation failed: status=$($Api.StatusCode), ok=$($Bootstrap.ok), data_dir='$($Bootstrap.data_dir)', expected='$DataRoot'."
    }
    Write-Host "Packaged server smoke passed: version=$($Bootstrap.version), port=$Port, isolated data=$DataRoot"
} finally {
    if ($Process -and -not $Process.HasExited) {
        Stop-Process -Id $Process.Id -Force
        $Process.WaitForExit()
    }
    foreach ($Name in $OldEnvironment.Keys) {
        $OldValue = $OldEnvironment[$Name]
        if ($null -eq $OldValue) { Remove-Item -Path "Env:$Name" -ErrorAction SilentlyContinue }
        else { Set-Item -Path "Env:$Name" -Value $OldValue }
    }
}
