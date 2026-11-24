"""用真实的直播伴侣界面截图，验证「开播判定」逻辑（不会点任何东西）。

用户当前账号状态是「申请开播权限」，所以这段测试的核心断言是：
程序必须识别出这是权限问题并明确报错，而不是傻点半天。
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PIL import Image

from app.companion.controller import Companion
from app.companion.ocr import OCR, Box
from app.config import CFG

PASS, FAIL = [], []
# 这张截图是本机截的直播伴侣界面，不进仓库。跑这个测试前把环境变量指过去：
#   set COMPANION_TEST_SHOT=C:\path\to\companion.png
# 不设就会落到下面那个占位名，SHOT.exists() 为假，测试自动跳过。
SHOT = Path(os.environ.get("COMPANION_TEST_SHOT", "companion_test.png"))


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""))


class FakeCompanion(Companion):
    """grab 返回固定截图，点击只记录不执行。"""

    def __init__(self, img: Image.Image, boxes: list[Box]):
        super().__init__(CFG)
        self._img = img
        self._boxes = boxes
        self.clicks: list[tuple[int, int]] = []

    async def ensure_running(self) -> bool:
        return True

    def hwnd_or_none(self):
        return 12345

    async def grab(self):
        return self._img, (0, 0, self._img.width, self._img.height), self._boxes

    async def _click_box(self, rect, box, tag):
        self.clicks.append((int(rect[0] + box.cx), int(rect[1] + box.cy)))

    async def _to_front(self):
        return None

    def _back_to_stage(self):
        return None


async def main() -> None:
    print("\n[1] 识别真实界面截图")
    if not SHOT.exists():
        print("  ! 找不到截图，跳过：", SHOT)
        return
    img = Image.open(SHOT).convert("RGB")
    boxes = OCR.read(img, 0.0)
    check("OCR 读到足够文字", len(boxes) >= 30, f"{len(boxes)} 行、图 {img.size}")

    texts = [b.text for b in boxes]
    for key, name in [
        ("申请开播权限", "权限申请按钮"),
        ("互动消息", "互动消息面板"),
        ("添加素材", "素材面板"),
    ]:
        hit = OCR.find(boxes, key, 0.35)
        check(f"认出「{name}」", hit is not None, hit.text if hit else "未识别")

    c0 = Companion(CFG)
    zb = c0._zoom_read(img, (0.0, 0.0, 0.40, 0.28), 2.0, 0.3)
    scene_hit = OCR.find(zb, "场景二", 0.4) or OCR.find(zb, "场景三", 0.4)
    check("放大后能认出场景标签", scene_hit is not None, scene_hit.text if scene_hit else "未识别")

    print("\n[2] 开播判定：必须识别为权限受阻，且不点击")
    c = FakeCompanion(img, boxes)
    res = await c.start_live(dry_run=True)
    check("识别为受阻", bool(res.get("blocked")), str(res.get("reason", ""))[:60])
    check("给出明确原因", "权限" in str(res.get("reason", "")), str(res.get("reason", ""))[:80])
    check("没有误点按钮", len(c.clicks) == 0, f"点击次数={len(c.clicks)}")
    check("留了证据截图", bool(res.get("evidence")))

    print("\n[3] 群演一张「已在直播」的界面，验证不会重复开播")
    fake = Image.new("RGB", (1280, 800), (28, 30, 38))
    from PIL import ImageDraw, ImageFont

    fnt = ImageFont.truetype("C:/Windows/Fonts/msyhbd.ttc", 30)
    d = ImageDraw.Draw(fake)
    d.text((40, 30), "直播伴侣", font=fnt, fill=(230, 230, 240))
    d.rectangle([1020, 700, 1240, 768], fill=(240, 60, 88))
    d.text((1046, 714), "结束直播", font=fnt, fill=(255, 255, 255))
    boxes2 = OCR.read(fake, 0.0)
    c2 = FakeCompanion(fake, boxes2)
    res2 = await c2.start_live(dry_run=True)
    check("识别为已在播", bool(res2.get("ok") and res2.get("already")), str(res2)[:80])

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
