param(
    [string]$OutputDir = "dist/windows-check",
    [string]$TempRoot,
    [switch]$PackageOnly
)

$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $PSScriptRoot
if ($TempRoot) { $TempRoot = $ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($TempRoot) }
$envNames = @("TEMP", "TMP", "TMPDIR", "PYTHONUTF8", "SCILIB_PATH")
$originalEnv = @{}
foreach ($name in $envNames) { $originalEnv[$name] = [Environment]::GetEnvironmentVariable($name) }

function Invoke-Checked {
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Program exited with code $LASTEXITCODE" }
}

function New-BuildScratch {
    $candidates = if ($TempRoot) {
        @($TempRoot)
    } else {
        @($env:RUNNER_TEMP, (Join-Path ([Environment]::GetFolderPath("LocalApplicationData")) "PaperLibraryBuild"),
          [IO.Path]::GetTempPath())
    }
    $repoPath = [IO.Path]::GetFullPath($repo).TrimEnd('\')
    $failures = @()
    foreach ($base in $candidates) {
        if (-not $base) { continue }
        $path = [IO.Path]::GetFullPath((Join-Path $base ("pl-check-" + [Guid]::NewGuid().ToString("N"))))
        if ($path.StartsWith("$repoPath\", [StringComparison]::OrdinalIgnoreCase)) {
            $failures += "$base is inside the repository"
            continue
        }
        try {
            $null = [IO.Directory]::CreateDirectory($path)
            $file = Join-Path $path "write-probe"
            [IO.File]::WriteAllText($file, "ok")
            [IO.File]::Delete($file)
            return $path
        } catch {
            $failures += "$base ($($_.Exception.Message))"
            try { [IO.Directory]::Delete($path) } catch {}
        }
    }
    throw ("Cannot create a writable build temp directory outside the repository. " +
           "Pass -TempRoot with a writable external folder. Tried: " + ($failures -join ", "))
}

$scratch = $null
Push-Location $repo
try {
    $scratch = New-BuildScratch
    foreach ($name in @("TEMP", "TMP", "TMPDIR")) { [Environment]::SetEnvironmentVariable($name, $scratch) }
    $env:PYTHONUTF8 = "1"
    Write-Output "Build temp: $scratch"
    $assertTemp = @'
import pathlib, sys, tempfile
root, expected = (pathlib.Path(value).resolve() for value in sys.argv[1:])
actual = pathlib.Path(tempfile.gettempdir()).resolve()
if actual != expected or actual.is_relative_to(root):
    raise SystemExit(f'Expected a writable temporary folder outside {root}, got {actual}. Set -TempRoot.')
'@
    Invoke-Checked "python" @("-B", "-c", $assertTemp, $repo, $scratch)
    $nativeProbe = @'
$ErrorActionPreference = 'Stop'
$temp = [IO.Path]::GetTempPath().TrimEnd([IO.Path]::DirectorySeparatorChar)
if ($temp -ne $env:TMP) { throw 'Windows PowerShell did not use the build temp. Set -TempRoot.' }
$file = Join-Path $temp 'native-write-probe'
[IO.File]::WriteAllText($file, 'ok')
[IO.File]::Delete($file)
'@
    Invoke-Checked "powershell.exe" @("-NoProfile", "-Command", $nativeProbe)

    if ($PackageOnly) {
        $buildRuntime = (New-Item -ItemType Directory -Path (Join-Path $scratch "runtime")).FullName
        $existingPortable = Join-Path $repo "runtime/portable"
        if (Test-Path -LiteralPath $existingPortable -PathType Container) {
            Copy-Item -LiteralPath $existingPortable -Destination $buildRuntime -Recurse
        }
    } else {
        $testSource = (New-Item -ItemType Directory -Path (Join-Path $scratch "source")).FullName
        $testFiles = @("paperlib", "public", "shared", "test", "tools", "launcher", "vendor",
                       "requirements.txt", "version.json", "使用说明.txt", "README.md")
        Copy-Item -LiteralPath $testFiles -Destination $testSource -Recurse
        $buildRuntime = Join-Path $testSource "runtime"
    }
    Invoke-Checked "python" @("tools/make_portable.py", "--runtime-dir", $buildRuntime)
    $stagedPortable = Join-Path $buildRuntime "portable"

    $output = (New-Item -ItemType Directory -Force -Path $OutputDir).FullName
    $run = (New-Item -ItemType Directory -Path (Join-Path $output ([guid]::NewGuid().ToString("N")))).FullName
    if (-not $PackageOnly) {
        if (-not $env:SCILIB_PATH) {
            $reference = Join-Path (Split-Path -Parent $repo) "Sci-lib"
            if (Test-Path -LiteralPath $reference -PathType Container) { $env:SCILIB_PATH = $reference }
        }
        Push-Location $testSource
        try {
            $testPython = Join-Path $stagedPortable "python.exe"
            Invoke-Checked $testPython @("-X", "utf8", "-B", "-c", $assertTemp, $testSource, $scratch)
            Invoke-Checked $testPython @("-X", "utf8", "-B", "-m", "unittest", "discover", "-s", "test")
            $nodeTests = @(Get-ChildItem -Path "test/js" -Filter "*.test.mjs" | ForEach-Object FullName)
            Invoke-Checked "node" (@("--test") + $nodeTests)
            Invoke-Checked $testPython @("-X", "utf8", "-B", "tools/contract_check.py",
                                         "--json", (Join-Path $run "contracts.json"))
        }
        finally { Pop-Location }
    }

    $packageRoot = Join-Path $scratch "bundle"
    Invoke-Checked "python" @("-X", "utf8", "-B", "tools/release.py", "--out", $packageRoot,
                              "--portable-dir", $stagedPortable)
    $package = Get-ChildItem -LiteralPath $packageRoot -Directory -Filter "PaperLibrary-*" | Select-Object -First 1
    if (-not $package) { throw "Release build produced no package" }
    $zip = Join-Path $scratch "PaperLibrary-smoke.zip"
    Invoke-Checked "python" @("-X", "utf8", "-B", "tools/package_zip.py", "--src", $package.FullName, "--out", $zip)

    $extracted = Join-Path $scratch "extracted"
    Invoke-Checked "python" @("-B", "-m", "zipfile", "-e", $zip, $extracted)
    $packagePython = Join-Path $extracted "runtime/portable/python.exe"
    $packageContracts = Join-Path $extracted "tools/contract_check.py"
    Invoke-Checked $packagePython @("-X", "utf8", "-B", "-c", $assertTemp, $extracted, $scratch)
    Invoke-Checked $packagePython @("-X", "utf8", "-B", $packageContracts, "--json", (Join-Path $run "package-contracts.json"))
    Copy-Item -LiteralPath $zip -Destination $run
    Write-Output "Verification artifacts: $run"
}
finally {
    foreach ($name in $envNames) { [Environment]::SetEnvironmentVariable($name, $originalEnv[$name]) }
    Pop-Location
    if ($scratch -and (Test-Path -LiteralPath $scratch)) {
        $item = Get-Item -LiteralPath $scratch -Force
        if ($item.FullName -ceq $scratch -and $item.Name -match '^pl-check-[0-9a-f]{32}$' -and
            -not ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            try { Remove-Item -LiteralPath $item.FullName -Recurse -Force -ErrorAction Stop }
            catch { Write-Warning "Could not remove build temp: $scratch ($($_.Exception.Message))" }
        }
    }
}
