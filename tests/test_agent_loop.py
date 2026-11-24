"""智能体循环的单元测试（不联网、不花钱）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import CFG
from app.llm.agent import Agent, Memory
from app.llm.client import LLMError, LLMResult, ToolCall
from app.models import LiveEvent

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"  {extra}" if extra else ""))


class StubClient:
    def __init__(self, script: list):
        self.script = script
        self.ready = True
        self.calls = 0
        self.last_messages = None
        self.snapshot = lambda: {"provider": "stub", "ready": True, "why_not": ""}
        self.health = self._health

    async def _health(self) -> bool:
        return True

    async def chat(self, messages, tools=None, **kw):
        self.last_messages = messages
        item = self.script[min(self.calls, len(self.script) - 1)]
        self.calls += 1
        if isinstance(item, Exception):
            raise item
        return item


async def main() -> None:
    print("\n[1] 工具调用 → say 落地")
    seen: list[tuple[str, dict]] = []

    async def ex(name, args):
        seen.append((name, args))
        return "ok"

    client = StubClient(
        [LLMResult(tool_calls=[ToolCall("c1", "say", {"text": "你好，我在改 bug", "emotion": "happy"})])]
    )
    mem = Memory(ROOT / "data" / "test-memory.json")
    agent = Agent(CFG, client, mem, ex, lambda: {"scene": "coding", "online": 3, "uptime": "00:01:00"})
    await agent.think([LiveEvent(kind="comment", user="张三", text="在干嘛")], "有人说话")
    check("say 被调用一次", len(seen) == 1 and seen[0][0] == "say", str(seen))
    check("文本正确", seen and seen[0][1].get("text") == "你好，我在改 bug")
    check("last_said 已更新", agent.last_said.startswith("你好"))

    print("\n[2] 一轮里多个 say 只取第一条")
    seen.clear()
    client2 = StubClient(
        [
            LLMResult(
                tool_calls=[
                    ToolCall("c1", "say", {"text": "第一句"}),
                    ToolCall("c2", "say", {"text": "第二句"}),
                ]
            )
        ]
    )
    agent2 = Agent(CFG, client2, mem, ex, lambda: {"scene": "coding"})
    agent2.cfg.set("llm.min_interval_sec", 0)
    await agent2.think([LiveEvent(kind="gift", user="李四", gift_name="小心心")], "送礼")
    says = [s for s in seen if s[0] == "say"]
    check("只播一句", len(says) == 1 and says[0][1]["text"] == "第一句", str(says))

    print("\n[3] 超长文本被截断")
    seen.clear()
    long_text = "啊" * 200
    client3 = StubClient([LLMResult(tool_calls=[ToolCall("c1", "say", {"text": long_text})])])
    agent3 = Agent(CFG, client3, mem, ex, lambda: {"scene": "coding"})
    await agent3.think([LiveEvent(kind="comment", user="王五", text="说点啥")], "有人说话")
    said = seen[0][1]["text"] if seen else ""
    limit = int(CFG.get("behavior.reply_max_chars", 70))
    check("不超过上限", len(said) <= limit + 1, f"len={len(said)} limit={limit}")

    print("\n[4] 模型报错 → 兜底规则脑接住")
    seen.clear()
    client4 = StubClient([LLMError("模拟 401")])
    agent4 = Agent(CFG, client4, mem, ex, lambda: {"scene": "coding"})
    agent4.fallback._last_at = 0
    await agent4.think([LiveEvent(kind="comment", user="赵六", text="主播你好吗")], "有人说话")
    check("兜底有输出", len(seen) >= 1, str(seen))
    check("记了一次失败", agent4.consecutive_fails >= 1, str(agent4.consecutive_fails))

    print("\n[5] 连续失败达到阈值 → 进入降级")
    seen.clear()
    agent4.cfg.set("llm.min_interval_sec", 0)
    for _ in range(int(CFG.get("llm.fail_threshold", 3))):
        agent4.fallback._last_at = 0
        await agent4.think([LiveEvent(kind="comment", user="赵六", text="再来")], "有人说话")
    check("已降级", agent4.degraded is True)

    print("\n[6] 切换场景工具")
    seen.clear()
    hits: list[str] = []

    async def ex2(name, args):
        hits.append(name)
        return "切换成功"

    client5 = StubClient([LLMResult(tool_calls=[ToolCall("c1", "switch_scene", {"scene": "slack", "reason": "写累了"})])])
    agent5 = Agent(CFG, client5, mem, ex2, lambda: {"scene": "coding"})
    agent5.cfg.set("llm.min_interval_sec", 0)
    await agent5.think([], "安静")
    check("调用了 switch_scene", "switch_scene" in hits, str(hits))

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + ", ".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
