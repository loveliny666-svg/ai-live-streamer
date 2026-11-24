"""异步事件总线：环状缓冲 + 多订阅者 + 房间统计。"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

from .models import LiveEvent, RoomStats

log = logging.getLogger("bus")

Handler = Callable[[LiveEvent], Awaitable[None]]


class Bus:
    def __init__(self, history: int = 400, cfg=None):
        self._subs: list[Handler] = []
        self._history: deque[LiveEvent] = deque(maxlen=history)
        self._stats = RoomStats()
        self._lock = asyncio.Lock()
        self._seen: dict[str, float] = {}
        self.cfg = cfg
        self.on_event_ui: Callable[[LiveEvent], None] | None = None

    # ---------- 订阅 ----------
    def subscribe(self, handler: Handler) -> None:
        self._subs.append(handler)

    # ---------- 发布 ----------
    def _ttl(self, ev: LiveEvent) -> float:
        """同一条消息在多长时间内算重复。

        进场必须短：同一个观众离开再回来，字段和 eid 一模一样，用 2 分钟的
        默认窗口会把他第二次进场彻底吞掉 —— 观众就觉得「主播没欢迎我」。"""
        if ev.kind == "enter":
            if self.cfg is None:
                return 3.0
            try:
                return max(0.0, float(self.cfg.get("danmaku.enter_dedupe_sec", 3)))
            except Exception:  # noqa: BLE001
                return 3.0
        return 120.0

    async def publish(self, ev: LiveEvent) -> bool:
        """返回 False 表示被判为重复事件丢弃。"""
        async with self._lock:
            now = time.time()
            key = ev.eid
            last = self._seen.get(key)
            if last is not None and now - last < self._ttl(ev):
                return False
            self._seen[key] = now
            if len(self._seen) > 900:
                cut = now - 120
                self._seen = {k: v for k, v in self._seen.items() if v >= cut}
            self._history.append(ev)
            self._bump_stats(ev)

        if self.on_event_ui:
            try:
                self.on_event_ui(ev)
            except Exception:  # noqa: BLE001
                pass

        for h in list(self._subs):
            try:
                await h(ev)
            except Exception as exc:  # noqa: BLE001
                log.warning("事件处理器异常 %s: %s", getattr(h, "__name__", h), exc)
        return True

    def _bump_stats(self, ev: LiveEvent) -> None:
        s = self._stats
        if ev.kind == "comment":
            s.comment_count += 1
        elif ev.kind == "like":
            s.likes += max(1, ev.gift_count)
        elif ev.kind == "gift":
            s.gift_count += 1
            s.gift_value_total += max(1, ev.gift_value)
        elif ev.kind == "enter":
            s.online += 1
        elif ev.kind == "notice":
            try:
                s.online = int(ev.text)
            except (TypeError, ValueError):
                pass

    # ---------- 读取 ----------
    @property
    def stats(self) -> RoomStats:
        return self._stats

    def recent(self, seconds: float = 90, limit: int = 20, kinds: set[str] | None = None) -> list[LiveEvent]:
        cut = time.time() - seconds
        out = [e for e in self._history if e.ts >= cut]
        if kinds:
            out = [e for e in out if e.kind in kinds]
        return out[-limit:]

    def snapshot(self, limit: int = 60) -> list[dict[str, Any]]:
        return [e.to_dict() for e in list(self._history)[-limit:]]
