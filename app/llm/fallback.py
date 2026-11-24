"""没有 API Key、或模型连续失败时的兜底「规则脑」。

作用不是替代模型，而是保证直播间永远不会突然安静。
"""
from __future__ import annotations

import random
import re
import time
from typing import Any

_IDLE = [
    "这个需求写得，跟让我用中文写汇编一样。",
    "测试全过了，说明测试写得不对。",
    "先摸五分钟鱼，就五分钟。",
    "变量名想不出来了，先叫 data2 吧。",
    "老板说这改动很小，我信了。",
    "能跑就行，别动它。",
    "刚那个 bug 我复现了三次，第四次它自己好了。",
    "写注释的时间比写代码长，这很正常。",
    "再改最后一次，我说真的。",
    "这段代码是上一个人写的，上一个人也是我。",
    "咖啡凉了，说明我坐太久了。",
    "这个循环我看了十分钟，才发现没退出条件。",
    "又到了每天最快乐的时刻——等编译。",
    "需求文档只有一句话，我写了三百行。",
    "我不困，我只是眼睛想休息。",
    "秦始皇要是有需求文档，长城能少返工两回。",
    "明朝那个内阁制，说白了就是分布式系统，皇帝是单点故障。",
    "古人一天吃两顿饭，我们加班到十点吃第三顿，这叫产业升级。",
    "在没有搜索引擎的年代查资料，那才叫真的 debug。",
]

_GIFT = [
    "谢谢{user}的{gift}，这下今晚电费有着落了。",
    "{user}送{gift}，破费了，我多写两行。",
    "哟，{user}的{gift}收到了，谢谢啊。",
    "{gift}收到了，{user}你这也太客气。",
    "谢{user}，{gift}我收下了，继续干活。",
]

_FOLLOW = [
    "谢谢{user}关注，别学我，这行不好干。",
    "{user}关注了，行，以后一起熬夜。",
    "感谢{user}，这直播间居然还有人关注。",
]

_ENTER = [
    "{user}来了，随便坐，地上干净。",
    "欢迎{user}，正好赶上我在改 bug。",
]

_QUESTION = [
    "你问{short}？说实话我也在查。",
    "{short}这个事儿，等我跑完测试跟你说。",
    "问到点子上了，{short}我上周才踩过坑。",
]

_ANSWER_HINT = [
    "这个得看情况，反正能跑。",
    "理论上可以，实际上别试。",
    "我建议你问产品经理，他比较敢说。",
    "你说的这个，我文档里好像写过，但我不确定了。",
]


class FallbackBrain:
    def __init__(self, cfg):
        self.cfg = cfg
        self._last_kind = ""
        self._last_at = 0.0

    def _pick(self, pool: list[str], **kw: Any) -> str:
        line = random.choice(pool)
        try:
            return line.format(**kw)
        except Exception:  # noqa: BLE001
            return line

    @staticmethod
    def _short(text: str, n: int = 12) -> str:
        t = re.sub(r"\s+", "", text or "")
        return t[:n] + ("…" if len(t) > n else "")

    def think(self, events: list, scene: str) -> dict[str, Any] | None:
        """返回 {"tool": "say", "args": {...}} 或 None。"""
        now = time.time()
        # 刚说过就缓一下，别刷屏
        if now - self._last_at < 6:
            return None

        gifts = [e for e in events if e.kind == "gift"]
        follows = [e for e in events if e.kind == "follow"]
        enters = [e for e in events if e.kind == "enter"]
        comments = [e for e in events if e.kind == "comment" and e.text.strip()]

        if gifts:
            g = max(gifts, key=lambda e: e.gift_value)
            self._last_at = now
            return {
                "tool": "say",
                "args": {
                    "text": self._pick(_GIFT, user=g.user, gift=g.gift_name or "礼物"),
                    "emotion": "happy",
                },
            }
        if follows:
            self._last_at = now
            return {
                "tool": "say",
                "args": {"text": self._pick(_FOLLOW, user=follows[-1].user), "emotion": "happy"},
            }
        if comments:
            c = comments[-1]
            self._last_at = now
            if re.search(r"[?？]|吗|什么|怎么|为啥|为什么|能不能", c.text):
                return {
                    "tool": "say",
                    "args": {
                        "text": self._pick(_QUESTION, short=self._short(c.text))
                        if random.random() < 0.5
                        else random.choice(_ANSWER_HINT),
                        "emotion": "normal",
                        "to": c.user,
                    },
                }
            return {
                "tool": "say",
                "args": {
                    "text": f"{c.user}，{self._pick(_ANSWER_HINT)}",
                    "emotion": "normal",
                    "to": c.user,
                },
            }
        if enters and random.random() < 0.4:
            self._last_at = now
            return {
                "tool": "say",
                "args": {"text": self._pick(_ENTER, user=enters[-1].user), "emotion": "normal"},
            }
        # 完全没人说话 → 自言自语
        if now - self._last_at >= float(self.cfg.get("behavior.idle_talk_sec", 150)) * 0.5:
            self._last_at = now
            return {"tool": "say", "args": {"text": random.choice(_IDLE), "emotion": "tired"}}
        return None
