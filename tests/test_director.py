"""导演状态机测试：自动轮换、冷却、老板键。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import Config
from app.director import Director

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""), flush=True)


async def main() -> None:
    cfg = Config(ROOT / "config.yaml")
    cfg.set("director.coding.min_sec", 1)
    cfg.set("director.coding.max_sec", 2)
    cfg.set("director.slack.min_sec", 1)
    cfg.set("director.slack.max_sec", 2)
    cfg.set("director.switch_cooldown_sec", 3)
    cfg.set("director.boss_key_hold_sec", 1.2)

    changes: list[tuple[str, str]] = []

    async def on_scene(scene: str, reason: str) -> None:
        changes.append((scene, reason))

    d = Director(cfg, on_scene)
    d.start()
    print("\n[1] 自动轮换（喂了 1-2 秒的短周期）")
    await asyncio.sleep(7)
    scenes = [c[0] for c in changes]
    print("  变化序列：", scenes)
    check("至少切换过 2 次", len(changes) >= 2, str(len(changes)))
    check("写代码与摸鱼都出现过", "slack" in scenes and "coding" in scenes, str(scenes))

    print("\n[2] 冷却期内的切换请求应被拒绝")
    d2 = Director(cfg, on_scene)
    d2.start()
    await asyncio.sleep(0.2)
    ok1 = await d2.request("slack", "想摸鱼")
    ok2 = await d2.request("slack", "再摸一次")
    check("第一次可以切", ok1, str(ok1))
    check("紧接着再切被拦", ok2 is False, str(ok2))

    print("\n[3] 老板键：切回写代码并在 hold 秒后回 coding")
    d3 = Director(cfg, on_scene)
    seen: list[str] = []

    async def on3(scene: str, reason: str) -> None:
        seen.append(scene)

    d3.on_scene = on3
    await d3.force("slack", "先摸鱼")
    await d3.boss_key("老板来了")
    check("立刻进入 boss", d3.scene == "boss", d3.scene)
    await asyncio.sleep(2.0)
    check("hold 之后回到 coding", d3.scene == "coding", d3.scene)
    check("场景回调顺序正确", seen[:3] == ["slack", "boss", "coding"] or seen[-1] == "coding", str(seen))

    print("\n[4] 非法场景被拒")
    check("拒绝未知场景", (await d3.force("nope")) is False)

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
