"""FastAPI 服务：舞台页、控制台、WebSocket 指令通道、TTS 音频。"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from ..logging_setup import RING

log = logging.getLogger("server")
STATIC = Path(__file__).parent / "static"


class StageHub:
    """管理舞台页与控制台的 WebSocket 连接。"""

    def __init__(self) -> None:
        self.stage: set[WebSocket] = set()
        self.admin: set[WebSocket] = set()
        self.last_stage_seen: float = 0.0
        self.last_stage_hb: float = 0.0
        self.stage_ready = asyncio.Event()

    @property
    def stage_online(self) -> bool:
        return bool(self.stage)

    async def broadcast_stage(self, msg: dict[str, Any]) -> None:
        dead = []
        for ws in list(self.stage):
            try:
                await ws.send_text(json.dumps(msg, ensure_ascii=False))
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.stage.discard(ws)

    async def broadcast_admin(self, msg: dict[str, Any]) -> None:
        payload = json.dumps(msg, ensure_ascii=False)
        dead = []
        for ws in list(self.admin):
            try:
                await ws.send_text(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self.admin.discard(ws)

    async def push_event(self, ev) -> None:
        """把弹幕事件同步到画面右下角的弹幕栏。"""
        await self.broadcast_stage(
            {
                "type": "dm",
                "kind": ev.kind,
                "user": ev.user,
                "text": ev.text,
                "gift": ev.gift_name,
                "count": ev.gift_count,
            }
        )


def build_app(runtime) -> FastAPI:
    app = FastAPI(title="AI 打工人直播间", docs_url=None, redoc_url=None)
    hub: StageHub = runtime.hub

    @app.middleware("http")
    async def _no_cache_static(request: Request, call_next):
        resp = await call_next(request)
        if request.url.path.startswith("/static"):
            resp.headers["Cache-Control"] = "no-store, max-age=0"
        return resp

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    audio_dir: Path = runtime.cfg.resolve("audio")
    audio_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/audio", StaticFiles(directory=str(audio_dir)), name="audio")

    # ---------------- 页面 ----------------
    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse("/stage")

    @app.get("/stage")
    async def stage() -> FileResponse:
        return FileResponse(STATIC / "stage.html", media_type="text/html; charset=utf-8")

    @app.get("/admin")
    async def admin() -> HTMLResponse:
        return HTMLResponse((STATIC / "dashboard.html").read_text(encoding="utf-8"))

    @app.get("/healthz")
    async def healthz() -> dict[str, Any]:
        return {"ok": True, "stage": hub.stage_online, "ts": time.time()}

    # ---------------- 状态 ----------------
    @app.get("/api/status")
    async def status() -> JSONResponse:
        return JSONResponse(runtime.snapshot_status())

    @app.get("/api/events")
    async def events(limit: int = 40) -> JSONResponse:
        return JSONResponse(runtime.bus.snapshot(limit))

    @app.post("/api/scene")
    async def api_scene(req: Request) -> JSONResponse:
        body = await req.json()
        scene = body.get("scene", "coding")
        reason = body.get("reason", "手动切换")
        ok = await runtime.force_scene(scene, reason)
        return JSONResponse({"ok": ok, "scene": scene})

    @app.post("/api/say")
    async def api_say(req: Request) -> JSONResponse:
        body = await req.json()
        text = str(body.get("text", "")).strip()
        if not text:
            return JSONResponse({"ok": False, "error": "empty"}, status_code=400)
        await runtime.say(text, source="manual")
        return JSONResponse({"ok": True})

    @app.post("/api/test-danmaku")
    async def api_test_dm(req: Request) -> JSONResponse:
        body = await req.json()
        ev = runtime.make_test_event(
            kind=body.get("kind", "comment"),
            user=body.get("user", "测试观众"),
            text=body.get("text", ""),
            gift=body.get("gift", ""),
            count=int(body.get("count", 1) or 1),
        )
        await runtime.bus.publish(ev)
        return JSONResponse({"ok": True, "eid": ev.eid})

    @app.post("/api/live/start")
    async def api_live_start() -> JSONResponse:
        res = await runtime.companion.start_live(force=True)
        return JSONResponse(res)

    @app.post("/api/live/stop")
    async def api_live_stop() -> JSONResponse:
        res = await runtime.companion.stop_live()
        return JSONResponse(res)

    @app.post("/api/reload-config")
    async def api_reload() -> JSONResponse:
        try:
            runtime.reload_config()
            return JSONResponse({"ok": True})
        except Exception as exc:  # noqa: BLE001
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=500)

    @app.get("/api/config")
    async def api_config() -> JSONResponse:
        data = runtime.cfg.snapshot()
        # 不外泄 key
        if data.get("llm", {}).get("api_key"):
            k = str(data["llm"]["api_key"])
            data["llm"]["api_key"] = k[:4] + "****" + k[-4:] if len(k) > 8 else "****"
        return JSONResponse(data)

    @app.get("/api/log")
    async def api_log() -> JSONResponse:
        return JSONResponse(list(RING)[-300:])

    # ---------------- WebSocket ----------------
    @app.websocket("/ws/stage")
    async def ws_stage(ws: WebSocket) -> None:
        await ws.accept()
        hub.stage.add(ws)
        hub.last_stage_seen = time.time()
        hub.stage_ready.set()
        log.info("舞台页已连接（当前 %d 个）", len(hub.stage))
        try:
            await ws.send_text(
                json.dumps(runtime.stage_init_message(), ensure_ascii=False)
            )
            while True:
                raw = await ws.receive_text()
                hub.last_stage_hb = time.time()
                try:
                    msg = json.loads(raw)
                except Exception:  # noqa: BLE001
                    continue
                if msg.get("type") == "hb":
                    await ws.send_text(json.dumps({"type": "pong", "t": time.time()}))
                elif msg.get("type") == "hello":
                    log.info("舞台页握手成功")
                else:
                    try:
                        await runtime.on_stage_message(msg)
                    except Exception:  # noqa: BLE001
                        pass
        except WebSocketDisconnect:
            pass
        except Exception as exc:  # noqa: BLE001
            log.debug("舞台 WS 异常: %s", exc)
        finally:
            hub.stage.discard(ws)
            log.info("舞台页断开（剩余 %d 个）", len(hub.stage))

    @app.websocket("/ws/admin")
    async def ws_admin(ws: WebSocket) -> None:
        await ws.accept()
        hub.admin.add(ws)
        try:
            await ws.send_text(json.dumps({"type": "status", **runtime.snapshot_status()}, ensure_ascii=False))
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            pass
        except Exception:  # noqa: BLE001
            pass
        finally:
            hub.admin.discard(ws)

    app.state.runtime = runtime
    app.state.hub = hub
    return app
