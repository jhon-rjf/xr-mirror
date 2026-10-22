"""H.264 Annex-B 바이트 스트림을 PyAV로 디코딩해 numpy RGB 프레임으로 변환."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterator

import av
import numpy as np

log = logging.getLogger(__name__)

NAL_SPS, NAL_PPS, NAL_IDR = 7, 8, 5


@dataclass
class Decoded:
    packet: av.Packet
    frames: list[np.ndarray]   # RGB24 (H, W, 3)


def nal_types(data: bytes) -> list[int]:
    """Annex-B 패킷 안의 NAL unit 타입 목록(간단 스캔)."""
    out: list[int] = []
    i = 0
    n = len(data)
    while True:
        j = data.find(b"\x00\x00\x01", i)
        if j < 0 or j + 3 >= n:
            break
        out.append(data[j + 3] & 0x1F)
        i = j + 3
    return out


class H264Decoder:
    """바이트 덩어리를 feed()하면 파싱된 패킷과 디코딩된 프레임을 돌려준다.

    컨테이너(demuxer)를 쓰지 않고 CodecContext.parse()로 직접 파싱하므로
    소켓/파이프의 라이브 스트림을 버퍼링 지연 없이 처리할 수 있다.
    """

    def __init__(self, threads: int = 0):
        self.codec = av.CodecContext.create("h264", "r")
        if threads:
            self.codec.thread_count = threads
            self.codec.thread_type = "SLICE"
        try:  # 지연 최소화. PyAV 버전에 따라 enum 위치가 달라 실패해도 무시
            from av.codec.context import Flags  # type: ignore
            self.codec.flags |= Flags.low_delay
        except Exception:  # noqa: BLE001
            pass
        self.width = 0
        self.height = 0
        self.frames_decoded = 0
        self.bytes_in = 0

    def feed(self, data: bytes) -> Iterator[Decoded]:
        self.bytes_in += len(data)
        for packet in self.codec.parse(data):
            frames: list[np.ndarray] = []
            try:
                for frame in self.codec.decode(packet):
                    arr = frame.to_ndarray(format="rgb24")
                    self.height, self.width = arr.shape[0], arr.shape[1]
                    self.frames_decoded += 1
                    frames.append(arr)
            except av.error.InvalidDataError as e:  # 키프레임 이전 조각 등
                log.debug("decode skip: %s", e)
            yield Decoded(packet, frames)

    def flush(self) -> list[np.ndarray]:
        """스트림 종료 시 파서/디코더에 남은 프레임을 꺼낸다 (라이브에서는 쓰지 않음)."""
        out: list[np.ndarray] = []
        try:
            for packet in self.codec.parse(None):
                for frame in self.codec.decode(packet):
                    out.append(frame.to_ndarray(format="rgb24"))
            for frame in self.codec.decode(None):
                out.append(frame.to_ndarray(format="rgb24"))
        except Exception:  # noqa: BLE001
            pass
        return out
