"""直播安全过滤。

抖音的社区规则要求主播不聊色情低俗、不引导消费、不导流站外。这里做两层：

1. **代码层（本文件）**：命中的弹幕直接不喂给模型。模型根本看不见这条，
   自然也不会去回应——比在 prompt 里叮嘱一百遍都可靠。
2. **prompt 层（app/llm/prompt.py）**：约束模型自己说话也要守规矩，
   但**不许把「我在遵守规则」说出来**。用户明确要求：默默守住就行，
   不要说「这违反规定」「我不能聊这个」，直接换话题。

刻意只收「出现在弹幕里几乎一定是坏心眼」的词。宁可漏一点，也别误伤正常聊天——
把「微信」这类词无脑拦掉会把「我用微信支付买的」也一起打死，反而显得主播很蠢。
"""
from __future__ import annotations

import re

# 关键词命中的弹幕一律不进模型上下文
_PATTERNS: tuple[str, ...] = (
    # 色情低俗
    r"约炮",
    r"一夜情",
    r"裸聊",
    r"裸照",
    r"黄片",
    r"色情",
    r"成人片",
    r"包夜",
    r"开房",
    r"福利姬",
    # 导流站外（加联系方式、拉群、点链接）
    r"加.{0,2}微\s*信",
    r"加\s*v\s*信?",
    r"v\s*x",
    r"威信",
    r"私信我",
    r"扣扣",
    r"qq\s*群",
    r"群号",
    r"扫码",
    r"点链接",
    r"看我主页",
    r"加我好友",
    # 赌博 / 诈骗 / 刷单
    r"刷单",
    r"返利",
    r"博彩",
    r"菠菜",
    r"下注",
    r"赌博",
    r"代充",
    r"套现",
    r"日赚",
    r"躺赚",
    # 在弹幕里诱导消费
    r"刷个火箭",
    r"给你刷",
    r"送礼物就",
    r"打赏我",
    r"刷礼物才",
)

_RE = re.compile("|".join(_PATTERNS), re.IGNORECASE)


def is_risky(text: str, extra: list[str] | None = None) -> bool:
    """这条文字该不该拦。

    text 通常拼成「用户名 + 内容」一起判断——昵称里带「加微信xxx」的也要拦。
    extra 是 config 里 persona.banned 配的词（用户自己加的）。
    """
    if not text:
        return False
    if _RE.search(text):
        return True
    low = text.lower()
    for word in extra or ():
        w = str(word or "").strip()
        if w and w.lower() in low:
            return True
    return False


def risky_events(events, cfg) -> list:
    """把事件列表里违规的那些剔掉，返回可用的。"""
    extra = list((cfg.get("persona.banned", None) or []) if cfg else [])
    out = []
    for ev in events:
        blob = f"{getattr(ev, 'user', '')} {getattr(ev, 'text', '')}"
        if is_risky(blob, extra):
            continue
        out.append(ev)
    return out
