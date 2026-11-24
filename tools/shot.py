"""开发用：用 CDP 精确 1920x1080 截舞台页，并打印关键布局测量值。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.browser.cdp import ChromeCDP  # noqa: E402

MEASURE_JS = """
(() => {
  const q = (s) => document.querySelector(s);
  const r = (el) => el ? (({x,y,width,height}) => ({x:Math.round(x),y:Math.round(y),w:Math.round(width),h:Math.round(height)}))(el.getBoundingClientRect()) : null;
  const ca = q('.code-area');
  return {
    inner: [window.innerWidth, window.innerHeight],
    stage: r(q('#stage')),
    codeLines: document.querySelectorAll('.code-line').length,
    codeArea: ca ? {h: ca.clientHeight, w: ca.clientWidth} : null,
    codeScroll: q('#codeScroll') ? {h: q('#codeScroll').clientHeight, t: getComputedStyle(q('#codeScroll')).transform} : null,
    term: r(q('#term')),
    monitor: r(q('.monitor')),
    charWrap: r(q('#charWrap')),
    desk: r(q('.desk')),
    keyboard: r(q('#kb')),
    botdeck: r(q('.botdeck')),
    subtitle: r(q('#subtitle')),
    dmCol: r(q('#dmLayer')),
    scene: document.body.dataset.scene,
  };
})()
"""


async def main() -> None:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8899/stage?shot=1"
    out = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "data" / "shot.png"
    wait = float(sys.argv[3]) if len(sys.argv) > 3 else 4.0
    cdp = ChromeCDP(
        url,
        headless=True,
        width=1920,
        height=1080,
        user_data_dir=ROOT / "data" / "tmp" / "cdp-shot",
    )
    await cdp.start()
    try:
        await cdp.set_viewport()
        await asyncio.sleep(wait)
        data = await cdp.eval(MEASURE_JS)
        import json

        print(json.dumps(data, ensure_ascii=False, indent=1))
        await cdp.screenshot(out)
        print("saved", out)
    finally:
        await cdp.close()


if __name__ == "__main__":
    asyncio.run(main())
