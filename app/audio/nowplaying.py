"""读取播放器当前播的歌：UI Automation 直读 Apple Music 等的播放条。

为什么选 UIA
------------
* SMTC（系统媒体会话）需要 winsdk/WinRT 包，Python 3.13 没有可用 wheel；
* 窗口标题实测只显示「Apple Music」，不带歌名；
* UIA 用已装的 comtypes 直调系统接口，零第三方依赖，实测 0.25s 读完一次。

识别规则（按 Apple Music 真实 UI 树实测得出）：
* 播放条锚点：「向前跳 / 向后跳」按钮只在迷你播放条出现（歌曲列表里没有）；
* 「暂停」按钮存在 = 正在播放（暂停后它会变成「播放」）；
* 播放条歌名组是一个 Group，Name 形如「歌名 歌手 — 专辑」，它下面第一个
  Text 是歌名，第二个 Text 是「歌手 — 专辑」，取 — 前面的就是歌手。

播放器没开 / 结构变了 / 任何异常 → 返回 None，画面自动隐藏歌词条，不影响直播。
"""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

log = logging.getLogger("nowplaying")

# 窗口标题匹配（宽松包含）。Apple Music 实测标题就是「Apple Music」
DEFAULT_TITLES = ("Apple Music", "Music", "Spotify", "网易云音乐", "QQ音乐", "酷狗音乐")
_BAR_BUTTONS = ("向前跳", "向后跳")   # 只在播放条出现，列表区没有

_LRC_TIME = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")


def parse_lrc(text: str) -> list[tuple[float, str]]:
    """LRC 文本 → [(秒, 歌词), ...] 升序。一行多时间戳、空行、元数据都处理掉。"""
    out: list[tuple[float, str]] = []
    for raw in str(text or "").splitlines():
        stamps = _LRC_TIME.findall(raw)
        if not stamps:
            continue
        body = _LRC_TIME.sub("", raw).strip().strip("<>").strip()
        if not body or body.startswith("{") or body.startswith("["):
            continue  # 空行 / 翻译标记 / 剩余元数据
        for mm, ss in stamps:
            out.append((int(mm) * 60 + float(ss), body))
    out.sort(key=lambda x: x[0])
    return out


def current_line(lines: list[tuple[float, str]], pos: float) -> tuple[str, str]:
    """按播放进度取 (当前行, 下一行)。pos 提前 0.35 秒切行，接近 KTV 手感。"""
    cur, nxt = "", ""
    for t, text in lines:
        if t <= float(pos or 0) + 0.35:
            cur = text
        else:
            nxt = text
            break
    return cur, nxt


def _clean_title(title: str) -> str:
    """去掉歌名里的括号注释、引号等，提高歌词匹配率。
    「铁血丹心 (无线电视剧《射鵰英雄传》主题曲)」→「铁血丹心」；
    「Together (From "Reply 1988") [Original Television Soundtrack]」→「Together」"""
    t = str(title or "")
    t = re.sub(r"\[[^\[\]]*\]", "", t)      # [方括号注释]
    t = re.sub(r"[（(][^（）()]*[)）]", "", t)  # (圆括号注释)，只去一层
    t = re.sub(r"""[""«»„“”''']+""", "", t)  # 引号
    return t.strip(" -–—·")


def _artist_match(result_artist: str, query_artist: str) -> bool:
    """lrclib 结果的歌手和查询的歌手是否对得上（子串或词交集），防止同名歌串台。"""
    a1 = str(result_artist or "").lower().replace(" ", "")
    a2 = str(query_artist or "").lower().replace(" ", "")
    if not a1 or not a2:
        return True  # 没有歌手信息就不过滤
    if a1 in a2 or a2 in a1:
        return True
    w1 = {w for w in re.findall(r"[a-z0-9\u4e00-\u9fff]+", a1) if len(w) > 1}
    w2 = {w for w in re.findall(r"[a-z0-9\u4e00-\u9fff]+", a2) if len(w) > 1}
    return bool(w1 & w2)


def _pick_result(items: list, artist: str) -> dict | None:
    """优先选歌手对得上且有同步歌词的结果，其次随便一个有同步歌词的。"""
    synced_items = [it for it in items or [] if (it.get("syncedLyrics") or "").strip()]
    for it in synced_items:
        if _artist_match(it.get("artistName") or "", artist):
            return it
    return synced_items[0] if synced_items else None


def _needs_translation(lines: list[tuple[float, str]]) -> bool:
    """歌词是不是「非中文主导」（韩文/日文/英文…）→ 需要翻成简体中文。
    粤语歌也是汉字，不算外语，只做简体转写。"""
    sample = "".join(t for _, t in lines[:24])
    if not sample:
        return False
    han = len(re.findall(r"[\u4e00-\u9fff]", sample))
    foreign = len(re.findall(r"[A-Za-z\uac00-\ud7af\u3040-\u30ff]", sample))
    return foreign > han


class LyricsClient:
    """lrclib.net 的免费歌词（开源公益接口，无需 key，不花钱）。

    结果落盘缓存到 data/lyrics/，同一首歌整个直播期间只查一次；
    查不到也缓存空结果，不会每首歌反复打接口。失败静默返回 None。
    """

    URL = "https://lrclib.net/api/search"

    def __init__(self, cfg):
        self.cfg = cfg
        self._last_error_at = 0.0
        self.last_error = ""

    def _cache_path(self, title: str, artist: str) -> Path:
        h = hashlib.md5(f"{title}|{artist}".encode("utf-8")).hexdigest()[:20]
        return Path(self.cfg.data_dir) / "lyrics" / f"{h}.json"

    def get(self, title: str, artist: str, duration: float = 0.0) -> dict | None:
        if not str(title or "").strip():
            return None
        cache = self._cache_path(title, artist)
        if cache.exists():
            try:
                return json.loads(cache.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                pass  # 缓存坏了就重新查
        result = self._fetch(title, artist, duration)
        if result is None:
            cleaned = _clean_title(title)
            if cleaned and cleaned != title:
                result = self._fetch(cleaned, artist, duration)
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        except Exception:  # noqa: BLE001
            pass
        return result

    def _fetch(self, title: str, artist: str, duration: float) -> dict | None:
        try:
            import httpx

            headers = {"User-Agent": "AIGirlLive/1.0 (local stream)"}

            def _search(params: dict) -> list:
                r = httpx.get(self.URL, params=params, timeout=8.0, trust_env=False, headers=headers)
                return r.json() if r.status_code == 200 and isinstance(r.json(), list) else []

            cleaned = _clean_title(title)
            clean_artist = _clean_title(artist)
            # 轮 1：按 track/artist 官方字段搜
            params = {"track_name": title, **({"artist_name": artist} if artist else {})}
            it = _pick_result(_search(params), artist)
            # 轮 2：q 全文搜（歌名+歌手），OST 版歌名往往只能这样搜到
            if it is None:
                it = _pick_result(_search({"q": f"{cleaned} {clean_artist}".strip()}), artist)
            # 轮 3：只按歌名搜，但歌手必须对得上，防止同名歌串台
            if it is None and clean_artist:
                it = next(
                    (
                        x
                        for x in _search({"q": cleaned})
                        if (x.get("syncedLyrics") or "").strip() and _artist_match(x.get("artistName") or "", artist)
                    ),
                    None,
                )
            if it:
                lines = parse_lrc(it.get("syncedLyrics") or "")
                if lines:
                    # 中文歌词统一转成简体（繁体/港台歌常见），缓存里就是简体
                    lines = _to_simplified(lines)
                    return {"synced": lines, "plain": it.get("plainLyrics") or ""}
            return None
        except Exception as exc:  # noqa: BLE001
            now = time.time()
            self.last_error = f"{type(exc).__name__}: {exc}"[:140]
            if now - self._last_error_at > 60:
                log.info("歌词接口暂时不可用（不影响直播）：%s", self.last_error)
                self._last_error_at = now
            return None


def _to_simplified(lines: list[tuple[float, str]]) -> list[tuple[float, str]]:
    """繁体 → 简体（zhconv 纯本地转换，没装就原样返回）。"""
    try:
        from zhconv import convert
    except Exception:  # noqa: BLE001
        return lines
    try:
        return [(t, convert(text, "zh-hans")) for t, text in lines]
    except Exception:  # noqa: BLE001
        return lines


def split_artist_line(line: str) -> str:
    """「张靓颖 & 曾舜晞 — 在奇迹之城遇见你 - Single」→「张靓颖 & 曾舜晞」。"""
    return str(line or "").split(" — ")[0].strip()


class NowPlaying:
    """每次 read() 实时读一次。调用方应该放在工作线程里（COM + 全树遍历）。"""

    def __init__(self, cfg):
        self.cfg = cfg
        self._uia: Any = None
        self._U: Any = None           # comtypes.gen.UIAutomationClient 模块
        self._hwnd: int | None = None
        self._last_error_at = 0.0
        self.last_error = ""
        self.reload()

    def reload(self) -> None:
        self.enabled = bool(self.cfg.get("stage.nowplaying.enabled", True))
        raw = self.cfg.get("stage.nowplaying.window_titles", None)
        self.titles = tuple(
            str(x) for x in (raw if isinstance(raw, (list, tuple)) else DEFAULT_TITLES)
        )

    # ---------------- 对外 ----------------
    def read(self) -> dict | None:
        """返回 {"title","artist","playing"}，读不到返回 None。绝不抛异常。"""
        if not self.enabled:
            return None
        try:
            return self._read()
        except Exception as exc:  # noqa: BLE001
            self._fail(exc)
            self._uia = None            # 下次重建，COM 状态可能已经坏了
            self._hwnd = None
            return None

    def snapshot(self) -> dict:
        return {
            "enabled": self.enabled,
            "titles": list(self.titles),
            "found": self._hwnd is not None,
            "last_error": self.last_error,
        }

    # ---------------- 内部 ----------------
    def _fail(self, exc: BaseException) -> None:
        self.last_error = f"{type(exc).__name__}: {exc}"[:160]
        now = time.time()
        if now - self._last_error_at > 30:
            log.warning("读歌名失败（画面会隐藏歌词条）：%s", self.last_error)
            self._last_error_at = now

    def _ensure_uia(self) -> None:
        if self._uia is not None:
            return
        import comtypes
        from comtypes.client import CreateObject, GetModule

        try:
            comtypes.CoInitializeEx(comtypes.COINIT_MULTITHREADED)
        except Exception:  # noqa: BLE001
            pass  # 线程里已经初始化过
        GetModule("UIAutomationCore.dll")
        import comtypes.gen.UIAutomationClient as U

        self._U = U
        self._uia = CreateObject(U.CUIAutomation, interface=U.IUIAutomation)

    def _find_hwnd(self) -> int | None:
        user32 = ctypes.windll.user32
        CB = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        found: list[int] = []
        wanted = [t.lower() for t in self.titles]

        def cb(hwnd, _):
            if user32.IsWindowVisible(hwnd):
                n = user32.GetWindowTextLengthW(hwnd)
                if n:
                    buf = ctypes.create_unicode_buffer(n + 1)
                    user32.GetWindowTextW(hwnd, buf, n + 1)
                    t = buf.value.strip().lower()
                    if t and any(w == t or w in t for w in wanted):
                        found.append(int(hwnd))
            return True

        user32.EnumWindows(CB(cb), 0)
        return found[0] if found else None

    def _read(self) -> dict | None:
        self._ensure_uia()
        U = self._U
        user32 = ctypes.windll.user32

        hwnd = self._hwnd
        if not hwnd or not user32.IsWindow(hwnd):
            hwnd = self._find_hwnd()
            if not hwnd:
                return None
            self._hwnd = hwnd

        el = self._uia.ElementFromHandle(ctypes.c_void_p(hwnd))
        all_el = el.FindAll(U.TreeScope_Descendants, self._uia.CreateTrueCondition())
        text_cond = self._uia.CreatePropertyCondition(
            U.UIA_ControlTypePropertyId, U.UIA_TextControlTypeId
        )

        playing = False
        has_bar = False
        result: dict | None = None
        for i in range(all_el.Length):
            try:
                e = all_el.GetElement(i)
                ct = e.CurrentControlType
                nm = (e.CurrentName or "").strip()
            except Exception:  # noqa: BLE001
                continue  # 元素中途失效很常见
            if ct == U.UIA_ButtonControlTypeId:
                if nm == "暂停":
                    playing = True
                elif nm in _BAR_BUTTONS:
                    has_bar = True
            elif nm and " — " in nm and ct == U.UIA_GroupControlTypeId and result is None:
                # 播放条歌名组：下面第一个 Text 是歌名，第二个是「歌手 — 专辑」
                sub = e.FindAll(U.TreeScope_Descendants, text_cond)
                texts = []
                for k in range(sub.Length):
                    t = sub.GetElement(k).CurrentName
                    if t and t.strip():
                        texts.append(t.strip())
                if len(texts) >= 2:
                    result = {"title": texts[0], "artist": split_artist_line(texts[1])}

        if not has_bar or not result:
            return None
        out = {"title": result["title"], "artist": result["artist"], "playing": playing}
        out.update(self._read_progress(el))
        return out

    def _read_progress(self, el) -> dict:
        """播放条进度：Slider 的 RangeValue 就是 (当前秒, 总秒)。
        音量条的 Maximum 只有 1，按 >30 秒过滤掉。"""
        U = self._U
        try:
            sliders = el.FindAll(
                U.TreeScope_Descendants,
                self._uia.CreatePropertyCondition(U.UIA_ControlTypePropertyId, U.UIA_SliderControlTypeId),
            )
            for k in range(sliders.Length):
                s = sliders.GetElement(k)
                pat = s.GetCurrentPattern(U.UIA_RangeValuePatternId)
                if not pat:
                    continue
                rp = pat.QueryInterface(U.IUIAutomationRangeValuePattern)
                mx = float(rp.CurrentMaximum)
                val = float(rp.CurrentValue)
                if mx > 30:
                    return {"position": round(val, 2), "duration": round(mx, 2)}
        except Exception:  # noqa: BLE001
            pass  # 进度读不到就只显示歌名，歌词没法同步而已
        return {}
