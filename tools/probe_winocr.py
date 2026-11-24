"""探针：对比 Windows 自带 OCR 与 RapidOCR 的速度/准确度。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from winrt.windows.globalization import Language  # noqa: E402
from winrt.windows.graphics.imaging import BitmapDecoder  # noqa: E402
from winrt.windows.media.ocr import OcrEngine  # noqa: E402
from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream  # noqa: E402

from app.companion import win  # noqa: E402


async def to_bitmap(img):
    import io

    buf = io.BytesIO()
    img.convert("RGB").save(buf, format="BMP")
    data = buf.getvalue()
    stream = InMemoryRandomAccessStream()
    writer = DataWriter(stream.get_output_stream_at(0))
    writer.write_bytes(data)
    await writer.store_async()
    await writer.flush_async()
    stream.seek(0)
    decoder = await BitmapDecoder.create_async(stream)
    return await decoder.get_software_bitmap_async()


async def main() -> None:
    win.enable_dpi_awareness()
    w = win.find_window(["直播伴侣"], 300)
    img, _ = win.capture_window(w.hwnd, False)
    panel = img.crop((1225, 420, 1602, 830))

    lang = Language("zh-Hans-CN")
    engine = OcrEngine.try_create_from_language(lang)
    print("engine", engine, "lang", lang.language_tag)

    for i in range(3):
        t0 = time.time()
        bmp = await to_bitmap(panel)
        t1 = time.time()
        res = await engine.recognize_async(bmp)
        t2 = time.time()
        lines = list(res.lines)
        print(f"round{i} bmp {1000*(t1-t0):.0f}ms ocr {1000*(t2-t1):.0f}ms lines={len(lines)}")
        if i == 0:
            for ln in lines:
                r = ln.words[0].bounding_rect if ln.words else None
                print("   ", repr(ln.text), (r.x, r.y, r.width, r.height) if r else None)


asyncio.run(main())
