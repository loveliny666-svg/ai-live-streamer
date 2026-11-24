"""配置加载：yaml + 默认值深合并 + 环境变量覆盖 + 热重载。"""
from __future__ import annotations

import copy
import os
import threading
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.yaml"

DEFAULTS: dict[str, Any] = {
    "app": {
        "name": "小饭的打工直播间",
        "data_dir": "data",
        "log_level": "INFO",
        "shutdown_at": "",
    },
    "server": {"host": "127.0.0.1", "port": 8735},
    "llm": {
        "provider": "mock",
        "api_key": "",
        "api_key_env": "LLM_API_KEY",
        "base_url": "",
        "model": "",
        "temperature": 0.85,
        "max_tokens": 320,
        "timeout_sec": 25,
        "max_tool_rounds": 3,
        "min_interval_sec": 1.5,
        "rpm": 8,
        "rate_limit_cooldown_sec": 60,
        "context_events": 14,
        "fail_threshold": 3,
        "recover_probe_sec": 90,
        # 本地模型（Ollama）：云端额度用完 / 出故障时接管简单回复
        "local": {
            "enabled": False,
            "provider": "ollama",
            "base_url": "",
            "model": "",
            "temperature": 0.9,
            "max_tokens": 120,
            "timeout_sec": 15,
            # spill = 云端额度见底才溢出到本地；split = 简单任务直接给本地
            "strategy": "spill",
            "spill_ratio": 0.4,
            "fail_threshold": 2,
            "recover_probe_sec": 60,
            "warmup": True,
            "keep_alive": -1,
            "think": False,
            "repeat_penalty": 1.15,
        },
    },
    "persona": {
        "name": "小饭",
        "role": "被老板抓来通宵改需求的 AI 程序员",
        "style": "口语、短句、有点自嘲的幽默",
        "catchphrases": [],
        "banned": [],
    },
    "danmaku": {
        "enabled": True,
        "room_url": "",
        "sources": {
            "ws": {
                "enabled": True,
                "chrome_path": "",
                "headless": False,
                "offscreen": True,
            },
            "dom": {
                "enabled": False,
                "chrome_path": "",
                "headless": False,
                "poll_ms": 400,
                "offscreen": True,
            },
            "ocr": {
                "enabled": True,
                "poll_ms": 200,
                "interval_sec": 0.7,
                "bottom_ratio": 0.45,
                "scale": 1.0,
                "fallback_only": True,
                "region": [],
            },
            "simulator": {"enabled": False, "interval_sec": 12},
        },
        "dedupe_window_sec": 25,
        "enter_dedupe_sec": 3,
        "gift_silent_below": 1,
    },
    "tts": {
        "engine": "edge",
        "voice": "zh-CN-XiaoyiNeural",
        "pitch": "+18Hz",
        "rate": "+8%",
        "volume": "+0%",
        "polish": True,
        "max_chars": 85,
        "queue_max": 4,
        "fallback_sapi": True,
    },
    "audio": {
        "duck": {
            "enabled": True,
            "targets": [
                "AMPLibraryAgent", "AppleMusic", "Music", "iTunes",
                "cloudmusic", "QQMusic", "Spotify", "foobar2000", "PotPlayer",
            ],
            "exclude": [
                "chrome", "msedge", "MediaSDK_Server", "obs", "webcast", "ApplicationFrameHost"
            ],
            "level": 0.18,
            "fade_out_sec": 0.25,
            "fade_in_sec": 0.60,
            "hold_sec": 0.40,
            "default_hold_sec": 8.0,
            "max_duck_sec": 30.0,
        },
    },
    "stage": {
        "width": 1920,
        "height": 1080,
        "browser": "auto",
        "mode": "fullscreen",
        "position": [0, 0],
        "heartbeat_timeout_sec": 8,
        "title": "小饭的打工日常 · 通宵改需求中",
        "bgm": {
            "enabled": False,
            "volume": 0.30,
            "coding": "calm.mp3",
            "slack": "playful.mp3",
            "boss": "",
        },
        "nowplaying": {
            "enabled": True,
            "lyrics": True,
            "window_titles": ["Apple Music", "Music", "Spotify", "网易云音乐", "QQ音乐", "酷狗音乐"],
        },
        "welcome": {
            "enabled": True,
            "sound": True,
            "duration_ms": 2800,
            "max_visible": 5,
        },
    },
    "director": {
        "start_scene": "coding",
        "games": ["pvz", "snake", "tetris", "g2048"],
        "coding": {"min_sec": 240, "max_sec": 900},
        "slack": {"min_sec": 100, "max_sec": 300},
        "agent_can_switch": True,
        "boss_key_hold_sec": 12,
        "switch_cooldown_sec": 20,
    },
    "behavior": {
        "idle_talk_sec": 18,
        "idle_history_ratio": 0.35,
        "max_replies_per_min": 10,
        "same_user_cooldown_sec": 12,
        "enter_cooldown_sec": 45,
        "welcome_enter": True,
        "thank_follow": True,
        "thank_gift_above": 5,
        "big_gift_above": 50,
        "reply_max_chars": 65,
    },
    "companion": {
        "enabled": True,
        "exe_path": "",
        "auto_launch": True,
        "auto_start_live": True,
        "assume_live": False,
        "scene_name": "",
        "ocr_min_score": 0.55,
        "click_retries": 3,
        "permission_keywords": ["申请开播权限"],
        "recover_keywords": ["重新连接", "重试", "我知道了", "确定"],
        "health_check_sec": 45,
        "restart_cooldown_sec": 120,
    },
    "safety": {
        "mute_on_repeated_errors": True,
        "error_budget": 8,
        "log_keep_days": 14,
    },
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """线程安全的配置容器，支持点路径读取。"""

    def __init__(self, path: Path | str = CONFIG_PATH):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._data: dict[str, Any] = copy.deepcopy(DEFAULTS)
        self.reload()

    # ---------- IO ----------
    def reload(self) -> None:
        raw: dict[str, Any] = {}
        if self.path.exists():
            try:
                raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
            except Exception as exc:  # noqa: BLE001
                raise RuntimeError(f"config.yaml 解析失败: {exc}") from exc
        with self._lock:
            self._data = _deep_merge(DEFAULTS, raw)
        self._apply_env()

    def _apply_env(self) -> None:
        env_key = os.environ.get(
            str(self.get("llm.api_key_env", "LLM_API_KEY")) or "LLM_API_KEY"
        )
        if env_key and not self.get("llm.api_key"):
            with self._lock:
                self._data["llm"]["api_key"] = env_key.strip()
        # 服务器端口也允许环境变量覆盖，方便多开
        if os.environ.get("LIVE_PORT"):
            with self._lock:
                self._data["server"]["port"] = int(os.environ["LIVE_PORT"])

    def save(self, data: dict[str, Any] | None = None) -> None:
        payload = data if data is not None else self.snapshot()
        text = yaml.safe_dump(payload, allow_unicode=True, sort_keys=False, width=100)
        tmp = self.path.with_suffix(".yaml.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(self.path)

    # ---------- 访问 ----------
    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur if cur is not None else default

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        with self._lock:
            cur = self._data
            for part in parts[:-1]:
                cur = cur.setdefault(part, {})
            cur[parts[-1]] = value

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return copy.deepcopy(self._data)

    # ---------- 派生 ----------
    @property
    def data_dir(self) -> Path:
        d = ROOT / str(self.get("app.data_dir", "data"))
        d.mkdir(parents=True, exist_ok=True)
        return d

    def resolve(self, *parts: str) -> Path:
        p = self.data_dir
        for part in parts:
            p = p / part
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def has_api_key(self) -> bool:
        return bool(str(self.get("llm.api_key", "")).strip())


CFG = Config()
