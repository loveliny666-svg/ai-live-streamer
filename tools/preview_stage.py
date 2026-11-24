"""开发用：单独起舞台页 + 截图，方便调版式（不依赖完整运行时）。"""
from __future__ import annotations

import asyncio
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from app.bus import Bus  # noqa: E402
from app.config import CFG  # noqa: E402
from app.models import LiveEvent, guess_gift_value  # noqa: E402
from app.stage.server import StageHub, build_app  # noqa: E402


class StubCompanion:
    async def start_live(self, force=False):
        return {"ok": True, "note": "stub"}

    async def stop_live(self):
        return {"ok": True, "note": "stub"}


class StubRuntime:
    def __init__(self):
        self.cfg = CFG
        self.hub = StageHub()
        self.bus = Bus()
        self.companion = StubCompanion()
        self.scene = "coding"

    def snapshot_status(self):
        s = self.bus.stats
        return {
            "scene": self.scene,
            "stage_online": self.hub.stage_online,
            "stats": {
                "online": 37,
                "likes": 12480,
                "gifts": 860,
                "uptime": int(time.time() - s.started_at),
                "focus_text": "02:31:07",
                "slack": 3,
                "caught": 1,
                "lines": 1240,
                "goal": 2400,
                "speed": 1.3,
                "countdown": 192,
                "typing": True,
            },
            "llm": {"provider": "mock", "alive": False},
            "danmaku": {"dom": False, "ocr": False},
            "live": {"is_live": False},
        }

    async def force_scene(self, scene, reason=""):
        self.scene = scene
        await self.hub.broadcast_stage({"type": "scene", "scene": scene, "reason": reason})
        return True

    async def say(self, text, source="manual"):
        await self.hub.broadcast_stage({"type": "say", "text": text, "hold_ms": 4000})

    def make_test_event(self, kind="comment", user="观众", text="", gift="", count=1):
        return LiveEvent(
            kind=kind,
            user=user,
            text=text,
            gift_name=gift,
            gift_count=count,
            gift_value=guess_gift_value(gift, count) if gift else 0,
            source="test",
        )

    def stage_init_message(self):
        return {
            "type": "init",
            "title": "小饭的打工日常 · <b>通宵改需求中</b>",
            "persona_name": "小饭",
            "scene": self.scene,
            "stats": self.snapshot_status()["stats"],
        }

    def reload_config(self):
        CFG.reload()


def main() -> None:
    scene = sys.argv[1] if len(sys.argv) > 1 else "coding"
    out = sys.argv[2] if len(sys.argv) > 2 else str(ROOT / "data" / "preview.png")
    runtime = StubRuntime()
    runtime.scene = scene
    app = build_app(runtime)
    cfg = uvicorn.Config(app, host="127.0.0.1", port=8899, log_level="warning")
    server = uvicorn.Server(cfg)
    import threading

    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    time.sleep(2.0)

    chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    out_path = Path(out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    profile = (ROOT / "data" / "tmp" / "chromeshot").resolve()
    profile.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            chrome,
            "--headless=new",
            "--disable-gpu",
            "--hide-scrollbars",
            "--force-device-scale-factor=1",
            "--window-size=1920,1080",
            f"--user-data-dir={profile}",
            f"--screenshot={out_path}",
            "--virtual-time-budget=7000",
            "--autoplay-policy=no-user-gesture-required",
            "http://127.0.0.1:8899/stage?shot=1",
        ],
        timeout=120,
    )
    print("saved", out)
    server.should_exit = True
    time.sleep(0.4)


if __name__ == "__main__":
    main()
