"""探针：实时解析直播间 WebSocket，把进场/评论/礼物/点赞打印出来。"""
from __future__ import annotations

import asyncio
import base64
import gzip
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.browser.cdp import ChromeCDP  # noqa: E402
from app.config import CFG  # noqa: E402


def read_varint(b: bytes, i: int) -> tuple[int, int]:
    shift = 0
    val = 0
    while i < len(b):
        x = b[i]
        i += 1
        val |= (x & 0x7F) << shift
        if not x & 0x80:
            return val, i
        shift += 7
    raise ValueError("varint")


def parse(b: bytes) -> dict[int, list]:
    out: dict[int, list] = {}
    i = 0
    while i < len(b):
        key, i = read_varint(b, i)
        f, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(b, i)
        elif wt == 2:
            ln, i = read_varint(b, i)
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
        out.setdefault(f, []).append(v)
    return out


def s(d: dict, field: int) -> str:
    v = d.get(field)
    if not v:
        return ""
    try:
        return bytes(v[0]).decode("utf-8", "ignore")
    except Exception:
        return ""


def n(d: dict, field: int) -> int:
    v = d.get(field)
    return int(v[0]) if v else 0


def decode_frame(raw: bytes) -> list[dict]:
    top = parse(raw)
    payload = top.get(8, [b""])[0]
    if not payload:
        return []
    try:
        inner = gzip.decompress(payload)
    except Exception:
        inner = payload
    resp = parse(inner)
    out = []
    for body in resp.get(1, []):
        m = parse(body)
        method = s(m, 1)
        data = m.get(2, [b""])[0]
        out.append({"method": method, "data": data})
    return out


def describe(method: str, data: bytes) -> str:
    d = parse(data)
    if method == "WebcastMemberMessage":
        user = parse(d.get(2, [b""])[0])
        return f"进场 {s(user, 3)}（{s(user, 38)}） 在线={n(d, 3)}"
    if method == "WebcastChatMessage":
        user = parse(d.get(2, [b""])[0])
        return f"评论 {s(user, 3)}：{s(d, 3)}"
    if method == "WebcastLikeMessage":
        user = parse(d.get(4, [b""])[0])
        return f"点赞 {s(user, 3)} count={n(d, 2)} total={n(d, 3)}"
    if method == "WebcastSocialMessage":
        user = parse(d.get(2, [b""])[0])
        return f"社交 {s(user, 3)} action={n(d, 4)} type={n(d, 3)}"
    if method == "WebcastGiftMessage":
        keys = {k: (len(v[0]) if isinstance(v[0], bytes) else v[0]) for k, v in sorted(d.items())}
        user = parse(d.get(7, [b""])[0]) if d.get(7) else {}
        return f"礼物 user={s(user, 3)} fields={keys}"
    return ""


async def main() -> None:
    seconds = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    url = str(CFG.get("danmaku.room_url", "") or "").strip()
    cdp = ChromeCDP(url, headless=False, width=1280, height=900, position=(-2600, 40),
                    user_data_dir=CFG.data_dir / "chrome-probe",
                    extra_args=["--mute-audio", "--disable-notifications"])
    await cdp.start()
    seen: dict[str, float] = {}
    stats = {"frames": 0, "msgs": 0}

    def on_frame(params: dict) -> None:
        resp = params.get("response") or {}
        if resp.get("opcode") != 2:
            return
        stats["frames"] += 1
        try:
            raw = base64.b64decode(resp.get("payloadData") or "")
        except Exception:
            return
        try:
            msgs = decode_frame(raw)
        except Exception as exc:  # noqa: BLE001
            print("解析失败", exc)
            return
        for m in msgs:
            stats["msgs"] += 1
            desc = describe(m["method"], m["data"])
            if desc:
                print(f"[{time.strftime('%H:%M:%S')}] {desc}")

    cdp.on_event("Network.webSocketFrameReceived", on_frame)
    await cdp.call("Network.enable")
    await asyncio.sleep(seconds)
    print(f"\n帧 {stats['frames']}，消息 {stats['msgs']}")
    await cdp.close()


if __name__ == "__main__":
    asyncio.run(main())
