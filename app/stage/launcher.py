"""舞台窗口：用本机 Chrome 以「应用模式」开一个无边框 1920x1080 窗口。"""
from __future__ import annotations

import asyncio
import logging
import time

from ..browser.cdp import ChromeCDP
from ..companion import win

log = logging.getLogger("stage")

STAGE_TITLES = ["直播舞台", "小饭工作室", "AI 打工人"]


class StageWindow:
    def __init__(self, cfg, port: int):
        self.cfg = cfg
        self.port = port
        self.cdp: ChromeCDP | None = None
        self.last_reload = 0.0
        self.reloads = 0

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/stage"

    def hwnd(self) -> int | None:
        info = win.find_window(STAGE_TITLES, min_size=400)
        return info.hwnd if info else None

    async def ensure(self) -> None:
        if self.cdp and self.cdp.alive:
            return
        if self.cdp:
            try:
                await self.cdp.close()
            except Exception:  # noqa: BLE001
                pass
        browser = str(self.cfg.get("stage.browser", "auto") or "")
        chrome = ""
        if browser.lower() in ("edge", "msedge"):
            chrome = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
        elif browser.lower() == "chrome":
            chrome = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
        w = int(self.cfg.get("stage.width", 1920))
        h = int(self.cfg.get("stage.height", 1080))
        pos = self.cfg.get("stage.position", [0, 0]) or [0, 0]
        # 窗口模式用单独的一组尺寸，别影响全屏模式（全屏必须等于屏幕分辨率）
        if str(self.cfg.get("stage.mode", "fullscreen")).lower() == "window":
            w = int(self.cfg.get("stage.window_width", 1280))
            h = int(self.cfg.get("stage.window_height", 720))
            pos = [int(self.cfg.get("stage.window_x", 320)), int(self.cfg.get("stage.window_y", 60))]
        # fullscreen：全屏无边框，零客户端边框，窗口捕获最干净
        # window：固定 1920x1080 窗口，Chrome 会带一条自绘标题栏（窗口捕获时需裁掉）
        self.mode = str(self.cfg.get("stage.mode", "fullscreen") or "fullscreen").lower()
        self.cdp = ChromeCDP(
            self.url,
            chrome_path=chrome,
            headless=False,
            width=w,
            height=h,
            position=(int(pos[0]), int(pos[1])),
            user_data_dir=self.cfg.data_dir / "chrome-stage",
            app_mode=True,
            extra_args=[
                "--autoplay-policy=no-user-gesture-required",
                "--force-device-scale-factor=1",
                "--high-dpi-support=1",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
                "--disable-features=TranslateUI",
                # 窗口模式下直播伴侣按「窗口」捕获，会把 Chrome 自绘的那条标题栏一起拍进去。
                # 那条栏是浅色的，压在深色画面的顶上很扎眼。强制深色模式让它变成深灰，
                # 和舞台顶部的深色区域融在一起，基本看不出来。
                # （只影响浏览器 UI，舞台页自己有 CSS，不会跟着变色）
                "--force-dark-mode",
            ]
            + (["--start-fullscreen"] if self.mode == "fullscreen" else []),
        )
        await self.cdp.start()
        await asyncio.sleep(1.2)
        # 窗口模式：把客户区精确摆到配置的位置，并把 Chrome 的标题栏推到屏幕外，
        # 这样窗口捕获拍到的只有画面，不会带一条"直播舞台 口 ─ ✕"。
        if self.mode != "fullscreen":
            hwnd = self.hwnd()
            if not hwnd:
                await asyncio.sleep(1.0)
                hwnd = self.hwnd()
            if hwnd:
                loop = asyncio.get_running_loop()
                # 首选直接去掉窗口装饰：直播伴侣按「窗口」捕获时会连标题栏一起拍，
                # 去边框之后捕出来就是纯画面，不用在伴侣里手工裁 40px。
                ok = await loop.run_in_executor(
                    None, win.make_borderless, hwnd, w, h, int(pos[0]), int(pos[1])
                )
                if not ok:
                    # 去边框失败（比如 Chrome 又把样式改回来）就退回推标题栏的老办法
                    await loop.run_in_executor(
                        None, win.place_client_area, hwnd, int(pos[0]), int(pos[1]), w, h
                    )
                await asyncio.sleep(0.5)
        else:
            # 全屏模式本来就没有标题栏（--start-fullscreen 是真正的无边框全屏）。
            # 再把它整体挪到所有显示器之外：屏幕上彻底看不见，但直播伴侣按「窗口」
            # 捕获照样能拿到满屏纯画面，一个像素的窗口装饰都不带。
            # 这是不装虚拟显示器也能去掉那条标题栏的办法。
            ox = self.cfg.get("stage.offscreen_x", None)
            if ox is not None:
                hwnd = self.hwnd()
                if not hwnd:
                    await asyncio.sleep(1.0)
                    hwnd = self.hwnd()
                if hwnd:
                    win.user32.SetWindowPos(
                        hwnd,
                        0,
                        int(ox),
                        int(self.cfg.get("stage.offscreen_y", 0) or 0),
                        0,
                        0,
                        0x0001 | 0x0004,  # SWP_NOSIZE | SWP_NOZORDER
                    )
                    log.info("舞台全屏窗口已移出屏幕：x=%s y=%s",
                             ox, self.cfg.get("stage.offscreen_y", 0))
                await asyncio.sleep(0.5)
        log.info("舞台窗口已打开：%s", self.url)

    async def reload(self) -> None:
        if not self.cdp or not self.cdp.alive:
            await self.ensure()
            return
        try:
            await self.cdp.reload()
            self.reloads += 1
            self.last_reload = time.time()
            log.info("舞台页已重新加载（第 %d 次）", self.reloads)
        except Exception as exc:  # noqa: BLE001
            log.warning("重载失败，重启浏览器：%s", exc)
            try:
                await self.cdp.close()
            except Exception:  # noqa: BLE001
                pass
            self.cdp = None
            await self.ensure()

    async def close(self) -> None:
        if self.cdp:
            await self.cdp.close()
            self.cdp = None

    def snapshot(self) -> dict:
        return {
            "url": self.url,
            "browser_alive": bool(self.cdp and self.cdp.alive),
            "hwnd": self.hwnd(),
            "reloads": self.reloads,
        }
