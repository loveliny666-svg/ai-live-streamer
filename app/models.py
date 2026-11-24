"""统一事件模型。所有输入源都把原始数据归一化成 LiveEvent。"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field, asdict
from typing import Any, Literal

EventKind = Literal["comment", "gift", "like", "enter", "follow", "system", "notice"]

# 抖币 → 人民币大约 1:0.1，这里只用于内部排序，不对外展示
GIFT_VALUE_HINT = {
    "小心心": 1,
    "玫瑰": 1,
    "粉丝团灯牌": 1,
    "棒棒糖": 1,
    "你很棒": 1,
    "大啤酒": 2,
    "甜甜圈": 5,
    "炸酱面": 5,
    "爱心": 5,
    "小心心x10": 10,
    "跑车": 1000,
    "直升机": 3000,
    "火箭": 5000,
    "嘉年华": 30000,
    "梦幻城堡": 8000,
}


@dataclass
class LiveEvent:
    kind: EventKind
    user: str = "观众"
    text: str = ""
    ts: float = field(default_factory=time.time)
    gift_name: str = ""
    gift_count: int = 1
    gift_value: int = 0
    source: str = "unknown"
    raw: dict[str, Any] = field(default_factory=dict)
    eid: str = ""

    def __post_init__(self) -> None:
        if not self.eid:
            basis = f"{self.kind}|{self.user}|{self.text}|{self.gift_name}|{self.gift_count}"
            self.eid = hashlib.md5(basis.encode("utf-8", "ignore")).hexdigest()[:16]

    # ---------- 序列化 ----------
    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_public(self) -> dict[str, Any]:
        """给前端/模型的精简版，省 token。"""
        d = {
            "kind": self.kind,
            "user": self.user,
            "ts": round(self.ts, 1),
        }
        if self.text:
            d["text"] = self.text
        if self.gift_name:
            d["gift"] = self.gift_name
            if self.gift_count > 1:
                d["count"] = self.gift_count
        return d

    # ---------- 语义辅助 ----------
    @property
    def is_big_gift(self) -> bool:
        return self.gift_value >= 50 or self.gift_count >= 5

    @property
    def speakable(self) -> bool:
        """是否值得为它开口说话。"""
        if self.kind in ("comment", "follow"):
            return bool(self.text) or self.kind == "follow"
        if self.kind == "gift":
            return self.gift_value >= 1
        if self.kind == "enter":
            return self.raw.get("vip", False)
        return False

    def describe(self) -> str:
        """给模型看的一行人话。"""
        if self.kind == "comment":
            return f"[弹幕] {self.user}：{self.text}"
        if self.kind == "gift":
            cnt = f"x{self.gift_count}" if self.gift_count > 1 else ""
            return f"[礼物] {self.user} 送出 {self.gift_name}{cnt}"
        if self.kind == "enter":
            return f"[进场] {self.user} 进入直播间"
        if self.kind == "follow":
            return f"[关注] {self.user} 关注了主播"
        if self.kind == "like":
            return f"[点赞] {self.user} 点了赞"
        return f"[{self.kind}] {self.user} {self.text}"


def guess_gift_value(name: str, count: int = 1) -> int:
    for key, val in GIFT_VALUE_HINT.items():
        if key and key in name:
            return val * max(1, count)
    return max(1, count)


@dataclass
class RoomStats:
    """房间实时状态，来自弹幕事件汇总 + 直播伴侣 OCR。"""

    online: int = 0
    likes: int = 0
    gift_value_total: int = 0
    gift_count: int = 0
    comment_count: int = 0
    started_at: float = field(default_factory=time.time)

    def uptime_text(self) -> str:
        sec = int(time.time() - self.started_at)
        h, rem = divmod(sec, 3600)
        m, s = divmod(rem, 60)
        return f"{h:02d}:{m:02d}:{s:02d}"
