"""合成直播间的背景音乐 —— 全部用 numpy 算出来，零版权、零成本、不联网。

生成两段可无缝循环的 lo-fi：
    calm.mp3     写代码时放，70 BPM，Am7-Dm7-G7-Cmaj7
    playful.mp3  摸鱼时放，85 BPM，Cmaj7-Am7-Fmaj7-G7

用法：
    python tools/make_bgm.py            # 生成到 data/bgm/
    python tools/make_bgm.py --out D:/x # 指定目录
"""
from __future__ import annotations

import argparse
import subprocess
import sys
import wave
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
SR = 44100

# 音名 → 频率（A4=440）
_NAMES = {"C": 0, "C#": 1, "D": 2, "D#": 3, "E": 4, "F": 5,
          "F#": 6, "G": 7, "G#": 8, "A": 9, "A#": 10, "B": 11}


def hz(name: str, octave: int) -> float:
    return 440.0 * 2 ** ((_NAMES[name] + (octave - 4) * 12 - 9) / 12)


def env_exp(n: int, attack: float, decay: float, sr: int = SR) -> np.ndarray:
    t = np.arange(n) / sr
    a = 1.0 - np.exp(-t / max(1e-4, attack))
    return a * np.exp(-t * decay)


def rhodes(freq: float, dur: float, amp: float = 0.18) -> np.ndarray:
    """电钢音色：基频 + 少量谐波，慢起音、指数衰减，带一点失谐的厚度。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = (
        np.sin(2 * np.pi * freq * t)
        + 0.32 * np.sin(2 * np.pi * freq * 2.001 * t)
        + 0.10 * np.sin(2 * np.pi * freq * 3.002 * t)
        + 0.18 * np.sin(2 * np.pi * freq * 0.998 * t)
    )
    # 极轻的颤音，模拟磁带抖动
    x *= 1 + 0.0035 * np.sin(2 * np.pi * 4.8 * t)
    return x * env_exp(n, 0.012, 1.55) * amp


def pad(freqs, dur: float, amp: float = 0.055) -> np.ndarray:
    """持续的和弦垫音：慢起慢落，专门用来填和弦之间的空档。"""
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.zeros(n)
    for f in freqs:
        x += np.sin(2 * np.pi * f * t)
        x += 0.3 * np.sin(2 * np.pi * f * 1.004 * t)
    x /= max(1, len(freqs)) * 1.3
    env = np.minimum(1.0, t / 1.1) * np.minimum(1.0, np.maximum(0.0, (dur - t)) / 1.2)
    return x * env * amp


def bass(freq: float, dur: float, amp: float = 0.22) -> np.ndarray:
    n = int(dur * SR)
    t = np.arange(n) / SR
    x = np.sin(2 * np.pi * freq * t) + 0.22 * np.sin(2 * np.pi * freq * 2 * t)
    return np.tanh(x * 1.15) * env_exp(n, 0.02, 1.8) * amp


def kick(amp: float = 0.42) -> np.ndarray:
    dur = 0.38
    n = int(dur * SR)
    t = np.arange(n) / SR
    f = 58 * np.exp(-t * 26) + 40
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 10) * amp


def snare(amp: float = 0.14, rng=None) -> np.ndarray:
    dur = 0.30
    n = int(dur * SR)
    t = np.arange(n) / SR
    rng = rng or np.random.default_rng(1)
    noise = rng.normal(0, 1, n)
    # 一阶带通，让噪声落在军鼓的频段
    noise = np.convolve(noise, [1, -0.7], mode="same")
    body = np.sin(2 * np.pi * 185 * t) * 0.5
    return (noise * 0.8 + body) * np.exp(-t * 16) * amp


def hat(amp: float = 0.05, rng=None) -> np.ndarray:
    dur = 0.09
    n = int(dur * SR)
    t = np.arange(n) / SR
    rng = rng or np.random.default_rng(2)
    noise = rng.normal(0, 1, n)
    noise = np.diff(noise, prepend=0)  # 高通
    return noise * np.exp(-t * 70) * amp


def add_wrap(buf: np.ndarray, sig: np.ndarray, start: int) -> None:
    """把音符写进缓冲；超出末尾的部分绕回开头 —— 这样循环接缝听不出来。"""
    if len(sig) <= 0 or len(buf) == 0:
        return
    start = int(start) % len(buf)      # 超出末尾就绕回开头
    end = start + len(sig)
    if end <= len(buf):
        buf[start:end] += sig
    else:
        cut = len(buf) - start
        buf[start:] += sig[:cut]
        rest = sig[cut:]
        while len(rest) > 0:
            take = min(len(rest), len(buf))
            buf[:take] += rest[:take]
            rest = rest[take:]


def vinyl_noise(n: int, rng, amp: float = 0.006) -> np.ndarray:
    """黑胶底噪 + 偶尔的噼啪声。"""
    x = rng.normal(0, 1, n) * amp
    x = np.convolve(x, [0.02, 1, 0.02], mode="same")
    cracks = rng.random(n) < 0.000045
    x[cracks] += rng.normal(0, 0.09, cracks.sum())
    return x


def reverb(x: np.ndarray, rng, wet: float = 0.18, decay: float = 1.1) -> np.ndarray:
    """指数衰减噪声当脉冲响应，用 FFT 做卷积（直接卷积在两百万样本上太慢）。"""
    n_ir = int(1.4 * SR)
    t = np.arange(n_ir) / SR
    ir = rng.normal(0, 1, n_ir) * np.exp(-t / decay)
    ir[0] = 0
    ir = ir / (np.abs(ir).sum() ** 0.5)

    n_fft = 1
    need = len(x) + len(ir) - 1
    while n_fft < need:
        n_fft <<= 1
    wet_sig = np.fft.irfft(np.fft.rfft(x, n_fft) * np.fft.rfft(ir, n_fft), n_fft)[: len(x)]
    return x * (1 - wet) + wet_sig * wet * 0.9


def build(progression, bpm: float, bars_per_chord: int, loops: int,
          melody_scale, rng, bright: float = 1.0) -> np.ndarray:
    spb = 60.0 / bpm                      # 每拍秒数
    bar = spb * 4                          # 每小节秒数
    chord_len = bar * bars_per_chord
    total = chord_len * len(progression) * loops
    n = int(total * SR)
    buf = np.zeros(n)

    # ---- 和弦 + 贝斯 ----
    step = 0
    for loop in range(loops):
        for chord in progression:
            t0 = step * chord_len
            # 电钢和弦：轻微琶音，别砸在一起
            for k, name in enumerate(chord["notes"]):
                start = t0 + k * 0.035 + rng.normal(0, 0.006)
                amp = 0.155 * (1.0 - 0.12 * k) * (0.92 + rng.random() * 0.16)
                add_wrap(buf, rhodes(hz(name, chord["oct"]), chord_len * 0.98, amp),
                         int(start * SR))
            # 持续垫音，把和弦缝住
            add_wrap(buf, pad([hz(n_, chord["oct"] + 1) for n_ in chord["notes"]],
                              chord_len * 1.02, 0.05), int(t0 * SR))
            # 贝斯：第 1 拍和第 3 拍
            for beat in (0, 2):
                start = t0 + beat * spb
                add_wrap(buf, bass(hz(chord["bass"], chord["bass_oct"]), spb * 1.7, 0.2),
                         int(start * SR))
            step += 1

    # ---- 鼓 ----
    n_bars = int(total / bar)
    for b in range(n_bars):
        t_bar = b * bar
        # 底鼓 1、3 拍；偶尔加一个切分
        for beat in (0, 2):
            add_wrap(buf, kick(0.36 + rng.random() * 0.06), int((t_bar + beat * spb) * SR))
        if rng.random() < 0.30:
            add_wrap(buf, kick(0.20), int((t_bar + 3.5 * spb) * SR))
        # 2.5 拍补一个轻底鼓，节奏更连贯
        if rng.random() < 0.25:
            add_wrap(buf, kick(0.16), int((t_bar + 2.5 * spb) * SR))
        # 军鼓 2、4 拍
        for beat in (1, 3):
            add_wrap(buf, snare(0.105 + rng.random() * 0.03, rng), int((t_bar + beat * spb) * SR))
        # 踩镲 8 分音符，第 4 个稍轻
        for i in range(8):
            amp = 0.038 if i % 2 == 0 else 0.024
            add_wrap(buf, hat(amp * (1 + rng.normal(0, 0.12)), rng),
                     int((t_bar + i * spb * 0.5 + rng.normal(0, 0.004)) * SR))

    # ---- 旋律：稀疏，落在五声音阶上，不会难听 ----
    for b in range(n_bars):
        if rng.random() > 0.55:
            continue
        for _ in range(rng.integers(1, 3)):
            beat = float(rng.choice([0, 1.5, 2, 2.5, 3]))
            name, octv = melody_scale[int(rng.integers(0, len(melody_scale)))]
            dur = float(rng.choice([0.9, 1.4, 2.2]))
            add_wrap(buf, rhodes(hz(name, octv), dur, 0.075 * (0.85 + rng.random() * 0.3)),
                     int((b * bar + beat * spb) * SR))

    # ---- 质感 ----
    buf = reverb(buf, rng, wet=0.16)
    buf += vinyl_noise(n, rng)

    # 按 RMS 校准到 -20 dBFS：BGM 要垫在下面，但不能小到听不见
    rms = float(np.sqrt(np.mean(buf ** 2))) or 1.0
    buf = buf / rms * 0.10
    peak = float(np.abs(buf).max())
    if peak > 0.85:            # 万一削顶就压回来
        buf = buf / peak * 0.85
    # 首尾各 8ms 淡入淡出（环绕混音已经处理了主体，这个只是防爆音）
    fade = int(0.008 * SR)
    buf[:fade] *= np.linspace(0, 1, fade)
    buf[-fade:] *= np.linspace(1, 0, fade)
    return buf


def save(buf: np.ndarray, path: Path, cutoff: float = 3800) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    wav = path.with_suffix(".wav")
    data = np.clip(buf, -1, 1)
    pcm = (data * 32767).astype("<i2")
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm.tobytes())

    try:
        af = f"lowpass=f={int(cutoff)},acompressor=threshold=0.3:ratio=1.6:attack=12:release=220"
        r = subprocess.run(
            ["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-af", af,
             "-b:a", "96k", "-ac", "1", str(path)],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0:
            print("  ffmpeg 转换失败，保留 wav：", r.stderr.decode("utf-8", "ignore")[:200])
            return
        wav.unlink(missing_ok=True)
    except Exception as exc:  # noqa: BLE001
        print("  没有 ffmpeg，保留 wav：", exc)
        return
    print(f"  {path.name}  {path.stat().st_size // 1024} KB  {len(buf)/SR:.1f}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "app" / "stage" / "static" / "bgm"))
    args = ap.parse_args()
    out = Path(args.out)

    print("生成 calm（写代码，70 BPM）…")
    calm = build(
        progression=[
            {"notes": ["A", "C", "E", "G"], "oct": 3, "bass": "A", "bass_oct": 1},
            {"notes": ["D", "F", "A", "C"], "oct": 3, "bass": "D", "bass_oct": 1},
            {"notes": ["G", "B", "D", "F"], "oct": 3, "bass": "G", "bass_oct": 1},
            {"notes": ["C", "E", "G", "B"], "oct": 3, "bass": "C", "bass_oct": 1},
        ],
        bpm=70, bars_per_chord=2, loops=2,
        melody_scale=[("A", 4), ("C", 4), ("D", 4), ("E", 4), ("G", 4), ("A", 5)],
        rng=np.random.default_rng(20261123), bright=1.0,
    )
    save(calm, out / "calm.mp3", cutoff=3800)

    print("生成 playful（摸鱼，85 BPM）…")
    playful = build(
        progression=[
            {"notes": ["C", "E", "G", "B"], "oct": 3, "bass": "C", "bass_oct": 1},
            {"notes": ["A", "C", "E", "G"], "oct": 3, "bass": "A", "bass_oct": 1},
            {"notes": ["F", "A", "C", "E"], "oct": 3, "bass": "F", "bass_oct": 1},
            {"notes": ["G", "B", "D", "F"], "oct": 3, "bass": "G", "bass_oct": 1},
        ],
        bpm=85, bars_per_chord=2, loops=2,
        melody_scale=[("C", 4), ("D", 4), ("E", 4), ("G", 4), ("A", 4), ("C", 5)],
        rng=np.random.default_rng(771), bright=1.12,
    )
    save(playful, out / "playful.mp3", cutoff=4300)

    print(f"\n完成：{out}")


if __name__ == "__main__":
    main()
