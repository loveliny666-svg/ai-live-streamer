"""智能体可调用的工具定义（OpenAI function-calling 格式）。"""
from __future__ import annotations

TOOLS: list[dict] = [
    {
        "name": "say",
        "description": ("对直播间说一句话（会转成语音并出现在字幕上）。"
                        "道谢、欢迎进场、回答提问、自言自语——凡是「要让人听见内容」的场合都用这个。"
                        "注意：表达感谢必须用 say 说出具体内容，光放特效不算回应。"),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {"type": "string", "description": "口语化的一句话，不要超过 60 字，不要表情符号"},
                "emotion": {
                    "type": "string",
                    "enum": ["normal", "happy", "tired", "excited", "sigh", "panic"],
                    "description": "语气，会影响表情和语速",
                },
                "to": {"type": "string", "description": "对谁说的，填观众昵称；自言自语留空"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "noop",
        "description": "这轮不需要说话（比如消息重复、无意义、正在专心改 bug）。",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string", "description": "为什么不说"}},
            "required": ["reason"],
        },
    },
    {
        "name": "switch_scene",
        "description": "切换直播画面：coding=认真写代码，slack=摸鱼打游戏（画面会随机轮换小游戏：植物大战僵尸/贪吃蛇/俄罗斯方块/2048）。切换要自然，别太频繁。",
        "parameters": {
            "type": "object",
            "properties": {
                "scene": {"type": "string", "enum": ["coding", "slack"]},
                "reason": {"type": "string", "description": "用一句话说明理由，会显示在日志里"},
            },
            "required": ["scene", "reason"],
        },
    },
    {
        "name": "boss_key",
        "description": "「老板来了」：瞬间切回写代码并保持十几秒，用来做效果。收到大礼物或有人喊老板时可用。",
        "parameters": {
            "type": "object",
            "properties": {"reason": {"type": "string"}},
            "required": [],
        },
    },
    {
        "name": "write_project",
        "description": ("观众点单「写个XX」「做一个XX」时调用：立刻切到写代码状态，"
                        "编辑器里开始写对应小游戏的源码（贪吃蛇/俄罗斯方块/2048/坦克大战/"
                        "植物大战僵尸/CS 靶场）。写完自己就会去玩。"
                        "没有现成项目也没关系，工具会嘴上答应并继续写手头需求。"
                        "调用后记得用 say 回应观众「这就安排」。"),
        "parameters": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "观众想让你写的东西，如「坦克大战」「贪吃蛇」「扫雷」"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "play_effect",
        "description": ("在画面上放一个纯视觉特效。它**不出声**，不能代替说话；"
                        "要感谢/欢迎的话必须另外调用 say。"),
        "parameters": {
            "type": "object",
            "properties": {
                "effect": {
                    "type": "string",
                    "enum": ["hearts", "confetti", "fire", "rain", "flash", "shake"],
                },
                "text": {"type": "string", "description": "配合特效的大字，可留空"},
            },
            "required": ["effect"],
        },
    },
    {
        "name": "remember",
        "description": "记住一件关于观众或直播间的事，以后可以想起来。",
        "parameters": {
            "type": "object",
            "properties": {
                "key": {"type": "string", "description": "例如 观众:张三 / 直播间:梗"},
                "value": {"type": "string", "description": "不超过 40 字"},
            },
            "required": ["key", "value"],
        },
    },
    {
        "name": "recall",
        "description": "回忆之前记住的事。",
        "parameters": {
            "type": "object",
            "properties": {"key": {"type": "string"}},
            "required": ["key"],
        },
    },
    {
        "name": "start_poll",
        "description": "发起一个弹幕投票，让观众参与。",
        "parameters": {
            "type": "object",
            "properties": {
                "question": {"type": "string"},
                "options": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "2 到 4 个选项，每个不超过 8 字",
                },
            },
            "required": ["question", "options"],
        },
    },
    {
        "name": "get_room_stats",
        "description": "查看当前在线人数、点赞、礼物、直播时长。",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "read_events",
        "description": "回看最近一段时间还没处理的弹幕和礼物。",
        "parameters": {
            "type": "object",
            "properties": {
                "seconds": {"type": "number", "description": "往回看多少秒，默认 90"},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string", "enum": ["comment", "gift", "enter", "follow", "like"]},
                },
            },
        },
    },
]


def tool_names() -> list[str]:
    return [t["name"] for t in TOOLS]
