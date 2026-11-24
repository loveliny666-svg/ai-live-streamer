"""模型 RPM 闸门自测：不该让程序自己把平台限额撞爆。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import CFG  # noqa: E402
from app.llm.agent import Agent  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""))


def main() -> None:
    rpm = int(CFG.get("llm.rpm", 9))
    print(f"配置的 RPM = {rpm}")
    check("RPM 不超过平台免费额度 10", rpm <= 10, str(rpm))
    check("RPM 留了余量（<10）", rpm < 10, str(rpm))

    agent = Agent(CFG, None, None, None, None)  # type: ignore[arg-type]
    now = time.time()

    for i in range(rpm):
        agent._call_times.append(now - 5)
    check("打满 rpm 后不再调用", not agent._rpm_ok(now), f"{len(agent._call_times)} 次")

    # 一分钟窗口滑过之后应该恢复
    check("一分钟窗口滑过后恢复", agent._rpm_ok(now + 61))

    # 超过窗口的旧记录会被清掉
    agent._call_times = [now - 120] * 20
    check("旧记录被清理", agent._rpm_ok(now), f"剩余 {len(agent._call_times)}")

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    main()
