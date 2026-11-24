"""智能体主循环：批量事件 → 模型决策 → 调工具 → 落地成动作。

三层模型：云端 API（脑子）→ 本地 Ollama（嘴）→ 规则脑（保险丝）。
路由逻辑在 router.py，这里只负责按判决执行和逐层降级。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Awaitable, Callable

from .client import LLMClient, LLMError, ToolCall
from .fallback import FallbackBrain
from .prompt import build_batch, build_local_batch, build_local_system, build_system, clock_text
from .router import ModelRouter, Route
from .tools import TOOLS

log = logging.getLogger("agent")

Executor = Callable[[str, dict], Awaitable[str]]
StateProvider = Callable[[], dict]


def clean_local_text(raw: str) -> str:
    """把本地小模型的输出收拾成一句能直接念的话。

    小模型常见的三种毛病：换行继续解释、给自己加「小饭：」前缀、
    用引号把整句包起来。这里一次性抹掉。
    """
    t = (raw or "").strip()
    if not t:
        return ""
    t = t.splitlines()[0].strip()
    t = re.sub(r"^[\w\u4e00-\u9fa5]{1,8}[：:]\s*", "", t)
    t = re.sub(r"[（(][^）)]{0,24}[）)]", "", t)
    t = t.strip().strip('"“”\'‘’「」『』')
    return t.strip()


def guess_target(events: list) -> str:
    """本地模型不返回 to 字段，从事件里挑一个最该点名的观众。"""
    for kind in ("follow", "gift", "comment", "enter"):
        for ev in reversed(events):
            if getattr(ev, "kind", "") == kind and getattr(ev, "user", ""):
                return str(ev.user)
    return ""


class Memory:
    def __init__(self, path: Path, limit: int = 160):
        self.path = path
        self.limit = limit
        self.data: dict[str, str] = {}
        self.load()

    def load(self) -> None:
        try:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self.data = {str(k): str(v) for k, v in raw.items()}
        except Exception as exc:  # noqa: BLE001
            log.warning("记忆读取失败：%s", exc)

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except Exception as exc:  # noqa: BLE001
            log.debug("记忆写入失败：%s", exc)

    def set(self, key: str, value: str) -> None:
        self.data[key[:60]] = value[:120]
        while len(self.data) > self.limit:
            self.data.pop(next(iter(self.data)))
        self.save()

    def get(self, key: str) -> str:
        return self.data.get(key, "")

    def recent(self, n: int = 8) -> dict[str, str]:
        items = list(self.data.items())
        return dict(items[-n:])


class Agent:
    def __init__(
        self,
        cfg,
        client: LLMClient,
        memory: Memory,
        executor: Executor,
        state_provider: StateProvider,
        local: LLMClient | None = None,
    ):
        self.cfg = cfg
        self.client = client
        self.local = local
        self.memory = memory
        self.executor = executor
        self.state_provider = state_provider
        self.fallback = FallbackBrain(cfg)
        self.router = ModelRouter(cfg)
        self.last_said = ""
        self.last_call = 0.0
        self.degraded = False
        self.consecutive_fails = 0
        # 降级原因 / 下次允许试探的时间（自愈用）
        self.last_error = ""
        self.degraded_reason = ""
        self._retry_at = 0.0
        # 最近一分钟的调用时间戳，用来卡平台的 RPM 限额（免费用户 10 次/分钟）
        self._call_times: list[float] = []
        # 本地模型独立记失败，不影响云端的状态机
        self.local_fails = 0
        self._local_retry_at = 0.0
        self.local_error = ""
        # 这一轮最后走了哪条路，给前端看
        self.last_route: Route | None = None
        self.last_route_at = 0.0
        self.thinking = False
        self.stats = {"rounds": 0, "says": 0, "fallback_says": 0, "local_says": 0, "errors": 0}

    # 这几类错误换 key 才能解决，别白白重试
    PERMANENT_HINTS = ("HTTP 401", "HTTP 403", "HTTP 404", "没有填 API Key")

    @classmethod
    def _is_permanent(cls, exc: Exception) -> bool:
        msg = str(exc)
        return any(h in msg for h in cls.PERMANENT_HINTS)

    def reset_degraded(self) -> None:
        """换 key / 改配置后调用，让它立刻重新信任模型。"""
        self.degraded = False
        self.consecutive_fails = 0
        self.last_error = ""
        self.degraded_reason = ""
        self._retry_at = 0.0
        self.local_fails = 0
        self._local_retry_at = 0.0
        self.local_error = ""
        log.info("模型降级状态已重置")

    def _llm_allowed(self) -> bool:
        """降级期间隔一段时间允许试探一次，成功后自动恢复。"""
        if not self.degraded:
            return True
        return time.time() >= self._retry_at

    def _quota_ratio(self, now: float) -> float:
        """本分钟还剩多少云端额度，1.0 = 一次没用，0 = 用满。

        免费用户 10 RPM、企业 20 RPM，配置里默认填 8，留两成余量防撞 429。
        """
        rpm = max(1, int(self.cfg.get("llm.rpm", 8) or 8))
        cut = now - 60.0
        self._call_times = [t for t in self._call_times if t >= cut]
        return max(0.0, (rpm - len(self._call_times)) / rpm)

    def _rpm_ok(self, now: float) -> bool:
        return self._quota_ratio(now) > 0

    # ---------------- 本地模型 ----------------
    def _local_usable(self) -> bool:
        if not self.local or not self.local.ready:
            return False
        if not self.router.local_enabled():
            return False
        return time.time() >= self._local_retry_at

    def _local_ok(self, exc: Exception) -> None:
        """本地失败不必大动干戈，停一会儿再试就行，云端还在。"""
        self.local_fails += 1
        self.local_error = str(exc)[:200]
        threshold = max(1, int(self.cfg.get("llm.local.fail_threshold", 2) or 2))
        if self.local_fails >= threshold:
            cool = float(self.cfg.get("llm.local.recover_probe_sec", 60) or 60)
            self._local_retry_at = time.time() + cool
            log.warning(
                "本地模型连续失败 %d 次（%s），%.0f 秒内只用云端",
                self.local_fails, self.local_error, cool,
            )

    def _local_succeeded(self) -> None:
        if self.local_fails or self._local_retry_at:
            log.info("本地模型已恢复")
        self.local_fails = 0
        self._local_retry_at = 0.0
        self.local_error = ""

    async def warmup_local(self) -> bool:
        """开机预热：把模型压进显存，免得第一条弹幕等五秒加载。"""
        if not self.local or not self.local.ready:
            return False
        try:
            await self.local.chat(
                [{"role": "user", "content": "在吗"}], max_tokens=4, temperature=0
            )
            log.info("本地模型预热完成：%s", self.local.model)
            return True
        except Exception as exc:  # noqa: BLE001
            self.local_error = str(exc)[:200]
            log.warning("本地模型预热失败：%s", exc)
            return False

    # ---------------- 主入口 ----------------
    async def think(self, events: list, reason: str) -> bool:
        """返回 False 表示这轮没吃下去（正在想 / 被节流），调用方应该把事件还回去。"""
        if self.thinking:
            return False
        now = time.time()
        gap = float(self.cfg.get("llm.min_interval_sec", 1.5))
        if now - self.last_call < gap:
            return False
        self.last_call = now
        self.thinking = True
        try:
            route = self._pick_route(events, reason, now)
            self.last_route = route
            self.last_route_at = now
            log.debug("路由 → %s", self.router.explain(route, events, reason))

            if route is Route.CLOUD:
                if await self._try_cloud(events, reason, now):
                    return True
                # 云端这轮失手，看本地能不能接住
                if await self._try_local(events, reason):
                    return True
                return await self._rules_round(events)

            if route is Route.LOCAL:
                if await self._try_local(events, reason):
                    return True
                # 本地也没接住：额度还在就给云端一次机会
                if self.client.ready and self._llm_allowed() and self._rpm_ok(now):
                    if await self._try_cloud(events, reason, now):
                        return True
                return await self._rules_round(events)

            return await self._rules_round(events)
        finally:
            self.thinking = False

    def _pick_route(self, events: list, reason: str, now: float) -> Route:
        return self.router.decide(
            cloud_ready=self.client.ready,
            cloud_ok=self._llm_allowed(),
            quota_ratio=self._quota_ratio(now),
            local_ready=self._local_usable(),
            events=events,
            reason=reason,
        )

    # ---------------- 第一层：云端 ----------------
    async def _try_cloud(self, events: list, reason: str, now: float) -> bool:
        self._call_times.append(now)
        try:
            await self._llm_round(events, reason)
            self.consecutive_fails = 0
            if self.degraded:
                self.degraded = False
                self.degraded_reason = ""
                self.last_error = ""
                log.info("云端模型已恢复，切回智能体模式")
            return True
        except Exception as exc:  # noqa: BLE001
            self.stats["errors"] += 1
            self.last_error = str(exc)[:240]
            self.consecutive_fails += 1
            if "HTTP 429" in self.last_error:
                # RPM 限流：继续重试只会更糟，直接歇一分钟
                cool = float(self.cfg.get("llm.rate_limit_cooldown_sec", 60) or 60)
                self.degraded = True
                self.degraded_reason = "平台 RPM 限流"
                self._retry_at = time.time() + cool
                log.warning("云端 429 限流，%.0f 秒内交给本地/规则脑", cool)
            elif self._is_permanent(exc):
                # 401/403/404：重试再多次也没用，立刻降级，省下每一次调用
                if not self.degraded:
                    self.degraded = True
                    self.degraded_reason = f"key/配置无效：{self.last_error[:80]}"
                    log.error("云端不可用（永久性错误）：%s", self.last_error)
            else:
                log.warning("云端调用失败(%d/%d)：%s", self.consecutive_fails,
                            int(self.cfg.get("llm.fail_threshold", 3)), exc)
                if self.consecutive_fails >= int(self.cfg.get("llm.fail_threshold", 3)):
                    self.degraded = True
                    self.degraded_reason = f"连续失败：{self.last_error[:80]}"
                    log.warning("云端连续失败，降级到本地/规则脑")
            if self.degraded and "HTTP 429" not in self.last_error:
                # 非限流类降级：每 90 秒试探一次；成功就自动回来
                self._retry_at = time.time() + float(
                    self.cfg.get("llm.recover_probe_sec", 90) or 90
                )
            return False

    # ---------------- 第二层：本地 ----------------
    async def _try_local(self, events: list, reason: str) -> bool:
        if not self._local_usable():
            return False
        try:
            text, to = await self._local_say(events, reason)
        except Exception as exc:  # noqa: BLE001
            self._local_ok(exc)
            return False
        if not text:
            log.debug("本地模型返回空文本，这轮交给规则脑")
            return False
        self._local_succeeded()
        await self._say(text, "normal", to)
        self.stats["local_says"] += 1
        log.info("本地说话：%s", text[:48])
        return True

    async def _local_say(self, events: list, reason: str) -> tuple[str, str]:
        """本地只负责说一句话，不给工具、不做多轮，延迟才压得住。"""
        state = self.state_provider()
        system = build_local_system(self.cfg, state, self.last_said)
        user = build_local_batch(events, reason, self.cfg)
        res = await self.local.chat(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            None,
            temperature=float(self.cfg.get("llm.local.temperature", 0.9)),
            max_tokens=int(self.cfg.get("llm.local.max_tokens", 120)),
        )
        return clean_local_text(res.text), guess_target(events)

    # ---------------- 第三层：规则脑 ----------------
    async def _rules_round(self, events: list) -> bool:
        act = self.fallback.think(events, self.state_provider().get("scene", "coding"))
        if act:
            args = act.get("args") or {}
            out = await self.executor(act["tool"], args)
            self.stats["fallback_says"] += 1
            if args.get("text"):
                self.last_said = str(args["text"])
            log.info("兜底说话：%s", str(args.get("text") or out)[:40])
        return True

    async def _llm_round(self, events: list, reason: str, client: LLMClient | None = None) -> None:
        cli = client or self.client
        state = self.state_provider()
        state["clock"] = clock_text()
        state["last_said"] = self.last_said
        system = build_system(self.cfg, state, self.memory.recent())
        user = build_batch(events, reason, self.cfg)

        msgs: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        rounds = int(self.cfg.get("llm.max_tool_rounds", 3))
        spoke = False
        for i in range(rounds):
            self.stats["rounds"] += 1
            res = await cli.chat(msgs, TOOLS)

            if not res.tool_calls:
                text = (res.text or "").strip().strip('"“”')
                if text and not spoke:
                    await self._say(text, "normal", "")
                    spoke = True
                break

            calls = []
            stop = False
            sent_results: list[dict[str, Any]] = []
            for tc in res.tool_calls:
                name = tc.name or ""
                args = tc.args or {}
                if name == "say":
                    if spoke:  # 一轮只说一句，多余的丢掉
                        sent_results.append(
                            {"role": "tool", "tool_call_id": tc.id, "name": name, "content": "已忽略：本轮已说过话"}
                        )
                        continue
                    text = str(args.get("text", "")).strip()
                    if text:
                        await self._say(text, str(args.get("emotion", "normal")), str(args.get("to", "")))
                        spoke = True
                        sent_results.append(
                            {"role": "tool", "tool_call_id": tc.id, "name": name, "content": "已播出"}
                        )
                    else:
                        sent_results.append(
                            {"role": "tool", "tool_call_id": tc.id, "name": name, "content": "文本为空，未播出"}
                        )
                    stop = True
                    continue
                try:
                    out = await self.executor(name, args)
                except Exception as exc:  # noqa: BLE001
                    out = f"执行失败：{exc}"
                sent_results.append({"role": "tool", "tool_call_id": tc.id, "name": name, "content": out[:400]})
                # 已经产生"动作"的工具不必再让模型多跑一轮，省 token
                if name in ("noop", "switch_scene", "boss_key", "play_effect", "start_poll"):
                    stop = True

            calls = [{"id": c.id, "name": c.name, "args": c.args} for c in res.tool_calls]
            msgs.append({"role": "assistant", "content": res.text or "", "tool_calls": calls})
            msgs.extend(sent_results)
            if stop:
                break

    async def _say(self, text: str, emotion: str, to: str) -> None:
        limit = int(self.cfg.get("behavior.reply_max_chars", 70))
        text = " ".join(str(text).split())
        if len(text) > limit:
            # 硬切会把一句话切在半截，念出来特别别扭。
            # 优先在最近的标点处断，找不到才退化成硬切。
            cut = text[:limit]
            for i in range(len(cut) - 1, max(8, limit // 2), -1):
                if cut[i] in "，。、；！？,.!?;":
                    cut = cut[:i]
                    break
            text = cut.rstrip("，。、； ") + "…"
        self.last_said = text
        self.stats["says"] += 1
        await self.executor("say", {"text": text, "emotion": emotion, "to": to})

    # ---------------- 降级探测 ----------------
    async def probe(self) -> None:
        """两层各自自愈：云端恢复就切回去，本地恢复就重新接管简单任务。"""
        if self.degraded and time.time() >= self._retry_at:
            if await self.client.health():
                self.degraded = False
                self.consecutive_fails = 0
                self.degraded_reason = ""
                self.last_error = ""
                log.info("云端模型恢复，切回智能体模式")

        if self.local and self.local.ready and self.local_fails:
            if time.time() >= self._local_retry_at:
                if await self.local.health():
                    self._local_succeeded()
                    log.info("本地模型恢复，重新接管简单回复")

    def snapshot(self) -> dict[str, Any]:
        route_cn = {Route.CLOUD: "云端", Route.LOCAL: "本地", Route.RULES: "规则脑"}
        return {
            **self.stats,
            "degraded": self.degraded,
            "degraded_reason": self.degraded_reason,
            "last_error": self.last_error,
            "next_retry_in": max(0, int(self._retry_at - time.time())) if self.degraded else 0,
            "client": self.client.snapshot(),
            "last_said": self.last_said,
            "memory_size": len(self.memory.data),
            # ---- 三层路由状态 ----
            "route": self.last_route.value if self.last_route else "",
            "route_text": route_cn.get(self.last_route, "") if self.last_route else "",
            "strategy": self.router.strategy(),
            "quota_ratio": round(self._quota_ratio(time.time()), 2),
            "local": self.local.snapshot() if self.local else {},
            "local_fails": self.local_fails,
            "local_error": self.local_error,
            "local_next_retry_in": (
                max(0, int(self._local_retry_at - time.time())) if self.local_fails else 0
            ),
        }
