"""开发用：只起一个带假数据的舞台服务（端口 8899），供截图/联调。"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from app.stage.server import build_app  # noqa: E402
from tools.preview_stage import StubRuntime  # noqa: E402

async def _demo_loop(rt):
    """给预览页喂假数据，方便调版式。"""
    import asyncio
    import time

    n = 0
    while True:
        await asyncio.sleep(3)
        n += 1
        typing = rt.scene != "slack"
        await rt.hub.broadcast_stage(
            {
                "type": "stats",
                "online": 37,
                "likes": 12480,
                "gifts": 860,
                "focus_text": "02:31:07",
                "slack": 3,
                "caught": 1,
                "lines": min(2400, 1180 + n * 3),
                "goal": 2400,
                "speed": 1.3,
                "countdown": max(0, 192 - n * 3),
                "typing": typing,
            }
        )


if __name__ == "__main__":
    rt = StubRuntime()
    rt.scene = sys.argv[1] if len(sys.argv) > 1 else "coding"
    app = build_app(rt)

    @app.on_event("startup")
    async def _start():
        import asyncio

        asyncio.create_task(_demo_loop(rt))

    uvicorn.run(app, host="127.0.0.1", port=8899, log_level="warning")
