"""面板源的解析层自测：不截图、不跑 OCR，只喂「识别出来的文字框」。

覆盖真实面板里会出现的几类行：
- 平台提示（诱导消费 / 防诈骗）不能当弹幕
- 「说点什么」输入框占位不能当弹幕
- 评论气泡没有昵称，user 应该是「观众」
- 进场 / 点赞 / 关注 带昵称
- 礼物被拆成两行时要拼回去
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.companion.ocr import Box  # noqa: E402
from app.config import CFG  # noqa: E402
from app.danmaku.ocr_source import OcrSource  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""))


def row(text: str, y: float, x: float = 60, h: float = 18) -> Box:
    w = max(12.0, len(text) * 13.0)
    return Box(text, 0.9, x, y, x + w, y + h)


async def main() -> None:
    got = []

    async def emit(ev):
        got.append(ev)

    src = OcrSource(CFG, emit, lambda: 0)

    boxes = [
        row("展示本场直播的礼物消息", 40),
        row("主播在直播中以不当方式诱导消费，请谨慎", 90),
        row("辨别。切勿私下交易，以防人身财产损失，", 115),
        row("谨防网络诈骗。", 140),
        row("打黑工，想尽一切办法摸鱼！！！", 200),
        row("yourself 来了", 240),
        row("老王 为主播点赞", 270),
        row("小李 关注了你", 300),
        row("臭宝 送出了", 330),
        row("小心心 x3", 360),
        row("说点什么...", 420),
    ]
    await src._emit_from(boxes)

    kinds = [(e.kind, e.user) for e in got]
    print("  解析结果：", kinds)

    check("平台提示被过滤", all("诱导消费" not in (e.text or "") for e in got))
    check("防诈骗提示被过滤", all("诈骗" not in (e.text or "") for e in got))
    check("输入框占位被过滤", all("说点什么" not in (e.text or "") for e in got))
    check("评论识别（无昵称→观众）",
          any(e.kind == "comment" and e.user == "观众" and "摸鱼" in e.text for e in got))
    check("进场识别", any(e.kind == "enter" and e.user == "yourself" for e in got))
    check("点赞识别", any(e.kind == "like" and e.user == "老王" for e in got))
    check("关注识别", any(e.kind == "follow" and e.user == "小李" for e in got))
    gift = [e for e in got if e.kind == "gift"]
    check("拆行的礼物拼回去了",
          bool(gift) and gift[0].user == "臭宝" and gift[0].gift_name == "小心心"
          and gift[0].gift_count == 3,
          str([(e.user, e.gift_name, e.gift_count) for e in gift]))

    # 同一条消息重复出现时只发一次
    before = len(got)
    await src._emit_from(boxes)
    check("重复画面不重复发", len(got) == before, f"{before} -> {len(got)}")

    print("\n[2] 掉字的平台提示也要拦住")
    noisy = [
        row("主播在直播中以不当方式诱导消，请谨值", 60),
        row("辨别。切勿私下交，以防人身产损实", 90),
        row("谨防网络诈。", 120),
    ]
    before = len(got)
    await src._emit_from(noisy)
    check("掉字提示没被当成弹幕", len(got) == before, f"多出 {len(got) - before} 条")

    print("\n[3] 消息从面板消失后可以再来一次")
    await src._emit_from([])   # 第一轮：看不见
    await src._emit_from([])   # 第二轮：确认消失
    before = len(got)
    await src._emit_from([row("yourself 来了", 240)])
    check("消失后重新进场能再次发", len(got) == before + 1, f"{before} -> {len(got)}")

    print("\n[4] 自动生成的用户 ID 被读坏时不要当评论")
    before = len(got)
    await src._emit_from([row("7田户1765750190075±7", 200)])
    check("长串数字的残句被丢掉", len(got) == before, f"多出 {len(got) - before} 条")

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
