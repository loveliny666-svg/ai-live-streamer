"""探针：解析抖音直播间的 PushFrame（protobuf + gzip），看消息结构。"""
from __future__ import annotations

import gzip
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


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
    raise ValueError("varint 越界")


def parse(b: bytes) -> list[tuple[int, int, object]]:
    """通用 protobuf 解析：返回 [(field_no, wire_type, value)]。"""
    out = []
    i = 0
    while i < len(b):
        key, i = read_varint(b, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = read_varint(b, i)
            out.append((field, wt, v))
        elif wt == 2:
            ln, i = read_varint(b, i)
            out.append((field, wt, b[i:i + ln]))
            i += ln
        elif wt == 5:
            out.append((field, wt, b[i:i + 4]))
            i += 4
        elif wt == 1:
            out.append((field, wt, b[i:i + 8]))
            i += 8
        else:
            break
    return out


def show(b: bytes, depth: int = 0, max_depth: int = 3, limit: int = 40) -> None:
    try:
        fields = parse(b)
    except Exception:
        return
    for n, (f, wt, v) in enumerate(fields[:limit]):
        pad = "  " * depth
        if wt == 2 and isinstance(v, (bytes, bytearray)):
            txt = ""
            try:
                s = v.decode("utf-8")
                if s.isprintable() and len(s) > 1:
                    txt = f'  "{s[:60]}"'
            except Exception:
                pass
            print(f"{pad}{f}: bytes[{len(v)}]{txt}")
            if depth < max_depth and not txt:
                show(bytes(v), depth + 1, max_depth, limit)
        else:
            print(f"{pad}{f}: {v}")


def main() -> None:
    p = Path("data/tmp/ws_frame.bin")
    if not p.exists():
        print("没有 ws_frame.bin，先跑 probe_ws.py")
        return
    raw = p.read_bytes()
    print(f"PushFrame {len(raw)} 字节")
    fields = parse(raw)
    print("顶层字段：", [(f, wt, len(v) if isinstance(v, bytes) else v) for f, wt, v in fields])

    payload = next((v for f, wt, v in fields if f == 8 and isinstance(v, bytes)), b"")
    if not payload:
        print("没找到 field 8 payload")
        return
    try:
        inner = gzip.decompress(payload)
        print(f"gzip 解压后 {len(inner)} 字节")
    except Exception as exc:
        print("解压失败", exc)
        inner = payload

    print("\n--- Response 顶层 ---")
    show(inner, 0, 1, 10)

    print("\n--- 逐条消息 ---")
    for f, wt, v in parse(inner):
        if f != 1 or not isinstance(v, bytes):
            continue
        m = parse(v)
        method = ""
        body = b""
        for mf, mwt, mv in m:
            if mf == 1 and isinstance(mv, bytes):
                method = mv.decode("utf-8", "ignore")
            if mf == 2 and isinstance(mv, bytes):
                body = mv
        print(f"\n[{method}] body {len(body)}B")
        if method in ("WebcastMemberMessage", "WebcastChatMessage", "WebcastLikeMessage"):
            show(body, 1, 2, 12)


if __name__ == "__main__":
    main()
