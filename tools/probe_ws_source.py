"""探针：单独跑 WebSocket 源，验证事件映射。"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import CFG  # noqa: E402
from app.danmaku.ws_source import WsSource  # noqa: E402


async def main() -> None:
    seconds = float(sys.argv[1]) if len(sys.argv) > 1 else 40
    url = str(CFG.get("danmaku.room_url", "") or "").strip()
    t0 = time.time()

    async def emit(ev):
        print(f"[{time.time() - t0:6.2f}s] {ev.kind:<7} {ev.user!r} "
              f"text={ev.text!r} gift={ev.gift_name!r}x{ev.gift_count}")

    src = WsSource(CFG, emit, url)
    await src.start()
    deadline = time.time() + seconds
    while time.time() < deadline:
        await asyncio.sleep(5)
        s = src.snapshot()
        print(f"    · ok={s['ok']} 事件={s['count']} 帧={s['frames']} "
              f"在线={s['online']} err={s['last_error'][:40]!r}")
    await src.stop()
    s = src.snapshot()
    print(f"\n=== 结果：事件 {s['count']}，帧 {s['frames']}，在线 {s['online']} ===")


if __name__ == "__main__":
    asyncio.run(main())
