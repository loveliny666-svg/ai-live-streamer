"""WebSocket 源的解码自测：自己造 protobuf 帧，验证 method / 昵称 / 内容 / 礼物。

不联网、不开浏览器，纯离线校验字段号；哪天抖音改了协议，这里会先炸。
"""
from __future__ import annotations

import asyncio
import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import CFG  # noqa: E402
from app.danmaku.ws_source import WsSource, decode_frame  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""))


def varint(n: int) -> bytes:
    out = b""
    while True:
        b = n & 0x7F
        n >>= 7
        if n:
            out += bytes([b | 0x80])
        else:
            return out + bytes([b])


def fld(no: int, val) -> bytes:
    if isinstance(val, int):
        return varint((no << 3) | 0) + varint(val)
    return varint((no << 3) | 2) + varint(len(val)) + val


def user(name: str, uid: int = 10086) -> bytes:
    return fld(1, uid) + fld(3, name.encode())


def frame(method: str, payload: bytes, msg_id: int) -> bytes:
    msg = fld(1, method.encode()) + fld(2, payload) + fld(3, msg_id)
    resp = fld(1, msg)
    return fld(1, 1) + fld(8, gzip.compress(resp))


async def main() -> None:
    print("[1] 解帧")
    f = frame("WebcastMemberMessage", fld(2, user("赵思兰")) + fld(3, 11), 777)
    msgs = decode_frame(f)
    check("解出 1 条消息", len(msgs) == 1, str(len(msgs)))
    check("method 正确", msgs and msgs[0][0] == "WebcastMemberMessage", str(msgs[:1]))
    check("msg_id 正确", msgs and msgs[0][2] == 777, str(msgs[0][2] if msgs else None))

    got = []

    async def emit(ev):
        got.append(ev)

    src = WsSource(CFG, emit, "https://live.douyin.com/0")

    print("\n[2] 进场")
    await src._emit(*msgs[0])
    check("进场带昵称", len(got) == 1 and got[0].kind == "enter" and got[0].user == "赵思兰",
          str([(e.kind, e.user) for e in got]))

    print("\n[3] 同一条消息再来一次不应重复")
    await src._emit(*msgs[0])
    check("msg_id 去重", len(got) == 1, f"{len(got)} 条")

    print("\n[4] 弹幕")
    chat = frame("WebcastChatMessage", fld(2, user("小王")) + fld(3, "主播在吗".encode()), 778)
    await src._emit(*decode_frame(chat)[0])
    check("弹幕带昵称和内容",
          got[-1].kind == "comment" and got[-1].user == "小王" and got[-1].text == "主播在吗",
          str((got[-1].kind, got[-1].user, got[-1].text)))

    print("\n[5] 礼物")
    gift_struct = fld(5, "小心心".encode()) + fld(10, 1)
    gift = frame("WebcastGiftMessage", fld(7, user("老王")) + fld(5, 3) + fld(15, gift_struct), 779)
    await src._emit(*decode_frame(gift)[0])
    check("礼物名字/数量/抖币",
          got[-1].kind == "gift" and got[-1].user == "老王"
          and got[-1].gift_name == "小心心" and got[-1].gift_count == 3 and got[-1].gift_value == 3,
          str((got[-1].user, got[-1].gift_name, got[-1].gift_count, got[-1].gift_value)))

    print("\n[6] 真实在线人数")
    seq = frame("WebcastRoomUserSeqMessage", fld(2, 829) + fld(3, 7), 780)
    await src._emit(*decode_frame(seq)[0])
    check("在线人数上报为 notice", got[-1].kind == "notice" and got[-1].text == "7",
          str((got[-1].kind, got[-1].text)))

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
