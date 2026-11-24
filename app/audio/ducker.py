"""系统级人声闪避（duck）：主播说话时压低外部音乐，说完淡回原音量。

为什么必须在系统层做
--------------------
舞台页自带的 lo-fi BGM 是页面里的 <audio>，页面内已经做了 duck；
但 Apple Music 是独立进程，声音走 Windows 系统混音，直播伴侣采集的
正是这个混音。所以只能按「音频会话」改它自己的音量。

只改目标音乐进程的会话音量，不动系统主音量，也不动 Chrome —— 舞台人声
和直播间网页都在 chrome.exe 里，压了就把主播一起压没了。

硬约束（和整个项目一致）
------------------------
* 任何一步失败都降级成「这次不 duck」，绝不把异常抛进直播主链路；
* 有超时看门狗，万一哪一步卡住也一定把音量还给用户；
* 不用任何付费/需注册的服务，pycaw 只是本机 COM 封装。
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("duck")

# Windows 版 Apple Music 真正出声的是 AMPLibraryAgent.exe（不是 AppleMusic.exe），
# 所以两个都列上；顺手带上常见播放器，用户换软件时不用改代码。
DEFAULT_TARGETS = (
    "amplibraryagent",
    "applemusic",
    "music",
    "itunes",
    "cloudmusic",      # 网易云音乐
    "qqmusic",
    "spotify",
    "foobar2000",
    "aimp",
    "potplayer",
)
# 这些绝对不碰：舞台人声、直播伴侣采集、OBS 都在里面
DEFAULT_EXCLUDE = (
    "chrome",
    "msedge",
    "mediasdk_server",
    "obs64",
    "obs32",
    "webcast",
    "applicationframehost",
)


def _norm(name: Any) -> str:
    n = str(name or "").lower().strip()
    if n.endswith(".exe"):
        n = n[:-4]
    return n


class AudioDucker:
    """按会话压低/恢复外部音乐音量。所有 COM 操作都在工作线程里跑。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._lock = asyncio.Lock()          # 保护事件循环侧的状态
        self._com_lock = threading.Lock()    # 串行化 COM 调用
        self._saved: dict[str, float] = {}    # 会话 key -> 原始音量
        self._last_set: dict[str, float] = {} # 会话 key -> 我们最后一次写入的音量
        self._ducking = False
        self._gen = 0                       # 渐变代号，新的 begin/restore 会让旧渐变自动退出
        self._active_until = 0.0
        self._watcher: asyncio.Task | None = None
        self._last_error = ""
        self._last_error_at = 0.0
        self.available = True
        self.duck_count = 0
        self.last_duck_at = 0.0
        self.last_targets: list[str] = []
        self.reload()

    # ---------------- 配置 ----------------
    def reload(self) -> None:
        def _f(key: str, default: float) -> float:
            try:
                return float(self.cfg.get(key, default) or default)
            except (TypeError, ValueError):
                return default

        self.enabled = bool(self.cfg.get("audio.duck.enabled", True))
        self.level = max(0.0, min(1.0, _f("audio.duck.level", 0.18)))
        self.fade_out = max(0.0, _f("audio.duck.fade_out_sec", 0.25))
        self.fade_in = max(0.0, _f("audio.duck.fade_in_sec", 0.60))
        self.hold = max(0.0, _f("audio.duck.hold_sec", 0.40))
        self.max_duck = max(5.0, _f("audio.duck.max_duck_sec", 30.0))
        # 舞台没上报时的兜底时长：够说完一句话，又不会把音乐压太久
        self.default_hold = max(3.0, _f("audio.duck.default_hold_sec", 8.0))

        raw = self.cfg.get("audio.duck.targets", None)
        self.targets = {
            _norm(x) for x in (raw if isinstance(raw, (list, tuple)) else DEFAULT_TARGETS)
        }
        raw = self.cfg.get("audio.duck.exclude", None)
        self.exclude = {
            _norm(x) for x in (raw if isinstance(raw, (list, tuple)) else DEFAULT_EXCLUDE)
        }

    # ---------------- 残留音量恢复 ----------------
    def _state_file(self) -> Path | None:
        try:
            return Path(self.cfg.data_dir) / "duck_state.json"
        except Exception:  # noqa: BLE001
            return None

    def _persist(self) -> None:
        """把「进程名 -> 原始音量」落盘。key 里带 pid，所以只按进程名聚合。

        为什么必须有这个文件：压低时用 `_saved.setdefault(key, cur)` 记原音量，
        key 是「进程名|pid|会话id」，而 _saved 只在内存里。进程被强杀（或者像今天
        这样连着重启五六次）之后记录全丢，下一次启动就会把「已经被压低的值」当成
        原始音量再乘一遍 level：1.0 → 0.18 → 0.03 → 0.006 → 0，音乐彻底没声。
        落盘之后，下次启动能把真正该有的音量还回去。
        """
        p = self._state_file()
        if p is None:
            return
        try:
            if not self._saved:
                if p.exists():
                    p.unlink()
                return
            by_name: dict[str, float] = {}
            for key, val in self._saved.items():
                name = key.split("|", 1)[0]
                by_name[name] = max(by_name.get(name, 0.0), float(val))
            p.write_text(json.dumps(by_name, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass  # 落盘失败只影响恢复能力，绝不能影响直播

    async def recover_stale(self) -> int:
        """启动时把上次没来得及恢复的音量还回去，返回恢复的会话数。"""
        p = self._state_file()
        if p is None or not p.exists():
            return 0
        try:
            saved = {
                str(k): float(v)
                for k, v in json.loads(p.read_text(encoding="utf-8")).items()
            }
        except Exception:  # noqa: BLE001
            saved = {}
        if not saved:
            try:
                p.unlink()
            except Exception:  # noqa: BLE001
                pass
            return 0
        try:
            items = await asyncio.to_thread(self._collect)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            return 0
        fixed = 0
        for _key, name, vol, cur in items:
            want = saved.get(_norm(name))
            if want is None:
                continue
            # 已经是正常音量就别动——用户可能自己调过
            if cur >= want - 0.05:
                continue
            try:
                vol.SetMasterVolume(float(want), None)
                fixed += 1
                log.info("恢复 %s 音量 %.2f → %.2f（上次没收回去）", name, cur, want)
            except Exception:  # noqa: BLE001
                pass
        try:
            p.unlink()
        except Exception:  # noqa: BLE001
            pass
        return fixed

    def _is_target(self, name: str) -> bool:
        n = _norm(name)
        if not n or n in self.exclude:
            return False
        if n in self.targets:
            return True
        return any(t and t in n for t in self.targets)

    # ---------------- 对外接口 ----------------
    async def begin(self, duration: float = 0.0) -> None:
        """主播要说话了。duration 是预估语音秒数（舞台不上报时的兜底）。"""
        if not self.enabled or not self.available:
            return
        async with self._lock:
            now = time.time()
            span = float(duration or 0) or self.default_hold
            until = now + max(0.6, span) + self.hold
            # 硬上限：无论多少句排队，单次 duck 不会超过 max_duck 秒
            self._active_until = min(max(self._active_until, until), now + self.max_duck)
            if not self._ducking:
                self._ducking = True
                self._gen += 1
                gen = self._gen
                self.duck_count += 1
                self.last_duck_at = now
                asyncio.create_task(self._run_ramp(gen, True), name="duck-down")
            self._ensure_watcher()

    async def end(self) -> None:
        """舞台页上报「语音队列空了」：留一点点尾巴就恢复，不用等预估时长。"""
        async with self._lock:
            if not self._ducking:
                return
            self._active_until = min(self._active_until, time.time() + self.hold)
            self._ensure_watcher()

    async def restore_now(self) -> None:
        """收播 / 退出时无条件把音量还给用户，不做渐变。"""
        async with self._lock:
            if not self._ducking and not self._saved:
                return
            self._ducking = False
            self._active_until = 0.0
            self._gen += 1
            gen = self._gen
        try:
            await asyncio.to_thread(self._ramp, gen, False, True)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    async def tick(self) -> None:
        """看门狗钩子：watcher 意外挂了就重启，超时则强制恢复。"""
        async with self._lock:
            if not self._ducking:
                return
            if time.time() > self._active_until + 5:
                self._active_until = time.time()
            self._ensure_watcher()

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "available": self.available,
            "ducking": self._ducking,
            "targets": sorted(set(self.last_targets)),
            "level": self.level,
            "duck_count": self.duck_count,
            "pending_restore": len(self._saved),
            "last_error": self._last_error,
        }

    # ---------------- 调度 ----------------
    def _ensure_watcher(self) -> None:
        # 调用方已持有 self._lock
        if self._watcher is None or self._watcher.done():
            self._watcher = asyncio.create_task(self._watch(), name="duck-watch")

    async def _watch(self) -> None:
        """等到 active_until 就恢复；循环体单独兜异常，保证不会半路死掉。"""
        try:
            while True:
                try:
                    async with self._lock:
                        if not self._ducking:
                            return
                        left = self._active_until - time.time()
                        if left <= 0:
                            self._ducking = False
                            self._gen += 1
                            gen = self._gen
                            break
                    await asyncio.sleep(min(0.20, max(0.02, left)))
                except asyncio.CancelledError:
                    raise
                except Exception as exc:  # noqa: BLE001
                    self._fail(exc)
                    await asyncio.sleep(0.2)
            try:
                await asyncio.to_thread(self._ramp, gen, False, False)
            except Exception as exc:  # noqa: BLE001
                self._fail(exc)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    async def _run_ramp(self, gen: int, down: bool) -> None:
        try:
            await asyncio.to_thread(self._ramp, gen, down, False)
        except ImportError as exc:
            # 依赖没装：直接停用，别每句话都重试一遍
            self._fail(exc, fatal=True)
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)

    def _fail(self, exc: BaseException, fatal: bool = False) -> None:
        self._last_error = f"{type(exc).__name__}: {exc}"[:180]
        now = time.time()
        if now - self._last_error_at > 30:
            log.warning("音频闪避失败（不影响直播）：%s", self._last_error)
            self._last_error_at = now
        if fatal:
            self.available = False
            log.warning("音频闪避已停用：缺少 pycaw/comtypes 或 COM 不可用")

    # ---------------- COM ----------------
    def _collect(self) -> list[tuple[str, str, Any, float]]:
        """枚举目标进程的音频会话。返回 (key, 进程名, 音量接口, 当前音量)。"""
        import comtypes
        from pycaw.pycaw import AudioUtilities

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:  # noqa: BLE001
            pass  # 已经初始化过（S_FALSE）或换过线程模型，都不影响
        out: list[tuple[str, str, Any, float]] = []
        for s in AudioUtilities.GetAllSessions():
            try:
                p = s.Process
                name = p.name() if p else ""
                if not self._is_target(name):
                    continue
                vol = s.SimpleAudioVolume  # POINTER(ISimpleAudioVolume)
                cur = float(vol.GetMasterVolume())
                iid = str(getattr(s, "InstanceIdentifier", "") or "")
                key = f"{_norm(name)}|{p.pid if p else 0}|{iid[:48]}"
                out.append((key, name, vol, cur))
            except Exception:  # noqa: BLE001
                continue  # 单个会话失效不影响其他会话
        return out

    def _ramp(self, gen: int, down: bool, instant: bool = False) -> None:
        """在工作线程里做淡出/淡入。gen 不匹配立刻退出，让新渐变接管。"""
        with self._com_lock:
            if gen != self._gen and not instant:
                return
            items = self._collect()
            self.last_targets = [i[1] for i in items]

            targets: list[tuple[str, Any, float, float]] = []
            restore_keys: set[str] = set()
            if down:
                # 清掉已经消失的会话，避免残留越攒越多
                live = {i[0] for i in items}
                for key in list(self._saved):
                    if key not in live:
                        self._saved.pop(key, None)
                        self._last_set.pop(key, None)
                # 记录原音量；音量接口每次重新枚举，避免进程重启后指针失效
                for key, _name, vol, cur in items:
                    self._saved.setdefault(key, cur)
                self._persist()  # 落盘，防止进程被强杀后原音量丢失
                for key, _name, vol, cur in items:
                    orig = self._saved.get(key, cur)
                    targets.append((key, vol, cur, orig * self.level))
            else:
                restore_keys = set(self._saved)
                for key, _name, vol, cur in items:
                    orig = self._saved.get(key)
                    if orig is None:
                        continue
                    # 用「我们最后写进去的值」比对照，而不是固定目标值：
                    # 渐变可能被下一句话打断停在半路，那样也得能接着恢复。
                    want = self._last_set.get(key, orig * self.level)
                    if not instant and abs(cur - want) > 0.07:
                        # 用户在我们压低期间自己调过音量：尊重他的设置，别覆盖。
                        # 强制恢复（收播）例外，必须把音量还回去。
                        self._saved.pop(key, None)
                        self._last_set.pop(key, None)
                        continue
                    targets.append((key, vol, cur, orig))

            if not targets:
                if not down:
                    # 目标进程已经退出，没有会话可恢复了，记录也别留着
                    for key in restore_keys:
                        self._saved.pop(key, None)
                        self._last_set.pop(key, None)
                return
            secs = 0.0 if instant else (self.fade_out if down else self.fade_in)
            steps = 1 if secs <= 0.05 else max(1, min(24, int(secs / 0.06)))
            dt = secs / steps if steps else 0.0
            for k in range(1, steps + 1):
                if gen != self._gen and not instant:
                    return
                ratio = k / steps
                for key, vol, frm, to in targets:
                    try:
                        value = frm + (to - frm) * ratio
                        vol.SetMasterVolume(value, None)
                        self._last_set[key] = value
                    except Exception:  # noqa: BLE001
                        pass  # 会话中途关掉很正常
                if dt:
                    time.sleep(dt)
            if not down:
                # 会话可能已经关掉（不在 items 里），也要把记录清干净
                for key in restore_keys:
                    self._saved.pop(key, None)
                    self._last_set.pop(key, None)
                self._persist()  # 恢复完就删掉残留文件
