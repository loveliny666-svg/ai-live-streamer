"""启动入口：python -m app [参数]"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

import uvicorn

from .config import CFG
from .logging_setup import setup
from .runtime import Runtime
from .stage.server import build_app

log = logging.getLogger("main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="AI 打工人直播间 · 无人值守版")
    p.add_argument("--port", type=int, default=None, help="服务端口，默认读 config.yaml")
    p.add_argument("--no-stage", action="store_true", help="不自动打开舞台窗口")
    p.add_argument("--no-live", action="store_true", help="不自动开播（只跑画面和互动）")
    p.add_argument("--sim", action="store_true", help="打开模拟弹幕，方便本地自测")
    p.add_argument("--scene", default=None, choices=["coding", "slack", "boss"], help="启动时的画面")
    p.add_argument("--log-level", default=None)
    return p.parse_args()


async def amain(args: argparse.Namespace) -> None:
    port = int(args.port or CFG.get("server.port", 8735))
    if args.sim:
        CFG.set("danmaku.sources.simulator.enabled", True)
        CFG.set("danmaku.sources.simulator.interval_sec", 9)
    if args.no_live:
        CFG.set("companion.auto_start_live", False)
    if args.scene:
        CFG.set("director.start_scene", args.scene)

    rt = Runtime(CFG, port)
    rt.stage_enabled = not args.no_stage
    app = build_app(rt)
    server = uvicorn.Server(
        uvicorn.Config(
            app,
            host=str(CFG.get("server.host", "127.0.0.1")),
            port=port,
            log_level=(args.log_level or "info").lower(),
            access_log=False,
            timeout_graceful_shutdown=3,
        )
    )

    async def boot() -> None:
        # 等服务起来再开舞台，避免窗口白屏
        for _ in range(30):
            if server.started:
                break
            await asyncio.sleep(0.2)
        try:
            await rt.boot()
        except Exception as exc:  # noqa: BLE001
            log.exception("运行时启动失败：%s", exc)
        if not args.no_stage:
            try:
                await rt.stage.ensure()
            except Exception as exc:  # noqa: BLE001
                log.error("打开舞台窗口失败：%s", exc)
                rt.notes.append(f"舞台窗口打不开：{exc}")

    task = asyncio.create_task(boot())
    log.info("控制台：http://127.0.0.1:%d/admin", port)
    log.info("舞台页：http://127.0.0.1:%d/stage", port)
    try:
        await server.serve()
    finally:
        task.cancel()
        await rt.shutdown()
        log.info("已退出")


def main() -> None:
    from .companion import win as _win

    _win.enable_dpi_awareness()
    args = parse_args()
    CFG.reload()
    setup(CFG.data_dir / "logs", args.log_level or str(CFG.get("app.log_level", "INFO")))
    try:
        asyncio.run(amain(args))
    except KeyboardInterrupt:
        pass
    except Exception as exc:  # noqa: BLE001
        log.exception("启动失败：%s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
