# xr-mirror 올인원 설치 + 실행 (Windows PowerShell 5.1 / 7)
#
#   아무것도 없는 PC에서 (PowerShell 창에 붙여넣기):
#     irm https://raw.githubusercontent.com/jhon-rjf/xr-mirror/main/install.ps1 | iex
#   저장소 폴더 안에서:
#     install.bat 더블클릭  (또는 powershell -ExecutionPolicy Bypass -File install.ps1)
#
# 하는 일: 1) 저장소 확보  2) Python 3.10+ 확인, 없으면 설치(winget 또는 python.org 설치기)
#         3) venv + 의존성  4) platform-tools(adb) + scrcpy-server  5) 실행
# 모든 출력은 작업 폴더의 install.log에도 기록된다.
#
# 이 파일은 반드시 UTF-8 BOM으로 저장할 것. BOM이 없으면 Windows PowerShell 5.1이
# 한글을 CP949로 읽어 깨지고, 따옴표가 삼켜져 파싱 오류가 난다.

$ErrorActionPreference = "Continue"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# 콘솔과 자식 프로세스(python, pip, adb)를 전부 UTF-8로 통일
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
    Write-Host "[xr-mirror] 오류: $m" -ForegroundColor Red
    if ($Error.Count -gt 0) { Write-Host "  마지막 오류: $($Error[0])" -ForegroundColor DarkGray }
    Write-Host "  로그 파일: $(Join-Path (Get-Location) 'install.log')" -ForegroundColor DarkGray
    try { Stop-Transcript | Out-Null } catch {}
    Read-Host "Enter를 누르면 닫힙니다" | Out-Null
    exit 1
}

# ---------------------------------------------------------------- 1) 저장소
if ($PSScriptRoot -and (Test-Path (Join-Path $PSScriptRoot "xr_mirror"))) {
    Set-Location $PSScriptRoot
} elseif (Test-Path (Join-Path $InstallDir "xr_mirror")) {
    Set-Location $InstallDir
} else {
    Log "저장소 내려받기 -> $InstallDir"
    try {
        $tmp = Join-Path $env:TEMP ("xr-mirror-" + [guid]::NewGuid().ToString("N"))
        New-Item -ItemType Directory -Path $tmp -ErrorAction Stop | Out-Null
        $zip = Join-Path $tmp "src.zip"
        Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing -ErrorAction Stop
        Expand-Archive $zip -DestinationPath $tmp -Force -ErrorAction Stop
        New-Item -ItemType Directory -Path $InstallDir -Force -ErrorAction Stop | Out-Null
        Copy-Item (Join-Path $tmp "xr-mirror-main\*") $InstallDir -Recurse -Force -ErrorAction Stop
        Remove-Item $tmp -Recurse -Force -ErrorAction SilentlyContinue
    } catch { Die "저장소 다운로드 실패: $_" }
    Set-Location $InstallDir
}
try { Start-Transcript -Path (Join-Path (Get-Location) "install.log") -Append | Out-Null } catch {}
Log "작업 폴더: $(Get-Location)"
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

# 실행 파일이 진짜 Python 3.10+인지 확인하고 실행 파일 경로를 돌려준다.
# Microsoft Store의 python.exe 자리표시자(WindowsApps)는 Store 창만 띄우므로 제외한다.
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
    # PATH에 없어도 기본 설치 위치에 있으면 사용
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
    Log "Python 3.10 이상이 없습니다. Python $PyVer 설치를 시작합니다."
    $done = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Log "winget으로 설치 시도"
        try {
            & winget install -e --id Python.Python.3.12 --silent --accept-package-agreements --accept-source-agreements 2>&1 | Out-Host
            $done = ($LASTEXITCODE -eq 0)
        } catch { $done = $false }
        if (-not $done) { Log "winget 설치 실패 또는 미지원. python.org 설치기로 넘어갑니다." }
    }
    if (-not $done) {
        $exe = Join-Path $env:TEMP "python-$PyVer-amd64.exe"
        Log "python.org에서 설치 프로그램 다운로드 (약 25MB)"
        try {
            Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-amd64.exe" -OutFile $exe -UseBasicParsing -ErrorAction Stop
        } catch { Die "Python 설치기 다운로드 실패: $_" }
        Log "조용히 설치 중 (1~2분, 창이 뜨지 않습니다)"
        $p = Start-Process $exe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0" -Wait -PassThru
        if ($p.ExitCode -ne 0) { Die "Python 설치 실패 (코드 $($p.ExitCode))" }
    }
    Refresh-Path
}

$Py = Find-Python
if (-not $Py) { Install-Python; $Py = Find-Python }
if (-not $Py) { Die "Python 설치 후에도 찾지 못했습니다. PowerShell 창을 새로 열고 다시 실행하세요." }
$pyVersion = ((& $Py --version 2>&1) | Out-String).Trim()
Log "Python: $pyVersion ($Py)"

# ---------------------------------------------------------------- 3) venv
$VPy = Join-Path (Get-Location) ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    Log "가상환경 생성"
    & $Py -m venv .venv 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path $VPy)) { Die "venv 생성 실패 (Python: $Py)" }
}
$marker = Join-Path (Get-Location) ".venv\.deps-ok"
if (-not (Test-Path $marker) -or (Get-Item requirements.txt).LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
    Log "의존성 설치 (av, numpy, PySide6-Essentials). 수 분 걸릴 수 있습니다."
    & $VPy -m pip install --upgrade pip 2>&1 | Out-Host
    & $VPy -m pip install --retries 10 --timeout 60 -r requirements.txt 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) { Die "pip 설치 실패. 위 로그를 확인하세요." }
    "ok" | Set-Content $marker
}

# ---------------------------------------------------------------- 4) adb, 서버
$haveAdb = (Test-Path "platform-tools\adb.exe") -or [bool](Get-Command adb -ErrorAction SilentlyContinue)
if ($haveAdb) { & $VPy setup_env.py --no-adb 2>&1 | Out-Host } else { & $VPy setup_env.py 2>&1 | Out-Host }
if ($LASTEXITCODE -ne 0) { Die "adb/scrcpy-server 준비 실패. 위 로그를 확인하세요." }

# ---------------------------------------------------------------- 5) 실행
Log "준비 완료. 프로그램을 실행합니다. (다음부터는 run.bat 더블클릭)"
try { Stop-Transcript | Out-Null } catch {}
& $VPy -m xr_mirror @args
