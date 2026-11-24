"""弹幕源 · 直播伴侣「互动消息」面板（主源）。

为什么以它为主：直播伴侣收到的就是平台推给主播端的原始互动，
弹幕、礼物、进场、关注、点赞全在这一个面板里，不用登录网页、不碰签名。

做法：高频只截「互动消息」那一小块（屏幕可见时走 BitBlt，几毫秒），
算个指纹，画面没变就直接跳过；一变立刻跑 OCR，所以延迟基本等于一次识别的时间。
OCR 用「只缩不放」的快速参数，短边不再被放大到 736，小图能快十倍。
"""
from __future__ import annotations

import asyncio
import logging
import re
import time

from ..companion.ocr import OCR, Box
from .base import DanmakuSource, classify_text

log = logging.getLogger("danmaku.panel")

# 舞台被全屏录制时，PrintWindow 会抓到覆盖在上面的舞台像素。
# 这些特征用来判断「截到的是不是代码编辑器」，命中就别当弹幕。
CODE_HINTS = ("def ", "async ", "await ", "return ", "import ", "const ", "let ",
              "() =>", "-> None", "pytest", "passed", "console.log", "#333",
              "self.", "print(", "class ", "export ")


_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")
_CALL = re.compile(r"[A-Za-z_]\w*\s*\(")      # Requirement( / load(id)
_ASSIGN = re.compile(r"\w+\s*=\s*[\"']")      # title="首页高级感"

# 面板里的固定文案 / 平台提示，不是观众消息
PANEL_NOISE = {
    "互动消息", "在线观众榜", "礼物消息", "评论互动信息", "观众榜",
    "展示本场直播的礼物消息", "展示本场观众榜单", "展示本场直播的评论互动信息",
    "说点什么", "说点什么...", "说点什么…", "发送", "聊天", "小时榜",
    "主播中心", "直播伴侣", "更多", "收起", "展开",
}
PANEL_NOISE_PREFIX = ("主播在直播中", "辨别。切勿", "谨防网络", "展示本场",
                      "点击查看", "正在加载", "网络异常", "重新连接")
PANEL_NOISE_PARTS = ("诱导消费", "私下交易", "人身财产", "网络诈骗", "理性消费",
                     "严禁未成年", "违法违规", "分享了本次直播", "分享了直播间",
                     "正在去直播间", "邀请你连麦")
# 平台那段「诱导消费 / 防诈骗」提示，OCR 偶尔会掉字（"诱导消费"→"诱导消"），
# 单靠整词匹配会漏，所以再加一层：命中两个以上特征词就按系统提示丢掉。
WARN_HINTS = ("诱导", "消费", "私下", "交易", "人身", "财产", "诈骗",
              "谨防", "辨别", "谨慎", "理性", "违规")

# 礼物常被拆成两行：「臭宝 送出了」+「小心心 x3」
GIFT_HEAD = re.compile(r"^(?P<user>.{1,26}?)\s*(?:送出了|送给主播|送出|赠送了?|投喂了?)\s*$")
GIFT_ONLY = re.compile(
    r"^(?P<gift>[\u4e00-\u9fa5A-Za-z0-9·\-]{1,14})\s*[x×]\s*(?P<count>\d+)$"
)

# 进场 / 礼物行前面有个粉丝牌（紫色小胶囊里写着等级），OCR 会把数字粘进昵称：
# 「◆2 搬砖工」→「2搬砖工」。只剥 1~2 位、且后面还跟着两个字以上的名字。
BADGE_HEAD = re.compile(r"^\d{1,2}(?=[\u4e00-\u9fa5A-Za-z]{2,})")
# 抖音自动生成的「用户1765750190075」被 OCR 读坏后会长这样，
# 与其当成弹幕回一句莫名其妙的，不如直接丢掉
LONG_DIGITS = re.compile(r"\d{8,}")
_DEDUPE_NOISE = re.compile(r"[\s，。！？、,.!?~…·:：;；\"'“”‘’()（）\[\]【】]+")


def dedupe_key(text: str) -> str:
    """OCR 同一句话每次读出来的标点/空格会抖，去重键必须先归一。"""
    return _DEDUPE_NOISE.sub("", text or "").lower()


def strip_badge(user: str) -> str:
    m = BADGE_HEAD.match(user or "")
    if not m:
        return user
    rest = user[m.end():]
    return rest if len(rest) >= 2 else user


def looks_like_code(text: str) -> bool:
    """判断这段文字是不是代码/编辑器内容，而不是弹幕。"""
    if not text:
        return False
    hits = sum(1 for h in CODE_HINTS if h in text)
    punct = sum(text.count(c) for c in "{};=<>()[]#")
    if hits >= 2 or punct >= 10:
        return True
    if _HEX_COLOR.search(text):
        return True
    # 单行：有强特征 + 代码符号，或明显是函数调用 / 赋值
    if hits >= 1 and punct >= 2:
        return True
    if _CALL.search(text) and punct >= 2:
        return True
    if _ASSIGN.search(text):
        return True
    return False


def is_panel_noise(text: str) -> bool:
    t = text.strip().strip("：: ")
    if not t or t in PANEL_NOISE:
        return True
    if t.startswith(PANEL_NOISE_PREFIX):
        return True
    if any(x in t for x in PANEL_NOISE_PARTS):
        return True
    if sum(1 for h in WARN_HINTS if h in t) >= 2:
        return True
    return False


class OcrSource(DanmakuSource):
    name = "panel"
    label = "直播伴侣·互动消息"

    def __init__(self, cfg, emit, hwnd_provider, sibling_check=None):
        super().__init__(cfg, emit)
        self.hwnd_provider = hwnd_provider
        # 默认面板就是主源，不存在「网页活着就跳过」这回事。
        # 只有显式配了 fallback_only 才退回兜底模式。
        self.fallback_only = bool(self.cfg.get("danmaku.sources.ocr.fallback_only", False))
        self.sibling_check = sibling_check or (lambda: False)
        self.skipped = 0
        self._region: tuple[int, int, int, int] | None = None
        # 「已经发过、现在还挂在面板上」的消息；消失两轮后才允许重新发
        self._emitted: set[str] = set()
        self._missing: dict[str, int] = {}
        self._last_hit = 0.0
        self._prev_sig = None
        self._last_ocr = 0.0
        self._covered = False
        self._covered_at = 0.0
        self._located_at = 0.0
        self._input_top = 0
        self._fails = 0
        self._win_size: tuple[int, int] | None = None
        self.blocked = False
        # 只读面板下半截 + 缩到 0.8 倍：实测识别结果一样，CPU 少一半
        self._bottom_ratio = min(1.0, max(0.3, float(self.cfg.get("danmaku.sources.ocr.bottom_ratio", 0.62))))
        self._scale = min(1.0, max(0.4, float(self.cfg.get("danmaku.sources.ocr.scale", 0.8))))

    # ---------- 区域探测 ----------
    async def _locate(self, hwnd: int) -> bool:
        """在窗口右侧找到「互动消息」面板，记下它的像素范围。"""
        from ..companion import win

        loop = asyncio.get_running_loop()
        full, _ = await loop.run_in_executor(None, lambda: win.capture_window(hwnd, False))
        if full is None:
            self.ok = False
            self.last_error = "截图失败"
            return False

        w, h = full.size
        x0 = int(w * 0.66)
        # 只在右半边上半截找「互动消息」标题，别把整窗都丢给 OCR
        head = full.crop((x0, 0, w, int(h * 0.66)))
        boxes = await loop.run_in_executor(None, lambda: OCR.read(head, 0.32, True))
        if boxes and looks_like_code(" ".join(b.text for b in boxes)):
            # 截到的是舞台画面（直播伴侣被全屏盖住）
            self.blocked = True
            self.ok = False
            self.last_error = "直播伴侣被遮挡（截到的是舞台画面）"
            self._region = None
            return False

        anchor = OCR.find(boxes, "互动消息", 0.40) or OCR.find(boxes, "礼物消息", 0.40)
        if not anchor:
            self.ok = False
            self.blocked = False
            self.last_error = "没找到「互动消息」面板（可能没开播）"
            return False

        x1 = max(0, int(anchor.x1) + x0 - 26)
        y1 = int(anchor.y2) + 8

        # 输入框的位置：面板读到输入框就够了，再往下是「发送」和状态栏，
        # 既不是消息，还会让每次 OCR 多算一大块。它在下半截，单独截一条找。
        yb = int(h * 0.74)
        foot = full.crop((x0, yb, w, h))
        fboxes = await loop.run_in_executor(None, lambda: OCR.read(foot, 0.28, True))
        inp = OCR.find(fboxes, "说点什么", 0.26) or OCR.find(fboxes, "发送", 0.26)
        self._input_top = (int(inp.y1) + yb) if inp else h - 56
        y2 = min(h - 4, self._input_top - 6)
        if y2 - y1 < 80:
            y2 = h - 4
            self._input_top = h - 56
        self._region = (x1, y1, w - 4, y2)
        self._win_size = (w, h)
        self._prev_sig = None

        self.blocked = False
        self._located_at = time.time()
        log.info("互动消息面板定位成功：%s（输入框 y=%s）", self._region, self._input_top)
        return True

    # ---------- 主循环 ----------
    async def _run(self) -> None:
        from ..companion import win

        poll = max(0.08, float(self.cfg.get("danmaku.sources.ocr.poll_ms", 200)) / 1000.0)
        min_gap = max(0.15, float(self.cfg.get("danmaku.sources.ocr.interval_sec", 0.5)))
        cfg_region = self.cfg.get("danmaku.sources.ocr.region", []) or []
        loop = asyncio.get_running_loop()

        # 兜底模式：先给 WebSocket 源一点时间连上，别在启动那几秒里两路一起发
        if self.fallback_only:
            await asyncio.sleep(20)

        while True:
            t0 = time.time()
            if self.fallback_only and self.sibling_check():
                self.skipped += 1
                self.ok = True
                await asyncio.sleep(2.0)
                continue

            hwnd = self.hwnd_provider()
            if not hwnd:
                self.ok = False
                self.last_error = "没找到直播伴侣窗口"
                await asyncio.sleep(2.0)
                continue

            if len(cfg_region) == 4:
                self._region = tuple(int(v) for v in cfg_region)  # type: ignore[assignment]
            if self._region is None:
                if not await self._locate(hwnd):
                    self._fails += 1
                    # 没开播 / 面板不在时别死循环重试，退避到 15 秒一次
                    await asyncio.sleep(min(15.0, 2.0 * (1.8 ** min(self._fails, 6))))
                    continue
                self._fails = 0
            region = self._region
            assert region is not None

            now = time.time()
            if now - self._covered_at > 1.0:
                self._covered = await loop.run_in_executor(None, lambda: win.is_covered(hwnd))
                self._covered_at = now
            if self._covered:
                self.ok = False
                self.blocked = True
                self.last_error = "直播伴侣被其他窗口遮挡，OCR 暂停"
                self._region = None
                await asyncio.sleep(1.5)
                continue

            t_cap = time.time()
            img, wrect = await loop.run_in_executor(
                None, lambda: win.grab_region(hwnd, region, covered=self._covered)
            )
            cap_cost = time.time() - t_cap
            # 窗口被拖动 / 改了大小，之前算好的面板坐标就废了，重新定位
            if wrect and wrect[2] > wrect[0]:
                size = (wrect[2] - wrect[0], wrect[3] - wrect[1])
                if self._win_size and size != self._win_size:
                    self._region = None
                    await asyncio.sleep(0.2)
                    continue
            # 窗口在屏幕外时只能走 PrintWindow（整窗重绘，慢得多），
            # 这时候把轮询放宽一点，别为了 200ms 的粒度把 CPU 吃满
            pace = poll if cap_cost < 0.15 else max(poll, 0.45)
            if img is None or img.width < 40 or img.height < 30:
                self.ok = False
                self.last_error = "截图失败"
                self._region = None
                await asyncio.sleep(0.5)
                continue

            # 指纹用「缩小后的灰度图 + 平均差」而不是逐像素哈希：
            # Chromium 的 PrintWindow 偶尔会有抗锯齿抖动，逐像素比对会把它当成新消息，
            # 结果就是画面明明没变却一直跑 OCR。
            cut = max(0, min(img.height, self._input_top - region[1] - 4))
            sig_src = img.crop((0, 0, img.width, cut)) if cut > 20 else img
            sig = self._signature(sig_src)
            changed = self._is_changed(sig)

            # 画面没变就完全不跑 OCR（只留 30 秒一次的兜底刷新，
            # 防止极端情况下指纹卡住导致再也不识别）
            if not changed and time.time() - self._last_ocr < 30:
                await asyncio.sleep(pace)
                continue
            if time.time() - self._last_ocr < min_gap:
                await asyncio.sleep(pace)
                continue

            # 新消息从底部往上堆，只读下面这一截就够了；顺带把顶部留白省掉
            keep = int(img.height * self._bottom_ratio)
            desired = max(0, img.height - keep)
            y_off = self._snap_top(img, desired) if desired else 0
            work = img.crop((0, y_off, img.width, img.height)) if y_off else img
            if self._scale and self._scale != 1.0:
                work = work.resize(
                    (max(40, int(work.width * self._scale)), max(30, int(work.height * self._scale)))
                )

            boxes = await loop.run_in_executor(None, lambda: OCR.read(work, 0.32, True))
            self._last_ocr = time.time()
            self._prev_sig = sig

            if y_off and boxes:
                # 顶部那行可能被切掉一半，读出来是残句，别当弹幕
                boxes = [b for b in boxes if b.y1 > 4]

            if boxes and looks_like_code(" ".join(b.text for b in boxes)):
                self.blocked = True
                self.ok = False
                self.last_error = "直播伴侣被遮挡（截到的是舞台画面）"
                self._region = None
                await asyncio.sleep(2.0)
                continue

            if boxes:
                self.ok = True
                self.blocked = False
                self.last_error = ""
            await self._emit_from(boxes)

            # 很久没读到东西，重新定位一次（窗口被拖动 / 改了布局）
            if self._last_hit and time.time() - self._last_hit > 600:
                self._region = None
                self._last_hit = time.time()

            cost = time.time() - t0
            await asyncio.sleep(max(0.0, pace - cost))

    def snapshot(self) -> dict:
        d = super().snapshot()
        d["blocked"] = self.blocked
        d["region"] = self._region
        return d

    # ---------- 画面指纹 ----------
    @staticmethod
    def _snap_top(img, desired: int) -> int:
        """把裁剪线上移到两行文字之间的空隙，别切出半行字（半行字会被当成弹幕发出去）。"""
        import numpy as np

        g = np.asarray(img.convert("L"), dtype=np.uint8)
        if g.size == 0:
            return desired
        rows = (g > 90).sum(axis=1)
        h = len(rows)
        lo = max(0, desired - 26)
        hi = min(h - 1, desired + 26)
        if hi <= lo:
            return desired
        best_y, best_v = desired, None
        for y in range(lo, hi + 1):
            v = int(rows[y])
            if best_v is None or v < best_v or (v == best_v and abs(y - desired) < abs(best_y - desired)):
                best_y, best_v = y, v
        return best_y

    @staticmethod
    def _signature(img):
        """缩到 48x32 的灰度图，抗锯齿抖动会被平均掉，新消息仍能看出来。"""
        import numpy as np

        return np.asarray(img.convert("L").resize((48, 32)), dtype=np.int16)

    def _is_changed(self, sig) -> bool:
        import numpy as np

        prev = self._prev_sig
        if prev is None or getattr(prev, "shape", None) != sig.shape:
            return True
        diff = np.abs(sig - prev)
        ratio = float((diff > 25).mean())
        return ratio > 0.002 or float(diff.mean()) > 3.0

    # ---------- 行合并 + 分类 ----------
    async def _emit_from(self, boxes: list[Box]) -> None:
        rows: list[list[Box]] = []
        for b in sorted(boxes, key=lambda b: (b.cy, b.x1)):
            placed = False
            for row in rows:
                if abs(row[-1].cy - b.cy) < max(9.0, min(17.0, b.h * 0.7)):
                    row.append(b)
                    placed = True
                    break
            if not placed:
                rows.append([b])

        texts: list[str] = []
        for row in rows:
            row.sort(key=lambda b: b.x1)
            text = ""
            prev = None
            for b in row:
                if prev is not None and b.x1 - prev.x2 > b.h * 0.8:
                    text += " "
                text += b.text
                prev = b
            text = " ".join(text.split()).strip()
            if len(text) < 2:
                continue
            if is_panel_noise(text) or looks_like_code(text):
                continue
            texts.append(text)

        # 礼物被拆成两行时拼回去
        merged: list[str] = []
        for text in texts:
            if merged:
                head = GIFT_HEAD.match(merged[-1])
                only = GIFT_ONLY.match(text)
                if head and only:
                    merged[-1] = f"{head.group('user')} 送出了 {only.group('gift')} x{only.group('count')}"
                    continue
            merged.append(text)

        # 按「出现」去重，而不是按时间窗口：
        # 面板里的消息会一直挂在屏幕上，OCR 每轮都会再读一遍。
        # 只要它还看得见，就说明是同一件事，绝不能因为过了 12 秒又欢迎一次；
        # 等它从面板上消失（连续两轮没读到），才允许同一个 key 再来一次。
        now = time.time()
        current: dict[str, LiveEvent] = {}
        for text in merged:
            ev = classify_text(text)
            if not ev:
                continue
            if ev.kind in ("enter", "gift", "like", "follow"):
                ev.user = strip_badge(ev.user)
            key = f"{ev.kind}|{dedupe_key(text)}"
            if key.endswith("|"):
                continue
            if ev.kind == "comment" and LONG_DIGITS.search(text):
                continue
            current[key] = ev

        for key, ev in current.items():
            if key in self._emitted:
                self._missing[key] = 0
                continue
            self._emitted.add(key)
            self._missing[key] = 0
            self._last_hit = now
            ev.source = "panel"
            await self._push(ev)

        for key in list(self._emitted):
            if key in current:
                continue
            self._missing[key] = self._missing.get(key, 0) + 1
            if self._missing[key] >= 2:
                self._emitted.discard(key)
                self._missing.pop(key, None)
        if len(self._emitted) > 400:
            self._emitted.clear()
            self._missing.clear()
