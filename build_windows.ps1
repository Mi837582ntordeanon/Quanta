$ErrorActionPreference = "Stop"

$Root = (Resolve-Path -LiteralPath $PSScriptRoot).Path
$Venv = Join-Path $env:TEMP "Quanta-build-py311-venv"
$Python = Join-Path $Venv "Scripts\python.exe"
$PyInstaller = Join-Path $Venv "Scripts\pyinstaller.exe"

if (-not (Test-Path -LiteralPath $Python)) {
    py -3.11 -m venv $Venv
}
if (-not (Test-Path -LiteralPath $Python)) { throw "Python 3.11 is required." }
& $Python -m pip install --disable-pip-version-check -r (Join-Path $Root "requirements.txt") -r (Join-Path $Root "requirements-build.txt")
if ($LASTEXITCODE -ne 0) { throw "Could not install build dependencies." }

$Targets = @((Join-Path $Root "build\pyinstaller"), (Join-Path $Root "build\minecraft-runtime"), (Join-Path $Root "dist\Quanta"), (Join-Path $Root "dist\Quanta-Setup.exe"))
foreach ($Target in $Targets) {
    $ResolvedTarget = [System.IO.Path]::GetFullPath($Target)
    if (-not $ResolvedTarget.StartsWith($Root + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project: $ResolvedTarget"
    }
    if (Test-Path -LiteralPath $ResolvedTarget) {
        Remove-Item -LiteralPath $ResolvedTarget -Recurse -Force
    }
}
& $Python (Join-Path $Root "tools\prepare_minecraft_bundle.py")
if ($LASTEXITCODE -ne 0) { throw "Could not prepare the Minecraft companion runtime." }
& $Python (Join-Path $Root "tools\create_app_icon.py")
if ($LASTEXITCODE -ne 0) { throw "Could not create the application icon." }

& $PyInstaller --noconfirm --clean --distpath (Join-Path $Root "dist") --workpath (Join-Path $Root "build\pyinstaller") (Join-Path $Root "Quanta.spec")
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed." }

$CompilerCandidates = @(
    (Get-Command ISCC.exe -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Source),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 6\ISCC.exe"),
    (Join-Path $env:LOCALAPPDATA "Programs\Inno Setup 7\ISCC.exe")
)
$Compiler = $CompilerCandidates | Where-Object { $_ -and (Test-Path -LiteralPath $_) } | Select-Object -First 1
if ($Compiler) {
    & $Compiler /Qp (Join-Path $Root "installer\Quanta.iss")
    if ($LASTEXITCODE -ne 0) { throw "Inno Setup compilation failed." }
} else {
    Write-Warning "Quanta.exe was built. Install Inno Setup and rerun to create Quanta-Setup.exe."
}

& (Join-Path $Root "tools\smoke_packaged_app.ps1")
if ($LASTEXITCODE -ne 0) { throw "Packaged application smoke check failed." }

Write-Host "Build complete: $(Join-Path $Root 'dist\Quanta\Quanta.exe')"
