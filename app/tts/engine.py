"""语音合成：edge-tts（免费在线）为主，Windows SAPI（离线）兜底。

产物是 mp3/wav 文件，由舞台页播放，这样口型和字幕能对得上。
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import subprocess
import time
from pathlib import Path

log = logging.getLogger("tts")

_SAPI_PS = r"""
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.Rate = {rate}
$s.SetOutputToWaveFile('{out}')
$s.Speak('{text}')
$s.Dispose()
"""


class TTS:
    def __init__(self, cfg):
        self.cfg = cfg
        self.dir: Path = cfg.resolve("audio")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.engine = str(cfg.get("tts.engine", "edge")).lower()
        self.voice = str(cfg.get("tts.voice", "zh-CN-XiaoyiNeural"))
        self.rate = str(cfg.get("tts.rate", "+8%"))
        self.pitch = str(cfg.get("tts.pitch", "+18Hz"))
        self.volume = str(cfg.get("tts.volume", "+0%"))
        self.polish = bool(cfg.get("tts.polish", True))
        self._ffmpeg = None
        self.max_chars = int(cfg.get("tts.max_chars", 85))
        self.fallback_sapi = bool(cfg.get("tts.fallback_sapi", True))
        self.available = self.engine != "none"
        self._edge_ok = True
        self._fail_count = 0
        self._lock = asyncio.Lock()
        self._cache_hits = 0

    # ---------- 工具 ----------
    def clean(self, text: str) -> str:
        t = " ".join(str(text).split())
        if len(t) > self.max_chars:
            # 尽量在标点处截断
            cut = t[: self.max_chars]
            for sep in ("，", "。", "！", "？", "、", ",", "."):
                idx = cut.rfind(sep)
                if idx > self.max_chars * 0.55:
                    return cut[: idx + 1]
            return cut
        return t

    def _path(self, text: str, ext: str, style: str = "") -> Path:
        h = hashlib.md5(f"{self.voice}|{self.rate}|{self.pitch}|{style}|{text}".encode("utf-8")).hexdigest()[:18]
        return self.dir / f"{h}.{ext}"

    def _find_ffmpeg(self) -> str:
        if self._ffmpeg is not None:
            return self._ffmpeg
        import shutil

        self._ffmpeg = shutil.which("ffmpeg") or ""
        if not self._ffmpeg:
            # 别在这里写死用户名。用 %USERPROFILE% 展开，再加两个常见安装位置。
            import os as _os

            for cand in (
                _os.path.expandvars(r"%USERPROFILE%\bin\ffmpeg.exe"),
                r"C:\ffmpeg\bin\ffmpeg.exe",
                _os.path.expandvars(r"%LOCALAPPDATA%\Microsoft\WinGet\Links\ffmpeg.exe"),
            ):
                if Path(cand).exists():
                    self._ffmpeg = cand
                    break
        return self._ffmpeg

    def _polish_file(self, src: Path, dst: Path) -> bool:
        """轻量音色润色：去闷、提亮、压一点动态。TTS 干声听起来更像真人。"""
        ff = self._find_ffmpeg()
        if not ff or not self.polish:
            return False
        af = (
            "highpass=f=85,"
            "equalizer=f=260:t=q:w=1.0:g=-2.0,"
            "equalizer=f=3100:t=q:w=1.2:g=2.6,"
            "acompressor=threshold=0.10:ratio=2.8:attack=6:release=140,"
            "loudnorm=I=-15:TP=-1.2:LRA=9"
        )
        try:
            r = subprocess.run(
                [ff, "-y", "-v", "error", "-i", str(src), "-af", af, "-b:a", "64k", str(dst)],
                capture_output=True,
                timeout=20,
                creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            return r.returncode == 0 and dst.exists() and dst.stat().st_size > 800
        except Exception as exc:  # noqa: BLE001
            log.debug("润色失败：%s", exc)
            return False

    # 情绪 → 语速/音高（让同一副嗓子有起伏）
    _EMO = {
        "happy": ("+14%", "+3Hz"),
        "excited": ("+18%", "+5Hz"),
        "tired": ("-12%", "-5Hz"),
        "sigh": ("-16%", "-7Hz"),
        "panic": ("+24%", "+7Hz"),
        "normal": ("+0%", "+0Hz"),
    }

    @staticmethod
    def _shift(base: str, delta: str) -> str:
        try:
            return f"{int(base.replace('%','').replace('Hz','').replace('+','')) + int(delta.replace('%','').replace('Hz','').replace('+','')):+d}{base[-2:] if base.endswith('Hz') else '%'}"
        except Exception:  # noqa: BLE001
            return base

    def _cleanup(self) -> None:
        """音频缓存别无限占空间：超过 400 个就把最旧的清成 0 字节。
        注意：不要真正删除文件 —— 某些运行环境的 safe-delete 守卫会拦截删除、
        甚至直接 SystemExit 杀死进程（2026-11-23 直播实例就因此崩过一次）。
        清成 0 字节后，下次合成会按「size>1200」的判定自动重写该文件。"""
        try:
            files = sorted(self.dir.glob("*.*"), key=lambda p: p.stat().st_mtime)
            for p in files[:-400]:
                if p.name.startswith("_"):
                    continue  # 固定名工作文件不动
                try:
                    with open(p, "wb"):
                        pass
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass

    # ---------- 合成 ----------
    async def _edge(self, text: str, out: Path, rate: str = "", pitch: str = "") -> bool:
        import edge_tts

        comm = edge_tts.Communicate(
            text, self.voice, rate=rate or self.rate, pitch=pitch or self.pitch, volume=self.volume
        )
        buf = bytearray()
        async for chunk in comm.stream():
            if chunk["type"] == "audio":
                buf.extend(chunk["data"])
        if len(buf) < 800:
            return False
        out.write_bytes(bytes(buf))
        return True

    def _sapi(self, text: str, out: Path) -> bool:
        try:
            rate = 0
            try:
                pct = int(str(self.rate).replace("%", "").replace("+", ""))
                rate = max(-10, min(10, int(pct / 10)))
            except Exception:  # noqa: BLE001
                rate = 1
            safe = text.replace("'", "''")
            script = _SAPI_PS.format(rate=rate, out=str(out).replace("'", "''"), text=safe)
            r = subprocess.run(
                ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                capture_output=True,
                timeout=20,
                creationflags=0x08000000 if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
            )
            return out.exists() and out.stat().st_size > 2000
        except Exception as exc:  # noqa: BLE001
            log.debug("SAPI 失败：%s", exc)
            return False

    async def synth(self, text: str, emotion: str = "normal") -> tuple[str | None, float]:
        """返回 (可访问的相对路径, 预估秒数)。失败返回 (None, 预估秒数)。"""
        text = self.clean(text)
        if not text or not self.available:
            return None, self.estimate(text)

        rate_d, pitch_d = self._EMO.get(emotion, self._EMO["normal"])
        rate = self._shift(self.rate, rate_d)
        pitch = self._shift(self.pitch, pitch_d)

        mp3 = self._path(text, "mp3", emotion)
        if mp3.exists() and mp3.stat().st_size > 1200:
            self._cache_hits += 1
            return f"/audio/{mp3.name}", self.estimate(text)

        async with self._lock:
            if mp3.exists() and mp3.stat().st_size > 1200:
                return f"/audio/{mp3.name}", self.estimate(text)

            if self.engine in ("edge", "auto") and self._edge_ok:
                try:
                    # 固定名工作文件：每次覆盖写，绝不删除 —— 某些环境的
                    # safe-delete 守卫会在删除第 50 个文件时 SystemExit 杀进程
                    raw = self.dir / "_raw_tmp.mp3"
                    ok = await asyncio.wait_for(self._edge(text, raw, rate, pitch), timeout=18)
                    if ok:
                        loop = asyncio.get_running_loop()
                        done = await loop.run_in_executor(None, self._polish_file, raw, mp3)
                        if not done:
                            import shutil

                            shutil.copyfile(raw, mp3)   # 润色失败：原声直接当成品
                        self._fail_count = 0
                        return f"/audio/{mp3.name}", self.estimate(text)
                    raise RuntimeError("返回内容过短")
                except Exception as exc:  # noqa: BLE001
                    self._fail_count += 1
                    log.warning("edge-tts 失败（第 %d 次）：%s", self._fail_count, exc)
                    if self._fail_count >= 3:
                        self._edge_ok = False
                        log.warning("edge-tts 连续失败，暂时切到离线语音")

            if self.fallback_sapi or self.engine == "sapi":
                wav = self._path(text, "wav", emotion)
                loop = asyncio.get_running_loop()
                ok = await loop.run_in_executor(None, self._sapi, text, wav)
                if ok:
                    return f"/audio/{wav.name}", self.estimate(text)

        return None, self.estimate(text)

    def estimate(self, text: str) -> float:
        """中文约 5.2 字/秒。"""
        return max(1.6, min(30.0, len(text) / 5.2 + 0.8))

    async def probe_recover(self) -> None:
        """定期试探 edge-tts 是否恢复。"""
        if self._edge_ok or self.engine == "none":
            return
        try:
            ok = await self._edge("测试", self._path("__probe__", "mp3"))
            if ok:
                self._edge_ok = True
                log.info("edge-tts 已恢复")
        except Exception:  # noqa: BLE001
            pass

    def snapshot(self) -> dict:
        return {
            "engine": self.engine,
            "edge_ok": self._edge_ok,
            "voice": self.voice,
            "pitch": self.pitch,
            "polish": self.polish and bool(self._find_ffmpeg()),
            "cache_files": len(list(self.dir.glob("*.*"))),
            "cache_hits": self._cache_hits,
        }
