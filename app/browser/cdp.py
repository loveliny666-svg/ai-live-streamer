"""极简 Chrome DevTools Protocol 客户端（不装 playwright，直接用本机 Chrome）。

用途：
1. 舞台页精确 1920x1080 截图（调版式 / 自检）
2. 弹幕源：往直播间页面注入 MutationObserver，读回结构化消息
3. 舞台页看门狗：心跳丢失时远程 reload
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import shutil
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import aiohttp

log = logging.getLogger("cdp")

CHROME_CANDIDATES = [
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

# 去掉 Headless 字样，减少被站点识别
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


def find_chrome(preferred: str = "") -> str:
    if preferred and Path(preferred).exists():
        return preferred
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    found = shutil.which("chrome") or shutil.which("msedge")
    if found:
        return found
    raise FileNotFoundError("找不到 Chrome/Edge，请在 config.yaml 里指定 stage.browser 路径")


def free_port(start: int = 9300, end: int = 9400) -> int:
    for p in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", p)) != 0:
                return p
    raise RuntimeError("没有空闲端口")


def kill_profile_holders(profile: str | Path) -> int:
    """清掉还占着这个 profile 的旧浏览器进程，避免「配置目录被占用」启动失败。"""
    try:
        import psutil
    except Exception:  # noqa: BLE001
        return 0
    target = str(profile).lower().replace("\\", "/")
    killed = 0
    for p in psutil.process_iter(["pid", "name", "cmdline"]):
        try:
            name = (p.info.get("name") or "").lower()
            if "chrome" not in name and "msedge" not in name:
                continue
            cl = " ".join(p.info.get("cmdline") or []).lower().replace("\\", "/")
        except Exception:  # noqa: BLE001
            continue
        if target and target in cl:
            try:
                p.terminate()
                killed += 1
            except Exception:  # noqa: BLE001
                pass
    if killed:
        log.info("清理了 %d 个占用 profile 的旧浏览器进程", killed)
        time.sleep(1.5)
    return killed


class ChromeCDP:
    def __init__(
        self,
        url: str,
        *,
        chrome_path: str = "",
        headless: bool = False,
        width: int = 1920,
        height: int = 1080,
        position: tuple[int, int] | None = (0, 0),
        user_data_dir: str | Path | None = None,
        extra_args: list[str] | None = None,
        port: int | None = None,
        app_mode: bool = False,
    ) -> None:
        self.url = url
        self.chrome = find_chrome(chrome_path)
        self.headless = headless
        self.width = width
        self.height = height
        self.position = position
        self.profile = Path(user_data_dir) if user_data_dir else Path(tempfile.mkdtemp(prefix="cdp-"))
        self.extra_args = extra_args or []
        self.port = port or free_port()
        self.app_mode = app_mode
        self.proc: subprocess.Popen | None = None
        self.ws: aiohttp.ClientWebSocketResponse | None = None
        self.session: aiohttp.ClientSession | None = None
        self._id = 0
        self._lock = asyncio.Lock()
        self.target_id = ""
        self._pending: dict[int, asyncio.Future] = {}
        self._reader_task: asyncio.Task | None = None
        # CDP 事件订阅（Runtime.bindingCalled 等）。回调是同步的，只做入队，别在这里 await。
        self._event_subs: dict[str, list[Any]] = {}

    # ---------------- 生命周期 ----------------
    def _args(self) -> list[str]:
        a = [
            self.chrome,
            f"--remote-debugging-port={self.port}",
            f"--user-data-dir={self.profile}",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-background-timer-throttling",
            "--disable-backgrounding-occluded-windows",
            "--disable-renderer-backgrounding",
            "--disable-features=Translate,MediaRouter,CalculateNativeWinOcclusion",
            "--autoplay-policy=no-user-gesture-required",
            "--disable-popup-blocking",
            "--noerrdialogs",
            f"--user-agent={UA}",
        ]
        if self.headless:
            a += ["--headless=new", "--disable-gpu", "--hide-scrollbars"]
        if self.app_mode:
            a += [f"--app={self.url}", f"--window-size={self.width},{self.height}"]
            if self.position:
                a += [f"--window-position={self.position[0]},{self.position[1]}"]
        else:
            a += [f"--window-size={self.width},{self.height}"]
            if self.position:
                a += [f"--window-position={self.position[0]},{self.position[1]}"]
            a += [self.url]
        a += self.extra_args
        return a

    def _mark_clean_exit(self) -> None:
        """我们重启时是强杀 Chrome 的，profile 会留下 crash 记录，下次启动
        弹「要恢复页面吗？」对话框挡住页面/别的窗口。提前把退出状态标成
        正常，让它安静地起来。"""
        try:
            import json as _json

            p = Path(self.profile) / "Default" / "Preferences"
            if not p.exists():
                return
            data = _json.loads(p.read_text(encoding="utf-8"))
            prof = data.setdefault("profile", {})
            if prof.get("exited_cleanly") is True and prof.get("exit_type") == "Normal":
                return
            prof["exit_type"] = "Normal"
            prof["exited_cleanly"] = True
            p.write_text(_json.dumps(data, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass  # 改不成也只是可能弹恢复提示，不影响启动

    async def start(self, timeout: float = 25) -> None:
        self.profile.mkdir(parents=True, exist_ok=True)
        kill_profile_holders(self.profile)
        self._mark_clean_exit()
        creation = 0
        if os.name == "nt":
            creation = 0x08000000  # CREATE_NO_WINDOW
        self.proc = subprocess.Popen(
            self._args(),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=creation,
        )
        self.session = aiohttp.ClientSession()
        deadline = time.time() + timeout
        target = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(f"Chrome 启动即退出，code={self.proc.returncode}")
            try:
                async with self.session.get(f"http://127.0.0.1:{self.port}/json/list") as r:
                    lst = await r.json()
                pages = [t for t in lst if t.get("type") == "page"]
                # 优先选目标 url
                for p in pages:
                    if self.url.split("?")[0] in (p.get("url") or ""):
                        target = p
                        break
                if target is None and pages:
                    target = pages[-1]
                if target:
                    break
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.25)
        if not target:
            raise TimeoutError("等待 Chrome 调试端口超时")
        self.target_id = target["id"]
        self.ws = await self.session.ws_connect(target["webSocketDebuggerUrl"], max_msg_size=0)
        self._reader_task = asyncio.create_task(self._reader())
        await asyncio.sleep(0.05)
        await self.call("Runtime.enable")
        await self.call("Page.enable")
        # 明确导航到目标页，避免连到残留的空标签
        try:
            await self.call("Page.navigate", {"url": self.url}, timeout=15)
        except Exception:  # noqa: BLE001
            pass
        await self.wait_ready()

    async def _reader(self) -> None:
        """唯一的读循环：把响应派发给 future，事件派发给订阅者。"""
        assert self.ws
        try:
            async for msg in self.ws:
                if msg.type != aiohttp.WSMsgType.TEXT:
                    if msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
                    continue
                try:
                    data = json.loads(msg.data)
                except Exception:  # noqa: BLE001
                    continue
                mid = data.get("id")
                if mid is None:
                    self._dispatch_event(data)
                    continue
                fut = self._pending.pop(mid, None)
                if fut and not fut.done():
                    if "error" in data:
                        fut.set_exception(RuntimeError(f"CDP 错误: {data['error']}"))
                    else:
                        fut.set_result(data.get("result", {}))
        except Exception as exc:  # noqa: BLE001
            log.debug("CDP 读循环退出: %s", exc)
        finally:
            for fut in list(self._pending.values()):
                if not fut.done():
                    fut.set_exception(RuntimeError("CDP 连接已断开"))
            self._pending.clear()

    # ---------------- 事件订阅 ----------------
    def on_event(self, method: str, cb) -> None:
        """订阅 CDP 事件。cb(params: dict) 必须是同步函数，只做入队这类快操作。"""
        self._event_subs.setdefault(method, []).append(cb)

    def _dispatch_event(self, data: dict) -> None:
        subs = self._event_subs.get(data.get("method") or "")
        if not subs:
            return
        params = data.get("params") or {}
        for cb in subs:
            try:
                cb(params)
            except Exception as exc:  # noqa: BLE001
                log.debug("CDP 事件回调异常 %s: %s", data.get("method"), exc)

    async def add_binding(self, name: str) -> None:
        """在页面里挂一个 window.<name>(payload) 函数，调用即触发
        Runtime.bindingCalled 事件。这是「页面一有动静 Python 立刻知道」的通道。"""
        await self.call("Runtime.addBinding", {"name": name})

    async def call(self, method: str, params: dict | None = None, timeout: float = 25) -> dict:
        if not self.ws or self.ws.closed:
            raise RuntimeError("CDP 未连接")
        self._id += 1
        mid = self._id
        fut: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pending[mid] = fut
        await self.ws.send_json({"id": mid, "method": method, "params": params or {}})
        try:
            return await asyncio.wait_for(fut, timeout)
        finally:
            self._pending.pop(mid, None)

    async def wait_ready(self, timeout: float = 20, need: str = "") -> None:
        """等页面加载完成；need 可指定必须存在的选择器。"""
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                state = await self.eval("document.readyState")
                ok = state in ("interactive", "complete")
                if ok and need:
                    ok = bool(await self.eval(f"!!document.querySelector({json.dumps(need)})"))
                if ok:
                    return
            except Exception:  # noqa: BLE001
                pass
            await asyncio.sleep(0.25)
        log.warning("等待页面就绪超时: %s", self.url)

    # ---------------- 常用操作 ----------------
    async def eval(self, expr: str, await_promise: bool = False) -> Any:
        res = await self.call(
            "Runtime.evaluate",
            {
                "expression": expr,
                "returnByValue": True,
                "awaitPromise": await_promise,
                "userGesture": True,
            },
        )
        if res.get("exceptionDetails"):
            raise RuntimeError(f"JS 异常: {res['exceptionDetails'].get('text')}")
        return res.get("result", {}).get("value")

    async def navigate(self, url: str) -> None:
        await self.call("Page.navigate", {"url": url})

    async def reload(self) -> None:
        await self.call("Page.reload", {"ignoreCache": False})

    async def screenshot(self, path: str | Path, claim_full: bool = True) -> Path:
        params: dict[str, Any] = {"format": "png"}
        if claim_full:
            params["captureBeyondViewport"] = True
            params["clip"] = {
                "x": 0,
                "y": 0,
                "width": self.width,
                "height": self.height,
                "scale": 1,
            }
        res = await self.call("Page.captureScreenshot", params, timeout=30)
        data = base64.b64decode(res.get("data", ""))
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
        return p

    async def set_viewport(self) -> None:
        await self.call(
            "Emulation.setDeviceMetricsOverride",
            {
                "width": self.width,
                "height": self.height,
                "deviceScaleFactor": 1,
                "mobile": False,
            },
        )

    async def close(self) -> None:
        if self._reader_task:
            self._reader_task.cancel()
        try:
            if self.ws:
                await self.ws.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            if self.session:
                await self.session.close()
        except Exception:  # noqa: BLE001
            pass
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    @property
    def alive(self) -> bool:
        return bool(self.proc and self.proc.poll() is None and self.ws and not self.ws.closed)
