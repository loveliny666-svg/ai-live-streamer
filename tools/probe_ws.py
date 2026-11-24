"""探针：截获抖音直播间网页的 WebSocket 帧，看能不能拿到弹幕/进场的原始数据。

思路：让页面自己连它的 WS（签名由页面 JS 处理），我们通过 CDP 的
Network.webSocketFrameReceived 只做旁观，不碰加密、不碰签名。
"""
from __future__ import annotations

import asyncio
import base64
import gzip
import json
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.browser.cdp import ChromeCDP  # noqa: E402
from app.config import CFG  # noqa: E402

frames: list[dict] = []


def on_ws(params: dict) -> None:
    resp = params.get("response") or {}
    payload = resp.get("payloadData") or ""
    opcode = resp.get("opcode")
    frames.append({"opcode": opcode, "payload": payload, "ts": time.time()})


def try_gunzip(data: bytes) -> bytes:
    for wbits in (31, 47, 15):
        try:
            return gzip.decompress(data) if wbits != 15 else data
        except Exception:
            continue
    try:
        import zlib

        return zlib.decompress(data)
    except Exception:
        return data


def ascii_hits(data: bytes) -> list[str]:
    hits = []
    for key in (b"WebcastMemberMessage", b"WebcastChatMessage", b"WebcastGiftMessage",
                b"WebcastLikeMessage", b"WebcastSocialMessage", b"WebcastRoomMessage"):
        if key in data:
            hits.append(key.decode())
    return hits


async def main() -> None:
    url = str(CFG.get("danmaku.room_url", "") or "").strip()
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    if not url:
        print("没有配置 room_url")
        return
    print("打开", url)
    cdp = ChromeCDP(
        url,
        headless=False,
        width=1280,
        height=900,
        position=(-2600, 40),
        user_data_dir=CFG.data_dir / "chrome-probe",
        extra_args=["--mute-audio", "--disable-notifications"],
    )
    await cdp.start()
    cdp.on_event("Network.webSocketFrameReceived", on_ws)
    cdp.on_event("Network.webSocketCreated", lambda e: None)
    await cdp.call("Network.enable")

    print(f"旁听 {seconds} 秒…")
    await asyncio.sleep(seconds)

    print(f"\n收到 {len(frames)} 帧")
    ops = Counter(f["opcode"] for f in frames)
    print("opcode 分布", dict(ops))
    kinds = Counter()
    samples = []
    for f in frames[:400]:
        raw = base64.b64decode(f["payload"]) if f["opcode"] == 2 else f["payload"].encode("utf-8", "ignore")
        data = try_gunzip(raw)
        hits = ascii_hits(data)
        for h in hits:
            kinds[h] += 1
        if hits and len(samples) < 3:
            samples.append((hits, data[:200]))
    print("消息类型命中：", dict(kinds))
    for hits, head in samples:
        print("  样例", hits, head[:120])

    # 保存一帧原始数据供后续解析
    if frames:
        f = frames[min(3, len(frames) - 1)]
        raw = base64.b64decode(f["payload"]) if f["opcode"] == 2 else f["payload"].encode()
        data = try_gunzip(raw)
        Path("data/tmp/ws_frame.bin").write_bytes(data)
        print("已保存 data/tmp/ws_frame.bin", len(data), "字节")
        print("前 80 字节 hex:", data[:80].hex())

    await cdp.close()


if __name__ == "__main__":
    asyncio.run(main())
