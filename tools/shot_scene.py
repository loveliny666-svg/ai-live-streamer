"""开发用：切场景后截图（验证摸鱼/老板键画面）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.browser.cdp import ChromeCDP  # noqa: E402


async def main() -> None:
    scene = sys.argv[1] if len(sys.argv) > 1 else "slack"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / f"shot_{scene}.png"
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 10.0
    cdp = ChromeCDP(
        "http://127.0.0.1:8899/stage?shot=1",
        headless=True,
        width=1920,
        height=1080,
        user_data_dir=ROOT / "data" / "tmp" / "cdp-scene",
    )
    await cdp.start()
    try:
        await cdp.set_viewport()
        await asyncio.sleep(3)
        async with aiohttp.ClientSession() as s:
            async with s.post("http://127.0.0.1:8899/api/scene", json={"scene": scene}) as r:
                print("scene ->", await r.json())
        await asyncio.sleep(wait)
        info = await cdp.eval(
            "({scene: document.body.dataset.scene, "
            "zombies: (window.PvZ && window.PvZ.zombies.length)||0, "
            "plants: (window.PvZ && window.PvZ.plants.length)||0, "
            "sun: (window.PvZ && window.PvZ.sun)||0, "
            "wave: (window.PvZ && window.PvZ.wave)||0})"
        )
        print(info)
        await cdp.screenshot(out)
        print("saved", out)
    finally:
        await cdp.close()


if __name__ == "__main__":
    asyncio.run(main())
