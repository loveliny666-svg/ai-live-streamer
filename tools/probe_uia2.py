"""探针：尝试激活直播伴侣（Chromium/CEF）的无障碍树，再用 UIA 直接读文字。

Chromium 默认关闭 accessibility，收到 WM_GETOBJECT(OBJID_CLIENT) 后会开启。
开启后用 UIA 遍历应该能看到 DOM 里的文本节点。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import comtypes.client  # noqa: E402
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

from app.companion import win  # noqa: E402

user32 = ctypes.WinDLL("user32", use_last_error=True)
oleacc = ctypes.WinDLL("oleacc", use_last_error=True)

WM_GETOBJECT = 0x003D
OBJID_CLIENT = -4

EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)


def children(hwnd):
    out = []

    def cb(h, _):
        cls = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(h, cls, 256)
        out.append((h, cls.value))
        return True

    user32.EnumChildWindows(hwnd, EnumProc(cb), 0)
    return out


def dump(el, automation, depth=0, out=None, limit=600):
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    try:
        name = el.CurrentName or ""
        cid = el.CurrentControlType
        cls = el.CurrentClassName or ""
        rect = el.CurrentBoundingRectangle
    except Exception:
        return out
    if name.strip():
        out.append((depth, cid, cls[:24], name.strip()[:90], (rect.left, rect.top)))
    walker = automation.RawViewWalker
    try:
        child = walker.GetFirstChildElement(el)
    except Exception:
        child = None
    while child:
        dump(child, automation, depth + 1, out, limit)
        if len(out) >= limit:
            break
        try:
            child = walker.GetNextSiblingElement(child)
        except Exception:
            break
    return out


def main() -> None:
    win.enable_dpi_awareness()
    info = win.find_window(["直播伴侣"], 300)
    if not info:
        print("没找到窗口")
        return
    kids = children(info.hwnd)
    print("子窗口：")
    for h, cls in kids[:12]:
        print(f"  {h} {cls}")

    automation = comtypes.client.CreateObject(UIA.CUIAutomation, interface=UIA.IUIAutomation)

    for h, cls in kids:
        if "RenderWidget" not in cls and "Chrome" not in cls:
            continue
        ptr = ctypes.c_void_p()
        hr = oleacc.AccessibleObjectFromWindow(
            wt.HWND(h), wt.DWORD(OBJID_CLIENT), ctypes.byref(
                comtypes.GUID("{618736E0-3C3D-11CF-810C-00AA00389B71}")
            ), ctypes.byref(ptr)
        )
        print(f"AccessibleObjectFromWindow({cls}) hr={hr:#x} ptr={bool(ptr.value)}")
        if ptr.value:
            try:
                comtypes.cast(ptr, ctypes.POINTER(comtypes.IUnknown)).Release()
            except Exception:
                pass

    time.sleep(2.0)
    root = automation.ElementFromHandle(info.hwnd)
    t0 = time.time()
    nodes = dump(root, automation)
    print(f"\nUIA 节点 {len(nodes)} 个，用时 {time.time() - t0:.2f}s")
    for depth, cid, cls, name, pos in nodes:
        print(f"{'  ' * min(depth, 10)}[{cid}] {cls:<22} {name}")


if __name__ == "__main__":
    main()
