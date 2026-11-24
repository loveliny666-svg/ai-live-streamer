"""探针：测试「只截窗口的某一块」是否更快（PrintWindow + 视口偏移）。"""
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

PW_CLIENTONLY = 0x00000001
PW_RENDERFULLCONTENT = 0x00000002
SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wt.DWORD), ("biWidth", wt.LONG), ("biHeight", wt.LONG),
        ("biPlanes", wt.WORD), ("biBitCount", wt.WORD), ("biCompression", wt.DWORD),
        ("biSizeImage", wt.DWORD), ("biXPelsPerMeter", wt.LONG),
        ("biYPelsPerMeter", wt.LONG), ("biClrUsed", wt.DWORD), ("biClrImportant", wt.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wt.DWORD * 3)]


def grab_region(hwnd: int, x: int, y: int, w: int, h: int, viewport: bool, flags: int):
    hdc = user32.GetWindowDC(hwnd)
    mem = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mem, bmp)
    if viewport:
        gdi32.SetViewportOrgEx(mem, -x, -y, None)
    ok = user32.PrintWindow(hwnd, mem, flags)
    if viewport:
        gdi32.SetViewportOrgEx(mem, 0, 0, None)
    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    got = gdi32.GetDIBits(mem, bmp, 0, h, buf, ctypes.byref(bi), DIB_RGB_COLORS)
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(hwnd, hdc)
    if not got:
        return None
    img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1).convert("RGB")
    return img, ok


def main() -> None:
    win.enable_dpi_awareness()
    info = win.find_window(["直播伴侣"], 300)
    r = info.rect
    px, py = 1225, 620
    pw, ph = 377, 210

    for tag, vp, flags in [
        ("full+viewport", True, PW_RENDERFULLCONTENT),
        ("full+noviewport", False, PW_RENDERFULLCONTENT),
        ("clientonly+vp", True, PW_CLIENTONLY | PW_RENDERFULLCONTENT),
    ]:
        times = []
        img = None
        for i in range(4):
            t = time.time()
            img, ok = grab_region(info.hwnd, px, py, pw, ph, vp, flags)
            times.append(time.time() - t)
        print(f"{tag:<18} ms={[round(t*1000) for t in times]} median={round(statistics.median(times)*1000)} ok={ok}")
        if img:
            img.save(f"data/tmp/region_{tag.replace('+','_')}.png")
            print("   saved", img.size)


if __name__ == "__main__":
    main()
