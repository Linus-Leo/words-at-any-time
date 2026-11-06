[CmdletBinding()]
param(
    [string]$Version = "1.0.0",
    [string]$NsisExe = "",
    [switch]$SkipInstaller
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
Set-Location $root

$buildDir = Join-Path $PSScriptRoot "build"
$distDir = Join-Path $PSScriptRoot "dist"
$stageDir = Join-Path $PSScriptRoot "stage"
$artifactsDir = Join-Path $PSScriptRoot "artifacts"
$specPath = $PSScriptRoot

function Invoke-Py {
    param([string[]]$PyArgs)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    & py -3.11 @PyArgs
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    if ($code -ne 0) {
        throw "Python command failed (exit $code): py -3.11 $($PyArgs -join ' ')"
    }
}

function Test-PyModule {
    param([string]$Module)
    $previous = $ErrorActionPreference
    $ErrorActionPreference = "SilentlyContinue"
    & py -3.11 -c "import $Module" 2>$null | Out-Null
    $code = $LASTEXITCODE
    $ErrorActionPreference = $previous
    return ($code -eq 0)
}

Write-Host "[1/5] Checking build dependencies"
if (-not (Test-PyModule -Module "PySide6")) {
    Invoke-Py -PyArgs @("-m", "pip", "install", "-r", (Join-Path $root "requirements.txt"))
}
if (-not (Test-PyModule -Module "PyInstaller")) {
    Invoke-Py -PyArgs @("-m", "pip", "install", "-r", (Join-Path $PSScriptRoot "requirements-build.txt"))
}

Write-Host "[2/5] Cleaning previous build outputs"
foreach ($path in @($buildDir, $distDir, $stageDir)) {
    if (Test-Path -LiteralPath $path) {
        Remove-Item -LiteralPath $path -Recurse -Force
    }
    New-Item -ItemType Directory -Path $path -Force | Out-Null
}
New-Item -ItemType Directory -Path $artifactsDir -Force | Out-Null

Write-Host "[3/5] Building FloatingWordBall with PyInstaller"
$pyiArgs = @(
    "-m", "PyInstaller", "--noconfirm", "--clean", "--windowed", "--onedir",
    "--name", "FloatingWordBall",
    "--icon", (Join-Path $root "floatingwordball.ico"),
    "--version-file", (Join-Path $PSScriptRoot "version_info.txt"),
    "--add-data", "$(Join-Path $root 'vocab');vocab",
    "--add-data", "$(Join-Path $root 'importance.json');.",
    "--add-data", "$(Join-Path $root 'LICENSES');LICENSES",
    "--add-data", "$(Join-Path $root 'THIRD_PARTY_NOTICES.md');.",
    "--add-data", "$(Join-Path $root 'README.md');.",
    "--distpath", $distDir,
    "--workpath", $buildDir,
    "--specpath", $specPath,
    (Join-Path $root "main.py")
)
Invoke-Py -PyArgs $pyiArgs

$appDir = Join-Path $distDir "FloatingWordBall"
$exePath = Join-Path $appDir "FloatingWordBall.exe"
if (-not (Test-Path -LiteralPath $exePath)) {
    throw "PyInstaller did not produce $exePath"
}

Write-Host "[4/5] Building portable ZIP and Setup EXE"
$portableRoot = Join-Path $stageDir "portable"
New-Item -ItemType Directory -Path $portableRoot -Force | Out-Null
Copy-Item -LiteralPath $appDir -Destination $portableRoot -Recurse -Force
$portableApp = Join-Path $portableRoot "FloatingWordBall"
Set-Content -LiteralPath (Join-Path $portableApp "portable.flag") `
    -Value "Portable mode: user data is stored beside this EXE." -Encoding UTF8

$zipPath = Join-Path $artifactsDir "FloatingWordBall-Portable-$Version.zip"
if (Test-Path -LiteralPath $zipPath) { Remove-Item -LiteralPath $zipPath -Force }
Compress-Archive -LiteralPath $portableApp -DestinationPath $zipPath -CompressionLevel Optimal

$setupPath = Join-Path $artifactsDir "FloatingWordBall-Setup-$Version.exe"
if (-not $SkipInstaller) {
    $installerRoot = Join-Path $stageDir "installer"
    New-Item -ItemType Directory -Path $installerRoot -Force | Out-Null
    Copy-Item -LiteralPath $appDir -Destination $installerRoot -Recurse -Force
    $installerApp = Join-Path $installerRoot "FloatingWordBall"

    if (-not $NsisExe) {
        $bundled = Join-Path $PSScriptRoot "tools\nsis\makensis.exe"
        if (Test-Path -LiteralPath $bundled) {
            $NsisExe = $bundled
        }
        else {
            $found = Get-Command makensis -ErrorAction SilentlyContinue
            if ($found) { $NsisExe = $found.Source }
        }
    }
    if (-not $NsisExe) {
        Write-Host "NSIS not found; downloading NSIS 3.13 into packaging\tools\nsis"
        $toolsDir = Join-Path $PSScriptRoot "tools\nsis"
        New-Item -ItemType Directory -Path $toolsDir -Force | Out-Null
        $nsisZip = Join-Path $toolsDir "nsis-3.13.zip"
        Invoke-WebRequest -Uri "https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.13/nsis-3.13.zip" `
            -OutFile $nsisZip -UseBasicParsing
        Expand-Archive -LiteralPath $nsisZip -DestinationPath $toolsDir -Force
        $candidate = Get-ChildItem -LiteralPath $toolsDir -Recurse -Filter makensis.exe |
            Select-Object -First 1
        if (-not $candidate) { throw "NSIS download did not contain makensis.exe" }
        $NsisExe = $candidate.FullName
    }

    # NSIS cannot create its output when the compiler or source tree lives under
    # a non-ASCII path (the Chinese user profile here triggers
    # "Internal compiler error #12345: error creating mmap"). Stage everything
    # under an ASCII-only work directory and copy the finished Setup back.
    $nsisRoot = Split-Path -Parent $NsisExe
    if ((Split-Path -Leaf $nsisRoot) -eq "Bin") { $nsisRoot = Split-Path -Parent $nsisRoot }
    $asciiBase = if ($env:PUBLIC -and $env:PUBLIC -notmatch "[^\x00-\x7F]") { $env:PUBLIC } else { $env:SystemDrive + "\" }
    $nsisWork = Join-Path $asciiBase ("FloatingWordBall-build-" + [guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory -Path $nsisWork -Force | Out-Null
    try {
        $workNsisRoot = Join-Path $nsisWork "nsis"
        Copy-Item -LiteralPath $nsisRoot -Destination $workNsisRoot -Recurse -Force
        $workApp = Join-Path $nsisWork "app"
        Copy-Item -LiteralPath $installerApp -Destination $workApp -Recurse -Force
        $workIcon = Join-Path $nsisWork "floatingwordball.ico"
        Copy-Item -LiteralPath (Join-Path $root "floatingwordball.ico") -Destination $workIcon -Force
        $workScript = Join-Path $nsisWork "installer.nsi"
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot "installer.nsi") -Destination $workScript -Force
        $workSetup = Join-Path $nsisWork "FloatingWordBall-Setup.exe"
        $workNsis = Join-Path $workNsisRoot "makensis.exe"
        Push-Location $nsisWork
        try {
            & $workNsis /V2 "/DAPP_VERSION=$Version" "/DSOURCE_DIR=$workApp" `
                "/DOUTPUT_FILE=$workSetup" "/DICON_FILE=$workIcon" $workScript
        }
        finally { Pop-Location }
        if ($LASTEXITCODE -ne 0) { throw "NSIS failed (exit $LASTEXITCODE)" }
        if (-not (Test-Path -LiteralPath $workSetup)) { throw "NSIS did not produce a Setup EXE" }
        Copy-Item -LiteralPath $workSetup -Destination $setupPath -Force
    }
    finally {
        Remove-Item -LiteralPath $nsisWork -Recurse -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "[5/5] Verifying sizes and writing SHA256SUMS.txt"
$deliverables = @($zipPath)
if (-not $SkipInstaller) { $deliverables += $setupPath }
$lines = foreach ($file in $deliverables) {
    if (-not (Test-Path -LiteralPath $file)) { throw "Missing deliverable: $file" }
    $item = Get-Item -LiteralPath $file
    if ($item.Length -le 0) { throw "Empty deliverable: $file" }
    $hash = (Get-FileHash -LiteralPath $file -Algorithm SHA256).Hash.ToLowerInvariant()
    "{0}  {1}" -f $hash, $item.Name
}
$sumPath = Join-Path $artifactsDir "SHA256SUMS.txt"
Set-Content -LiteralPath $sumPath -Value $lines -Encoding ASCII

$lines | ForEach-Object { Write-Host $_ }
Write-Host "Artifacts: $artifactsDir"
