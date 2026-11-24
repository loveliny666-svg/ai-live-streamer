"""探针：用 Windows UI Automation 读「直播伴侣」窗口里的文字（不截图、不做 OCR）。

如果这条路走得通，弹幕就是毫秒级、且带昵称结构，比 OCR 强得多。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import comtypes.client  # noqa: E402
from comtypes.gen import UIAutomationClient as UIA  # noqa: E402

from app.companion import win  # noqa: E402

CTRL = {
    UIA.UIA_TextControlTypeId: "Text",
    UIA.UIA_ButtonControlTypeId: "Button",
    UIA.UIA_ListItemControlTypeId: "ListItem",
    UIA.UIA_ListControlTypeId: "List",
    UIA.UIA_ImageControlTypeId: "Image",
    UIA.UIA_PaneControlTypeId: "Pane",
    UIA.UIA_DocumentControlTypeId: "Document",
    UIA.UIA_GroupControlTypeId: "Group",
    UIA.UIA_CustomControlTypeId: "Custom",
    UIA.UIA_EditControlTypeId: "Edit",
}


def dump(el, automation, depth=0, out=None, limit=500):
    if out is None:
        out = []
    if len(out) >= limit:
        return out
    try:
        name = el.CurrentName or ""
        cid = el.CurrentControlType
        cls = el.CurrentClassName or ""
        aid = el.CurrentAutomationId or ""
        rect = el.CurrentBoundingRectangle
    except Exception:
        return out
    if name.strip() or depth < 4:
        out.append((depth, CTRL.get(cid, str(cid)), cls[:28], aid[:18], name.strip()[:80],
                    (rect.left, rect.top, rect.right, rect.bottom)))
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
        print("没找到直播伴侣窗口")
        return
    print(f"窗口：{info.title!r} hwnd={info.hwnd} {info.width}x{info.height}")

    automation = comtypes.client.CreateObject(
        UIA.CUIAutomation, interface=UIA.IUIAutomation
    )
    t0 = time.time()
    root = automation.ElementFromHandle(info.hwnd)
    print(f"ElementFromHandle {time.time() - t0:.2f}s")

    t0 = time.time()
    nodes = dump(root, automation)
    print(f"遍历 {len(nodes)} 个节点，{time.time() - t0:.2f}s")
    print("-" * 70)
    for depth, ctrl, cls, aid, name, rect in nodes:
        if not name and ctrl not in ("Document", "Pane"):
            continue
        print(f"{'  ' * min(depth, 12)}{ctrl:<9} {cls:<26} {aid:<16} {name}")

    t0 = time.time()
    nodes2 = dump(root, automation)
    print(f"\n第二次遍历 {time.time() - t0:.2f}s，{len(nodes2)} 节点")


if __name__ == "__main__":
    main()
