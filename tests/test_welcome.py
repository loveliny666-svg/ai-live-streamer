"""进场欢迎链路自检：从「观众进门」到「画面出现欢迎条」到底卡在哪。

这个测试盯的是三件事，任何一件挂了都意味着「有观众进来但没欢迎」：
  1. 昵称干不干净（「欢迎 老王： 来到直播间」是上不了台的）
  2. 去重窗口会不会把同一个人的二次进场吞掉
  3. 事件进总线之后，多久能到舞台页（走真实 WebSocket，不看模型脸色）
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

import aiohttp
import uvicorn

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.bus import Bus
from app.config import CFG
from app.danmaku.base import Router, classify_text, clean_user
from app.models import LiveEvent

PASS, FAIL = [], []
PORT = 8899


class MiniCfg:
    """只实现 Router 用到的 get()，方便把去重窗口调成几百毫秒。"""

    def __init__(self, data: dict):
        self.data = data

    def get(self, key: str, default=None):
        return self.data.get(key, default)


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""), flush=True)


async def test_names() -> None:
    print("\n[1] 昵称清洗")
    cases = [
        ("链路测试乙：来了", "enter", "链路测试乙"),
        ("←15达康 来了", "enter", "15达康"),
        ("· 小美 关注了主播", "follow", "小美"),
        ("老王：这需求我熟", "comment", "老王"),
        ("夜猫子 送出了 小心心 x3", "gift", "夜猫子"),
    ]
    for raw, kind, want in cases:
        ev = classify_text(raw)
        ok = bool(ev) and ev.kind == kind and ev.user == want
        check(f"{raw!r} → {want}", ok, "" if ok else f"得到 {getattr(ev, 'kind', None)}/{getattr(ev, 'user', None)}")
    check("空昵称兜底成「观众」", clean_user("  ：  ") == "观众")


async def test_dedupe() -> None:
    print("\n[2] 去重窗口（进场必须短）")
    cfg = MiniCfg({"danmaku.enter_dedupe_sec": 0.3, "danmaku.dedupe_window_sec": 25})

    bus = Bus(cfg=cfg)
    router = Router(cfg, bus)
    e1 = LiveEvent(kind="enter", user="乙")
    await router.handle(e1)
    await router.handle(LiveEvent(kind="enter", user="乙"))
    check("同一个观众 0.3 秒内重复进场只算一次", router.published == 1, f"published={router.published}")
    await asyncio.sleep(0.35)
    await router.handle(LiveEvent(kind="enter", user="乙"))
    check("过了窗口再来，算新的一次进场", router.published == 2, f"published={router.published}")

    bus2 = Bus(cfg=cfg)
    e = LiveEvent(kind="enter", user="丙")
    ok1 = await bus2.publish(e)
    ok2 = await bus2.publish(LiveEvent(kind="enter", user="丙"))
    check("总线：窗口内同一人二次进场算重复", ok1 and not ok2, f"{ok1}/{ok2}")
    await asyncio.sleep(0.35)
    ok3 = await bus2.publish(LiveEvent(kind="enter", user="丙"))
    check("总线：过了窗口算新的一次进场", ok3)


async def test_end_to_end() -> None:
    print("\n[3] 端到端：事件 → 舞台 WebSocket")
    from app.runtime import Runtime
    from app.stage.server import build_app

    rt = Runtime(CFG, PORT)
    app = build_app(rt)
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning", access_log=False)
    )
    task = asyncio.create_task(server.serve())
    for _ in range(60):
        if server.started:
            break
        await asyncio.sleep(0.1)
    check("测试服务已启动", server.started, f"port={PORT}")

    # 模型这条线彻底掐断：欢迎特效不许等它
    def _boom(*a, **kw):
        raise AssertionError("进场欢迎不该走模型")

    rt.agent.think = _boom  # type: ignore[assignment]

    async with aiohttp.ClientSession() as sess:
        ws = await sess.ws_connect(f"http://127.0.0.1:{PORT}/ws/stage")
        init = await ws.receive_json(timeout=5)
        check("舞台页收到 init", init.get("type") == "init")
        check("init 带欢迎配置", isinstance(init.get("welcome"), dict), str(init.get("welcome")))

        async def wait_type(kind: str, timeout: float = 3.0) -> dict | None:
            end = time.perf_counter() + timeout
            while time.perf_counter() < end:
                try:
                    msg = await ws.receive_json(timeout=max(0.05, end - time.perf_counter()))
                except asyncio.TimeoutError:
                    return None
                if msg.get("type") == kind:
                    return msg
            return None

        lags = []
        for i in range(5):
            ev = LiveEvent(kind="enter", user=f"观众{i}")
            t0 = time.perf_counter()
            await rt.bus.publish(ev)
            msg = await wait_type("welcome")
            dt = (time.perf_counter() - t0) * 1000
            if msg:
                lags.append(dt)
            check(f"第 {i + 1} 位观众收到欢迎", bool(msg), f"{dt:.1f}ms  user={msg.get('user') if msg else '-'}")

        check("5 个人 5 条欢迎（没被去重吞）", len(lags) == 5, f"{len(lags)}/5")
        if lags:
            avg = sum(lags) / len(lags)
            print(f"  延迟：平均 {avg:.1f}ms  最大 {max(lags):.1f}ms")
            check("欢迎延迟 < 50ms", max(lags) < 50, f"max={max(lags):.1f}ms")

        # 关掉开关就不该再发
        CFG.set("stage.welcome.enabled", False)
        await rt.bus.publish(LiveEvent(kind="enter", user="关掉之后"))
        got = await wait_type("welcome", 0.6)
        check("stage.welcome.enabled=false 时不发", got is None)
        CFG.set("stage.welcome.enabled", True)

        await ws.close()

    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=5)
    except Exception:  # noqa: BLE001
        task.cancel()


async def main() -> None:
    await test_names()
    await test_dedupe()
    await test_end_to_end()

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
