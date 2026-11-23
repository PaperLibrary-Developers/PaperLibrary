param(
  [string]$OutDir = "dist",
  [string]$WebView2Dir = ""
)
$ErrorActionPreference = "Stop"

# IMPORTANT: this file must stay UTF-8 WITH BOM. It is executed by Windows
# PowerShell 5.1 (release.py uses powershell.exe -File); a BOM-less file is read
# as ANSI and the Chinese UI strings compiled into the launcher would be garbled.
#
# The launcher is a WinForms host: it starts the Python service in the background
# and shows the existing web UI (public/) inside a WebView2 window. No external
# browser is opened on the normal path.
#
# csc.exe from .NET Framework 4.x only supports C# 5: no string interpolation,
# no "?.", no "nameof" in the embedded source below.

$root = Split-Path -Parent $PSScriptRoot
$icon = Join-Path $root "public\paperlibrary.ico"
if (-not (Test-Path -LiteralPath $icon)) {
  throw "Missing launcher icon: $icon (the package must include public/paperlibrary.ico)"
}

if (-not $WebView2Dir) { $WebView2Dir = Join-Path $root "tools\webview2" }
$wvCore = Join-Path $WebView2Dir "Microsoft.Web.WebView2.Core.dll"
$wvWinForms = Join-Path $WebView2Dir "Microsoft.Web.WebView2.WinForms.dll"
$wvLoader = Join-Path $WebView2Dir "WebView2Loader.dll"
foreach ($file in @($wvCore, $wvWinForms, $wvLoader)) {
  if (-not (Test-Path -LiteralPath $file)) {
    throw "Missing WebView2 SDK file: $file (see tools/webview2/README.txt)"
  }
}

$manifest = Join-Path $PSScriptRoot "launcher.manifest"
if (-not (Test-Path -LiteralPath $manifest)) {
  throw "Missing launcher manifest: $manifest"
}
$appConfig = Join-Path $PSScriptRoot "launcher.exe.config"

$csc = Join-Path $env:WINDIR "Microsoft.NET\Framework64\v4.0.30319\csc.exe"
if (-not (Test-Path -LiteralPath $csc)) {
  $csc = Join-Path $env:WINDIR "Microsoft.NET\Framework\v4.0.30319\csc.exe"
}
if (-not (Test-Path -LiteralPath $csc)) {
  throw "csc.exe (.NET Framework 4.x) not found; cannot build the launcher"
}

if (-not (Test-Path $OutDir)) { New-Item -ItemType Directory -Path $OutDir | Out-Null }

$launcherSource = Join-Path $root "launcher\PaperLibraryLauncher.cs"
$coreSource = Join-Path $root "launcher\LauncherCore.cs"
foreach ($sourceFile in @($launcherSource, $coreSource)) {
  if (-not (Test-Path -LiteralPath $sourceFile)) { throw "Missing launcher source: $sourceFile" }
}

$startExe = Join-Path $OutDir "PaperLibrary.exe"
$stamp = [guid]::NewGuid().ToString("N")
$csPath = Join-Path ([System.IO.Path]::GetTempPath()) ("paperlibrary-launcher-" + $stamp + ".cs")
$corePath = Join-Path ([System.IO.Path]::GetTempPath()) ("paperlibrary-launcher-core-" + $stamp + ".cs")
try {
  $utf8Bom = New-Object System.Text.UTF8Encoding($true)
  [System.IO.File]::WriteAllText($csPath, [System.IO.File]::ReadAllText($launcherSource), $utf8Bom)
  [System.IO.File]::WriteAllText($corePath, [System.IO.File]::ReadAllText($coreSource), $utf8Bom)
  $cscArgs = @(
    "/nologo", "/target:winexe", "/optimize+",
    "/win32icon:$icon", "/win32manifest:$manifest", "/out:$startExe",
    "/r:System.dll", "/r:System.Core.dll", "/r:System.Drawing.dll", "/r:System.Windows.Forms.dll",
    "/r:System.Web.Extensions.dll", "/r:$wvCore", "/r:$wvWinForms", $corePath, $csPath
  )
  & $csc $cscArgs
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $startExe)) {
    throw "launcher compile failed (csc exit code $LASTEXITCODE)"
  }
} finally {
  Remove-Item $csPath -Force -ErrorAction SilentlyContinue
  Remove-Item $corePath -Force -ErrorAction SilentlyContinue
}

Copy-Item -LiteralPath $wvCore -Destination $OutDir -Force
Copy-Item -LiteralPath $wvWinForms -Destination $OutDir -Force
Copy-Item -LiteralPath $wvLoader -Destination $OutDir -Force

# The .NET CLR only ever reads the application config as "<exe file name>.config"
# (AppDomainSetup.ConfigurationFile = exe path + ".config"), so this file cannot be
# renamed without silently dropping supportedRuntime + the WinForms DPI settings.
# With Explorer's "hide known extensions" on, "PaperLibrary.exe.config" is shown
# as "PaperLibrary.exe" right under the real launcher, and users double-click the
# wrong file. Keep the name, mark the file Hidden: the settings still load and the
# package root shows exactly one visible entry.
$startConfig = Join-Path $OutDir "PaperLibrary.exe.config"
if (-not (Test-Path -LiteralPath $appConfig)) {
  throw "Missing launcher config: $appConfig"
}
Copy-Item -LiteralPath $appConfig -Destination $startConfig -Force
$configItem = Get-Item -LiteralPath $startConfig -Force
$configItem.Attributes = $configItem.Attributes -bor [System.IO.FileAttributes]::Hidden
if (($configItem.Attributes -band [System.IO.FileAttributes]::Hidden) -eq 0) {
  throw "could not hide the launcher config: $startConfig"
}
Write-Output ("launcher built: " + $startExe)
Write-Output ("launcher config hidden: " + $startConfig)
Write-Output ("webview2 files copied: " + $OutDir)
