"""探针：用 Windows Graphics Capture 抓直播伴侣窗口，看帧率/画质/CPU。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from windows_capture import Frame, InternalCaptureControl, WindowsCapture  # noqa: E402

from app.companion import win  # noqa: E402

stats = {"n": 0, "t0": 0.0, "saved": False, "size": None, "extrema": None}
lock = threading.Lock()


def main() -> None:
    win.enable_dpi_awareness()
    info = win.find_window(["直播伴侣"], 300)
    if not info:
        print("没找到窗口")
        return
    print("hwnd", info.hwnd, info.rect)

    capture = WindowsCapture(
        cursor_capture=False,
        draw_border=False,
        window_hwnd=info.hwnd,
        minimum_update_interval=0,
    )

    @capture.event
    def on_frame_arrived(frame: Frame, control: InternalCaptureControl) -> None:
        with lock:
            stats["n"] += 1
            stats["size"] = (frame.width, frame.height)
            if not stats["saved"] and stats["n"] >= 3:
                buf = frame.frame_buffer  # BGRA
                img = Image.fromarray(buf[:, :, :3][:, :, ::-1])
                img.save("data/tmp/wgc_frame.png")
                stats["extrema"] = img.convert("L").getextrema()
                stats["saved"] = True
                stats["t0"] = time.time()

    @capture.event
    def on_closed() -> None:
        print("capture closed")

    control = capture.start_free_threaded()
    time.sleep(4)
    control.stop()
    time.sleep(0.5)

    with lock:
        n = stats["n"]
        print("frames", n, "size", stats["size"], "extrema", stats["extrema"])
        if stats["t0"]:
            print("fps after warmup ~", round((n - 3) / max(0.1, time.time() - stats["t0"]), 1))


if __name__ == "__main__":
    main()
