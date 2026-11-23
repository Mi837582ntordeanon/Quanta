[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$')]
    [string]$Stage
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$reviewDir = Join-Path $repoRoot 'review'
$python = (Get-Command python -ErrorAction Stop).Source

function Invoke-Git {
    param([string[]]$GitArguments)
    $output = @(& git -C $repoRoot @GitArguments 2>&1)
    $exitCode = $LASTEXITCODE
    $outputText = ($output | ForEach-Object { [string]$_ }) -join "`n"
    if ($exitCode -ne 0) {
        throw "git $($GitArguments -join ' ') failed with exit code $exitCode`n$outputText"
    }
    return $outputText
}

function Invoke-CheckedCommand {
    param(
        [string]$Label,
        [string]$Executable,
        [string[]]$Arguments
    )
    Write-Host "==> $Label"
    $output = @(& $Executable @Arguments 2>&1)
    $exitCode = $LASTEXITCODE
    foreach ($line in $output) {
        Write-Host ([string]$line)
    }
    if ($exitCode -ne 0) {
        throw "$Label failed with exit code $exitCode"
    }
    return (($output | ForEach-Object { [string]$_ }) -join "`n")
}

function Get-CiResult {
    param([string]$Commit)
    $ghCommand = Get-Command gh -ErrorAction SilentlyContinue
    if (-not $ghCommand) {
        return @{ Run = 'не найден'; Result = 'CI не проверен' }
    }

    $runOutput = @(& $ghCommand.Source run list --commit $Commit --json databaseId,status,conclusion,headSha --limit 20 2>$null)
    if ($LASTEXITCODE -ne 0) {
        return @{ Run = 'не найден'; Result = 'CI не проверен' }
    }
    try {
        $runs = (($runOutput -join "`n") | ConvertFrom-Json)
    } catch {
        return @{ Run = 'не найден'; Result = 'CI не проверен' }
    }
    $run = $runs | Where-Object { $_.headSha -eq $Commit } | Select-Object -First 1
    if (-not $run) {
        return @{ Run = 'не найден'; Result = 'CI не проверен' }
    }

    if ($run.status -ne 'completed') {
        Write-Host "Waiting for GitHub Actions run $($run.databaseId)..."
        & $ghCommand.Source run watch $run.databaseId --exit-status
        $watchExit = $LASTEXITCODE
        $viewOutput = @(& $ghCommand.Source run view $run.databaseId --json databaseId,status,conclusion,headSha 2>$null)
        if ($LASTEXITCODE -eq 0) {
            try {
                $run = (($viewOutput -join "`n") | ConvertFrom-Json)
            } catch {
                # Keep the run-list state when the follow-up JSON cannot be parsed.
            }
        }
        if ($run.status -ne 'completed') {
            return @{ Run = [string]$run.databaseId; Result = 'CI не проверен' }
        }
        if ($watchExit -ne 0 -and -not $run.conclusion) {
            return @{ Run = [string]$run.databaseId; Result = 'CI не проверен' }
        }
    }
    return @{ Run = [string]$run.databaseId; Result = [string]$run.conclusion }
}

$tempArchive = Join-Path ([IO.Path]::GetTempPath()) ("Quanta-review-{0}-{1}.zip" -f $Stage, [guid]::NewGuid().ToString('N'))
$tempExtract = Join-Path ([IO.Path]::GetTempPath()) ("Quanta-review-extract-{0}" -f [guid]::NewGuid().ToString('N'))
$published = $false

try {
    $statusText = (Invoke-Git @('status', '--porcelain', '--untracked-files=all', '--', '.', ':(exclude)review/**')).Trim()
    if ($statusText) {
        Write-Host 'сначала закоммить изменения'
        Write-Host $statusText
        throw 'сначала закоммить изменения'
    }

    $commit = (Invoke-Git @('rev-parse', '--short', 'HEAD')).Trim()
    $fullCommit = (Invoke-Git @('rev-parse', 'HEAD')).Trim()
    $branch = (Invoke-Git @('branch', '--show-current')).Trim()
    if (-not $commit -or -not $fullCommit) {
        throw 'Could not determine the current commit.'
    }

    Write-Host "==> git archive HEAD ($commit)"
    $null = Invoke-Git @('archive', '--format=zip', "--output=$tempArchive", 'HEAD')
    $null = Invoke-CheckedCommand 'Archive secret-file denylist' $python @('-m', 'tools.review_archive', '--check-zip', $tempArchive)

    New-Item -ItemType Directory -Path $tempExtract -Force | Out-Null
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::ExtractToDirectory($tempArchive, $tempExtract)
    Push-Location $tempExtract
    try {
        $testOutput = Invoke-CheckedCommand 'Unit tests' $python @('-m', 'unittest', 'discover', '-s', 'tests', '-v')
        if ($testOutput -notmatch 'Ran\s+(\d+)\s+tests?') {
            throw 'Could not read the test count from unittest output.'
        }
        $testCount = [int]$Matches[1]
        $null = Invoke-CheckedCommand 'Python compileall' $python @('-m', 'compileall', '-q', 'server.py', 'assistant', 'tests')
        $node = (Get-Command node -ErrorAction Stop).Source
        $null = Invoke-CheckedCommand 'UI JavaScript syntax' $node @('--check', 'ui/app.js')
        $null = Invoke-CheckedCommand 'Minecraft JavaScript syntax' $node @('--check', 'assistant/minecraft_bot.js')
    } finally {
        Pop-Location
    }

    New-Item -ItemType Directory -Path $reviewDir -Force | Out-Null
    $archiveName = "Quanta-review-$Stage-$commit.zip"
    $archivePath = Join-Path $reviewDir $archiveName
    Move-Item -LiteralPath $tempArchive -Destination $archivePath -Force

    $tagOutput = @(& git -C $repoRoot describe --tags --abbrev=0 'HEAD^' 2>$null)
    if ($LASTEXITCODE -eq 0 -and $tagOutput.Count -gt 0) {
        $diffBase = ($tagOutput -join '').Trim()
    } else {
        $parentOutput = @(& git -C $repoRoot rev-parse 'HEAD^' 2>$null)
        if ($LASTEXITCODE -eq 0 -and $parentOutput.Count -gt 0) {
            $diffBase = ($parentOutput -join '').Trim()
        } else {
            $diffBase = '--root'
        }
    }
    if ($diffBase -eq '--root') {
        $changedOutput = Invoke-Git @('diff-tree', '--root', '--no-commit-id', '--name-status', '-r', 'HEAD')
    } else {
        $changedOutput = Invoke-Git @('diff', '--name-status', $diffBase, 'HEAD')
    }
    $changedFiles = @($changedOutput -split "`r?`n" | Where-Object { $_ })

    $ci = Get-CiResult $fullCommit
    $archiveHash = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $archiveSize = (Get-Item -LiteralPath $archivePath).Length
    $manifestName = "Quanta-review-$Stage-$commit-manifest.txt"
    $manifestPath = Join-Path $reviewDir $manifestName
    $changedText = if ($changedFiles.Count -gt 0) { $changedFiles -join "`n" } else { '(изменений нет)' }
    $manifest = @(
        "Коммит: $fullCommit",
        "Ветка: $branch",
        "Тесты: $testCount",
        'Результат локальных проверок: успешно',
        "SHA256 архива: $archiveHash",
        "Размер архива: $archiveSize байт",
        "Изменённые файлы относительно $diffBase`:",
        $changedText,
        "CI run: $($ci.Run)",
        "Результат CI: $($ci.Result)"
    )
    [IO.File]::WriteAllText($manifestPath, ($manifest -join "`r`n") + "`r`n", [Text.UTF8Encoding]::new($false))

    $progressCopy = Join-Path $reviewDir "PROGRESS-$commit.md"
    $reportCopy = Join-Path $reviewDir "IMPLEMENTATION_REPORT-$commit.md"
    Copy-Item -LiteralPath (Join-Path $repoRoot 'docs/PROGRESS.md') -Destination $progressCopy -Force
    Copy-Item -LiteralPath (Join-Path $repoRoot 'docs/IMPLEMENTATION_REPORT.md') -Destination $reportCopy -Force
    $published = $true

    Write-Host 'Review files to upload:'
    Write-Host "- $archivePath"
    Write-Host "- $manifestPath"
    Write-Host "- $progressCopy"
    Write-Host "- $reportCopy"
} catch {
    [Console]::Error.WriteLine("Сборка review-архива не завершена: $($_.Exception.Message)")
    exit 1
} finally {
    if (Test-Path -LiteralPath $tempArchive) {
        Remove-Item -LiteralPath $tempArchive -Force
    }
    if (Test-Path -LiteralPath $tempExtract) {
        Remove-Item -LiteralPath $tempExtract -Recurse -Force
    }
    if (-not $published) {
        # No final archive path is created until all extracted-tree checks pass.
    }
}
