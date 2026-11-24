"""探针：单独跑「直播伴侣·互动消息」源，把抓到的事件实时打出来。

用法：python tools/probe_panel.py --seconds 25
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.companion import win  # noqa: E402
from app.companion.ocr import OCR  # noqa: E402
from app.config import CFG  # noqa: E402
from app.danmaku.ocr_source import OcrSource  # noqa: E402

STATS = {"ocr_calls": 0, "ocr_sec": 0.0, "max_sec": 0.0}


def timed_read(img, min_score=0.0, fast=False):
    t0 = time.time()
    out = _orig_read(img, min_score, fast)
    dt = time.time() - t0
    STATS["ocr_calls"] += 1
    STATS["ocr_sec"] += dt
    STATS["max_sec"] = max(STATS["max_sec"], dt)
    return out


_orig_read = OCR.read
OCR.read = timed_read  # type: ignore[assignment]


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=25)
    args = ap.parse_args()

    win.enable_dpi_awareness()
    info = win.find_window(["直播伴侣"], 300)
    if not info:
        print("没找到直播伴侣窗口")
        return
    print(f"窗口 {info.width}x{info.height} rect={info.rect}")

    t_start = time.time()

    async def emit(ev):
        print(f"[{time.time() - t_start:6.2f}s] {ev.kind:<7} {ev.user!r} "
              f"text={ev.text!r} gift={ev.gift_name!r}x{ev.gift_count}")

    src = OcrSource(CFG, emit, lambda: info.hwnd)
    await src.start()
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        await asyncio.sleep(2)
        s = src.snapshot()
        print(f"    · {s['label']} ok={s['ok']} count={s['count']} "
              f"err={s['last_error'][:40]!r} region={s.get('region')}")
    await src.stop()

    s = src.snapshot()
    print("\n=== 结果 ===")
    print(f"事件 {s['count']} 条；OCR {STATS['ocr_calls']} 次，"
          f"平均 {STATS['ocr_sec'] / max(1, STATS['ocr_calls']):.2f}s，"
          f"最慢 {STATS['max_sec']:.2f}s")
    print(f"定位区域 {s.get('region')}，错误 {s['last_error']!r}")


if __name__ == "__main__":
    asyncio.run(main())
