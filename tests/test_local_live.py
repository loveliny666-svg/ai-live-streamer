"""真实本地模型端到端测试（需要 Ollama 在跑、模型已拉取）。

和 test_llm_router.py 的分工：
    那个用 StubClient 验证「逻辑对不对」，不碰网络，随时能跑。
    这个用真 Ollama 验证「接上去能不能用」，没有环境就跳过，不算失败。

用法：
    python tests/test_local_live.py
    python tests/test_local_live.py --model qwen3:4b
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config  # noqa: E402
from app.llm.agent import Agent, Memory  # noqa: E402
from app.llm.client import LLMClient  # noqa: E402
from app.models import LiveEvent  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""))


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="")
    args = ap.parse_args()

    cfg = Config()
    if args.model:
        cfg.set("llm.local.model", args.model)
    cfg.set("llm.provider", "mock")      # 云端故意弄成不可用，强制走本地
    cfg.set("llm.local.enabled", True)
    cfg.set("llm.min_interval_sec", 0)

    cloud = LLMClient(cfg)
    local = LLMClient(cfg, "llm.local")

    print("=" * 52)
    print("  本地模型端到端")
    print("=" * 52)
    print(f"  模型：{local.model}")
    print(f"  地址：{local.base_url}")
    print()

    if not await local.health():
        print("  本地模型不可用，跳过。先确认：")
        print("    1. 装好 Ollama 并让它跑起来")
        print(f"    2. ollama pull {local.model}")
        print(f"    3. python tools/check_local_llm.py")
        return 0

    check("本地模型健康检查通过", True)

    said: list[tuple[str, dict]] = []

    async def ex(name, args):
        said.append((name, args))
        return "ok"

    mem = Memory(ROOT / "data" / "test-local-memory.json")
    agent = Agent(
        cfg, cloud, mem, ex,
        lambda: {"scene": "coding", "online": 8, "uptime": "00:12:00"},
        local=local,
    )

    print("\n[1] 单条弹幕 → 本地接住")
    t0 = time.perf_counter()
    await agent.think([LiveEvent(kind="comment", user="张三", text="主播在干嘛呢")], "有人说话")
    cost = time.perf_counter() - t0

    says = [s for s in said if s[0] == "say"]
    check("说出了话", len(says) == 1, str(says)[:100])
    check("走的是本地", agent.stats["local_says"] == 1, str(agent.stats))
    check("没落到规则脑", agent.stats["fallback_says"] == 0, str(agent.stats))
    check("延迟可接受（<5s）", cost < 5, f"{cost:.2f}s")
    if says:
        text = says[0][1]["text"]
        check("文本长度合理", 2 <= len(text) <= 60, f"{len(text)} 字")
        check("没有思考残留", "think" not in text.lower(), text[:40])
        print(f"      模型说：{text}")
        print(f"      情绪：{says[0][1].get('emotion')}  对象：{says[0][1].get('to') or '（无）'}")

    print("\n[2] 关注事件 → 本地会道谢")
    said.clear()
    agent.last_call = 0
    await agent.think([LiveEvent(kind="follow", user="李四")], "有人关注")
    says = [s for s in said if s[0] == "say"]
    check("关注有回应", len(says) == 1, str(says)[:100])
    if says:
        print(f"      模型说：{says[0][1]['text']}")

    print("\n[3] 礼物事件 → 本地会道谢")
    said.clear()
    agent.last_call = 0
    await agent.think(
        [LiveEvent(kind="gift", user="王五", gift_name="小心心", gift_value=1)], "有人送礼"
    )
    says = [s for s in said if s[0] == "say"]
    check("礼物有回应", len(says) == 1, str(says)[:100])
    if says:
        print(f"      模型说：{says[0][1]['text']}")

    print("\n[4] 连续 5 轮不崩、不复读")
    said.clear()
    ok_rounds = 0
    texts: list[str] = []
    for i in range(5):
        agent.last_call = 0
        before = agent.stats["local_says"]
        said.clear()
        await agent.think(
            [LiveEvent(kind="comment", user=f"观众{i}", text=f"第{i}个问题，主播累不累")],
            "有人说话",
        )
        if agent.stats["local_says"] > before:
            ok_rounds += 1
            texts.append(said[-1][1]["text"] if said else "")
    check("五轮都说话了", ok_rounds == 5, f"{ok_rounds}/5")
    uniq = len(set(texts))
    check("没有大量复读", uniq >= 4, f"{uniq}/5 句不同")
    for t in texts:
        print(f"      {t}")
    check("全程没降级", agent.local_fails == 0, f"失败 {agent.local_fails} 次")

    print("\n[5] 配额耗尽时直接走本地，不碰云端")
    said.clear()
    agent.last_call = 0
    now = time.time()
    for _ in range(int(cfg.get("llm.rpm", 9))):
        agent._call_times.append(now)
    await agent.think([LiveEvent(kind="comment", user="赵六", text="还在吗")], "有人说话")
    says = [s for s in said if s[0] == "say"]
    check("额度满了仍能说话", len(says) == 1, str(says)[:80])

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        pass
    sys.exit(asyncio.run(main()))
