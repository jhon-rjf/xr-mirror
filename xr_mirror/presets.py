"""XR 헤드셋별 크롭 프리셋.

XR 기기의 화면 캡처는 좌우 눈이 나란히 붙은(side-by-side) 넓은 프레임이므로
한쪽 눈만 잘라 보는 편이 보기 좋다. 좌표는 기기 '전체 해상도' 기준이며
스트림이 max_size로 축소되면 같은 비율로 자동 스케일된다.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Crop:
    name: str
    mode: str                   # full | left | right | rect
    rect: tuple[int, int, int, int] | None = None   # (w, h, x, y) 전체 해상도 기준
    base_size: tuple[int, int] | None = None        # rect가 기준으로 삼는 기기 해상도

    def apply(self, frame_w: int, frame_h: int,
              device_size: tuple[int, int] | None) -> tuple[int, int, int, int] | None:
        """프레임 픽셀 좌표의 (x0, y0, x1, y1)을 돌려준다. None이면 크롭 없음."""
        if self.mode == "full":
            return None
        if self.mode == "left":
            return (0, 0, frame_w // 2, frame_h)
        if self.mode == "right":
            return (frame_w // 2, 0, frame_w, frame_h)
        if self.mode == "rect" and self.rect:
            w, h, x, y = self.rect
            base = self.base_size or device_size
            if base and base[0] > 0:
                s = frame_w / base[0]
                w, h, x, y = (int(round(v * s)) for v in (w, h, x, y))
            x0 = max(0, min(frame_w - 2, x))
            y0 = max(0, min(frame_h - 2, y))
            x1 = max(x0 + 2, min(frame_w, x + w))
            y1 = max(y0 + 2, min(frame_h, y + h))
            return (x0, y0, x1, y1)
        return None


PRESETS: list[Crop] = [
    Crop("전체 (양안)", "full"),
    Crop("왼쪽 눈 (좌측 절반)", "left"),
    Crop("오른쪽 눈 (우측 절반)", "right"),
    # scrcpy 커뮤니티에서 널리 쓰는 Quest 2 왼눈 왜곡 보정 영역 (3664x1920 기준)
    Crop("Meta Quest 2 (왼쪽 눈 보정)", "rect", (1600, 900, 2017, 510), (3664, 1920)),
    Crop("사용자 지정 (W:H:X:Y)", "rect", None),
]


def parse_rect(text: str) -> tuple[int, int, int, int] | None:
    parts = text.replace(" ", "").split(":")
    if len(parts) != 4:
        return None
    try:
        w, h, x, y = (int(p) for p in parts)
    except ValueError:
        return None
    if w <= 0 or h <= 0 or x < 0 or y < 0:
        return None
    return (w, h, x, y)
