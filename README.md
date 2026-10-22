# xr-mirror

Windows PC와 Android 기반 XR 헤드셋(Meta Quest, Pico 등)을 USB-C to C 케이블로 연결해
헤드셋 화면을 PC 창에 실시간으로 미러링하는 Python 도구입니다. ADB 위에서 동작하며
무선 캐스팅 없이 유선으로 저지연 화면을 얻는 것이 목적입니다.

## 동작 원리

1. adb로 헤드셋을 인식합니다 (USB 디버깅 허용 필요).
2. 영상 소스는 두 방식 중 하나를 씁니다.
   - **scrcpy 서버 (기본, 권장)**: Apache-2.0 scrcpy의 `scrcpy-server` 파일을 기기에 올려
     `app_process`로 실행하고, `adb forward`로 연결한 소켓에서 H.264 스트림을 받습니다.
     지연 30~60ms 수준, 60fps, 비트레이트와 해상도를 서버에서 조절합니다.
   - **screenrecord**: `adb exec-out screenrecord --output-format=h264 -` 파이프를 읽습니다.
     서버 파일이 필요 없지만 Android 제한으로 3분마다 자동 재시작하며 지연이 조금 더 큽니다.
3. PyAV(FFmpeg)로 H.264를 디코딩해 PySide6 창에 그립니다. 디코딩은 컨테이너 없이
   패킷 파서를 직접 돌리므로 버퍼링 지연이 없습니다.
4. XR 화면은 좌우 눈이 나란히 붙은 넓은 프레임이므로 "왼쪽 눈"처럼 한쪽만 잘라 볼 수 있습니다.

## 설치

아무것도 설치되지 않은 PC에서도 한 줄로 끝납니다. Python이 없으면 Python 3.12까지 자동 설치합니다.

**Windows** (PowerShell 창을 열고 붙여넣기):

```powershell
irm https://raw.githubusercontent.com/jhon-rjf/xr-mirror/main/install.ps1 | iex
```

**macOS / Linux / Windows Git Bash**:

```bash
curl -fsSL https://raw.githubusercontent.com/jhon-rjf/xr-mirror/main/install.sh | bash
```

스크립트가 하는 일:

1. 저장소를 `~/xr-mirror`에 내려받습니다 (git이 없으면 zip으로).
2. Python 3.10 이상을 찾고, 없으면 설치합니다 (Windows: winget 또는 python.org 설치기 무인 설치, macOS: Homebrew 또는 python.org pkg, Linux: apt/dnf/pacman).
3. 가상환경을 만들고 av, numpy, PySide6를 설치합니다.
4. platform-tools(adb)를 내려받습니다. scrcpy-server v5.0.1은 저장소 `vendor/`에 동봉되어 있어 다운로드가 없습니다
   (Google 서버가 막힌 망에서는 이 저장소의 GitHub Release 예비 경로로 adb를 받습니다).
5. 프로그램을 실행합니다. 이후에는 `run.bat`(Windows) 또는 `./install.sh`로 바로 실행합니다.

저장소를 이미 받아 둔 경우에는 폴더 안의 `install.bat`을 더블클릭하거나 `./install.sh`를 실행해도 같습니다.
설치 위치를 바꾸려면 `XR_MIRROR_DIR` 환경변수를 지정합니다.

**헤드셋 쪽 준비**

- 개발자 모드 활성화 (Quest: Meta Horizon 앱 > 기기 > 헤드셋 설정 > 개발자 모드).
- USB-C 케이블로 PC와 연결. 헤드셋을 쓰고 "USB 디버깅을 허용하시겠습니까?"에서 허용
  ("이 컴퓨터에서 항상 허용" 체크 권장).
- Windows가 기기를 인식하지 못하면 OEM USB 드라이버(Quest: Meta Quest ADB Drivers)를 설치.
- 데이터 전송이 되는 케이블과 PC 포트인지 확인 (충전 전용 케이블, 전원 전용 USB-C 포트 주의).

이미 adb가 PATH에 있거나 `%LOCALAPPDATA%\Android\Sdk\platform-tools`에 있으면 그것을 씁니다.
`XR_MIRROR_ADB` 환경변수로 adb 경로를 강제할 수도 있습니다.

## 사용

```bat
run.bat
```

창 상단에서 기기, 방식, 최대 크기, 비트레이트, 크롭을 고르고 **연결**을 누릅니다.

| 단축키 | 동작 |
| --- | --- |
| Ctrl+S | 스크린샷 PNG 저장 (`내 비디오\xr-mirror\`) |
| Ctrl+R | 녹화 시작/중지 (mp4, 실제 기기 스트림만) |
| F11 / Esc | 전체화면 켜기 / 끄기 |
| Ctrl+Q | 종료 |

명령줄 옵션 예:

```bat
run.bat --crop left --max-size 1600 --bitrate 12M --fullscreen
run.bat --backend screenrecord
run.bat --backend demo
run.bat -s 1WMHH8XXXXXXXX --crop 1600:900:2017:510
```

- `--crop`: `full` | `left` | `right` | `W:H:X:Y`(기기 전체 해상도 기준, 스트림 축소 시 자동 환산) | 프리셋 이름
- `--max-size`: 긴 변 최대 픽셀(0=원본). 기본 1920
- `--bitrate`: 기본 8M. 화질이 깨지면 12M~20M
- `--keyframe-interval`: scrcpy I-프레임 간격(초). 녹화 시작 지연과 끊김 복구 속도에 영향. 기본 5
- `--display-id`: 기기에 디스플레이가 여럿일 때 선택

## 폴더 구성

```
xr_mirror/
  adb.py        adb 탐색, 기기 목록, forward/push 래퍼
  sources.py    ScrcpySource / ScreenrecordSource / DemoSource (스레드)
  decoder.py    PyAV CodecContext.parse 기반 저지연 H.264 디코더
  recorder.py   패킷 그대로 .h264 저장 후 mp4 재포장 (SPS/PPS 캐시, 키프레임부터 기록)
  presets.py    XR 크롭 프리셋 (양안/왼눈/오른눈/Quest 2 보정/사용자 지정)
  ui.py         PySide6 창
  __main__.py   CLI
setup_env.py    platform-tools, scrcpy-server 다운로드
install.ps1     Windows 올인원 설치기 (Python 설치 포함), install.bat은 더블클릭 래퍼
install.sh      macOS/Linux/Git Bash 올인원 설치기
run.bat         Windows 일상 실행기 (venv, 의존성, 다운로드 확인 후 실행)
```

## 문제 해결

- **`install.ps1`을 더블클릭하면 메모장만 열림**: 정상입니다. Windows는 .ps1을 더블클릭으로 실행하지 않습니다.
  `install.bat`(처음) 또는 `run.bat`(이후)을 더블클릭하세요.
- **설치 중 `[xr-mirror] ERROR`가 뜸**: 바로 위에 Python이 찍은 원인(예외 종류, URL, 원인 추정, 수동 설치 방법)이 있습니다.
  전체 로그는 같은 폴더의 `install.log`에 남습니다. 로그를 전달하려면 PowerShell에서
  `Get-Content $HOME\xr-mirror\install.log -Tail 40 | Set-Clipboard` 후 붙여넣기 하면 됩니다.
- **adb/scrcpy-server setup failed**: 대부분 네트워크(방화벽, 프록시, DNS)입니다. 출력의 "수동 설치" 안내대로
  브라우저로 zip을 받아 `platform-tools\adb.exe` 위치에 풀어 두면 다음 실행부터 다운로드를 건너뜁니다.
- **콘솔 한글이 깨짐**: 설치기가 콘솔을 UTF-8(chcp 65001)로 바꾸므로 최신 버전에서는 나지 않습니다.
  `install.ps1`과 `.bat`은 인코딩 문제를 피하려고 영문만 쓰고, 한글 메시지는 Python 프로그램이 출력합니다.
- **`python.exe`를 찾을 수 없다고 나옴**: Python이 없어 가상환경이 안 만들어진 상태입니다. `install.bat`을 실행하면
  Python 3.12부터 자동으로 설치합니다. Microsoft Store의 python 자리표시자는 무시합니다.
- **실행 중 멈추거나 창이 사라짐**: `logs\xr-mirror.log`(일반 로그)와 `logs\crash.log`(네이티브 크래시 스택)를 보세요.
  run.bat은 비정상 종료 시 콘솔을 닫지 않고 종료 코드와 로그 위치를 보여 줍니다. 두 파일의 끝부분을 보내 주시면 됩니다.
- **기기 목록에 `[unauthorized]`**: 헤드셋 안의 USB 디버깅 허용 창을 수락하세요.
- **`no permissions` / 목록에 없음**: 드라이버 문제이거나 케이블이 데이터 전송을 지원하지 않습니다.
  장치 관리자에서 "Android Composite ADB Interface"가 보여야 합니다.
- **scrcpy 서버가 종료됨 / 버전 불일치**: `scrcpy-server-v5.0.1` 파일과 코드의 `SCRCPY_VERSION`이
  정확히 같아야 합니다. `python setup_env.py --force --no-adb`로 다시 받으세요.
- **화면이 양안으로 넓게 보임**: 크롭을 "왼쪽 눈"으로 바꾸세요. Quest 2는 전용 프리셋이 있습니다.
- **다른 미러링 앱과 충돌**: Meta Quest Developer Hub 등 adb를 쓰는 프로그램이 다른 adb 버전을
  띄워 두면 `adb server version doesn't match`가 납니다. 그 프로그램을 끄거나 같은 adb를 쓰세요.
- **끊김/깨짐**: 비트레이트를 낮추거나 최대 크기를 1280으로 줄이세요. USB 2.0 포트도 8Mbps 스트림에는 충분합니다.

## 개발 메모

- scrcpy 서버 프로토콜(v5.0.1): `send_device_meta=false send_frame_meta=false send_stream_meta=false
  send_dummy_byte=true`로 띄우면 accept 직후 0x00 한 바이트 뒤에 순수 Annex-B H.264만 옵니다.
  `adb forward`는 기기 쪽 리스너가 없어도 TCP 연결을 받아준 뒤 닫기 때문에 더미 바이트 수신으로
  진짜 연결을 판별하고, 못 받으면 재시도합니다.
- 기기 없이 검증하려면 `--backend demo`를 쓰거나, 테스트용 가짜 adb 스텁(`adb devices/forward/shell
  app_process/exec-out screenrecord`를 흉내)으로 두 백엔드를 모두 돌릴 수 있습니다.
- 서버 버전을 올릴 때는 `xr_mirror/__init__.py`의 `SCRCPY_VERSION`과 `SCRCPY_SERVER_SHA256`,
  `run.bat`의 파일명을 함께 바꿉니다.
