"""三层模型路由：云端 API / 本地 Ollama / 规则脑。

设计目标不是「省钱」，而是「别让直播间因为配额限制变成复读机」。
免费用户 10 RPM，一场两小时的直播如果全走云端，大概有 90% 的互动
是喂给模板随机数的。本地模型接管这部分，成本是零，质量比模板高一个量级。

优先级：
    cloud  —— 需要动工具、需要脑子的活（写项目、大礼物、多事件混战）
    local  —— 单条弹幕、道谢、欢迎、自言自语这类一句话的事
    rules  —— 前面两层都趴下时的最后防线，保证永远有声音

两种策略（llm.local.strategy）：
    spill —— 默认云端，云端额度见底才溢出到本地。质量优先，保守。
    split —— 简单任务直接给本地，云端额度全留给复杂任务。省额度，激进。
"""
from __future__ import annotations

import logging
from enum import Enum

log = logging.getLogger("router")


class Route(str, Enum):
    CLOUD = "cloud"
    LOCAL = "local"
    RULES = "rules"


# 观众点单类需求：要调 write_project，本地小模型搞不定
_ORDER_HINTS = (
    "写个", "写一个", "写一下", "做个", "做一个", "做一下", "来一个",
    "生成一个", "帮我写", "帮我做", "画个", "画一个", "来个",
)

# 真提问（需要组织答案），比纯闲聊难
_QUESTION_HINTS = (
    "怎么", "为什么", "为啥", "是什么", "能不能", "可以吗", "如何",
    "推荐", "哪个好", "多少钱", "有没有",
)


class ModelRouter:
    """只看输入和额度，不碰网络。决策是纯函数，方便单测。"""

    def __init__(self, cfg):
        self.cfg = cfg

    # ---------------- 开关 ----------------
    def local_enabled(self) -> bool:
        return bool(self.cfg.get("llm.local.enabled", False))

    def strategy(self) -> str:
        s = str(self.cfg.get("llm.local.strategy", "spill") or "spill").lower()
        return s if s in ("spill", "split") else "spill"

    def spill_ratio(self) -> float:
        try:
            r = float(self.cfg.get("llm.local.spill_ratio", 0.4))
        except (TypeError, ValueError):
            r = 0.4
        return min(0.95, max(0.0, r))

    # ---------------- 任务复杂度 ----------------
    def complexity(self, events: list, reason: str = "") -> str:
        """simple / complex。分数只用来分流，不需要很精确。"""
        score = 0
        n = len(events)
        if n >= 6:
            # 一次涌进来六条以上，得先判断优先级再决定回谁，本地小模型容易挑错
            score += 4
        elif n >= 3:
            score += 1

        for ev in events:
            kind = getattr(ev, "kind", "")
            if kind == "gift" and getattr(ev, "gift_value", 0) >= 50:
                # 大礼物要「说话 + 特效 + 老板键」组合拳，本地做不好
                score += 4
            elif kind == "comment":
                text = str(getattr(ev, "text", "") or "")
                if any(h in text for h in _ORDER_HINTS):
                    score += 6
                elif len(text) >= 30:
                    score += 1
                elif any(h in text for h in _QUESTION_HINTS):
                    score += 1

        return "complex" if score >= 4 else "simple"

    # ---------------- 主决策 ----------------
    def decide(
        self,
        *,
        cloud_ready: bool,
        cloud_ok: bool,
        quota_ratio: float,
        local_ready: bool,
        events: list,
        reason: str = "",
    ) -> Route:
        """返回这一轮该由谁来说话。

        cloud_ready  —— 有 key、provider 不是 mock
        cloud_ok     —— 没在降级冷却里
        quota_ratio  —— 本分钟剩余额度占比，1.0 表示一次没用
        local_ready  —— Ollama 在线且配置启用
        """
        usable_local = self.local_enabled() and local_ready
        cloud_usable = cloud_ready and cloud_ok and quota_ratio > 0

        # 云端彻底没戏 → 本地顶上
        if not cloud_usable:
            return Route.LOCAL if usable_local else Route.RULES

        # 本地没开/挂了 → 只能云端，额度用尽就规则脑
        if not usable_local:
            return Route.CLOUD

        level = self.complexity(events, reason)

        if self.strategy() == "split":
            # 分流：复杂留给云端，简单交给本地
            return Route.CLOUD if level == "complex" else Route.LOCAL

        # spill：默认云端，额度水位低于阈值时把简单的溢出到本地
        if level == "complex":
            return Route.CLOUD
        return Route.CLOUD if quota_ratio > self.spill_ratio() else Route.LOCAL

    # ---------------- 说明（给前端/日志看） ----------------
    def explain(self, route: Route, events: list, reason: str = "") -> str:
        level = self.complexity(events, reason)
        return {
            Route.CLOUD: f"云端（{level}）",
            Route.LOCAL: f"本地（{level}）",
            Route.RULES: "规则脑",
        }.get(route, str(route))
