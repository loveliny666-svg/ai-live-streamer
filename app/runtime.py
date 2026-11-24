"""运行时总装：把所有模块接起来，并负责全自动调度与自愈。"""
from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from typing import Any

from .audio.ducker import AudioDucker
from .audio.nowplaying import LyricsClient, NowPlaying, _needs_translation, current_line
from .bus import Bus
from .companion.controller import Companion
from .config import Config
from .danmaku.base import Router
from .danmaku.dom_source import DomSource
from .danmaku.ocr_source import OcrSource
from .danmaku.simulator import SimulatorSource
from .danmaku.ws_source import WsSource
from .director import Director
from .llm.agent import Agent, Memory
from .llm.prompt import random_idle_topic
from .llm.client import LLMClient
from .models import LiveEvent, guess_gift_value
from .stage.launcher import StageWindow
from .stage.server import StageHub
from .tts.engine import TTS

log = logging.getLogger("runtime")

SCENE_LABEL = {"coding": "写代码", "slack": "摸鱼", "boss": "老板来了"}

# 摸鱼小游戏池（对应舞台页 app/stage/static/ 里的各游戏脚本）
GAME_NAMES = {
    "pvz": "植物大战僵尸",
    "snake": "贪吃蛇",
    "tetris": "俄罗斯方块",
    "g2048": "2048",
    "tank": "坦克大战",
    "cs": "CS 靶场",
}

# 每个小游戏对应的「源码文件」：平时写的代码就是它们，写完就自己玩
GAME_PROJECTS = {
    "pvz": "lawn_defense.js",
    "snake": "snake_ai.js",
    "tetris": "tetris_engine.js",
    "g2048": "game_2048.js",
    "tank": "tank_battle.js",
    "cs": "aim_trainer.js",
}

# 弹幕点单关键词 → 游戏 id
PROJECT_KEYWORDS = {
    "pvz": ("植物", "僵尸", "豌豆", "戴夫"),
    "snake": ("贪吃蛇", "蛇"),
    "tetris": ("俄罗斯方块", "方块", "tetris"),
    "g2048": ("2048", "二零四八"),
    "tank": ("坦克", "battle city"),
    "cs": ("cs", "靶场", "练枪", "枪", "爆头", "射击"),
}


class Runtime:
    def __init__(self, cfg: Config, port: int | None = None):
        self.cfg = cfg
        self.port = int(port or cfg.get("server.port", 8735))
        self.bus = Bus(cfg=cfg)
        self.hub = StageHub()
        self.director = Director(cfg, self._on_scene)
        self.tts = TTS(cfg)
        self.ducker = AudioDucker(cfg)   # 说话时压低 Apple Music，说完淡回
        self.nowplaying = NowPlaying(cfg)  # 读当前播的歌，画面上显示
        self.lyrics_client = LyricsClient(cfg)
        self.song: dict | None = None
        self.lyrics: dict | None = None      # 当前歌的歌词 {"synced": [(秒,行)...]}
        self.lyrics_for: tuple | None = None  # 歌词对应哪首歌，换歌才重新拉
        self.client = LLMClient(cfg)
        # 本地模型客户端。即使配置里没启用也建出来，热重载改 enabled 就不用重建对象。
        # 是否真的用它由 ModelRouter 判断，这里只管持有连接参数。
        self.local_client = LLMClient(cfg, "llm.local")
        self.memory = Memory(cfg.resolve("memory.json"))
        self.agent = Agent(
            cfg, self.client, self.memory, self._execute, self._state, local=self.local_client
        )
        self.router = Router(cfg, self.bus)
        self.companion = Companion(cfg, self._stage_hwnd)
        self.stage = StageWindow(cfg, self.port)

        self.pending: list[LiveEvent] = []
        self.reply_times: deque[float] = deque(maxlen=200)
        # 8 条而不是 4 条：要喂给模型做「别重复」的提示，4 条不够它看出自己的套路。
        # 这个 deque 同时用于事后拦截（_dup_of_recent）和事前提示（_state → prompt）。
        self.last_texts: deque[str] = deque(maxlen=8)
        self.user_replied: dict[str, float] = {}
        self.last_activity = time.time()
        self.started_at = time.time()
        self.live_started_at = 0.0
        self.poll: dict[str, Any] | None = None
        self.tasks: list[asyncio.Task] = []
        self.ready = False
        self.shutting_down = False
        self.stage_enabled = True
        self.hub.last_stage_seen = time.time()
        self.notes: list[str] = []
        self._last_live_attempt = 0.0
        self.game_id = "pvz"   # 当前摸鱼玩的小游戏（每次进 slack 随机换）
        self.project_file = "snake_ai.js"  # 编辑器里正在写的「源码」文件
        self.last_game_project: str | None = None  # 刚写完的游戏 → 摸鱼优先玩它
        self._proj_cycle = 0
        self._project_override = False  # 弹幕点单指定项目时，下一次 coding 不轮转
        # 直播间的"游戏化"指标：专注时长 / 摸鱼次数 / 被抓次数 / 今日敲了多少字
        self.metrics = self._load_metrics()
        self._focus_mark = time.time()

        self.bus.subscribe(self._on_event)

    # ================= 指标 =================
    def _metrics_path(self):
        return self.cfg.resolve("metrics.json")

    def _load_metrics(self) -> dict:
        import json as _json

        today = time.strftime("%Y-%m-%d")
        data = {"date": today, "chars": 0, "goal": 2400, "slack": 0, "caught": 0, "focus": 0.0}
        try:
            p = self._metrics_path()
            if p.exists():
                raw = _json.loads(p.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    data.update({k: raw.get(k, data[k]) for k in data})
        except Exception:  # noqa: BLE001
            pass
        if data.get("date") != today:  # 跨天，今日产出清零，累计次数保留
            data["date"] = today
            data["chars"] = 0
            data["goal"] = 2400
            data["focus"] = 0.0
        return data

    def _save_metrics(self) -> None:
        import json as _json

        try:
            p = self._metrics_path()
            p.write_text(_json.dumps(self.metrics, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass

    def add_code_chars(self, n: int) -> None:
        try:
            n = max(0, min(4000, int(n)))
        except (TypeError, ValueError):
            return
        before = self.metrics["chars"] // 38
        self.metrics["chars"] += n
        after = self.metrics["chars"] // 38
        if after > before and after % 500 == 0 and after:
            # 每 500 行来一句鼓励
            asyncio.create_task(self.say(f"第 {after} 行了，绩效又涨了一点。", emotion="happy", source="system"))
        while self.metrics["chars"] >= self.metrics["goal"] * 38:
            self.metrics["goal"] += 1200
            asyncio.create_task(
                self.hub.broadcast_stage(
                    {"type": "flash", "big": "绩效达成", "sub": "老板说下周继续加油", "kind": ""}
                )
            )

    def _tick_focus(self) -> None:
        """按真实时间累计专注时长（只在写代码/老板键时算）。"""
        now = time.time()
        if self.director.scene in ("coding", "boss"):
            self.metrics["focus"] = float(self.metrics.get("focus", 0)) + max(0.0, now - self._focus_mark)
        self._focus_mark = now

    def _speed_buff(self) -> float:
        gifts = sum(1 for e in self.bus.recent(600, 200) if e.kind == "gift")
        return round(min(2.0, 1.0 + 0.1 * gifts), 1)

    async def on_stage_message(self, msg: dict) -> None:
        """舞台页主动上报：代码字数 + 语音播放状态（用来精确控制压音乐的时机）。"""
        if msg.get("type") == "code":
            self.add_code_chars(msg.get("chars", 0))
        elif msg.get("type") == "voice":
            state = msg.get("state")
            if state == "start":
                await self.ducker.begin(0)      # 舞台真的开始出声了，续上压制
            elif state == "idle":
                await self.ducker.end()         # 队列播完，立刻准备淡回音乐

    # ================= 生命周期 =================
    async def boot(self) -> None:
        log.info("=== %s 启动 ===", self.cfg.get("app.name", "直播间"))
        # 上一轮如果被强杀，音乐音量可能停在压低值上（叠几次就成 0 了）。先还回去。
        try:
            n = await self.ducker.recover_stale()
            if n:
                log.info("已恢复 %d 个音频会话的残留音量", n)
        except Exception as exc:  # noqa: BLE001
            log.warning("恢复残留音量失败（不影响直播）：%s", exc)
        self._build_sources()
        await self.router.start()
        self.director.start()
        self.tasks = [
            asyncio.create_task(self._batch_loop(), name="batch"),
            asyncio.create_task(self._proactive_loop(), name="proactive"),
            asyncio.create_task(self._stats_loop(), name="stats"),
            asyncio.create_task(self._watchdog_loop(), name="watchdog"),
            asyncio.create_task(self._nowplaying_loop(), name="now-playing"),
        ]
        if self.cfg.get("companion.auto_start_live", True):
            self.tasks.append(asyncio.create_task(self._live_boot(), name="live-boot"))
        if not self.client.ready:
            log.warning("模型未就绪（%s），先用规则脑直播，不影响开播", self.client.why_not)
            self.notes.append(f"模型未就绪：{self.client.why_not}")
        # 本地模型：先预热，免得第一条弹幕等它加载五秒
        if self.cfg.get("llm.local.enabled", False):
            log.info(
                "本地模型已启用：%s（策略 %s），云端额度见底时由它接管简单回复",
                self.local_client.model, self.agent.router.strategy(),
            )
            if self.cfg.get("llm.local.warmup", True):
                self.tasks.append(
                    asyncio.create_task(self.agent.warmup_local(), name="local-warmup")
                )
        self.ready = True
        await self._broadcast_init()

    def _build_sources(self) -> None:
        d = self.cfg.get("danmaku.sources", {}) or {}
        room = str(self.cfg.get("danmaku.room_url", "") or "").strip()
        if not self.cfg.get("danmaku.enabled", True):
            log.warning("弹幕监听被关闭（danmaku.enabled=false）")
            return
        if (d.get("ws") or {}).get("enabled", True):
            if room:
                self.router.add(WsSource(self.cfg, self.router.handle, room))
            else:
                log.warning("没填 danmaku.room_url，WebSocket 弹幕源不会启动")
                self.notes.append("未配置直播间地址，WebSocket 弹幕源未启动")
        if (d.get("dom") or {}).get("enabled", True):
            if room:
                self.router.add(DomSource(self.cfg, self.router.handle, room))
            else:
                log.warning("没填 danmaku.room_url，网页弹幕源不会启动")
                self.notes.append("未配置直播间地址，网页弹幕源未启动")
        if (d.get("ocr") or {}).get("enabled", True):
            self.router.add(
                OcrSource(
                    self.cfg,
                    self.router.handle,
                    self.companion.hwnd_or_none,
                    sibling_check=self._ws_healthy,
                )
            )
        if (d.get("simulator") or {}).get("enabled", False):
            self.router.add(SimulatorSource(self.cfg, self.router.handle))

    def _ws_healthy(self, seconds: float = 45) -> bool:
        """WebSocket 源最近还在收帧吗？（收帧正常就不必再跑 OCR）"""
        for s in self.router.sources:
            if s.name == "ws":
                last = float(getattr(s, "_last_frame", 0) or 0)
                return bool(last) and time.time() - last < seconds
        return False

    def _dom_recent(self, seconds: float = 75) -> bool:
        """网页弹幕源最近有没有正常出数据。"""
        for s in self.router.sources:
            if s.name == "dom" and s.last_event_at and time.time() - s.last_event_at < seconds:
                return True
        return False

    def _ocr_redundant(self) -> bool:
        """网页源活着、而且最近真的报过进场 —— 只有这时 OCR 才是多余的。

        只按「有没有数据」就跳过 OCR 是不行的：不少直播间网页端压根不渲染
        「XXX 来了」，网页源一直在出弹幕却永远不出进场，一跳过 OCR 就再也没人
        给进场发欢迎特效了。所以这里额外要求「网页源近期真的抓到过进场」。"""
        if not self._dom_recent(40):
            return False
        for s in self.router.sources:
            if s.name == "dom":
                last = float(getattr(s, "enter_last_at", 0) or 0)
                return bool(last) and time.time() - last < 600
        return False

    async def shutdown(self) -> None:
        self.shutting_down = True
        log.info("正在收尾…")
        for t in self.tasks:
            t.cancel()
        try:
            await self.ducker.restore_now()   # 收播必须把用户音乐音量还回去
        except Exception:  # noqa: BLE001
            pass
        await self.router.stop()
        await self.stage.close()
        try:
            self.memory.save()
            self._save_metrics()
        except Exception:  # noqa: BLE001
            pass

    # ================= 事件 =================
    def welcome_enabled(self) -> bool:
        return bool(self.cfg.get("stage.welcome.enabled", True)) and bool(
            self.cfg.get("behavior.welcome_enter", True)
        )

    async def _on_event(self, ev: LiveEvent) -> None:
        self.last_activity = time.time()
        self.pending.append(ev)
        if len(self.pending) > 80:
            self.pending = self.pending[-80:]

        # 进场：欢迎特效必须第一个出去 —— 不等模型、不等统计、不排队、不进批处理
        if ev.kind == "enter":
            if self.welcome_enabled():
                await self.hub.broadcast_stage(
                    {"type": "welcome", "user": ev.user, "at": round(ev.ts * 1000)}
                )
            return

        await self.hub.push_event(ev)
        # 大礼物立刻上特效，不等模型
        big = int(self.cfg.get("behavior.big_gift_above", 50))
        # 大礼物走全屏大字，小礼物走横幅 —— 两者同屏会打架
        if ev.kind == "gift" and ev.gift_value >= big:
            cnt = f" ×{ev.gift_count}" if ev.gift_count > 1 else ""
            await self.hub.broadcast_stage(
                {"type": "flash", "big": f"{ev.gift_name}{cnt}", "sub": f"{ev.user} 送出了大礼物", "kind": ""}
            )
        elif ev.kind == "gift":
            await self.hub.broadcast_stage(
                {"type": "gift", "user": ev.user, "gift": ev.gift_name, "count": ev.gift_count, "big": False}
            )

    def _passes_user_cd(self, ev: LiveEvent) -> bool:
        # 关注和礼物是一次性事件，必须回应；被"同一个人别刷屏"的冷却挡掉就没人谢了
        if ev.kind in ("follow", "gift"):
            return True
        if ev.kind == "enter":
            cd = float(self.cfg.get("behavior.enter_cooldown_sec", 45))
        else:
            cd = float(self.cfg.get("behavior.same_user_cooldown_sec", 12))
        return time.time() - self.user_replied.get(ev.user, 0) >= cd

    # ================= 循环 =================
    async def _batch_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(1.2)   # 原来是 2 秒，观众会觉得慢半拍
                if not self.pending:
                    continue
                batch = [e for e in self.pending if self._passes_user_cd(e)]
                self.pending.clear()
                if not batch:
                    continue
                # 关注/礼物先处理，避免被一堆进场挤掉
                hot = [e for e in batch if e.kind in ("follow", "gift")]
                if hot:
                    reason = "有人关注" if any(e.kind == "follow" for e in hot) else "有人送礼"
                    # 关注和礼物带上同批的进场作背景，但主角是他们
                    target = hot + [e for e in batch if e not in hot][:3]
                else:
                    reason = "有人进场" if all(e.kind == "enter" for e in batch) else "有人说话"
                    target = batch
                done = await self.agent.think(target, reason)
                if not done:
                    # 正在说话或被节流：放回队列下次再说，否则关注/礼物会被静默吞掉
                    self.pending = target + self.pending
                    if len(self.pending) > 80:
                        self.pending = self.pending[-80:]
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("批处理异常：%s", exc)

    async def _proactive_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(5)
                # 每次循环都读配置，改 idle_talk_sec 热重载就生效
                idle = float(self.cfg.get("behavior.idle_talk_sec", 150))
                if time.time() - self.last_activity >= idle:
                    self.last_activity = time.time()
                    await self.agent.think([], f"直播间安静了一会儿。随便自言自语一句：{random_idle_topic(self.cfg)}")
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("主动搭话异常：%s", exc)

    async def _nowplaying_loop(self) -> None:
        """每秒读一次播放进度，推给画面；歌词按进度逐行同步。
        歌词只在换歌时后台拉一次（lrclib 免费接口 + 本地缓存），失败静默。"""
        while True:
            try:
                await asyncio.sleep(1)
                if not self.cfg.get("stage.nowplaying.enabled", True):
                    continue
                info = await asyncio.to_thread(self.nowplaying.read)
                if not info:
                    if self.song:
                        self.song = None
                        self.lyrics = None
                        self.lyrics_for = None
                        await self.hub.broadcast_stage({"type": "now_playing", "playing": False})
                    continue
                key = (info.get("title"), info.get("artist"))
                if key != self.lyrics_for:
                    self.lyrics_for = key
                    self.lyrics = None
                    if self.cfg.get("stage.nowplaying.lyrics", True) and info.get("playing"):
                        asyncio.create_task(self._fetch_lyrics(info))
                cur = nxt = ""
                if self.lyrics and info.get("playing"):
                    cur, nxt = current_line(self.lyrics.get("synced") or [], info.get("position") or 0)
                self.song = info
                await self.hub.broadcast_stage(
                    {"type": "now_playing", **info, "lyric": cur, "lyric_next": nxt}
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.debug("读歌名循环异常：%s", exc)

    async def _fetch_lyrics(self, info: dict) -> None:
        try:
            data = await asyncio.to_thread(
                self.lyrics_client.get,
                info.get("title", ""),
                info.get("artist", ""),
                float(info.get("duration") or 0),
            )
        except Exception:  # noqa: BLE001
            data = None
        if not data or not data.get("synced"):
            self.lyrics = data
            return
        lines = data["synced"]
        # 外语歌词（韩/日/英…）翻成简体中文；粤语/繁体歌已在本地转好简体
        if self.cfg.get("stage.nowplaying.lyrics", True) and _needs_translation(lines):
            zh = await self._translate_lyrics(lines)
            if zh and len(zh) == len(lines):
                data["synced"] = [(t, z) for (t, _), z in zip(lines, zh)]
        self.lyrics = data

    async def _translate_lyrics(self, lines: list) -> list[str] | None:
        """用模型把歌词翻成简体中文。每首歌只翻一次并落盘缓存；失败返回 None 显示原文。"""
        import hashlib
        import json as _json
        from pathlib import Path

        body = "\n".join(f"{i + 1}. {text}" for i, (_t, text) in enumerate(lines))
        key = hashlib.md5(body.encode("utf-8")).hexdigest()[:20]
        cache = Path(self.cfg.data_dir) / "lyrics" / f"zh-{key}.json"
        if cache.exists():
            try:
                return _json.loads(cache.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass
        prompt = (
            "把下面的歌词转写成简体中文：中文歌词（含繁体、粤语）只做简体转写，"
            "外语歌词翻译成简体中文，可以顺句但别意译过头。\n"
            "输出必须和输入行数完全一致，每行照抄行号和「. 」，只输出译文，不要解释、不要空行。"
        )
        try:
            res = await self.client.chat(
                [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": body},
                ],
                temperature=0.2,
                max_tokens=1500,
            )
        except Exception as exc:  # noqa: BLE001
            log.info("歌词翻译跳过（显示原文）：%s", str(exc)[:120])
            return None
        out: dict[int, str] = {}
        for raw in str(res.text or "").splitlines():
            m = re.match(r"^\s*(\d+)\s*[.、．)）]\s*(.+)$", raw)
            if m:
                out[int(m.group(1))] = m.group(2).strip()
        zh = [out.get(i + 1, "").strip() for i in range(len(lines))]
        if sum(1 for x in zh if x) < max(1, len(lines) // 2):
            return None  # 译文对不上行号，宁可显示原文
        try:
            cache.write_text(_json.dumps(zh, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return zh

    async def _stats_loop(self) -> None:
        while True:
            try:
                await asyncio.sleep(3)
                self._tick_focus()
                await self.hub.broadcast_stage({"type": "stats", **self._stats()})
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                pass

    async def _live_boot(self) -> None:
        """等舞台起来再开播，避免推流推了个黑屏。"""
        for _ in range(40):
            if self.hub.stage_online:
                break
            await asyncio.sleep(1)
        await asyncio.sleep(2)
        # 已经在播就别点按钮了。以前是无条件调 start_live()，重启程序时
        # 直播伴侣本来就开着，找不到「开始直播」会连点三次、报一条红字挂在控制台上。
        try:
            h = await self.companion.health()
        except Exception as exc:  # noqa: BLE001
            log.warning("开播前状态检查失败（继续按未开播处理）：%s", exc)
            h = {}
        if h.get("is_live"):
            self.live_started_at = time.time()
            log.info("检测到已在直播，跳过自动开播")
            return
        res = await self.companion.start_live()
        if res.get("ok"):
            self.live_started_at = time.time()
            log.info("自动开播完成：%s", res.get("note") or res.get("error") or "")
        else:
            reason = res.get("reason") or res.get("error") or "未知原因"
            log.error("自动开播未成功：%s", reason)
            self.notes.append(f"自动开播失败：{reason}")

    async def _watchdog_loop(self) -> None:
        hb_timeout = float(self.cfg.get("stage.heartbeat_timeout_sec", 8))
        # 从「现在」开始算，别用 0：用 0 会让第一次循环（启动后 5 秒）立刻巡检，
        # 和 _live_boot 的启动开播撞车，两边都去调 start_live，一边必被冷却拒掉，
        # 控制台上就多一条「自动开播失败：冷却中」的红字。错开 45 秒即可。
        last_health = time.time()
        last_probe = 0.0
        while True:
            try:
                await asyncio.sleep(5)
                now = time.time()

                # 0) 音频闪避看门狗：watcher 挂了就重启，卡住超时强制恢复
                await self.ducker.tick()

                # 1) 舞台页保活
                if not self.stage_enabled:
                    pass
                elif not self.hub.stage_online:
                    if now - self.hub.last_stage_seen > 25:
                        log.warning("舞台页长时间离线，重新打开")
                        await self.stage.reload()
                        self.hub.last_stage_seen = now
                elif self.hub.last_stage_hb and now - self.hub.last_stage_hb > hb_timeout * 4:
                    log.warning("舞台页心跳超时，重载")
                    await self.stage.reload()
                    self.hub.last_stage_hb = now

                # 2) 模型 / 语音恢复探测
                if now - last_probe > 60:
                    last_probe = now
                    if self.agent.degraded:
                        await self.agent.probe()
                    await self.tts.probe_recover()

                # 3) 直播伴侣巡检
                if self.cfg.get("companion.enabled", True) and now - last_health > float(
                    self.cfg.get("companion.health_check_sec", 45)
                ):
                    last_health = now
                    h = await self.companion.health()
                    if h.get("recover_hint"):
                        await self.companion.recover()
                    if h.get("is_live") and not self.live_started_at:
                        self.live_started_at = now
                    # 掉播自动重开
                    if (
                        h.get("ok")
                        and not h.get("is_live")
                        and self.cfg.get("companion.auto_start_live", True)
                        and now - self._last_live_attempt > float(
                            self.cfg.get("companion.restart_cooldown_sec", 120)
                        )
                    ):
                        self._last_live_attempt = now
                        log.warning("检测到未在直播，尝试重新开播")
                        res = await self.companion.start_live()
                        if res.get("ok"):
                            self.live_started_at = now
                            log.info("重开播成功")

                # 4) 定时收播
                self._maybe_shutdown_at()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001
                log.warning("巡检异常：%s", exc)

    def _maybe_shutdown_at(self) -> None:
        at = str(self.cfg.get("app.shutdown_at", "") or "").strip()
        if not at or ":" not in at:
            return
        hh, mm = at.split(":")[:2]
        try:
            h, m = int(hh), int(mm)
        except ValueError:
            return
        now = time.localtime()
        if now.tm_hour == h and now.tm_min == m:
            log.warning("到达计划收播时间 %s，准备收播", at)
            asyncio.create_task(self._graceful_stop())

    async def _graceful_stop(self) -> None:
        if self.shutting_down:
            return
        try:
            await self.companion.stop_live()
        finally:
            self.cfg.set("app.shutdown_at", "")
            self.cfg.save()
            await asyncio.sleep(1)
            import os

            os._exit(0)

    # ================= 场景 =================
    def _pick_game(self) -> str:
        """进 slack 时随机挑一个小游戏：刚写完哪个就先玩哪个，否则随机（不跟上次重复）。"""
        if self.last_game_project:
            g = self.last_game_project
            self.last_game_project = None
            return g
        ids = self.cfg.get("director.games", None)
        if not isinstance(ids, list) or not ids:
            ids = list(GAME_NAMES)
        ids = [g for g in ids if g in GAME_NAMES] or list(GAME_NAMES)
        pool = [g for g in ids if g != self.game_id] or ids
        return random.choice(pool)

    def _pick_project(self) -> str:
        """平时「写代码」= 轮着写自己小游戏的源码（看起来像在上班）。"""
        ids = list(GAME_PROJECTS)
        self._proj_cycle = (self._proj_cycle + 1) % len(ids)
        return GAME_PROJECTS[ids[self._proj_cycle]]

    def match_project(self, text: str) -> str | None:
        """弹幕点单 → 游戏项目。返回 game_id，没有就 None。"""
        t = str(text or "").lower()
        if not t:
            return None
        for gid, kws in PROJECT_KEYWORDS.items():
            for kw in kws:
                if kw in t:
                    return gid
        return None

    async def _on_scene(self, scene: str, reason: str) -> None:
        self._tick_focus()
        game = None
        project = None
        if scene == "slack":
            game = self._pick_game()
            self.game_id = game
        elif scene == "boss":
            # 只有老板来了才写真正的需求
            project = "boss_requirement.py"
            self.project_file = project
        else:
            # 平时「写代码」= 写自己小游戏的源码，写完摸鱼就玩它
            # （弹幕点单刚指定的项目不覆盖）
            if not self._project_override:
                project = self._pick_project()
                self.project_file = project
                for gid, fname in GAME_PROJECTS.items():
                    if fname == project:
                        self.last_game_project = gid
                        break
            project = self.project_file
            self._project_override = False
        if scene == "slack":
            self.metrics["slack"] += 1
        elif scene == "boss":
            self.metrics["caught"] += 1
            self._save_metrics()
        await self.hub.broadcast_stage(
            {"type": "scene", "scene": scene, "reason": reason, "game": game, "project": project}
        )
        # 立刻补一条统计，让 mood / 倒计时文案跟上，不用等 3 秒
        await self.hub.broadcast_stage({"type": "stats", **self._stats()})
        await self.hub.broadcast_admin(
            {"type": "scene", "scene": scene, "reason": reason, "ts": time.time()}
        )
        # 切完画面，让智能体接一句，免得画面突然变了没人解释
        asyncio.create_task(self._scene_commentary(scene, reason))

    async def _scene_commentary(self, scene: str, reason: str) -> None:
        await asyncio.sleep(1.8)
        if scene == "slack":
            label = f"摸鱼打游戏（{GAME_NAMES.get(self.game_id, '小游戏')}）"
        elif scene == "boss":
            label = "老板来了，切回写真正的需求"
        else:
            label = f"继续写代码（{self.project_file}）"
        try:
            await self.agent.think([], f"你刚把画面切成「{label}」，原因：{reason}")
        except Exception:  # noqa: BLE001
            pass

    async def force_scene(self, scene: str, reason: str = "手动") -> bool:
        return await self.director.force(scene, reason)

    # ================= 工具执行 =================
    async def _execute(self, name: str, args: dict) -> str:
        if name == "say":
            text = str(args.get("text", "")).strip()
            if not text:
                return "文本为空"
            ok = await self.say(text, emotion=str(args.get("emotion", "normal")), to=str(args.get("to", "")))
            return "已播出" if ok else "被限流，没播"

        if name == "noop":
            return "好的，这轮不说"

        if name == "switch_scene":
            scene = str(args.get("scene", ""))
            ok = await self.director.request(scene, str(args.get("reason", "智能体觉得该换了"))[:40])
            return f"切换{'成功' if ok else '被拒（太频繁）'}：{scene}"

        if name == "boss_key":
            await self.director.boss_key(str(args.get("reason", "老板来了"))[:40])
            return "已切回写代码，老板键生效"

        if name == "write_project":
            want = str(args.get("name", "")).strip()
            gid = self.match_project(want)
            if gid:
                self.last_game_project = gid
                self.project_file = GAME_PROJECTS[gid]
                self._project_override = True
                await self.director.force("coding", f"弹幕点单：写{GAME_NAMES[gid]}")
                return f"已切到 coding 开始写 {GAME_NAMES[gid]} 的源码（{self.project_file}），写完就去玩"
            self.project_file = "boss_requirement.py"
            self._project_override = True
            await self.director.force("coding", f"弹幕点单：{want[:20]}")
            return f"没有现成的「{want}」项目，已切回写代码并嘴上答应观众，之后找机会安排"

        if name == "play_effect":
            eff = str(args.get("effect", "confetti"))
            text = str(args.get("text", ""))[:30]
            mapping = {
                "hearts": ("💗", text or "爱你", ""),
                "confetti": ("🎉", text or "好耶", ""),
                "fire": ("🔥", text or "燃起来了", ""),
                "rain": ("🌧", text or "下雨了", ""),
                "flash": ("✨", text or "", ""),
                "shake": ("💥", text or "地震了", ""),
            }
            ico, big, sub = mapping.get(eff, ("✨", text, ""))
            await self.hub.broadcast_stage({"type": "flash", "big": big, "sub": sub, "kind": ""})
            return f"特效 {eff} 已播放"

        if name == "remember":
            self.memory.set(str(args.get("key", "?")), str(args.get("value", "")))
            return "记住了"

        if name == "recall":
            v = self.memory.get(str(args.get("key", "")))
            return v or "没想起来"

        if name == "start_poll":
            opts = [str(o)[:8] for o in (args.get("options") or [])][:4]
            if len(opts) < 2:
                return "选项太少，没发"
            self.poll = {
                "question": str(args.get("question", "选一个"))[:20],
                "options": [{"label": o, "votes": 0} for o in opts],
            }
            await self.hub.broadcast_stage({"type": "poll", **self.poll})
            return "投票已发起"

        if name == "get_room_stats":
            s = self._stats()
            return (
                f"在线约 {s['online']} 人，点赞 {s['likes']}，礼物 {s['gifts']} 抖币，"
                f"已播 {s['uptime_text']}，画面是{SCENE_LABEL.get(self.director.scene, '')}"
            )

        if name == "read_events":
            sec = float(args.get("seconds", 90) or 90)
            kinds = args.get("kinds") or None
            evs = self.bus.recent(sec, 20, set(kinds) if kinds else None)
            if not evs:
                return "最近没有新消息"
            return "\n".join(e.describe() for e in evs[-12:])

        return f"未知工具 {name}"

    # ================= 说话 =================
    async def say(self, text: str, emotion: str = "normal", to: str = "", source: str = "agent") -> bool:
        if not self._rate_ok():
            log.info("说话被限流：%s", text[:20])
            return False
        if self._dup_of_recent(text):
            log.info("重复跳过：%s", text[:20])
            return False
        url, est = await self.tts.synth(text, emotion)
        # 人声出去之前先把外部音乐压下去；舞台页随后还会上报 start/idle 精修时长
        await self.ducker.begin(est)
        reply_to = None
        if to:
            for e in reversed(self.bus.recent(90, 60)):
                if e.kind == "comment" and e.user == to and e.text:
                    reply_to = {"user": e.user, "text": e.text[:42]}
                    break
        await self.hub.broadcast_stage(
            {
                "type": "say",
                "text": text,
                "audio": url,
                "emotion": emotion,
                "to": to,
                "reply_to": reply_to,
                "hold_ms": int(est * 1000),
            }
        )
        self.reply_times.append(time.time())
        self.last_texts.append(text)
        if to:
            self.user_replied[to] = time.time()
        log.info("说话：%s%s", text, f"（{est:.1f}s 语音）" if url else "（无声）")
        return True

    def _dup_of_recent(self, text: str) -> bool:
        """和最近几句几乎一样就不播了 —— 模型偶尔会对同一条弹幕复读，观众听着难受。"""
        t = text.strip()
        for old in self.last_texts:
            if len(old) < 6 and len(t) < 6:
                continue
            if old in t or t in old:
                return True
            overlap = sum(1 for x, y in zip(old, t) if x == y)
            if overlap > 0.75 * max(len(old), len(t)):
                return True
        return False

    def _rate_ok(self) -> bool:
        limit = int(self.cfg.get("behavior.max_replies_per_min", 8))
        now = time.time()
        while self.reply_times and now - self.reply_times[0] > 60:
            self.reply_times.popleft()
        return len(self.reply_times) < limit

    # ================= 状态 =================
    def _stats(self) -> dict:
        s = self.bus.stats
        users = {e.user for e in self.bus.recent(600, 400) if e.user and e.user != "观众"}
        start = self.live_started_at or self.started_at
        lines = self.metrics["chars"] // 38
        return {
            "online": max(len(users), s.online if s.online < 500 else len(users)),
            "likes": s.likes,
            "gifts": s.gift_value_total,
            "comments": s.comment_count,
            "uptime": int(time.time() - start),
            "uptime_text": _fmt_uptime(int(time.time() - start)),
            "focus_text": _fmt_uptime(int(self.metrics.get("focus", 0))),
            "slack": self.metrics["slack"],
            "caught": self.metrics["caught"],
            "lines": lines,
            "goal": self.metrics["goal"],
            "speed": self._speed_buff(),
            "countdown": int(self.director.remaining()),
            "typing": self.director.scene in ("coding", "boss"),
        }

    def _state(self) -> dict:
        st = self._stats()
        song = ""
        if self.song and self.song.get("title"):
            artist = self.song.get("artist") or ""
            song = f"{self.song['title']} - {artist}" if artist else self.song["title"]
        return {
            "scene": self.director.scene,
            "online": st["online"],
            "likes": st["likes"],
            "gift_value": st["gifts"],
            "uptime": st["uptime_text"],
            "song": song,
            # 最近说过的原话，喂给模型做「这轮别再说这些」的提示。
            # 光靠事后的 _dup_of_recent 拦不住「换个说法讲同一件事」，
            # 必须让模型在生成前就知道自己刚说过什么。
            "recent": list(self.last_texts),
            "task": (
                f"改 boss_requirement.py（老板盯着）"
                if self.director.scene == "boss"
                else f"打{GAME_NAMES.get(self.game_id, '游戏')}（摸鱼）"
                if self.director.scene == "slack"
                else f"写 {self.project_file}"
            ),
        }

    def stage_init_message(self) -> dict:
        st = self._stats()
        title = str(self.cfg.get("stage.title", "AI 打工人"))
        if "·" in title:
            head, tail = title.split("·", 1)
            title_html = f"{head.strip()} · <b>{tail.strip()}</b>"
        else:
            title_html = f"<b>{title}</b>"
        return {
            "type": "init",
            "title": title_html,
            "persona_name": str(self.cfg.get("persona.name", "小饭")),
            "scene": self.director.scene,
            "stats": st,
            "bgm": self._bgm_config(),
            "song": self.song,      # 舞台页连接/重连时立刻拿到当前歌名
            "game": self.game_id,   # 舞台页重连时恢复当前小游戏
            "project": self.project_file if self.director.scene != "slack" else None,
            "welcome": self._welcome_config(),
        }

    def _welcome_config(self) -> dict:
        return {
            "enabled": self.welcome_enabled(),
            "sound": bool(self.cfg.get("stage.welcome.sound", True)),
            "duration_ms": int(self.cfg.get("stage.welcome.duration_ms", 2800) or 2800),
            "max_visible": int(self.cfg.get("stage.welcome.max_visible", 6) or 6),
        }

    def _bgm_config(self) -> dict:
        """舞台页拿去放背景音乐；文件名放空表示该场景不放。"""
        def _track(key: str, default: str) -> str:
            name = str(self.cfg.get(f"stage.bgm.{key}", default) or "").strip()
            return f"/static/bgm/{name}" if name else ""

        return {
            "enabled": bool(self.cfg.get("stage.bgm.enabled", True)),
            "volume": float(self.cfg.get("stage.bgm.volume", 0.30) or 0.30),
            "tracks": {
                "coding": _track("coding", "calm.mp3"),
                "slack": _track("slack", "playful.mp3"),
                "boss": _track("boss", ""),
            },
        }

    def snapshot_status(self) -> dict:
        return {
            "ready": self.ready,
            "scene": self.director.scene,
            "scene_state": self.director.snapshot(),
            "stage_online": self.hub.stage_online,
            "stage": self.stage.snapshot(),
            "stats": self._stats(),
            "llm": self.client.snapshot(),
            "llm_local": self.local_client.snapshot(),
            "agent": self.agent.snapshot(),
            "tts": self.tts.snapshot(),
            "audio_duck": self.ducker.snapshot(),
            "song": self.song,
            "danmaku": self.router.snapshot(),
            "live": {
                "is_live": self.companion.is_live,
                "window": bool(self.companion.hwnd_or_none()),
                "last": self.companion.last_result,
            },
            "notes": self.notes[-6:],
            "metrics": {
                "slack": self.metrics["slack"],
                "caught": self.metrics["caught"],
                "lines": self.metrics["chars"] // 38,
                "goal": self.metrics["goal"],
                "speed": self._speed_buff(),
            },
            "poll": self.poll,
            "pending": len(self.pending),
            "ts": time.time(),
        }

    async def _broadcast_init(self) -> None:
        await self.hub.broadcast_stage(self.stage_init_message())

    # ================= 测试辅助 =================
    def make_test_event(self, kind="comment", user="测试观众", text="", gift="", count=1) -> LiveEvent:
        gift = gift or ("小心心" if kind == "gift" else "")
        return LiveEvent(
            kind=kind,  # type: ignore[arg-type]
            user=user,
            text=text,
            gift_name=gift,
            gift_count=max(1, int(count)),
            gift_value=guess_gift_value(gift, int(count)) if gift else 0,
            source="test",
        )

    def _stage_hwnd(self) -> int | None:
        return self.stage.hwnd()

    # ================= 配置热重载 =================
    def reload_config(self) -> None:
        self.cfg.reload()
        self.client.reload()
        self.local_client.reload()
        # 换了 key / 换了模型地址就重新信任模型，不用重启进程
        try:
            self.agent.reset_degraded()
        except Exception:  # noqa: BLE001
            pass
        self.tts.engine = str(self.cfg.get("tts.engine", "edge")).lower()
        self.tts.voice = str(self.cfg.get("tts.voice", "zh-CN-XiaoxiaoNeural"))
        self.tts.rate = str(self.cfg.get("tts.rate", "+8%"))
        self.tts.pitch = str(self.cfg.get("tts.pitch", "+18Hz"))
        self.tts.polish = bool(self.cfg.get("tts.polish", True))
        self.tts.max_chars = int(self.cfg.get("tts.max_chars", 85))
        self.ducker.reload()
        self.nowplaying.reload()
        # 欢迎特效的时长/音效/条数也一起热更新，不用刷新舞台
        try:
            asyncio.get_running_loop().create_task(
                self.hub.broadcast_stage({"type": "welcome_cfg", **self._welcome_config()})
            )
        except RuntimeError:
            pass
        log.info("配置已热重载")


def _fmt_uptime(sec: int) -> str:
    sec = max(0, int(sec))
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}"
