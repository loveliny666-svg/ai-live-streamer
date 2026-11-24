"""导演：控制写代码 / 摸鱼 / 老板来了 的场景切换与节奏。"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable

log = logging.getLogger("director")

SCENES = ("coding", "slack", "boss")


class Director:
    def __init__(self, cfg, on_scene: Callable[[str, str], Awaitable[None]]):
        self.cfg = cfg
        self.on_scene = on_scene
        self.scene = str(cfg.get("director.start_scene", "coding"))
        if self.scene not in SCENES:
            self.scene = "coding"
        self.since = time.time()
        self.until = self.since + self._duration(self.scene)
        self.last_switch = 0.0
        self._prev = "coding"
        self._task: asyncio.Task | None = None
        self._boss_task: asyncio.Task | None = None
        self.locked = False
        self.history: list[tuple[float, str, str]] = []

    # ---------- 时长 ----------
    def _duration(self, scene: str) -> float:
        key = "slack" if scene == "slack" else "coding"
        lo = float(self.cfg.get(f"director.{key}.min_sec", 180))
        hi = float(self.cfg.get(f"director.{key}.max_sec", 600))
        if hi <= lo:
            hi = lo + 60
        return random.uniform(lo, hi)

    def remaining(self) -> float:
        return max(0.0, self.until - time.time())

    def elapsed(self) -> float:
        return time.time() - self.since

    # ---------- 切换 ----------
    async def _apply(self, scene: str, reason: str) -> None:
        if scene == self.scene:
            return
        self._prev = self.scene if self.scene != "boss" else self._prev
        self.scene = scene
        self.since = time.time()
        self.until = self.since + (0 if scene == "boss" else self._duration(scene))
        self.last_switch = time.time()
        self.history.append((self.last_switch, scene, reason))
        self.history = self.history[-40:]
        log.info("场景 → %s（%s）", scene, reason)
        await self.on_scene(scene, reason)

    async def request(self, scene: str, reason: str = "智能体决定") -> bool:
        """智能体请求切场景：受冷却与开关约束。"""
        if scene not in SCENES:
            return False
        if scene == "boss":
            await self.boss_key(reason)
            return True
        if not self.cfg.get("director.agent_can_switch", True):
            return False
        cd = float(self.cfg.get("director.switch_cooldown_sec", 20))
        if time.time() - self.last_switch < cd:
            log.info("切换过快被拦（%.0fs < %.0fs）", time.time() - self.last_switch, cd)
            return False
        # 当前场景至少待够最短时长，除非智能体给的理由很急
        key = "slack" if self.scene == "slack" else "coding"
        min_sec = float(self.cfg.get(f"director.{key}.min_sec", 180))
        if self.elapsed() < min_sec * 0.6 and "摸鱼" not in reason:
            log.info("当前场景才 %.0fs，先不切", self.elapsed())
            return False
        await self._apply(scene, reason)
        return True

    async def force(self, scene: str, reason: str = "手动") -> bool:
        if scene not in SCENES:
            return False
        await self._apply(scene, reason)
        return True

    async def boss_key(self, reason: str = "老板来了") -> None:
        """紧急切回写代码，保持一小会儿。"""
        hold = float(self.cfg.get("director.boss_key_hold_sec", 12))
        await self._apply("boss", reason)
        if self._boss_task and not self._boss_task.done():
            self._boss_task.cancel()

        async def _back() -> None:
            try:
                await asyncio.sleep(hold)
                await self._apply("coding", "老板走了")
            except asyncio.CancelledError:
                pass

        self._boss_task = asyncio.create_task(_back())

    # ---------- 心跳 ----------
    async def run(self) -> None:
        log.info("导演启动，起始场景 %s", self.scene)
        while True:
            try:
                await asyncio.sleep(1.0)
                if self.scene == "boss":
                    continue
                if time.time() >= self.until:
                    nxt = "slack" if self.scene == "coding" else "coding"
                    await self._apply(nxt, "到点了，换换脑子")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("导演循环异常：%s", exc)

    def start(self) -> None:
        if not self._task or self._task.done():
            self._task = asyncio.create_task(self.run())

    def snapshot(self) -> dict:
        return {
            "scene": self.scene,
            "elapsed": round(self.elapsed(), 1),
            "remaining": round(self.remaining(), 1),
            "history": [
                {"ts": round(t, 1), "scene": s, "reason": r} for t, s, r in self.history[-8:]
            ],
        }
