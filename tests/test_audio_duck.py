"""系统音频闪避测试：压低 / 恢复 / 打断 / 降级，全部用假会话，不碰真实音量。

用法：python tests/test_audio_duck.py
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.audio.ducker import AudioDucker
from app.config import Config

PASS, FAIL = [], []


def check(name: str, cond: bool, extra: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ✓ " if cond else "  ✗ ") + name + (f"   {extra}" if extra else ""), flush=True)


# ---------------- 假的 Windows 音频会话 ----------------
class FakeVol:
    def __init__(self, value: float = 1.0):
        self.value = float(value)

    def GetMasterVolume(self):
        return self.value

    def GetMute(self):
        return 0

    def SetMasterVolume(self, value, _ctx):
        self.value = float(value)


class FakeProc:
    def __init__(self, name: str, pid: int):
        self._name = name
        self.pid = pid

    def name(self):
        return self._name


class FakeSession:
    def __init__(self, name: str, pid: int, value: float = 1.0):
        self.Process = FakeProc(name, pid)
        self.SimpleAudioVolume = FakeVol(value)
        self.InstanceIdentifier = f"fake-session-{name}-{pid}"


def make_ducker(sessions, cfg=None) -> AudioDucker:
    cfg = cfg or Config(ROOT / "config.yaml")
    d = AudioDucker(cfg)
    # 加速测试：去掉渐变等待，缩短兜底时长
    d.fade_out = 0.0
    d.fade_in = 0.0
    d.hold = 0.05
    d.default_hold = 0.3
    d.max_duck = 5.0
    def collect():
        # 和真实 _collect 一样：只返回命中的目标会话，并每次重读当前音量
        out = []
        for s in sessions:
            if not d._is_target(s.Process.name()):
                continue
            vol = s.SimpleAudioVolume
            key = f"{s.Process.name().lower()}|{s.Process.pid}|{s.InstanceIdentifier}"
            out.append((key, s.Process.name(), vol, vol.GetMasterVolume()))
        return out

    d._collect = collect
    return d


async def settle(d: AudioDucker, sec: float = 1.0) -> None:
    await asyncio.sleep(sec)


async def main() -> None:
    print("\n[1] 目标进程匹配（别把主播人声一起压了）")
    d0 = make_ducker([])
    check("Apple Music 出声进程命中", d0._is_target("AMPLibraryAgent.exe"))
    check("Apple Music 主界面命中", d0._is_target("AppleMusic.exe"))
    check("Chrome 舞台人声排除", not d0._is_target("chrome.exe"))
    check("直播伴侣采集排除", not d0._is_target("MediaSDK_Server.exe"))
    check("未知进程不压", not d0._is_target("notepad.exe"))

    print("\n[2] 一句话：1.0 → 0.18 → 1.0")
    music = FakeSession("AMPLibraryAgent.exe", 111, 1.0)
    human = FakeSession("chrome.exe", 222, 1.0)
    d = make_ducker([music, human])
    await d.begin(1.0)
    await asyncio.sleep(0.15)
    check("音乐被压到 18%", abs(music.SimpleAudioVolume.value - 0.18) < 0.02,
          f"{music.SimpleAudioVolume.value:.3f}")
    check("Chrome 人声没动", abs(human.SimpleAudioVolume.value - 1.0) < 0.001)
    check("状态显示压低中", d.snapshot()["ducking"] is True)
    await d.end()
    await settle(d)
    check("说完淡回原音量", abs(music.SimpleAudioVolume.value - 1.0) < 0.02,
          f"{music.SimpleAudioVolume.value:.3f}")
    check("没有残留待恢复会话", d.snapshot()["pending_restore"] == 0)

    print("\n[3] 淡出没结束就播完（渐变中途打断）")
    m = FakeSession("AMPLibraryAgent.exe", 333, 1.0)
    d = make_ducker([m])
    d.fade_out = 0.4  # 故意让淡出慢一点
    await d.begin(2.0)
    await asyncio.sleep(0.06)
    await d.end()
    await settle(d, 1.4)
    check("打断后仍能恢复", abs(m.SimpleAudioVolume.value - 1.0) < 0.02,
          f"{m.SimpleAudioVolume.value:.3f}")

    print("\n[4] 恢复过程中又来一句（连续说话）")
    m = FakeSession("AMPLibraryAgent.exe", 444, 1.0)
    d = make_ducker([m])
    d.fade_in = 0.4
    await d.begin(0.5)
    await asyncio.sleep(0.12)
    await d.end()
    await asyncio.sleep(0.15)          # 正在淡回
    await d.begin(1.0)
    await asyncio.sleep(0.15)
    check("第二句又压下去", abs(m.SimpleAudioVolume.value - 0.18) < 0.03,
          f"{m.SimpleAudioVolume.value:.3f}")
    await d.end()
    await settle(d, 1.4)
    check("最终回到 1.0", abs(m.SimpleAudioVolume.value - 1.0) < 0.02,
          f"{m.SimpleAudioVolume.value:.3f}")

    print("\n[5] 用户在我们压低期间自己调了音量：不覆盖")
    m = FakeSession("AMPLibraryAgent.exe", 555, 1.0)
    d = make_ducker([m])
    await d.begin(1.0)
    await asyncio.sleep(0.12)
    m.SimpleAudioVolume.value = 0.55   # 手滑去 Apple Music 里拖了一下
    await d.end()
    await settle(d)
    check("尊重用户设置，仍是 0.55", abs(m.SimpleAudioVolume.value - 0.55) < 0.02,
          f"{m.SimpleAudioVolume.value:.3f}")
    check("记录已清干净", d.snapshot()["pending_restore"] == 0)

    print("\n[6] 强制收播恢复（不管中途状态）")
    m = FakeSession("AMPLibraryAgent.exe", 666, 1.0)
    d = make_ducker([m])
    await d.begin(3.0)
    await asyncio.sleep(0.12)
    await d.restore_now()
    check("立刻还回 1.0", abs(m.SimpleAudioVolume.value - 1.0) < 0.02,
          f"{m.SimpleAudioVolume.value:.3f}")
    check("不再处于压低状态", d.snapshot()["ducking"] is False)

    print("\n[7] 依赖缺失 / COM 异常：降级停用，不抛异常、不影响主链路")
    d = make_ducker([])
    def boom():
        raise ImportError("pycaw not installed")
    d._collect = boom
    try:
        await d.begin(1.0)
        await asyncio.sleep(0.2)
        check("主链路没有抛异常", True)
        check("自动停用", d.available is False)
        check("错误有记录", bool(d.snapshot()["last_error"]))
    except Exception as exc:  # noqa: BLE001
        check("主链路没有抛异常", False, str(exc))

    print("\n[8] 配置开关与热重载")
    cfg = Config(ROOT / "config.yaml")
    cfg.set("audio.duck.enabled", False)
    m = FakeSession("AMPLibraryAgent.exe", 777, 1.0)
    d = make_ducker([m], cfg)
    await d.begin(1.0)
    await asyncio.sleep(0.15)
    check("关闭后不压音乐", abs(m.SimpleAudioVolume.value - 1.0) < 0.001)
    cfg.set("audio.duck.enabled", True)
    cfg.set("audio.duck.level", 0.5)
    d.reload()
    d.fade_out = 0.0   # reload 会读回配置里的渐变时长，测试里重新加速
    d.fade_in = 0.0
    d.hold = 0.05
    await d.begin(1.0)
    await asyncio.sleep(0.15)
    check("热重载后按新 level 压到 0.5", abs(m.SimpleAudioVolume.value - 0.5) < 0.02,
          f"{m.SimpleAudioVolume.value:.3f}")
    await d.restore_now()

    print(f"\n结果：{len(PASS)} 通过 / {len(FAIL)} 失败")
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
