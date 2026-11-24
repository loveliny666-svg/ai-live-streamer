"""弹幕源 · 模拟器（自检、演示、无网时兜底）。"""
from __future__ import annotations

import asyncio
import random

from ..models import LiveEvent, guess_gift_value
from .base import DanmakuSource

COMMENTS = [
    "主播在写什么呀",
    "这个代码看着好高级",
    "能讲讲刚才那个 bug 吗",
    "主播摸鱼被发现了",
    "声音好听",
    "植物大战僵尸打到第几波了",
    "老板真的会来吗",
    "我加班也在看这个",
    "这个需求改几次了",
    "求个表情包",
    "主播几点下播",
    "这个游戏我也想玩",
    "代码能跑吗",
    "666",
    "笑死，写代码写成这样",
]

GIFTS = [("小心心", 1, 1), ("玫瑰", 1, 1), ("甜甜圈", 5, 2), ("大啤酒", 2, 3), ("嘉年华", 30000, 1)]
USERS = ["老张", "夜猫子", "打工人小李", "秃头程序员", "隔壁老王", "产品经理本人", "摸鱼达人"]


class SimulatorSource(DanmakuSource):
    name = "simulator"
    label = "模拟弹幕"

    async def _run(self) -> None:
        interval = float(self.cfg.get("danmaku.sources.simulator.interval_sec", 12))
        await asyncio.sleep(4)
        n = 0
        while True:
            n += 1
            roll = random.random()
            user = random.choice(USERS)
            if roll < 0.15:
                name, cnt, val = random.choice(GIFTS)
                ev = LiveEvent(
                    kind="gift",
                    user=user,
                    gift_name=name,
                    gift_count=cnt,
                    gift_value=guess_gift_value(name, cnt) or val,
                    source="simulator",
                )
            elif roll < 0.22:
                ev = LiveEvent(kind="follow", user=user, source="simulator")
            elif roll < 0.3:
                ev = LiveEvent(kind="enter", user=user, source="simulator")
            else:
                ev = LiveEvent(kind="comment", user=user, text=random.choice(COMMENTS), source="simulator")
            await self._push(ev)
            self.ok = True
            await asyncio.sleep(interval * random.uniform(0.7, 1.5))
