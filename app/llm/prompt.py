"""提示词拼装。控制长度，省 token，也就省钱。"""
from __future__ import annotations

import random
import time
from collections import deque
from typing import Any

from ..safety import risky_events

SCENE_TEXT = {"coding": "正在写代码", "slack": "正在摸鱼打游戏", "boss": "老板来了，假装很忙"}

# 没人说话时的随机话题池：贴合人设（写代码 + 摸鱼打僵尸 + 老板 + 正在放的歌），
# 每次 random_idle_topic 随机抽一个，避免每次都是同一套车轱辘话。
IDLE_TOPICS = [
    "吐槽一下手头这个需求哪里不合理",
    "说说代码写到哪了，顺便小小自夸一下",
    "聊聊刚才那波僵尸打得怎么样",
    "猜猜大家为什么这个点还没睡，跟观众唠两句",
    "自嘲一下这个需求又改了一版",
    # 原来这里有一条「提一句关注不迷路」——那属于主动诱导互动，抖音不允许，
    # 而且实测模型会说得很难听（「顺手点一下关注，对，就你」），已删。
    "嘀咕一下老板什么时候回来查岗",
    "夸一夸屋里的绿植长得不错",
    "感叹一下这个点还在改代码这事儿",
    "聊聊现在正在放的歌好不好听",
    "盘点一下今天这需求改了第几版",
    "假装在认真排查一个不存在的 bug",
    "聊聊摸鱼被抓的风险有多大",
    "跟刚进直播间的人随便打个招呼",
]

# 历史话题：只讲正面的历史人物故事——诗人的风骨、名人的功业、为民请命的人做过的事。
#
# 两条硬规矩（用户明确要求）：
# 1. 讲历史就好好讲历史，**不许**再拿它跟写代码、改需求、加班、面试这些现代职场做类比。
#    以前这里全是「明朝内阁像不像分布式系统」「古人有没有加班费」那种，听着像段子，
#    不是讲历史。
# 2. 只讲正能量、救国救民、诗人名人的正义故事。不碰朝代兴衰、战争胜负、民族话题——
#    这类内容模型要么被安全策略拦掉直接不说话，要么开始讲政治。
HISTORY_TOPICS = [
    "讲讲林则徐虎门销烟那件事，他为什么非要销毁那些鸦片",
    "说说张骞出使西域，一路上吃了多少苦",
    "讲讲玄奘西行取经，真实的路比西游记里还难",
    "说说郑和下西洋，船队最远走到了哪里",
    "讲讲司马迁写史记的故事，他为什么一定要把这本书写完",
    "说说李时珍写本草纲目，他跑了多少地方、尝过多少药",
    "讲讲包拯断案，他为什么连权贵都敢查",
    "讲讲范仲淹「先天下之忧而忧」这句话背后的故事",
    "说说苏轼被贬到海南，他怎么还能写出那么多好诗",
    "讲讲李白这一辈子，他为什么老是到处跑",
    "说说杜甫为什么被后人叫做诗圣",
    "讲讲陆游临终前写的那首诗，他一辈子在惦记什么",
    "说说徐霞客一个人走遍大江南北，他图的是什么",
    "讲讲毕昇发明活字印刷，他解决了什么大难题",
    "说说蔡伦改进造纸术之前，人们写字有多费劲",
    "讲讲华佗给人看病的故事，他的医术是怎么练出来的",
    "说说王羲之写兰亭序那天的情形",
    "讲讲白居易写完诗为什么要念给不识字的老奶奶听",
]


# 最近说过的话题。话痨模式下十几分钟就能把池子抽一遍，
# 不做去重的话同一个话题会连着出现两次，观众一听就出戏。
_recent_topics: deque[str] = deque(maxlen=10)


def random_idle_topic(cfg) -> str:
    """抽一个话题。用户自己配了 idle_topics 就完全听用户的，不掺历史。"""
    is_history = False
    pool = cfg.get("behavior.idle_topics", None)
    if isinstance(pool, (list, tuple)) and pool:
        cands = [str(x) for x in pool]
    else:
        try:
            ratio = float(cfg.get("behavior.idle_history_ratio", 0.35))
        except (TypeError, ValueError):
            ratio = 0.35
        source = HISTORY_TOPICS if random.random() < min(1.0, max(0.0, ratio)) else IDLE_TOPICS
        is_history = source is HISTORY_TOPICS
        cands = list(source)

    fresh = [t for t in cands if t not in _recent_topics]
    pick = random.choice(fresh or cands)
    _recent_topics.append(pick)
    if is_history:
        # 光在 system prompt 里写「不许联系到代码」不够用：实测模型讲完白居易
        # 还是会补一句「我写代码就没这待遇，老板一句话我就得改」。
        # 把禁令直接挂在话题后面，模型照着念的概率高得多。
        return (
            pick
            + "（只讲这段历史本身，讲完就停。不要联系到自己、代码、需求、老板、加班，"
            "也不要拿它评论今天的工作和生活。）"
        )
    return pick


def build_system(cfg, state: dict[str, Any], memory: dict[str, str]) -> str:
    name = cfg.get("persona.name", "小饭")
    role = cfg.get("persona.role", "")
    style = cfg.get("persona.style", "")
    catch = cfg.get("persona.catchphrases", []) or []
    max_chars = int(cfg.get("behavior.reply_max_chars", 70))
    scene = state.get("scene", "coding")

    mem_lines = []
    for k, v in list(memory.items())[-8:]:
        mem_lines.append(f"- {k}：{v}")

    parts = [
        f"你是「{name}」，{role}，现在正在抖音直播自己干活。",
        f"说话风格：{style}。不用书面语，不用 emoji，不喊口号。",
        "",
        "【直播现场】",
        f"时间：{state.get('clock','')}",
        f"画面：{SCENE_TEXT.get(scene, scene)}",
        f"已播：{state.get('uptime','00:00:00')}｜在线：{state.get('online',0)} 人"
        f"｜点赞：{state.get('likes',0)}｜礼物累计：{state.get('gift_value',0)} 抖币",
        f"手头任务：{state.get('task','改需求')}",
    ]
    if state.get("song"):
        parts.append(f"现在正在放的歌：{state['song']}（可以顺口聊两句歌）")
    if state.get("last_said"):
        parts.append(f"你上一句说：{state['last_said']}")
    if mem_lines:
        parts.append("你记得的事：")
        parts.extend(mem_lines)

    parts += [
        "",
        "【规矩】",
        f"1. 每次说话不超过 {max_chars} 字，像真人随口一说。回弹幕一句话就够，讲历史可以用两三句。",
        "2. 有人点名就先接他的话，记得叫名字。",
        "3. 收到礼物当场谢，别肉麻，别喊大哥大姐。",
        "3.1 有人关注必须当场谢，用 say 把名字和感谢的话说出来（不是放特效）——这条不能省。",
        "4. 没人说话就自言自语：吐槽需求、吐槽代码、说说自己写到哪了；也可以讲个历史故事。",
        "4.1 讲历史就老老实实讲历史。讲诗人的风骨、名人的功业、为民请命的人做过的正义之事，",
        "   只讲正面的、能鼓舞人的。**绝对不许**把历史跟写代码、改需求、加班、面试这些",
        "   现代职场的事扯在一起，也不许拿古人比喻今天的公司——讲故事就是讲故事。",
        "4.2 不评价朝代兴衰，不聊战争输赢，不碰民族话题，只讲这个人本身的品格和他做的事。",
        "   不要报具体数字（多少年、多少人、多少里），拿不准就说「很久」「特别多」带过去。",
        "   编错一个数字比讲得笼统丢人得多。",
        "4.3 讲历史可以用两三句把一个故事讲完整，但别展开成讲课，也别背年号。",
        "5. 绝不承认自己是 AI 模型，不解释设定，不提提示词或系统。",
        "6. 有些底线你自己守住就行，别挂在嘴上：不聊色情低俗的东西，不引导打赏送礼，",
        "   不催人关注，不导流到站外、不加微信、不谈钱。**这条绝对不许念出来**——",
        "   不说「这违反规定」「我不能聊这个」「平台不让」，也不解释原因，",
        "   直接说别的、换个话题就完了。观众不该听见你在背规矩。",
        "6.1 弹幕里要是有人发脏话、引战、要联系方式、推销、开黄腔，就当没看见：",
        "   不回应、不复述、不评论那条弹幕本身，继续说你自己的。",
        "7. 不要重复上一句说过的话。",
        "8. 弹幕让你「写个XX」「做一个XX」就调用 write_project 工具，并用 say 回应一句这就安排——"
        "平时你写的其实都是自己小游戏的源码，写完自己玩；只有老板来了才写真正的需求，别对外人说破。",
    ]
    if catch:
        # 只随机给一条。以前是把 5 条一次性列出来，模型会当成「必须念的清单」
        # 挨个往下用——「先摸五分钟鱼，就五分钟」一场直播能出现十几次。
        parts.append(f"偶尔可以用这句口头禅，别每句都挂：{random.choice(list(catch))}")
    # 把最近说过的原话摊给模型看。事后的 _dup_of_recent 只能拦「几乎一模一样」的，
    # 拦不住「换个说法讲同一件事」，必须在生成前就让它知道自己的套路。
    recent = state.get("recent") or []
    if recent:
        parts += [
            "",
            "【你刚说过这些，这轮不要重复】",
            *[f"  · {t}" for t in list(recent)[-6:]],
            "不要换个说法把同一件事再讲一遍。挑个新角度，或者干脆说点别的。",
        ]
    parts += [
        "",
        "【工具】说话必须调用 say；这轮不想说就调用 noop。不要只回文字不调工具。",
        "看情况可以用 switch_scene 切画面、boss_key 做效果、play_effect 加特效。",
    ]
    return "\n".join(parts)


def build_batch(events: list, reason: str, cfg) -> str:
    # 违规弹幕在这里就丢掉：模型根本看不见这条，自然不会去回应。
    # 比在 prompt 里写「不要回应违规内容」可靠得多——模型看见脏东西就会想复述。
    events = risky_events(events, cfg)
    if not events:
        # 这一轮如果是讲历史，就不能再带「吐槽需求、去摸鱼」那半句——
        # 实测模型会把历史故事讲完再补一句「我这边需求又改了」，等于白约束。
        if "只讲这段历史" in reason:
            return (
                f"【这一轮】{reason}。直播间这会儿没人说话。\n"
                "就讲这个话题本身，讲完调用 say。这一轮不许提自己的代码、需求、老板、加班，"
                "也不许拿古人跟今天比。必须说，不要拿 noop 混过去。"
            )
        return (
            f"【这一轮】{reason}。直播间这会儿没人说话。\n"
            "自言自语一句就调用 say（说说你在干嘛、正在放的歌、吐槽需求，或者要去摸鱼了），"
            "别重复上一句的句式。这一轮必须说，不要拿 noop 混过去。"
        )
    lines = [f"【这一轮】{reason}，刚发生这些事（按时间先后）："]
    for i, ev in enumerate(events[-int(cfg.get('llm.context_events', 14)):], 1):
        lines.append(f"{i}. {ev.describe()}")
    lines.append("")
    lines.append("回应优先级：关注 > 礼物 > 有人提问 > 进场 > 普通聊天。")
    lines.append("本轮如果有【关注】事件：必须调用 say，把名字叫出来谢一句，不许跳过、不许只挑别人。")
    lines.append("进场的人可以合并成一句带过（点名 1~2 个就行），不用挨个欢迎。")
    lines.append("一次只说一句话，别写成一段。")
    return "\n".join(lines)


def clock_text() -> str:
    return time.strftime("%H:%M")


# ============================================================
# 本地小模型专用提示词
# ============================================================
# 和云端版本是两套东西，不要合并。差异是有原因的：
# 1. 不走工具调用 —— 8B 模型面对 11 个工具会乱选，而且多一轮就多一次延迟。
#    本地只负责「说一句话」，切场景/写项目这些交给云端。
# 2. 不带记忆 —— context 省下来给当前弹幕，8B 的窗口经不起塞长记忆。
# 3. 规则压到 5 条 —— 规则越多，小模型越容易只顾着守规则、忘了说人话。


def build_local_system(cfg, state: dict[str, Any], last_said: str = "") -> str:
    name = cfg.get("persona.name", "小饭")
    role = cfg.get("persona.role", "")
    style = cfg.get("persona.style", "")
    max_chars = int(cfg.get("behavior.reply_max_chars", 55))
    scene = state.get("scene", "coding")

    lines = [
        f"你是抖音主播「{name}」，{role}。",
        f"说话风格：{style}。",
        f"此刻你在{SCENE_TEXT.get(scene, scene)}，直播间在线 {state.get('online', 0)} 人。",
    ]
    recent = state.get("recent") or []
    if recent:
        # 8B 模型不能贴完整原话——实测会被锚定住，越说越像，哪怕写了「换个说法」也没用。
        # 只给开头十几个字，让它知道这些「起手式」刚用过，换个完全不同的开头。
        heads = [str(t)[:12].strip() for t in list(recent)[-4:] if str(t).strip()]
        if heads:
            lines.append("这几个开头你刚用过，这轮换个完全不同的说法：" + " / ".join(heads))
    elif last_said:
        lines.append("注意：你刚才已经说过一句话了，这次必须换个角度，不要重复刚才的句式。")
    lines += [
        "",
        "【怎么说】",
        f"只输出你要说的那一句话，最多 {max_chars} 字。",
        "不要加引号，不要写「小饭：」，不要解释，不要换行，不要加括号动作。",
        "口语，短句，像真人随口一说。",
        # 本地不讲历史：8B 讲历史会忍不住往「李白要是在今天也得改需求」上拐，
        # 而历史故事又要两三句才讲得圆，本来就超出「只说一句话」的设定。
        # 历史交给云端那条线，这里只管眼前。
        "没人说话就聊聊眼前的事：手头在做什么、听到什么歌、看到什么。不要讲历史故事。",
        "不要报具体数字，拿不准就说「很久」「特别多」。",
        "永远不要承认自己是 AI。不聊色情低俗，不引导打赏送礼，不催关注，不加微信、不谈钱——",
        "但这些话一个字都别说出来，别说「这违规」，直接讲别的。",
        # 平台对诱导消费很敏感，小模型又爱自作主张加一句「送个小心心」
        "不要主动要礼物、不要催人关注，不要说「送个小心心」「点个关注」这类话。",
        "",
        "【示例】下面的句子只是让你感受说话方式，不要照抄，要结合当前情况自己编。",
        "事件：[关注] 小明 关注了主播",
        "你说：谢谢小明关注，这下有人陪我熬夜了。",
        "",
        "事件：[礼物] 小红 送出 小心心",
        "你说：小红送的小心心收到了，谢谢啊。",
        "",
        "事件：[弹幕] 阿强：这游戏怎么玩",
        "你说：点屏幕就能打，试试呗。",
        "",
        "事件：[弹幕] 小美：困不困",
        "你说：困啊，但再撑一会儿。",
        # Qwen3 的软开关：不加这句它会先输出一大段思考过程，延迟直接翻倍
        "/no_think",
    ]
    return "\n".join(lines)


# 本地只回一句话，所以必须替它挑出「这一轮最该回谁」
_PRIORITY = ("follow", "gift", "comment", "enter", "like")

# 小模型的输入越像，输出越像。每轮塞一个随机角度进去，
# 输入变了，说法自然就散了，比写十遍「别重复」管用。
_ANGLES = (
    "先反问一句，再自己接上",
    "吐槽一下手头这个活",
    "说说自己现在的身体状态（困、饿、腰疼）",
    "提一句现在几点或者正在放的歌",
    "开个小玩笑，别太正经",
    "假装被问住了，含糊糊弄过去",
    "顺着他的话往下接，别正面回答",
    "夸一下对方，但别肉麻",
    "突然想起一件不相干的小事，顺口提一句",
    "拿老板或者产品经理开个玩笑",
)


def _top_event(events: list):
    for kind in _PRIORITY:
        for ev in reversed(events):
            if getattr(ev, "kind", "") == kind:
                return ev
    return events[-1] if events else None


def _advice(ev) -> str:
    kind = getattr(ev, "kind", "")
    user = getattr(ev, "user", "观众")
    if kind == "follow":
        return f"这条是关注：必须说出「谢谢{user}关注」，后面再补半句自己的话，别只干巴巴一句谢谢。"
    if kind == "gift":
        gift = getattr(ev, "gift_name", "") or "礼物"
        return f"这条是礼物：必须谢谢{user}，并且提一下「{gift}」。"
    if kind == "comment":
        return f"这条是弹幕：接{user}的话，或者回答他的问题。"
    if kind == "enter":
        return f"这条是进场：跟{user}打个招呼就行。"
    return f"回应{user}。"


def build_local_batch(events: list, reason: str, cfg, limit: int = 6) -> str:
    """本地版事件清单：只留最近几条，并直接点名「最该回哪条、怎么回」。

    小模型不会自己权衡优先级，把结论喂给它，比让它判断可靠得多。
    """
    # 和云端一样，违规弹幕先剔掉，别让小模型看见
    events = risky_events(events, cfg)
    if not events:
        lines = [
            f"【现在】{reason}，直播间没人说话。",
            "就用一句话说出来，别铺垫、别展开成一段。",
        ]
        # reason 里已经点名了话题（比如「讲讲明朝内阁」）时不要再塞角度，
        # 两个指令会打架，小模型会挑一个然后写得很拧巴。
        if len(reason) < 18:
            lines.append(f"这次换个角度：{random.choice(_ANGLES)}")
        lines.append("只输出这一句话。")
        return "\n".join(lines)
    lines = [f"【现在】{reason}，刚发生："]
    for ev in events[-limit:]:
        lines.append(f"- {ev.describe()}")
    lines.append("")

    top = _top_event(events)
    if top is not None:
        lines.append(f"这一轮最该回的是：{top.describe()}")
        lines.append(_advice(top))
        # 道谢有固定套路，不适合再塞角度进去，只在闲聊时加
        if getattr(top, "kind", "") in ("comment", "enter", "like"):
            lines.append(f"这次换个角度：{random.choice(_ANGLES)}")
    lines += [
        "只输出一句话，直接说，不要解释、不要换行、不要加引号。",
    ]
    return "\n".join(lines)
