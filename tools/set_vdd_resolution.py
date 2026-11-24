"""把虚拟显示器（Virtual Display Driver）的分辨率改成 1920x1080。

为什么不用驱动自带的 vdd_settings.xml：那个文件要放在 C:\\VirtualDisplayDriver\\，
写 C 盘根目录需要管理员。改 Windows 自己的显示设置更省事，而且系统会记住。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)

ENUM_CURRENT_SETTINGS = -1
CDS_UPDATEREGISTRY = 0x00000001
CDS_TEST = 0x00000002
DISP_CHANGE_SUCCESSFUL = 0
DM_PELSWIDTH = 0x00080000
DM_PELSHEIGHT = 0x00100000


class DISPLAY_DEVICE(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("DeviceName", wintypes.WCHAR * 32),
        ("DeviceString", wintypes.WCHAR * 128),
        ("StateFlags", wintypes.DWORD),
        ("DeviceID", wintypes.WCHAR * 128),
        ("DeviceKey", wintypes.WCHAR * 128),
    ]


class DEVMODE(ctypes.Structure):
    _fields_ = [
        ("dmDeviceName", wintypes.WCHAR * 32),
        ("dmSpecVersion", wintypes.WORD),
        ("dmDriverVersion", wintypes.WORD),
        ("dmSize", wintypes.WORD),
        ("dmDriverExtra", wintypes.WORD),
        ("dmFields", wintypes.DWORD),
        ("dmPositionX", wintypes.LONG),
        ("dmPositionY", wintypes.LONG),
        ("dmDisplayOrientation", wintypes.DWORD),
        ("dmDisplayFixedOutput", wintypes.DWORD),
        ("dmColor", ctypes.c_short),
        ("dmDuplex", ctypes.c_short),
        ("dmYResolution", ctypes.c_short),
        ("dmTTOption", ctypes.c_short),
        ("dmCollate", ctypes.c_short),
        ("dmFormName", wintypes.WCHAR * 32),
        ("dmLogPixels", wintypes.WORD),
        ("dmBitsPerPel", wintypes.DWORD),
        ("dmPelsWidth", wintypes.DWORD),
        ("dmPelsHeight", wintypes.DWORD),
        ("dmDisplayFlags", wintypes.DWORD),
        ("dmDisplayFrequency", wintypes.DWORD),
        ("dmICMMethod", wintypes.DWORD),
        ("dmICMIntent", wintypes.DWORD),
        ("dmMediaType", wintypes.DWORD),
        ("dmDitherType", wintypes.DWORD),
        ("dmReserved1", wintypes.DWORD),
        ("dmReserved2", wintypes.DWORD),
        ("dmPanningWidth", wintypes.DWORD),
        ("dmPanningHeight", wintypes.DWORD),
    ]


def list_devices() -> list[tuple[str, str, int]]:
    out = []
    i = 0
    while True:
        dd = DISPLAY_DEVICE()
        dd.cb = ctypes.sizeof(DISPLAY_DEVICE)
        if not user32.EnumDisplayDevicesW(None, i, ctypes.byref(dd), 0):
            break
        out.append((dd.DeviceName, dd.DeviceString, dd.StateFlags))
        i += 1
    return out


def current_mode(name: str) -> DEVMODE | None:
    dm = DEVMODE()
    dm.dmSize = ctypes.sizeof(DEVMODE)
    if not user32.EnumDisplaySettingsW(name, ENUM_CURRENT_SETTINGS, ctypes.byref(dm)):
        return None
    return dm


def set_resolution(name: str, w: int, h: int) -> tuple[int, str]:
    dm = current_mode(name)
    if dm is None:
        return -1, "读不到当前模式"
    if dm.dmPelsWidth == w and dm.dmPelsHeight == h:
        return 0, "已经是目标分辨率"
    dm.dmPelsWidth = w
    dm.dmPelsHeight = h
    dm.dmFields = (dm.dmFields | DM_PELSWIDTH | DM_PELSHEIGHT) & 0xFFFFFFFF
    # 先 TEST 一遍，避免把桌面搞乱
    rc = user32.ChangeDisplaySettingsExW(
        name, ctypes.byref(dm), None, CDS_TEST, None
    )
    if rc != DISP_CHANGE_SUCCESSFUL:
        return rc, f"不支持 {w}x{h}（返回 {rc}）"
    rc = user32.ChangeDisplaySettingsExW(
        name, ctypes.byref(dm), None, CDS_UPDATEREGISTRY, None
    )
    return rc, "已应用" if rc == DISP_CHANGE_SUCCESSFUL else f"应用失败（返回 {rc}）"


if __name__ == "__main__":
    print("=== 所有显示设备 ===")
    target = None
    for name, desc, flags in list_devices():
        dm = current_mode(name)
        size = f"{dm.dmPelsWidth}x{dm.dmPelsHeight}" if dm else "?"
        active = "活动" if flags & 0x1 else "未激活"
        print(f"{name:<24} {desc[:44]:<44} {size:<10} {active}")
        if "Virtual" in desc or "MttVDD" in desc:
            target = name

    if not target:
        print("\n没找到虚拟显示器")
        raise SystemExit(1)

    print(f"\n=== 把 {target} 改成 1920x1080 ===")
    rc, msg = set_resolution(target, 1920, 1080)
    print(f"结果: {msg} (rc={rc})")
    dm = current_mode(target)
    if dm:
        print(f"现在的分辨率: {dm.dmPelsWidth}x{dm.dmPelsHeight}")
