"""端到端自检：真起服务、真开浏览器、真发弹幕、真截图。

用法：python tests/selftest_e2e.py [--port 8751]
不花钱：不连真实直播间、不调用付费模型（无 Key 时走规则脑）。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import aiohttp

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.browser.cdp import ChromeCDP  # noqa: E402

PASS: list[str] = []
FAIL: list[str] = []
SHOT_DIR = ROOT / "data" / "selftest"


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""), flush=True)


async def api(base: str, path: str, body: dict | None = None) -> dict:
    async with aiohttp.ClientSession() as s:
        if body is None:
            async with s.get(base + path) as r:
                return await r.json()
        async with s.post(base + path, json=body) as r:
            try:
                return await r.json()
            except Exception:  # noqa: BLE001
                return {"raw": await r.text()}


async def wait_http(base: str, timeout: float = 40) -> bool:
    end = time.time() + timeout
    async with aiohttp.ClientSession() as s:
        while time.time() < end:
            try:
                async with s.get(base + "/healthz") as r:
                    if r.status == 200:
                        return True
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.5)
    return False


async def _ready(base: str):
    st = await api(base, "/api/status")
    return st if st.get("ready") else None


async def wait_until(fn, timeout: float, interval: float = 0.4):
    end = time.time() + timeout
    last = None
    while time.time() < end:
        try:
            last = await fn()
            if last:
                return last
        except Exception as exc:  # noqa: BLE001
            last = str(exc)
        await asyncio.sleep(interval)
    return last


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8751)
    ap.add_argument("--keep", action="store_true", help="测试结束不关服务")
    args = ap.parse_args()
    port = args.port
    base = f"http://127.0.0.1:{port}"
    SHOT_DIR.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PYTHONUNBUFFERED"] = "1"
    log_path = SHOT_DIR / "app.log"
    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app", "--no-stage", "--no-live", "--port", str(port), "--log-level", "INFO"],
        cwd=str(ROOT),
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
    )
    cdp = None
    try:
        print("\n[0] 启动服务")
        ok = await wait_http(base)
        check("服务在 40 秒内就绪", ok)
        if not ok:
            raise RuntimeError("服务没起来，见 data/selftest/app.log")

        # /healthz 先于运行时启动完成就返回了，这里等 ready 变 true
        st = await wait_until(lambda: _ready(base), 40)
        check("运行时 ready", bool(st and st.get("ready")), str(st and st.get("ready")))
        check("场景为 coding", st.get("scene") == "coding", str(st.get("scene")))
        check("模型未配置时给出原因", st["llm"]["ready"] or bool(st["llm"]["why_not"]),
              st["llm"].get("why_not", ""))

        print("\n[1] 打开舞台页（无头 Chrome）")
        cdp = ChromeCDP(
            f"{base}/stage?shot=1",
            headless=True,
            width=1920,
            height=1080,
            user_data_dir=ROOT / "data" / "tmp" / "selftest-chrome",
        )
        await cdp.start()
        await cdp.set_viewport()
        await asyncio.sleep(3.5)
        st_online = await wait_until(lambda: api(base, "/api/status"), 12)
        check("舞台页已连上后台", bool(st_online and st_online.get("stage_online")))

        geom = await cdp.eval(
            "(() => { const im = document.querySelector('.char-img'); return {"
            "stage: !!document.querySelector('#stage'), "
            "lines: document.querySelectorAll('.code-line').length, "
            "char: !!im, img_w: im ? im.naturalWidth : 0, img_h: im ? im.naturalHeight : 0, "
            "badge: !!document.querySelector('.badge'), "
            "scene: document.body.dataset.scene}; })()"
        )
        check("页面结构完整", bool(geom and geom["stage"] and geom["char"]), str(geom))
        check("角色图已加载", bool(geom and geom["img_w"] > 500), str(geom and (geom["img_w"], geom["img_h"])))
        check("无关角标已移除", bool(geom and not geom["badge"]))
        check("代码正在逐字敲出来", bool(geom and geom["lines"] >= 6), f"行数={geom and geom['lines']}")
        await cdp.screenshot(SHOT_DIR / "01-coding.png")

        print("\n[2] 弹幕 → 智能体 → 字幕/语音")
        await api(base, "/api/test-danmaku", {"kind": "comment", "user": "测试观众", "text": "主播好厉害"})
        said = await wait_until(
            lambda: cdp.eval("window.__lastSay || null"), 15
        )
        check("收到 say 指令", bool(said), str(said)[:80])
        check("语音已合成", bool(said and said.get("audio")), str(said and said.get("audio")))
        shown = await cdp.eval(
            "({show: document.querySelector('#subtitle').classList.contains('show'), "
            "text: document.querySelector('#subText').textContent, "
            "dm: document.querySelectorAll('#dmLayer .dm').length})"
        )
        check("字幕显示出来", bool(shown and shown["show"] and shown["text"]), str(shown))
        check("弹幕飘进画面", bool(shown and shown["dm"] >= 1), str(shown and shown["dm"]))
        bub = await cdp.eval(
            "({show: document.querySelector('#bubble').classList.contains('show'),"
            " user: document.querySelector('#bubUser').textContent,"
            " text: document.querySelector('#bubText').textContent})"
        )
        check("角色旁边弹出「她在回谁」", bool(bub and bub["show"] and bub["user"]), str(bub))
        await cdp.screenshot(SHOT_DIR / "02-talking.png")

        print("\n[3] 礼物 → 横幅 / 全屏大字")
        await asyncio.sleep(7)  # 等兜底脑的冷却
        # 小礼物走横幅
        await api(base, "/api/test-danmaku", {"kind": "gift", "user": "打工人小李", "gift": "小心心", "count": 3})
        banner = await wait_until(
            lambda: cdp.eval(
                "document.querySelector('#giftBanner').classList.contains('show') "
                "? document.querySelector('#giftName').textContent : ''"
            ),
            10,
        )
        check("小礼物横幅弹出", bool(banner), str(banner))
        await cdp.screenshot(SHOT_DIR / "03-gift.png")
        # 大礼物走全屏大字，且不再叠一条横幅
        await asyncio.sleep(1.0)
        await api(base, "/api/test-danmaku", {"kind": "gift", "user": "老板本人", "gift": "嘉年华", "count": 1})
        flash = await wait_until(
            lambda: cdp.eval(
                "document.querySelector('#flash').classList.contains('on') "
                "? document.querySelector('#flashBig').textContent : ''"
            ),
            10,
        )
        check("大礼物全屏大字", bool(flash), str(flash))
        both = await cdp.eval(
            "({flash: document.querySelector('#flash').classList.contains('on'),"
            " banner: document.querySelector('#giftBanner').classList.contains('show')})"
        )
        check("大字与横幅不叠在一起", bool(both and both["flash"] and not both["banner"]), str(both))
        await cdp.screenshot(SHOT_DIR / "03b-big-gift.png")

        print("\n[4] 切到摸鱼（植物大战僵尸）")
        await api(base, "/api/scene", {"scene": "slack", "reason": "自检"})
        await asyncio.sleep(1.0)
        scene = await cdp.eval("document.body.dataset.scene")
        check("画面切到 slack", scene == "slack", str(scene))
        game = await wait_until(
            lambda: cdp.eval(
                "(window.PvZ && window.PvZ.elapsed > 4) ? "
                "{plants: window.PvZ.plants.length, sun: window.PvZ.sun, wave: window.PvZ.wave, "
                "zombies: window.PvZ.zombies.length, elapsed: Math.round(window.PvZ.elapsed)} : null"
            ),
            30,
        )
        check("游戏在跑并有植物", bool(game and game["plants"] >= 1), str(game))
        await asyncio.sleep(6)
        await cdp.screenshot(SHOT_DIR / "04-slack.png")
        game2 = await cdp.eval(
            "({plants: window.PvZ.plants.length, zombies: window.PvZ.zombies.length, "
            "wave: window.PvZ.wave, elapsed: Math.round(window.PvZ.elapsed)})"
        )
        check("游戏持续自走（AI 托管）", bool(game2 and game2["elapsed"] >= 10), str(game2))

        print("\n[5] 老板键")
        await api(base, "/api/scene", {"scene": "boss", "reason": "自检"})
        await asyncio.sleep(1.2)
        boss = await cdp.eval(
            "({scene: document.body.dataset.scene, "
            "sweat: getComputedStyle(document.querySelector('#sweat')).opacity, "
            "vig: getComputedStyle(document.querySelector('.boss-vignette')).opacity})"
        )
        check("进入老板键状态", bool(boss and boss["scene"] == "boss"), str(boss))
        await cdp.screenshot(SHOT_DIR / "05-boss.png")

        print("\n[6] 后台状态汇总")
        st = await api(base, "/api/status")
        check("弹幕已入库", st["stats"]["comments"] >= 1 and st["stats"]["gifts"] >= 1,
              f"评论{st['stats']['comments']} 礼物{st['stats']['gifts']}")
        check("智能体说过话", st["agent"]["says"] + st["agent"]["fallback_says"] >= 1, str(st["agent"]))
        check("语音有缓存文件", st["tts"]["cache_files"] >= 1, str(st["tts"]["cache_files"]))
        check("巡检任务在跑（无异常）", st["ready"] is True)
        hud = await cdp.eval(
            "({perf: document.querySelector('#perfNow').textContent,"
            " pct: document.querySelector('#perfPct').textContent,"
            " slack: document.querySelector('#stSlack').textContent,"
            " caught: document.querySelector('#stCaught').textContent,"
            " speed: document.querySelector('#stSpeed').textContent,"
            " focus: document.querySelector('#hudFocus').textContent,"
            " notice: document.querySelector('#stNoticeText').textContent})"
        )
        check("绩效条吃到真实字数", bool(hud and int(hud["perf"].replace(",", "") or 0) > 0), str(hud and hud["perf"]))
        check("手速/摸鱼/被抓都有值",
              bool(hud and hud["speed"] and hud["slack"].isdigit() and hud["caught"].isdigit()), str(hud))
        check("摸鱼提示在走",
              bool(hud and any(k in hud["notice"] for k in ("可以休息", "马上就能休息", "老板"))),
              str(hud and hud["notice"]))

        print("\n[7] 控制台页面")
        async with aiohttp.ClientSession() as s:
            async with s.get(base + "/admin") as r:
                html = await r.text()
        check("/admin 可访问", "直播中枢" in html, f"{len(html)} 字节")

        print("\n[8] 画面留存")
        for f in sorted(SHOT_DIR.glob("*.png")):
            print(f"  · {f.name}  {f.stat().st_size // 1024} KB")

    finally:
        if cdp:
            await cdp.close()
        if not args.keep:
            proc.terminate()
            try:
                proc.wait(timeout=8)
            except Exception:  # noqa: BLE001
                proc.kill()
        log_file.close()

    print(f"\n===== 结果：{len(PASS)} 通过 / {len(FAIL)} 失败 =====")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
