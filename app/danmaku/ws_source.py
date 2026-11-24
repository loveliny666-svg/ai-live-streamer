"""弹幕源 · 直播间 WebSocket（通过 CDP 旁听，最实时）。

原理：让 Chrome 打开直播间网页，页面自己会连上抖音的弹幕 WebSocket。
我们用 CDP 的 `Network.webSocketFrameReceived` 只做旁观，不改包、不碰签名、
不碰登录态 —— 拿到的是平台原样的 protobuf 帧：

    PushFrame(field 8 = gzip 的 Response)
      └─ Response(field 1 = repeated Message)
           └─ Message(field 1 = method, field 2 = payload, field 3 = msg_id)
                ├─ WebcastMemberMessage  → 进场（昵称、在线人数）
                ├─ WebcastChatMessage    → 弹幕
                ├─ WebcastGiftMessage    → 礼物
                ├─ WebcastLikeMessage    → 点赞
                ├─ WebcastSocialMessage  → 关注/分享
                └─ WebcastRoomUserSeqMessage → 实时在线人数

相比 OCR：延迟从 1~2 秒降到几十毫秒；每条消息带 msg_id，不会重复欢迎；
评论自带昵称，不用再猜「观众」。
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import logging
import time

from ..browser.cdp import ChromeCDP
from ..models import LiveEvent, guess_gift_value
from .base import DanmakuSource, clean_user

log = logging.getLogger("danmaku.ws")


# ---------------- protobuf 最小解析 ----------------
def _varint(b: bytes, i: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while i < len(b):
        x = b[i]
        i += 1
        val |= (x & 0x7F) << shift
        if not x & 0x80:
            return val, i
        shift += 7
    raise ValueError("varint 越界")


def fields(b: bytes) -> dict[int, list]:
    """把一段 protobuf 拆成 {字段号: [值…]}，够用就行。"""
    out: dict[int, list] = {}
    i = 0
    while i < len(b):
        key, i = _varint(b, i)
        no, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 5:
            v = b[i:i + 4]
            i += 4
        elif wt == 1:
            v = b[i:i + 8]
            i += 8
        else:
            break
        out.setdefault(no, []).append(v)
    return out


def f_str(d: dict, no: int) -> str:
    v = d.get(no)
    if not v or not isinstance(v[0], (bytes, bytearray)):
        return ""
    try:
        return bytes(v[0]).decode("utf-8", "ignore")
    except Exception:  # noqa: BLE001
        return ""


def f_int(d: dict, no: int) -> int:
    v = d.get(no)
    if not v:
        return 0
    try:
        return int(v[0])
    except Exception:  # noqa: BLE001
        return 0


def f_sub(d: dict, no: int) -> dict:
    v = d.get(no)
    if not v or not isinstance(v[0], (bytes, bytearray)):
        return {}
    try:
        return fields(bytes(v[0]))
    except Exception:  # noqa: BLE001
        return {}


def decode_frame(raw: bytes) -> list[tuple[str, bytes, int]]:
    """PushFrame → [(method, payload, msg_id)]。"""
    top = fields(raw)
    payload = top.get(8, [b""])[0]
    if not isinstance(payload, (bytes, bytearray)) or not payload:
        return []
    try:
        inner = gzip.decompress(bytes(payload))
    except Exception:  # noqa: BLE001
        inner = bytes(payload)
    out: list[tuple[str, bytes, int]] = []
    for body in fields(inner).get(1, []):
        m = fields(bytes(body))
        method = f_str(m, 1)
        msg_id = f_int(m, 3)
        data = m.get(2, [b""])[0]
        if method and isinstance(data, (bytes, bytearray)):
            out.append((method, bytes(data), msg_id))
    return out


def user_name(d: dict, no: int) -> str:
    u = f_sub(d, no)
    name = f_str(u, 3) or f_str(u, 38)
    return clean_user(name) if name else ""


def gift_info(d: dict) -> tuple[str, int, int]:
    """返回 (礼物名, 数量, 单个抖币)。字段号对不上时退化为扫描短中文串。"""
    count = f_int(d, 5) or f_int(d, 6) or 1
    g = f_sub(d, 15)
    name = ""
    diamond = 0
    if g:
        name = f_str(g, 5) or f_str(g, 2)
        diamond = f_int(g, 10)
        if not name:
            for no in sorted(g.keys()):
                v = g[no][0]
                if isinstance(v, (bytes, bytearray)):
                    try:
                        s = bytes(v).decode("utf-8")
                    except Exception:  # noqa: BLE001
                        continue
                    if 1 <= len(s) <= 8 and not s.startswith("http") and "://" not in s:
                        name = s
                        break
    return name or "礼物", max(1, count), diamond


class WsSource(DanmakuSource):
    name = "ws"
    label = "直播间 WebSocket"

    def __init__(self, cfg, emit, room_url: str):
        super().__init__(cfg, emit)
        self.room_url = room_url
        self.cdp: ChromeCDP | None = None
        self._q: asyncio.Queue = asyncio.Queue(maxsize=800)
        self._seen: dict[int, float] = {}
        self._last_frame = 0.0
        self._online = -1
        self._online_at = 0.0
        self._frames = 0
        self._reloads = 0

    # ---------- 帧回调（同步、只入队） ----------
    def _on_frame(self, params: dict) -> None:
        resp = params.get("response") or {}
        if resp.get("opcode") != 2:
            return
        self._frames += 1
        self._last_frame = time.time()
        try:
            raw = base64.b64decode(resp.get("payloadData") or "")
        except Exception:  # noqa: BLE001
            return
        try:
            msgs = decode_frame(raw)
        except Exception:  # noqa: BLE001
            return
        for method, data, msg_id in msgs:
            try:
                self._q.put_nowait((method, data, msg_id))
            except asyncio.QueueFull:
                return

    # ---------- 主循环 ----------
    async def _run(self) -> None:
        while True:
            try:
                await self._session()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                self.ok = False
                self.last_error = str(exc)[:200]
                log.warning("[ws] 断开：%s（5 秒后重连）", exc)
                # 旧 CDP 必须显式关掉：它持有 aiohttp.ClientSession 和一个 Chrome 进程，
                # 直接丢弃会漏会话（日志里那条 Unclosed client session），重连几次就攒几个。
                if self.cdp is not None:
                    try:
                        await self.cdp.close()
                    except Exception:  # noqa: BLE001
                        pass
                    self.cdp = None
                await asyncio.sleep(5)

    async def _session(self) -> None:
        headless = bool(self.cfg.get("danmaku.sources.ws.headless", False))
        # 旁听浏览器放在哪。以前写死 -2600（左屏里），换显示器布局就跑到别人屏幕上了。
        # 现在跟着 stage 一样走配置，默认落在最右屏。
        if self.cfg.get("danmaku.sources.ws.offscreen", True):
            pos = (
                int(self.cfg.get("danmaku.sources.ws.window_x", 3840)),
                int(self.cfg.get("danmaku.sources.ws.window_y", 20)),
            )
        else:
            pos = (0, 0)
        self.cdp = ChromeCDP(
            self.room_url,
            chrome_path=str(self.cfg.get("danmaku.sources.ws.chrome_path", "") or ""),
            headless=headless,
            width=1280,
            height=900,
            position=pos,
            user_data_dir=self.cfg.data_dir / "chrome-live",
            extra_args=[
                "--mute-audio",
                "--disable-notifications",
                "--disable-session-crashed-bubble",
                "--hide-crash-restore-bubble",
            ],
        )
        await self.cdp.start()
        self.cdp.on_event("Network.webSocketFrameReceived", self._on_frame)
        await self.cdp.call("Network.enable")
        # start() 里已经导航过一次，再刷一次保证 WS 是在 Network.enable 之后建立的
        try:
            await self.cdp.call("Page.reload", {"ignoreCache": False})
        except Exception:  # noqa: BLE001
            pass
        self._last_frame = time.time()
        log.info("[ws] 开始旁听：%s", self.room_url)

        while True:
            try:
                item = await asyncio.wait_for(self._q.get(), timeout=5.0)
            except asyncio.TimeoutError:
                # 90 秒一帧都没有 → 页面可能掉线/白屏，刷一次
                if time.time() - self._last_frame > 90:
                    self._reloads += 1
                    log.warning("[ws] 90 秒没有数据，刷新页面（第 %d 次）", self._reloads)
                    try:
                        await self.cdp.call("Page.reload", {"ignoreCache": False})
                    except Exception as exc:  # noqa: BLE001
                        raise RuntimeError(f"刷新失败：{exc}")
                    self._last_frame = time.time()
                continue
            except asyncio.CancelledError:
                raise
            self.ok = True
            self.last_error = ""
            await self._emit(*item)

    # ---------- 事件映射 ----------
    async def _emit(self, method: str, data: bytes, msg_id: int) -> None:
        now = time.time()
        if msg_id:
            if msg_id in self._seen:
                return
            self._seen[msg_id] = now
            if len(self._seen) > 1200:
                cut = now - 300
                self._seen = {k: v for k, v in self._seen.items() if v >= cut}

        if method == "WebcastMemberMessage":
            d = fields(data)
            user = user_name(d, 2)
            if not user:
                return
            await self._push(LiveEvent(kind="enter", user=user))
            return

        if method == "WebcastChatMessage":
            d = fields(data)
            user = user_name(d, 2) or "观众"
            text = f_str(d, 3).strip()
            if not text:
                return
            await self._push(LiveEvent(kind="comment", user=user, text=text[:140]))
            return

        if method == "WebcastLikeMessage":
            d = fields(data)
            user = user_name(d, 4)
            cnt = f_int(d, 2) or 1
            await self._push(LiveEvent(kind="like", user=user or "观众", gift_count=cnt))
            return

        if method == "WebcastSocialMessage":
            d = fields(data)
            user = user_name(d, 2)
            if user:
                await self._push(LiveEvent(kind="follow", user=user))
            return

        if method == "WebcastGiftMessage":
            d = fields(data)
            user = user_name(d, 7) or user_name(d, 2) or "观众"
            name, cnt, diamond = gift_info(d)
            value = diamond * cnt if diamond else guess_gift_value(name, cnt)
            await self._push(
                LiveEvent(kind="gift", user=user, gift_name=name, gift_count=cnt, gift_value=value)
            )
            return

        if method == "WebcastRoomUserSeqMessage":
            d = fields(data)
            await self._maybe_online(f_int(d, 3), now)
            return

    async def _maybe_online(self, online: int, now: float) -> None:
        """真实在线人数，5 秒最多报一次，值没变就不报。"""
        if online <= 0 or online == self._online:
            return
        if now - self._online_at < 5:
            return
        self._online = online
        self._online_at = now
        await self._push(LiveEvent(kind="notice", text=str(online)))

    def snapshot(self) -> dict:
        d = super().snapshot()
        d.update({
            "frames": self._frames,
            "online": self._online,
            "reloads": self._reloads,
            "room_url": self.room_url,
        })
        return d

    async def stop(self) -> None:
        await super().stop()
        if self.cdp:
            try:
                await self.cdp.close()
            except Exception:  # noqa: BLE001
                pass
            self.cdp = None
