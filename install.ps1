# xr-mirror 올인원 설치 + 실행 (Windows PowerShell)
#
#   아무것도 없는 PC에서 (PowerShell 창에 붙여넣기):
#     irm https://raw.githubusercontent.com/jhon-rjf/xr-mirror/main/install.ps1 | iex
#   저장소 폴더 안에서:
#     install.bat  (또는 powershell -ExecutionPolicy Bypass -File install.ps1)
#
# 하는 일: 1) 저장소 확보  2) Python 3.10+ 확인, 없으면 설치(winget 또는 python.org 설치기)
#         3) venv + 의존성  4) platform-tools(adb) + scrcpy-server  5) 실행
$ErrorActionPreference = "Stop"
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$Repo = "jhon-rjf/xr-mirror"
$PyVer = "3.12.10"
$InstallDir = if ($env:XR_MIRROR_DIR) { $env:XR_MIRROR_DIR } else { Join-Path $env:USERPROFILE "xr-mirror" }

function Log($m) { Write-Host "[xr-mirror] $m" -ForegroundColor Cyan }
function Die($m) {
    Write-Host "[xr-mirror] 오류: $m" -ForegroundColor Red
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
    $tmp = Join-Path $env:TEMP ("xr-mirror-" + [guid]::NewGuid().ToString("N"))
    New-Item -ItemType Directory -Path $tmp | Out-Null
    $zip = Join-Path $tmp "src.zip"
    Invoke-WebRequest "https://github.com/$Repo/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
    Expand-Archive $zip -DestinationPath $tmp -Force
    New-Item -ItemType Directory -Path $InstallDir -Force | Out-Null
    Copy-Item (Join-Path $tmp "xr-mirror-main\*") $InstallDir -Recurse -Force
    Remove-Item $tmp -Recurse -Force
    Set-Location $InstallDir
}
Log "작업 폴더: $(Get-Location)"

# ---------------------------------------------------------------- 2) Python
function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                [Environment]::GetEnvironmentVariable("Path", "User")
    $local = Join-Path $env:LOCALAPPDATA "Programs\Python"
    if (Test-Path $local) {
        Get-ChildItem $local -Directory | ForEach-Object {
            $env:Path = "$($_.FullName);$($_.FullName)\Scripts;$env:Path"
        }
    }
}

function Test-PythonExe($exe, $extra) {
    try {
        $out = & $exe @extra -c "import sys; print(sys.executable) if sys.version_info >= (3, 10) else sys.exit(1)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
            $p = "$out".Trim()
            if (Test-Path $p) { return $p }
        }
    } catch {}
    return $null
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.12", "3.13", "3.11", "3.10") {
            $p = Test-PythonExe "py" @("-$v"); if ($p) { return $p }
        }
    }
    foreach ($n in "python3.12", "python3", "python") {
        if (Get-Command $n -ErrorAction SilentlyContinue) {
            $p = Test-PythonExe $n @(); if ($p) { return $p }
        }
    }
    return $null
}

function Install-Python {
    Log "Python 3.10 이상이 없습니다. Python $PyVer 설치를 시작합니다."
    $done = $false
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        try {
            winget install -e --id Python.Python.3.12 --scope user --silent `
                --accept-package-agreements --accept-source-agreements
            $done = ($LASTEXITCODE -eq 0)
        } catch {}
    }
    if (-not $done) {
        $exe = Join-Path $env:TEMP "python-$PyVer-amd64.exe"
        Log "python.org에서 설치 프로그램 다운로드"
        Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-amd64.exe" -OutFile $exe -UseBasicParsing
        Log "조용히 설치 중 (1~2분)"
        $p = Start-Process $exe -ArgumentList "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0" -Wait -PassThru
        if ($p.ExitCode -ne 0) { Die "Python 설치 실패 (코드 $($p.ExitCode))" }
    }
    Refresh-Path
}

$Py = Find-Python
if (-not $Py) { Install-Python; $Py = Find-Python }
if (-not $Py) { Die "Python 설치 후에도 찾지 못했습니다. PowerShell을 새로 열고 다시 실행하세요." }
Log "Python: $(& $Py --version) ($Py)"

# ---------------------------------------------------------------- 3) venv
$VPy = ".venv\Scripts\python.exe"
if (-not (Test-Path $VPy)) {
    Log "가상환경 생성"
    & $Py -m venv .venv
    if ($LASTEXITCODE -ne 0) { Die "venv 생성 실패" }
}
$marker = ".venv\.deps-ok"
if (-not (Test-Path $marker) -or (Get-Item requirements.txt).LastWriteTime -gt (Get-Item $marker).LastWriteTime) {
    Log "의존성 설치 (av, numpy, PySide6)"
    & $VPy -m pip install -q --upgrade pip
    & $VPy -m pip install -q --retries 10 --timeout 60 -r requirements.txt
    if ($LASTEXITCODE -ne 0) { Die "pip 설치 실패" }
    "ok" | Set-Content $marker
}

# ---------------------------------------------------------------- 4) adb, 서버
if (-not (Test-Path "platform-tools\adb.exe") -and -not (Get-Command adb -ErrorAction SilentlyContinue)) {
    & $VPy setup_env.py
} else {
    & $VPy setup_env.py --no-adb
}
if ($LASTEXITCODE -ne 0) { Die "adb/scrcpy-server 준비 실패" }

# ---------------------------------------------------------------- 5) 실행
Log "준비 완료. 프로그램을 실행합니다. (다음부터는 run.bat 더블클릭)"
& $VPy -m xr_mirror @args
