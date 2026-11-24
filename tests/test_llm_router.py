"""三层模型路由自测：云端 → 本地 → 规则脑，逐级降级不能断。

不联网、不花钱、不需要装 Ollama。用 StubClient 模拟两层的正常与故障。
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.llm.agent import Agent, Memory, clean_local_text  # noqa: E402
from app.llm.client import LLMError, LLMResult, ToolCall  # noqa: E402
from app.llm.router import ModelRouter, Route  # noqa: E402
from app.models import LiveEvent  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""))


class FakeCfg:
    """独立配置，避免污染全局 CFG。"""

    def __init__(self, over: dict | None = None, **kw):
        self.data = {
            "llm": {
                "rpm": 9,
                "min_interval_sec": 0,
                "fail_threshold": 3,
                "recover_probe_sec": 90,
                "max_tool_rounds": 2,
                "rate_limit_cooldown_sec": 60,
                "context_events": 14,
                "local": {
                    "enabled": True,
                    "strategy": "spill",
                    "spill_ratio": 0.4,
                    "fail_threshold": 2,
                    "recover_probe_sec": 60,
                    "temperature": 0.9,
                    "max_tokens": 120,
                },
            },
            "behavior": {"reply_max_chars": 55, "idle_talk_sec": 32},
            "persona": {"name": "小饭", "role": "程序员", "style": "口语", "catchphrases": []},
        }
        merged = dict(over or {})
        merged.update(kw)
        for k, v in merged.items():
            cur = self.data
            parts = k.split(".")
            for p in parts[:-1]:
                cur = cur.setdefault(p, {})
            cur[parts[-1]] = v

    def get(self, dotted: str, default=None):
        cur = self.data
        for p in dotted.split("."):
            if not isinstance(cur, dict) or p not in cur:
                return default
            cur = cur[p]
        return cur if cur is not None else default

    def set(self, dotted: str, value):
        cur = self.data
        parts = dotted.split(".")
        for p in parts[:-1]:
            cur = cur.setdefault(p, {})
        cur[parts[-1]] = value


class StubClient:
    def __init__(self, script: list, ready: bool = True, label: str = "stub"):
        self.script = script
        self.ready = ready
        self.label = label
        self.calls = 0
        self.last_messages = None
        self.model = f"{label}-model"

    async def health(self) -> bool:
        return True

    def snapshot(self) -> dict:
        return {"label": self.label, "ready": self.ready, "model": self.model}

    async def chat(self, messages, tools=None, **kw):
        self.last_messages = messages
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


def make_agent(cfg, cloud, local=None, sink=None):
    hits = sink if sink is not None else []

    async def ex(name, args):
        hits.append((name, args))
        return "ok"

    mem = Memory(ROOT / "data" / "test-router-memory.json")
    return Agent(cfg, cloud, mem, ex, lambda: {"scene": "coding", "online": 5}, local=local), hits


def test_complexity() -> None:
    print("\n[1] 任务复杂度判定")
    cfg = FakeCfg()
    r = ModelRouter(cfg)

    simple = [LiveEvent(kind="comment", user="张三", text="主播在干嘛")]
    check("单条闲聊 = simple", r.complexity(simple) == "simple", r.complexity(simple))

    order = [LiveEvent(kind="comment", user="张三", text="写个贪吃蛇")]
    check("观众点单 = complex", r.complexity(order) == "complex", r.complexity(order))

    big = [LiveEvent(kind="gift", user="土豪", gift_name="火箭", gift_value=5000)]
    check("大礼物 = complex", r.complexity(big) == "complex", r.complexity(big))

    many = [LiveEvent(kind="comment", user=f"u{i}", text="哈") for i in range(7)]
    check("七条事件 = complex", r.complexity(many) == "complex", r.complexity(many))

    small_gift = [LiveEvent(kind="gift", user="路人", gift_name="小心心", gift_value=1)]
    check("小礼物 = simple", r.complexity(small_gift) == "simple", r.complexity(small_gift))


def test_spill() -> None:
    print("\n[2] spill 策略：额度见底才溢出")
    cfg = FakeCfg()
    r = ModelRouter(cfg)
    ev = [LiveEvent(kind="comment", user="张三", text="在吗")]

    base = dict(cloud_ready=True, cloud_ok=True, local_ready=True, events=ev)

    check("额度 100% + 简单 → 云端", r.decide(quota_ratio=1.0, **base) is Route.CLOUD)
    check("额度 50% + 简单 → 云端", r.decide(quota_ratio=0.5, **base) is Route.CLOUD)
    check("额度 30% + 简单 → 本地", r.decide(quota_ratio=0.3, **base) is Route.LOCAL)
    check("额度 0% + 简单 → 本地", r.decide(quota_ratio=0.0, **base) is Route.LOCAL)

    hard = dict(base, events=[LiveEvent(kind="comment", user="张三", text="写个扫雷")])
    check("额度 10% + 复杂 → 仍走云端", r.decide(quota_ratio=0.1, **hard) is Route.CLOUD)


def test_split() -> None:
    print("\n[3] split 策略：简单任务直接给本地")
    cfg = FakeCfg(**{"llm.local.strategy": "split"})
    r = ModelRouter(cfg)
    ev = [LiveEvent(kind="comment", user="张三", text="在吗")]

    check(
        "额度充足 + 简单 → 本地",
        r.decide(cloud_ready=True, cloud_ok=True, quota_ratio=1.0, local_ready=True, events=ev)
        is Route.LOCAL,
    )
    check(
        "额度充足 + 复杂 → 云端",
        r.decide(
            cloud_ready=True, cloud_ok=True, quota_ratio=1.0, local_ready=True,
            events=[LiveEvent(kind="comment", user="张三", text="写个扫雷")],
        )
        is Route.CLOUD,
    )


def test_fallbacks() -> None:
    print("\n[4] 逐级降级：本地没开 → 规则脑")
    cfg = FakeCfg()
    r = ModelRouter(cfg)
    ev = [LiveEvent(kind="comment", user="张三", text="在吗")]

    check(
        "云端不可用 + 本地可用 → 本地",
        r.decide(cloud_ready=False, cloud_ok=True, quota_ratio=1.0, local_ready=True, events=ev)
        is Route.LOCAL,
    )
    check(
        "云端不可用 + 本地不可用 → 规则脑",
        r.decide(cloud_ready=False, cloud_ok=True, quota_ratio=1.0, local_ready=False, events=ev)
        is Route.RULES,
    )
    check(
        "云端降级中 + 本地可用 → 本地",
        r.decide(cloud_ready=True, cloud_ok=False, quota_ratio=1.0, local_ready=True, events=ev)
        is Route.LOCAL,
    )

    cfg2 = FakeCfg(**{"llm.local.enabled": False})
    r2 = ModelRouter(cfg2)
    check(
        "本地被关掉 + 额度用尽 → 规则脑",
        r2.decide(cloud_ready=True, cloud_ok=True, quota_ratio=0.0, local_ready=True, events=ev)
        is Route.RULES,
    )


def test_clean_text() -> None:
    print("\n[5] 本地输出清洗")
    cases = [
        ("小饭：你好呀", "你好呀"),
        ("「这个需求又改了」", "这个需求又改了"),
        ("第一行\n第二行是解释", "第一行"),
        ("（挠头）这个嘛，能跑就行", "这个嘛，能跑就行"),
        ("  正常一句话  ", "正常一句话"),
        ("", ""),
    ]
    for raw, want in cases:
        got = clean_local_text(raw)
        check(f"清洗 {raw[:12]!r} → {want!r}", got == want, f"got={got!r}")


async def test_chain() -> None:
    print("\n[6] 完整链路：云端 429 → 本地接住")
    cfg = FakeCfg()
    cloud = StubClient([LLMError("HTTP 429: rate limit")], label="cloud")
    local = StubClient([LLMResult(text="小饭：来了来了，刚在改 bug")], label="local")
    agent, hits = make_agent(cfg, cloud, local)
    agent.fallback._last_at = 0

    await agent.think([LiveEvent(kind="comment", user="张三", text="在吗")], "有人说话")

    says = [h for h in hits if h[0] == "say"]
    check("云端试过一次", cloud.calls == 1, str(cloud.calls))
    check("本地被调用", local.calls == 1, str(local.calls))
    check("本地的话落地了", len(says) == 1, str(hits))
    check("前缀被清洗掉", says and says[0][1]["text"] == "来了来了，刚在改 bug", str(says))
    check("云端进入降级", agent.degraded is True, agent.degraded_reason)
    check("统计记了本地说话", agent.stats["local_says"] == 1, str(agent.stats))

    print("\n[7] 完整链路：云端挂了 + 本地也挂了 → 规则脑")
    cfg2 = FakeCfg()
    cloud2 = StubClient([LLMError("HTTP 500: boom")], label="cloud")
    local2 = StubClient([LLMError("connection refused")], label="local")
    agent2, hits2 = make_agent(cfg2, cloud2, local2)
    agent2.fallback._last_at = 0

    await agent2.think([LiveEvent(kind="gift", user="李四", gift_name="小心心", gift_value=1)], "送礼")

    check("本地试过了", local2.calls == 1, str(local2.calls))
    check("规则脑接住了", agent2.stats["fallback_says"] >= 1, str(agent2.stats))
    check("记了本地失败", agent2.local_fails >= 1, str(agent2.local_fails))
    check("有输出落地", len(hits2) >= 1, str(hits2))

    print("\n[8] 本地连续失败两次后暂时不再尝试")
    cfg3 = FakeCfg()
    cloud3 = StubClient([LLMError("HTTP 500")], label="cloud")
    local3 = StubClient([LLMError("timeout")], label="local")
    agent3, _ = make_agent(cfg3, cloud3, local3)

    for _ in range(2):
        agent3.fallback._last_at = 0
        agent3.last_call = 0
        await agent3.think([LiveEvent(kind="comment", user="张三", text="喂")], "有人说话")

    check("本地失败计数到 2", agent3.local_fails >= 2, str(agent3.local_fails))
    check("本地进入冷却", not agent3._local_usable(), "should be cooling")
    before = local3.calls
    agent3.last_call = 0
    await agent3.think([LiveEvent(kind="comment", user="张三", text="还在吗")], "有人说话")
    check("冷却期内不再打本地", local3.calls == before, f"{before} → {local3.calls}")

    print("\n[9] 云端额度耗尽 → 直接走本地")
    cfg4 = FakeCfg()
    cloud4 = StubClient([LLMResult(tool_calls=[ToolCall("c1", "say", {"text": "云端说的"})])], label="cloud")
    local4 = StubClient([LLMResult(text="本地说的")], label="local")
    agent4, hits4 = make_agent(cfg4, cloud4, local4)

    now = time.time()
    for _ in range(9):
        agent4._call_times.append(now)
    await agent4.think([LiveEvent(kind="comment", user="张三", text="在吗")], "有人说话")

    says = [h for h in hits4 if h[0] == "say"]
    check("云端一次没调", cloud4.calls == 0, str(cloud4.calls))
    check("本地接住了", local4.calls == 1, str(local4.calls))
    check("说的是本地的话", says and says[0][1]["text"] == "本地说的", str(says))

    print("\n[10] 热重载后状态复位")
    agent4.degraded = True
    agent4.local_fails = 3
    agent4.reset_degraded()
    check("云端降级复位", agent4.degraded is False)
    check("本地失败清零", agent4.local_fails == 0)


async def main() -> None:
    test_complexity()
    test_spill()
    test_split()
    test_fallbacks()
    test_clean_text()
    await test_chain()

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
