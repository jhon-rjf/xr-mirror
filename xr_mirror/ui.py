"""PySide6 미러링 창."""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import numpy as np
from PySide6.QtCore import QObject, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QAction, QImage, QKeySequence, QPainter, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QLabel, QLineEdit, QMainWindow,
                               QMessageBox, QPushButton, QSizePolicy, QToolBar, QWidget)

from . import SCRCPY_VERSION, __version__
from .adb import STATE_HINTS, Adb, AdbError, Device, find_adb
from .presets import PRESETS, Crop, parse_rect
from .recorder import Recorder
from .sources import DemoSource, FrameSource, ScreenrecordSource, ScrcpySource

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent
BACKENDS = [("scrcpy", "scrcpy 서버 (저지연, 권장)"),
            ("screenrecord", "screenrecord (서버 파일 불필요)"),
            ("demo", "데모 (기기 없음)")]
MAX_SIZES = [("1920", 1920), ("1600", 1600), ("1280", 1280), ("1024", 1024), ("원본", 0)]
BITRATES = [("4 Mbps", 4_000_000), ("8 Mbps", 8_000_000), ("12 Mbps", 12_000_000),
            ("20 Mbps", 20_000_000)]


@dataclass
class Settings:
    serial: str | None = None
    backend: str = "scrcpy"
    max_size: int = 1920
    bit_rate: int = 8_000_000
    max_fps: int = 60
    crop: str | None = None          # 프리셋 이름 또는 W:H:X:Y
    fullscreen: bool = False
    always_on_top: bool = False
    adb_path: str | None = None
    server_jar: Path = field(default_factory=lambda: ROOT / f"scrcpy-server-v{SCRCPY_VERSION}")
    out_dir: Path = field(default_factory=lambda: Path.home() / "Videos" / "xr-mirror")
    display_id: int = 0
    keyframe_interval: int = 5


class Bridge(QObject):
    """소스 스레드의 콜백을 Qt 메인 스레드 시그널로 옮긴다."""
    frame_ready = Signal()
    status = Signal(str)
    error = Signal(str)


class DeviceScanner(QThread):
    found = Signal(list)
    failed = Signal(str)

    def __init__(self, adb: Adb):
        super().__init__()
        self.adb = adb

    def run(self) -> None:
        try:
            self.found.emit(self.adb.devices())
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))


class VideoWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._img: QImage | None = None
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.setMinimumSize(320, 180)
        self.setStyleSheet("background:#000;")
        self.setAutoFillBackground(True)
        self.message = "연결 대기"

    def set_frame(self, rgb: np.ndarray) -> None:
        h, w = rgb.shape[:2]
        if not rgb.flags["C_CONTIGUOUS"]:
            rgb = np.ascontiguousarray(rgb)
        # QImage는 버퍼를 참조하므로 복사본을 유지해야 한다
        self._buf = rgb
        self._img = QImage(rgb.data, w, h, w * 3, QImage.Format_RGB888)
        self.update()

    def clear(self, message: str = "연결 대기") -> None:
        self._img = None
        self.message = message
        self.update()

    def paintEvent(self, _ev) -> None:  # noqa: N802
        p = QPainter(self)
        p.fillRect(self.rect(), Qt.black)
        if self._img is None:
            p.setPen(Qt.gray)
            p.drawText(self.rect(), Qt.AlignCenter, self.message)
            return
        target = self._img.size().scaled(self.size(), Qt.KeepAspectRatio)
        x = (self.width() - target.width()) // 2
        y = (self.height() - target.height()) // 2
        p.setRenderHint(QPainter.SmoothPixmapTransform, target.width() < self._img.width())
        p.drawImage(x, y, self._img.scaled(target, Qt.IgnoreAspectRatio,
                                           Qt.SmoothTransformation if target.width() < self._img.width()
                                           else Qt.FastTransformation))


class MainWindow(QMainWindow):
    def __init__(self, settings: Settings):
        super().__init__()
        self.s = settings
        self.setWindowTitle(f"XR Mirror {__version__}")
        self.resize(1280, 760)

        self.adb: Adb | None = None
        self.source: FrameSource | None = None
        self.recorder = Recorder()
        self.devices: list[Device] = []
        self.device_size: tuple[int, int] | None = None
        self.current_crop: Crop = PRESETS[0]

        self._latest: np.ndarray | None = None
        self._latest_lock = threading.Lock()
        self._pending = False
        self._fps_count = 0
        self._fps = 0.0
        self._fps_t = time.time()
        self._bytes_t = (time.time(), 0)
        self._bitrate = 0.0

        self.bridge = Bridge()
        self.bridge.frame_ready.connect(self._on_frame_ready)
        self.bridge.status.connect(self._set_status)
        self.bridge.error.connect(self._on_error)

        self.video = VideoWidget()
        self.setCentralWidget(self.video)
        self._build_toolbar()
        self._build_actions()
        self.statusBar().showMessage("준비")
        self.lbl_stats = QLabel("")
        self.statusBar().addPermanentWidget(self.lbl_stats)

        self._stat_timer = QTimer(self)
        self._stat_timer.timeout.connect(self._update_stats)
        self._stat_timer.start(1000)

        self._scan_timer = QTimer(self)
        self._scan_timer.timeout.connect(self.refresh_devices)
        self._scanner: DeviceScanner | None = None

        self._init_adb()
        if settings.fullscreen:
            self.showFullScreen()
        if settings.always_on_top:
            self.act_top.setChecked(True)
        if settings.serial or settings.backend == "demo":
            QTimer.singleShot(300, self.connect_device)

    # ------------------------------------------------------------------ UI
    def _build_toolbar(self) -> None:
        tb = QToolBar("제어")
        tb.setMovable(False)
        self.addToolBar(tb)

        tb.addWidget(QLabel(" 기기 "))
        self.cb_device = QComboBox()
        self.cb_device.setMinimumWidth(260)
        tb.addWidget(self.cb_device)
        self.btn_refresh = QPushButton("새로고침")
        self.btn_refresh.clicked.connect(self.refresh_devices)
        tb.addWidget(self.btn_refresh)

        tb.addSeparator()
        tb.addWidget(QLabel(" 방식 "))
        self.cb_backend = QComboBox()
        for key, label in BACKENDS:
            self.cb_backend.addItem(label, key)
        self.cb_backend.setCurrentIndex([k for k, _ in BACKENDS].index(self.s.backend))
        tb.addWidget(self.cb_backend)

        tb.addWidget(QLabel(" 최대 크기 "))
        self.cb_size = QComboBox()
        for label, v in MAX_SIZES:
            self.cb_size.addItem(label, v)
        idx = next((i for i, (_, v) in enumerate(MAX_SIZES) if v == self.s.max_size), 0)
        self.cb_size.setCurrentIndex(idx)
        tb.addWidget(self.cb_size)

        tb.addWidget(QLabel(" 비트레이트 "))
        self.cb_bitrate = QComboBox()
        for label, v in BITRATES:
            self.cb_bitrate.addItem(label, v)
        idx = next((i for i, (_, v) in enumerate(BITRATES) if v == self.s.bit_rate), 1)
        self.cb_bitrate.setCurrentIndex(idx)
        tb.addWidget(self.cb_bitrate)

        tb.addSeparator()
        tb.addWidget(QLabel(" 크롭 "))
        self.cb_crop = QComboBox()
        for c in PRESETS:
            self.cb_crop.addItem(c.name, c)
        self.cb_crop.currentIndexChanged.connect(self._on_crop_changed)
        tb.addWidget(self.cb_crop)
        self.ed_crop = QLineEdit()
        self.ed_crop.setPlaceholderText("W:H:X:Y")
        self.ed_crop.setFixedWidth(150)
        self.ed_crop.setEnabled(False)
        self.ed_crop.editingFinished.connect(self._on_crop_changed)
        tb.addWidget(self.ed_crop)
        if self.s.crop:
            self._select_crop_from_text(self.s.crop)

        tb.addSeparator()
        self.btn_connect = QPushButton("연결")
        self.btn_connect.setStyleSheet("font-weight:bold; padding:4px 14px;")
        self.btn_connect.clicked.connect(self.toggle_connect)
        tb.addWidget(self.btn_connect)

    def _build_actions(self) -> None:
        tb = QToolBar("보기")
        tb.setMovable(False)
        self.addToolBar(tb)

        self.act_shot = QAction("스크린샷 (Ctrl+S)", self)
        self.act_shot.setShortcut(QKeySequence("Ctrl+S"))
        self.act_shot.triggered.connect(self.screenshot)
        tb.addAction(self.act_shot)

        self.act_rec = QAction("녹화 시작 (Ctrl+R)", self)
        self.act_rec.setShortcut(QKeySequence("Ctrl+R"))
        self.act_rec.setCheckable(True)
        self.act_rec.triggered.connect(self.toggle_record)
        tb.addAction(self.act_rec)

        self.act_full = QAction("전체화면 (F11)", self)
        self.act_full.setShortcut(QKeySequence("F11"))
        self.act_full.setCheckable(True)
        self.act_full.triggered.connect(self.toggle_fullscreen)
        tb.addAction(self.act_full)

        self.act_top = QAction("항상 위", self)
        self.act_top.setCheckable(True)
        self.act_top.toggled.connect(self._set_on_top)
        tb.addAction(self.act_top)

        act_quit = QAction("종료 (Ctrl+Q)", self)
        act_quit.setShortcut(QKeySequence("Ctrl+Q"))
        act_quit.triggered.connect(self.close)
        tb.addAction(act_quit)

        act_esc = QAction(self)
        act_esc.setShortcut(QKeySequence("Esc"))
        act_esc.triggered.connect(self._exit_fullscreen)
        self.addAction(act_esc)

    # ------------------------------------------------------------------ adb
    def _init_adb(self) -> None:
        try:
            self.adb = Adb(self.s.adb_path)
        except AdbError as e:
            msg = str(e)
            self.adb = None
            self._set_status("adb 없음")
            self.cb_device.addItem("adb를 찾을 수 없음", None)
            QTimer.singleShot(200, lambda: QMessageBox.warning(self, "adb 없음", msg))
            return
        self.adb.start_server()
        self.refresh_devices()
        self._scan_timer.start(3000)

    @Slot()
    def refresh_devices(self) -> None:
        if not self.adb or self.source is not None:
            return
        if self._scanner and self._scanner.isRunning():
            return
        self._scanner = DeviceScanner(self.adb)
        self._scanner.found.connect(self._on_devices)
        self._scanner.failed.connect(lambda m: self._set_status(f"adb 오류: {m}"))
        self._scanner.start()

    @Slot(list)
    def _on_devices(self, devs: list[Device]) -> None:
        current = self.cb_device.currentData()
        self.devices = devs
        self.cb_device.blockSignals(True)
        self.cb_device.clear()
        if not devs:
            self.cb_device.addItem("연결된 기기 없음 (USB 디버깅 확인)", None)
        for d in devs:
            self.cb_device.addItem(d.label(), d.serial)
        want = self.s.serial or current
        if want:
            i = self.cb_device.findData(want)
            if i >= 0:
                self.cb_device.setCurrentIndex(i)
        self.cb_device.blockSignals(False)
        if devs and not any(d.ready for d in devs):
            hint = STATE_HINTS.get(devs[0].state, devs[0].state)
            self._set_status(hint)
        elif devs:
            self._set_status(f"기기 {len(devs)}대 감지")

    # ------------------------------------------------------------- connect
    @Slot()
    def toggle_connect(self) -> None:
        if self.source is None:
            self.connect_device()
        else:
            self.disconnect_device()

    def connect_device(self) -> None:
        backend = self.cb_backend.currentData()
        serial = self.cb_device.currentData()
        cb = dict(on_frame=self._cb_frame, on_status=self.bridge.status.emit,
                  on_error=self.bridge.error.emit, on_packet=self._cb_packet)
        max_size = self.cb_size.currentData()
        bit_rate = self.cb_bitrate.currentData()
        try:
            if backend == "demo":
                self.source = DemoSource(**cb)
                self.device_size = (1600, 800)
            else:
                if not self.adb:
                    raise AdbError("adb를 사용할 수 없습니다.")
                if not serial:
                    raise AdbError("연결할 기기를 선택하세요.")
                dev = next((d for d in self.devices if d.serial == serial), None)
                if dev and not dev.ready:
                    raise AdbError(STATE_HINTS.get(dev.state, f"기기 상태: {dev.state}"))
                self.device_size = self.adb.display_size(serial)
                if backend == "scrcpy":
                    self.source = ScrcpySource(self.adb, serial, self.s.server_jar, max_size=max_size,
                                               bit_rate=bit_rate, max_fps=self.s.max_fps,
                                               keyframe_interval=self.s.keyframe_interval,
                                               display_id=self.s.display_id, **cb)
                else:
                    self.source = ScreenrecordSource(self.adb, serial, max_size=max_size,
                                                     bit_rate=bit_rate, **cb)
        except AdbError as e:
            self.source = None
            QMessageBox.warning(self, "연결 불가", str(e))
            return
        self._scan_timer.stop()
        self._set_controls_enabled(False)
        self.btn_connect.setText("연결 해제")
        self.video.clear("연결 중...")
        self._fps_count = 0
        self._bytes_t = (time.time(), 0)
        self.source.start()

    def disconnect_device(self, message: str = "연결 대기") -> None:
        if self.act_rec.isChecked():
            self.act_rec.setChecked(False)
            self._stop_record()
        src = self.source
        self.source = None
        if src:
            src.stop()
            src.join(timeout=3)
        self.video.clear(message)
        self.btn_connect.setText("연결")
        self._set_controls_enabled(True)
        self._set_status(message)
        self.lbl_stats.setText("")
        if self.adb:
            self._scan_timer.start(3000)
            self.refresh_devices()

    def _set_controls_enabled(self, on: bool) -> None:
        for w in (self.cb_device, self.btn_refresh, self.cb_backend, self.cb_size, self.cb_bitrate):
            w.setEnabled(on)

    # -------------------------------------------------------------- frames
    def _cb_frame(self, rgb: np.ndarray) -> None:
        """소스 스레드에서 호출. 최신 프레임만 보관하고 UI에 알린다."""
        with self._latest_lock:
            self._latest = rgb
            if self._pending:
                return
            self._pending = True
        self.bridge.frame_ready.emit()

    def _cb_packet(self, packet) -> None:
        if self.recorder.active:
            self.recorder.feed(packet)

    @Slot()
    def _on_frame_ready(self) -> None:
        with self._latest_lock:
            rgb = self._latest
            self._pending = False
        if rgb is None:
            return
        h, w = rgb.shape[:2]
        box = self.current_crop.apply(w, h, self.device_size)
        if box:
            x0, y0, x1, y1 = box
            rgb = rgb[y0:y1, x0:x1]
        self.video.set_frame(rgb)
        self._fps_count += 1

    @Slot()
    def _update_stats(self) -> None:
        now = time.time()
        dt = now - self._fps_t
        if dt > 0:
            self._fps = self._fps_count / dt
        self._fps_count = 0
        self._fps_t = now
        src = self.source
        if not src:
            return
        parts = []
        dec = src.decoder
        if dec and dec.width:
            parts.append(f"{dec.width}x{dec.height}")
            t0, b0 = self._bytes_t
            if now - t0 > 0:
                self._bitrate = (dec.bytes_in - b0) * 8 / (now - t0) / 1e6
            self._bytes_t = (now, dec.bytes_in)
            parts.append(f"{self._bitrate:.1f} Mbps")
        parts.append(f"{self._fps:.0f} fps")
        if src.started_at:
            el = int(now - src.started_at)
            parts.append(f"{el // 60:02d}:{el % 60:02d}")
        if self.recorder.active:
            if self.recorder.waiting_keyframe:
                parts.append("REC 키프레임 대기")
            else:
                parts.append(f"REC {self.recorder.bytes_written / 1e6:.1f} MB")
        self.lbl_stats.setText("  |  ".join(parts) + "  ")

    # ------------------------------------------------------------- status
    @Slot(str)
    def _set_status(self, text: str) -> None:
        self.statusBar().showMessage(text)

    @Slot(str)
    def _on_error(self, text: str) -> None:
        if self.source is None:
            return
        log.error(text)
        self.disconnect_device("오류로 연결 해제됨")
        QMessageBox.critical(self, "스트림 오류", text)

    # ------------------------------------------------------------- crop
    def _on_crop_changed(self) -> None:
        crop: Crop = self.cb_crop.currentData()
        custom = crop.mode == "rect" and crop.rect is None
        self.ed_crop.setEnabled(custom)
        if custom:
            rect = parse_rect(self.ed_crop.text())
            crop = Crop(crop.name, "rect", rect, None) if rect else PRESETS[0]
        self.current_crop = crop
        self._on_frame_ready_redraw()

    def _on_frame_ready_redraw(self) -> None:
        with self._latest_lock:
            self._pending = False
        self._on_frame_ready()

    def _select_crop_from_text(self, text: str) -> None:
        for i, c in enumerate(PRESETS):
            if c.name == text or (text == "left" and c.mode == "left") or \
               (text == "right" and c.mode == "right") or (text == "full" and c.mode == "full"):
                self.cb_crop.setCurrentIndex(i)
                return
        if parse_rect(text):
            self.cb_crop.setCurrentIndex(len(PRESETS) - 1)
            self.ed_crop.setText(text)
            self._on_crop_changed()

    # ------------------------------------------------------------ actions
    @Slot()
    def screenshot(self) -> None:
        with self._latest_lock:
            rgb = self._latest
        if rgb is None:
            self._set_status("저장할 프레임이 없습니다")
            return
        h, w = rgb.shape[:2]
        box = self.current_crop.apply(w, h, self.device_size)
        if box:
            x0, y0, x1, y1 = box
            rgb = np.ascontiguousarray(rgb[y0:y1, x0:x1])
        self.s.out_dir.mkdir(parents=True, exist_ok=True)
        path = self.s.out_dir / f"xr_{datetime.now():%Y%m%d_%H%M%S}.png"
        img = QImage(rgb.data, rgb.shape[1], rgb.shape[0], rgb.shape[1] * 3, QImage.Format_RGB888)
        img.save(str(path))
        self._set_status(f"스크린샷 저장: {path}")

    @Slot(bool)
    def toggle_record(self, on: bool) -> None:
        if on:
            if self.source is None or isinstance(self.source, DemoSource):
                self.act_rec.setChecked(False)
                self._set_status("녹화는 실제 기기 스트림에서만 가능합니다")
                return
            path = self.s.out_dir / f"xr_{datetime.now():%Y%m%d_%H%M%S}.mp4"
            self.recorder.start(path)
            self.act_rec.setText("녹화 중지 (Ctrl+R)")
            self._set_status(f"녹화 시작 (키프레임 대기): {path}")
        else:
            self._stop_record()

    def _stop_record(self) -> None:
        out = self.recorder.stop()
        self.act_rec.setText("녹화 시작 (Ctrl+R)")
        self._set_status(f"녹화 저장 중: {out}" if out else "녹화된 데이터 없음")

    @Slot(bool)
    def toggle_fullscreen(self, on: bool) -> None:
        if on:
            self.showFullScreen()
        else:
            self.showNormal()

    def _exit_fullscreen(self) -> None:
        if self.isFullScreen():
            self.act_full.setChecked(False)
            self.showNormal()

    @Slot(bool)
    def _set_on_top(self, on: bool) -> None:
        self.setWindowFlag(Qt.WindowStaysOnTopHint, on)
        self.show()

    def closeEvent(self, ev) -> None:  # noqa: N802
        self.disconnect_device()
        super().closeEvent(ev)


def run_app(settings: Settings) -> int:
    app = QApplication.instance() or QApplication([])
    app.setApplicationName("XR Mirror")
    win = MainWindow(settings)
    win.show()
    return app.exec()
