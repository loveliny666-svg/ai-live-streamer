"""探针：检查能否识别并操作「直播伴侣」（只读 + 演练，不真的开播）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.companion.controller import Companion  # noqa: E402
from app.companion.ocr import OCR  # noqa: E402
from app.config import CFG  # noqa: E402


async def main() -> None:
    c = Companion(CFG)
    print("配置里的启动路径：", CFG.get("companion.exe_path"))
    print("查找窗口…")
    info = c.find()
    if not info:
        print("没找到窗口，尝试启动…")
        c.launch()
        for i in range(45):
            await asyncio.sleep(1)
            if c.hwnd_or_none():
                print(f"窗口出现了（{i + 1}s）")
                break
        info = c.find()
    if not info:
        print("✗ 仍然没找到直播伴侣窗口")
        return

    print(f"✓ 窗口：{info.title!r}  {info.width}x{info.height}  hwnd={info.hwnd}")
    await asyncio.sleep(3)

    img, rect, boxes = await c.grab()
    if img is None:
        print("✗ 截图失败")
        return
    print(f"✓ 截图 {img.size}，rect={rect}，OCR {len(boxes)} 行")
    print("--- 识别到的文字（分数 >= 0.5）---")
    for b in sorted(boxes, key=lambda b: (round(b.cy / 12), b.x1)):
        if b.score >= 0.5:
            print(f"  {b.score:.2f}  ({int(b.cx)},{int(b.cy)})  {b.text}")

    print("\n--- 按钮探测 ---")
    for t in ["开始直播", "结束直播", "申请开播权限", "场景一", "场景二", "互动消息", "中控台"]:
        hit = OCR.find(boxes, t, 0.4)
        print(f"  {t}: {'✓ ' + hit.text + f' ({int(hit.cx)},{int(hit.cy)})' if hit else '✗ 没找到'}")

    print("\n--- 开播演练（不会真的点）---")
    res = await c.start_live(dry_run=True)
    import json

    print(json.dumps(res, ensure_ascii=False, indent=1))

    p = c.save_evidence(img, "probe")
    print("证据截图：", p)


if __name__ == "__main__":
    asyncio.run(main())
