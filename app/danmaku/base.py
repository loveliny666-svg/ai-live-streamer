"""弹幕源基类与事件路由（去重 / 限流 / 合并）。"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Awaitable, Callable

from ..models import LiveEvent, guess_gift_value

log = logging.getLogger("danmaku")

Emitter = Callable[[LiveEvent], Awaitable[None]]


class DanmakuSource:
    name = "base"
    label = "未命名源"

    def __init__(self, cfg, emit: Emitter):
        self.cfg = cfg
        self.emit = emit
        self.ok = False
        self.last_error = ""
        self.last_event_at = 0.0
        self.count = 0
        self._task: asyncio.Task | None = None

    async def _run(self) -> None:  # pragma: no cover - 子类实现
        raise NotImplementedError

    def _spawn(self) -> None:
        self._task = asyncio.create_task(self._supervise())

    async def _supervise(self) -> None:
        backoff = 3
        while True:
            try:
                await self._run()
                self.ok = True
                backoff = 3
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.ok = False
                self.last_error = str(exc)[:200]
                log.warning("[%s] 异常：%s（%ds 后重启）", self.name, exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(60, backoff * 2)

    async def start(self) -> None:
        self._spawn()

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()

    async def _push(self, ev: LiveEvent) -> None:
        self.count += 1
        self.last_event_at = time.time()
        await self.emit(ev)

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "ok": self.ok,
            "count": self.count,
            "last_error": self.last_error,
            "last_event_ago": round(time.time() - self.last_event_at, 1) if self.last_event_at else None,
        }


# ---------------- 文本分类（网页 DOM 与 OCR 共用）----------------
GIFT_RE = re.compile(
    r"^(?P<user>.{1,26}?)\s*(?:送出了|送给主播|送出|赠送了?|投喂了?)\s*(?P<gift>.{1,18}?)\s*(?:[x×]\s*(?P<count>\d+))?$"
)
FOLLOW_RE = re.compile(r"^(?P<user>.{1,26}?)\s*(?:关注了你|关注了主播|成为了主播的粉丝|加入了粉丝团)")
ENTER_RE = re.compile(r"^(?P<user>.{1,26}?)\s*(?:来了|进入了直播间|加入直播间|进入了直播)")
LIKE_RE = re.compile(r"^(?P<user>.{1,26}?)\s*(?:为主播点赞|点亮了|点了赞)")
COMMENT_RE = re.compile(r"^(?P<user>.{1,26}?)\s*[：:]\s*(?P<text>[\s\S]{1,120})$")

# 昵称清洗：网页里昵称后面会带「：」，OCR 会读进箭头/项目符号
_USER_TAIL = " \t\r\n:：,，、.。!！?？|｜·•*#"
_USER_HEAD = " \t\r\n←→↑↓↗↘↖↙·•*#@|｜"


def clean_user(raw) -> str:
    """把昵称里的冒号、箭头、零宽字符收拾干净，别让「欢迎 XX： 来到直播间」上屏。"""
    t = re.sub(r"\s+", " ", str(raw or "")).strip()
    if not t:
        return "观众"
    t = t.strip(_USER_TAIL)
    t = t.lstrip(_USER_HEAD).strip(_USER_TAIL)
    return (t or "观众")[:24]

UI_NOISE = {
    "互动消息", "在线观众榜", "说点什么", "发送", "礼物", "直播伴侣", "主播中心",
    "添加直播画面", "摄像头", "全屏", "窗口", "更多", "评论互动信息", "礼物消息",
    "展示本场直播的评论互动信息", "展示本场直播的礼物消息", "展示本场观众榜单",
    "正在加载", "网络异常", "重新连接",
}


def classify_text(raw: str) -> LiveEvent | None:
    """把一行文本变成事件；认不出来就返回 None。"""
    txt = " ".join(str(raw or "").split())
    if not txt or len(txt) > 140:
        return None
    if txt in UI_NOISE:
        return None
    if any(txt.startswith(x) for x in ("展示本场", "添加直播", "申请开播")):
        return None

    m = GIFT_RE.match(txt)
    if m and m.group("gift"):
        cnt = int(m.group("count") or 1)
        name = m.group("gift")
        return LiveEvent(
            kind="gift",
            user=clean_user(m.group("user")),
            gift_name=name,
            gift_count=cnt,
            gift_value=guess_gift_value(name, cnt),
        )
    m = FOLLOW_RE.match(txt)
    if m:
        return LiveEvent(kind="follow", user=clean_user(m.group("user")))
    m = ENTER_RE.match(txt)
    if m:
        return LiveEvent(kind="enter", user=clean_user(m.group("user")))
    m = LIKE_RE.match(txt)
    if m:
        return LiveEvent(kind="like", user=clean_user(m.group("user")), gift_count=1)
    m = COMMENT_RE.match(txt)
    if m:
        text = m.group("text").lstrip("：: ").strip()
        return LiveEvent(kind="comment", user=clean_user(m.group("user")), text=text)
    # 没有昵称前缀，当普通弹幕
    if 1 < len(txt) <= 90 and not re.match(r"^[\d\s:：.%-]+$", txt):
        return LiveEvent(kind="comment", user="观众", text=txt)
    return None


class Router:
    """把多个源的事件汇总，做二次去重与限流。"""

    def __init__(self, cfg, bus):
        self.cfg = cfg
        self.bus = bus
        self.sources: list[DanmakuSource] = []
        self._seen: dict[str, float] = {}
        self.published = 0

    def add(self, src: DanmakuSource) -> None:
        self.sources.append(src)

    async def handle(self, ev: LiveEvent) -> None:
        win = float(self.cfg.get("danmaku.dedupe_window_sec", 25))
        if ev.kind == "enter":
            # 进场要「每个观众都有欢迎」，窗口必须短：25 秒会把二次进场的人全吞掉
            win = float(self.cfg.get("danmaku.enter_dedupe_sec", 3))
        key = f"{ev.kind}|{ev.user}|{ev.text}|{ev.gift_name}|{ev.gift_count}"
        now = time.time()
        last = self._seen.get(key, 0)
        if now - last < win:
            return
        self._seen[key] = now
        if len(self._seen) > 800:
            cut = now - max(win * 2, 60)
            self._seen = {k: v for k, v in self._seen.items() if v >= cut}
        if await self.bus.publish(ev):
            self.published += 1

    async def start(self) -> None:
        for s in self.sources:
            await s.start()
        log.info("弹幕源已启动：%s", ", ".join(s.name for s in self.sources) or "无")

    async def stop(self) -> None:
        for s in self.sources:
            await s.stop()

    def snapshot(self) -> dict:
        return {
            "published": self.published,
            "sources": [s.snapshot() for s in self.sources],
        }
