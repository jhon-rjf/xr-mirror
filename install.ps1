# xr-mirror all-in-one installer + launcher (Windows PowerShell 5.1 / 7)
#
#   On a PC with nothing installed (paste into a PowerShell window):
#     irm https://raw.githubusercontent.com/jhon-rjf/xr-mirror/main/install.ps1 | iex
#   Inside the repository folder:
#     double-click install.bat  (or: powershell -ExecutionPolicy Bypass -File install.ps1)
#
# Steps: 1) fetch repo  2) find Python 3.10+, install it if missing (winget or python.org installer)
#        3) venv + dependencies  4) platform-tools (adb) + scrcpy-server  5) run
# Everything is also written to install.log in the working folder.
#
# This file is ASCII only on purpose: Windows PowerShell 5.1 reads BOM-less files in the
# system code page (CP949 on Korean Windows), which garbles non-ASCII text and can swallow
# quotes. Korean messages come from the Python program instead.

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# Console and child processes (python, pip, adb) all use UTF-8
try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch {}
try { [Console]::InputEncoding  = [Text.Encoding]::UTF8 } catch {}
$OutputEncoding = [Text.Encoding]::UTF8
$env:PYTHONUTF8 = "1"
$env:PYTHONIOENCODING = "utf-8"
cmd /c "chcp 65001 >nul 2>&1"

$Repo = "jhon-rjf/xr-mirror"
$PyVer = "3.12.10"
$InstallDir = if ($env:XR_MIRROR_DIR) { $env:XR_MIRROR_DIR } else { Join-Path $env:USERPROFILE "xr-mirror" }

function Log($m) { Write-Host "[xr-mirror] $m" -ForegroundColor Cyan }
function Die($m) {
    Write-Host ""
    Write-Host "[xr-mirror] ERROR: $m" -ForegroundColor Red
    if ($Error.Count -gt 0) { Write-Host "  last error: $($Error[0])" -ForegroundColor DarkGray }
    Write-Host "  log file: $(Join-Path (Get-Location) 'install.log')" -ForegroundColor DarkGray
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "Press Enter to close" | Out-Null
    exit 1
}

# ---------------------------------------------------------------- 1) repository
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "xr_mirror"))) {
    Set-Location $PSScriptRoot
} elseif (Test-Path (Join-Path $InstallDir "xr_mirror")) {
    Set-Location $InstallDir
} else {
    Log "Downloading repository -> $InstallDir"
    try {
        $tmp = Join-Path $env:TEMP ("xr-mirror-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $tmp -ErrorAction Stop | Out-Null
        $zip = Join-Path $tmp "src.zip"
        Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing -ErrorAction Stop
        Expand-Archive $zip -DestinationPath $tmp -Force -ErrorAction Stop
        New-Item -ItemType Directory -Path $InstallDir -Force -ErrorAction Stop | Out-Null
        Copy-Item (Join-Path $tmp "xr-mirror-main\*") $InstallDir -Recurse -Force -ErrorAction Stop
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    } catch { Die "Repository download failed: $_" }
    Set-Location $InstallDir
}
try { Start-Transcript -Path (Join-Path (Get-Location) "install.log") -Append | Out-Null } catch {}
Log "Working folder: $(Get-Location)"
Log "PowerShell $($PSVersionTable.PSVersion), Windows $([Environment]::OSVersion.Version)"

# ---------------------------------------------------------------- 2) Python
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User") + ";" + $env:Path
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $local) {
        Get-ChildItem $local -Directory | ForEach-Object {
            $env:Path = "$($_.FullName);$($_.FullName)\Scripts;$env:Path"
        }
    }
}

# Returns the executable path if it is a real Python 3.10+.
# The Microsoft Store placeholder python.exe (WindowsApps) only opens the Store, so skip it.
function Test-PythonExe([string]$exe, [string[]]$extra) {
    try {
        $cmd = Get-Command $exe -ErrorAction SilentlyContinue
        if (-not $cmd) { return $null }
        if ($cmd.Source -like "*\WindowsApps\*") { return $null }
        $code = "import sys; sys.exit(1) if sys.version_info < (3, 10) else print(sys.executable)"
        $out = (& $exe @extra -c $code 2>&1) | Out-String
        if ($LASTEXITCODE -ne 0) { return $null }
        $p = $out.Trim()
        if ($p -and (Test-Path $p) -and ($p -notlike "*\WindowsApps\*")) { return $p }
    } catch {}
    return $null
}

function Find-Python {
    foreach ($v in "3.12", "3.13", "3.11", "3.10", "3") {
        $p = Test-PythonExe "py" @("-$v"); if ($p) { return $p }
    }
    foreach ($n in "python3.12", "python3", "python") {
        $p = Test-PythonExe $n @(); if ($p) { return $p }
    }
    # Not on PATH but present in the default install location
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $local) {
        foreach ($d in (Get-ChildItem $local -Directory | Sort-Object Name -Descending)) {
            $exe = Join-Path $d.FullName "python.exe"
            if (Test-Path $exe) { $p = Test-PythonExe $exe @(); if ($p) { return $p } }
        }
    }
    return $null
}

function Install-Python {
    Log "Python 3.10+ not found. Installing Python $PyVer."
    $done = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Log "Trying winget"
        try {
            & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
            $done = ($LASTEXITCODE -eq 0)
        } catch { $done = $false }
        if (-not $done) { Log "winget failed or unavailable, falling back to the python.org installer" }
    }
    if (-not $done) {
        $exe = Join-Path $env:TEMP "python-$PyVer-amd64.exe"
        Log "Downloading the python.org installer (about 25 MB)"
        try {
            Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-amd64.exe" -OutFile $exe -UseBasicParsing -ErrorAction Stop
        } catch { Die "Python installer download failed: $_" }
        Log "Installing silently (1-2 minutes, no window will appear)"
        $p = Start-Process $exe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0" -Wait -PassThru
        if ($p.ExitCode -ne 0) { Die "Python install failed (exit code $($p.ExitCode))" }
    }
    Refresh-Path
}

$Py = Find-Python
if (-not $Py) { Install-Python; $Py = Find-Python }
if (-not $Py) { Die "Python still not found after install. Open a new PowerShell window and run again." }
$pyVersion = ((& $Py --version 2>&1) | Out-String).Trim()
Log "Python: $pyVersion ($Py)"

# ---------------------------------------------------------------- 3) venv
$VPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    Log "Creating virtual environment"
    & $Py -m venv .venv 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VPy)) { Die "venv creation failed (Python: $Py)" }
}
$marker = Join-Path (Get-Location) ".venv\.deps-ok"
if (-not (Test-Path $marker) -or (Get-Item requirements.txt).LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
    Log "Installing dependencies (av, numpy, PySide6-Essentials). This can take a few minutes."
    & $VPy -m pip install --upgrade pip 2>&1 | Out-Host
    & $VPy -m pip install --retries 10 --timeout 60 -r requirements.txt 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Die "pip install failed. See the log above." }
    "ok" | Set-Content $marker
}

# ---------------------------------------------------------------- 4) adb, server
# downloads only what is missing; the vendored scrcpy-server in vendor/ needs no download
& $VPy setup_env.py 2>&1 | Out-Host
if ($LASTEXITCODE -ne 0) { Die "adb/scrcpy-server setup failed. See the log above." }

# ---------------------------------------------------------------- 5) run
Log "Ready. Starting the program. (Next time: double-click run.bat)"
try { Stop-Transcript | Out-Null } catch {}
& $VPy -m xr_mirror @args
$rc = $LASTEXITCODE
if ($rc -ne 0) {
    Write-Host ""
    Write-Host "[xr-mirror] exited with code $rc" -ForegroundColor Red
    Write-Host "  logs: $(Join-Path (Get-Location) 'logs\xr-mirror.log') and logs\crash.log" -ForegroundColor DarkGray
    Read-Host "Press Enter to close" | Out-Null
}
exit $rc
