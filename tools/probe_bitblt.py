"""探针：窗口在屏幕外时，能否用 GetWindowDC + BitBlt 快速拿到画面。"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402

from app.companion import win  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
SRCCOPY = 0x00CC0020


class BMIH(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BMI(ctypes.Structure):
    _fields_ = [("bmiHeader", BMIH), ("bmiColors", wt.DWORD * 3)]


def bitblt_window(hwnd, x, y, w, h):
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    ok = gdi32.BitBlt(mem, 0, 0, w, h, hdc, x, y, SRCCOPY)
    bi = BMI()
    bi.bmiHeader.biSize = ctypes.sizeof(BMIH)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), 0)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not got:
        return None
    return Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")


def main() -> None:
    win.enable_dpi_awareness()
    info = win.find_window(["直播伴侣"], 300)
    print("win", info.rect)
    region = (1225, 424, 1596, 831)
    x1, y1, x2, y2 = region
    w, h = x2 - x1, y2 - y1

    times = []
    img = None
    for _ in range(5):
        t = time.time()
        img = bitblt_window(info.hwnd, x1, y1, w, h)
        times.append(time.time() - t)
    print("bitblt ms", [round(t * 1000) for t in times], "median", round(statistics.median(times) * 1000))
    if img:
        ex = img.convert("L").getextrema()
        print("extrema", ex, "size", img.size)
        img.save("data/tmp/bitblt_region.png")


if __name__ == "__main__":
    main()
