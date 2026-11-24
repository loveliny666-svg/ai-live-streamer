"""Windows 窗口操作（纯 ctypes，不依赖 pywin32）。

用途：给 OCR 弹幕源和「直播伴侣」自动开播提供截图 / 找窗口 / 点击能力。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import logging
import time
from dataclasses import dataclass
from typing import Iterable

from PIL import Image

log = logging.getLogger("win32")

user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

PW_RENDERFULLCONTENT = 0x00000002
SW_RESTORE = 9
SW_MINIMIZE = 6
VK_MENU = 0x12

_DEFAULT_KEYWORDS = ("直播伴侣", "抖音直播伴侣", "DouyinLiveAssistant")


def enable_dpi_awareness() -> None:
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # PER_MONITOR_AWARE
    except Exception:  # noqa: BLE001
        try:
            user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


@dataclass
class WinInfo:
    hwnd: int
    title: str
    cls: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom
    visible: bool

    @property
    def width(self) -> int:
        return self.rect[2] - self.rect[0]

    @property
    def height(self) -> int:
        return self.rect[3] - self.rect[1]


def list_windows(min_size: int = 200) -> list[WinInfo]:
    out: list[WinInfo] = []
    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        try:
            visible = bool(user32.IsWindowVisible(hwnd))
            n = user32.GetWindowTextLengthW(hwnd)
            title = ""
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value
            cls = ""
            cbuf = ctypes.create_unicode_buffer(256)
            user32.GetClassNameW(hwnd, cbuf, 256)
            cls = cbuf.value
            r = wt.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(r))
            w, h = r.right - r.left, r.bottom - r.top
            if w >= min_size and h >= min_size:
                out.append(WinInfo(hwnd, title, cls, (r.left, r.top, r.right, r.bottom), visible))
        except Exception:  # noqa: BLE001
            pass
        return True

    user32.EnumWindows(EnumProc(cb), 0)
    return out


def find_window(keywords: Iterable[str] = _DEFAULT_KEYWORDS, min_size: int = 300) -> WinInfo | None:
    kws = [k for k in keywords if k]
    wins = [w for w in list_windows(min_size) if w.visible]
    # 先精确匹配标题
    for w in wins:
        if any(k in w.title for k in kws):
            return w
    return None


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wt.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    return (pt.x, pt.y, pt.x + r.right, pt.y + r.bottom)


def is_minimized(hwnd: int) -> bool:
    return bool(user32.IsIconic(hwnd))


def is_foreground(hwnd: int) -> bool:
    return user32.GetForegroundWindow() == hwnd


def focus(hwnd: int) -> bool:
    """把窗口拉到前台（绕过 SetForegroundWindow 的限制）。

    实测最关键的一步是「先模拟按一下 Alt」。Windows 只允许「用户刚操作过」的
    进程抢前台，不按这一下 SetForegroundWindow 会静默失败——调用返回成功，
    GetForegroundWindow 却还是旧窗口，接着的点击就全部落在别人身上。
    直播伴侣切到副屏之后踩的就是这个坑：一直点「开始直播」，一次也没点中。
    """
    try:
        if is_minimized(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
            time.sleep(0.35)
        fg = user32.GetForegroundWindow()
        if fg == hwnd:
            return True
        VK_MENU = 0x12
        KEYEVENTF_KEYUP = 0x0002
        user32.keybd_event(VK_MENU, 0, 0, 0)
        user32.keybd_event(VK_MENU, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.06)
        cur_tid = ctypes.windll.kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != cur_tid:
            attached = bool(user32.AttachThreadInput(fg_tid, cur_tid, True))
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        if attached:
            user32.AttachThreadInput(fg_tid, cur_tid, False)
        time.sleep(0.25)
        if user32.GetForegroundWindow() == hwnd:
            return True
        # 还不行就压一次 topmost 再放掉，比反复 SetForegroundWindow 有效
        HWND_TOPMOST = -1
        HWND_NOTOPMOST = -2
        SWP_NOMOVE = 0x0002
        SWP_NOSIZE = 0x0001
        SWP_SHOWWINDOW = 0x0040
        user32.SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        time.sleep(0.2)
        user32.SetWindowPos(hwnd, HWND_NOTOPMOST, 0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_SHOWWINDOW)
        time.sleep(0.2)
        return user32.GetForegroundWindow() == hwnd
    except Exception as exc:  # noqa: BLE001
        log.debug("focus 失败：%s", exc)
        return False


def _grab_printwindow(hwnd: int, width: int, height: int) -> Image.Image | None:
    hwnd_dc = user32.GetWindowDC(hwnd)
    if not hwnd_dc:
        return None
    mfc_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, width, height)
    old = gdi32.SelectObject(mfc_dc, bmp)
    try:
        ok = user32.PrintWindow(hwnd, mfc_dc, PW_RENDERFULLCONTENT)
        if not ok:
            user32.PrintWindow(hwnd, mfc_dc, 0)

        class BMIH(ctypes.Structure):
            _fields_ = [
                ("biSize", wt.DWORD),
                ("biWidth", wt.LONG),
                ("biHeight", wt.LONG),
                ("biPlanes", wt.WORD),
                ("biBitCount", wt.WORD),
                ("biCompression", wt.DWORD),
                ("biSizeImage", wt.DWORD),
                ("biXPelsPerMeter", wt.LONG),
                ("biYPelsPerMeter", wt.LONG),
                ("biClrUsed", wt.DWORD),
                ("biClrImportant", wt.DWORD),
            ]

        bi = BMIH()
        bi.biSize = ctypes.sizeof(BMIH)
        bi.biWidth = width
        bi.biHeight = -height
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(width * height * 4)
        got = gdi32.GetDIBits(mfc_dc, bmp, 0, height, buf, ctypes.byref(bi), 0)
        if not got:
            return None
        img = Image.frombuffer("RGBA", (width, height), buf, "raw", "BGRA", 0, 1)
        return img.convert("RGB")
    finally:
        gdi32.SelectObject(mfc_dc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mfc_dc)
        user32.ReleaseDC(hwnd, hwnd_dc)


def _grab_screen(rect: tuple[int, int, int, int]) -> Image.Image | None:
    try:
        from PIL import ImageGrab

        img = ImageGrab.grab(bbox=rect, all_screens=True)
        return img.convert("RGB")
    except Exception as exc:  # noqa: BLE001
        log.debug("屏幕截图失败：%s", exc)
        return None


def _looks_blank(img: Image.Image) -> bool:
    """PrintWindow 有时拿到全黑图。"""
    try:
        small = img.resize((32, 32))
        ex = small.convert("L").getextrema()
        return ex[0] == ex[1] and ex[0] < 12
    except Exception:  # noqa: BLE001
        return False


SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79


def on_screen(rect: tuple[int, int, int, int]) -> bool:
    """这块矩形是不是完整落在（可能是多屏的）桌面范围内。"""
    try:
        vx = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        vy = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        vw = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        vh = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
    except Exception:  # noqa: BLE001
        return False
    x1, y1, x2, y2 = rect
    return x1 >= vx and y1 >= vy and x2 <= vx + vw and y2 <= vy + vh


def grab_region(
    hwnd: int,
    region: tuple[int, int, int, int],
    window_rect: tuple[int, int, int, int] | None = None,
    covered: bool | None = None,
) -> tuple[Image.Image | None, tuple[int, int, int, int]]:
    """只取窗口内的一块区域（窗口坐标）。

    窗口在桌面上露着的时候，直接用屏幕 BitBlt —— 几毫秒就能拿到，
    不用让 Chromium 把整个 1600x900 的界面重绘一遍（那要几百毫秒）。
    窗口在屏幕外或被挡住时退回 PrintWindow 全窗截图再裁剪。
    """
    if window_rect is None:
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return None, (0, 0, 0, 0)
        window_rect = (r.left, r.top, r.right, r.bottom)

    x1, y1, x2, y2 = region
    screen_box = (
        window_rect[0] + x1,
        window_rect[1] + y1,
        window_rect[0] + x2,
        window_rect[1] + y2,
    )
    if on_screen(screen_box):
        if covered is None:
            covered = is_covered(hwnd)
        if not covered:
            img = _grab_screen(screen_box)
            if img is not None and not _looks_blank(img):
                return img, window_rect

    w, h = window_rect[2] - window_rect[0], window_rect[3] - window_rect[1]
    if w <= 0 or h <= 0:
        return None, window_rect
    img = _grab_printwindow(hwnd, w, h)
    if img is None:
        return None, window_rect
    return img.crop(
        (
            max(0, x1),
            max(0, y1),
            min(img.width, x2),
            min(img.height, y2),
        )
    ), window_rect


def capture_window(hwnd: int, client_only: bool = False, bg: bool = True) -> tuple[Image.Image | None, tuple[int, int, int, int]]:
    """返回 (图, 该图在屏幕上的位置)。bg=True 表示窗口被遮挡也能截。"""
    if client_only:
        rect = client_rect(hwnd)
    else:
        r = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        rect = (r.left, r.top, r.right, r.bottom)
    w, h = rect[2] - rect[0], rect[3] - rect[1]
    if w <= 0 or h <= 0:
        return None, rect

    if bg:
        img = _grab_printwindow(hwnd, w, h)
        if img is not None and not _looks_blank(img):
            return img, rect
    # 退化为屏幕截图（需要窗口在最前）
    img = _grab_screen(rect)
    return img, rect


def click(x: int, y: int, double: bool = False) -> None:
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)
    user32.mouse_event(0x0002, 0, 0, 0, 0)  # LEFT DOWN
    time.sleep(0.04)
    user32.mouse_event(0x0004, 0, 0, 0, 0)  # LEFT UP
    if double:
        time.sleep(0.06)
        user32.mouse_event(0x0002, 0, 0, 0, 0)
        time.sleep(0.04)
        user32.mouse_event(0x0004, 0, 0, 0, 0)


def place_client_area(hwnd: int, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
    """把窗口摆到「客户区正好等于 (x,y,w,h)」。

    Chrome 的 app 窗口自带一条自绘标题栏，没法用 Win32 样式去掉。
    但可以量出这圈装饰的厚度，把窗口整体往上推同样的距离 ——
    标题栏就跑到屏幕外面去了，屏幕上看到的、以及窗口捕获里露出的都只有画面。
    返回实际窗口矩形。
    """
    r = wt.RECT()
    cr = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (0, 0, 0, 0)
    if not user32.GetClientRect(hwnd, ctypes.byref(cr)):
        return (0, 0, 0, 0)

    win_w, win_h = r.right - r.left, r.bottom - r.top
    cli_w, cli_h = cr.right - cr.left, cr.bottom - cr.top
    deco_w = max(0, win_w - cli_w)
    deco_h = max(0, win_h - cli_h)

    # 客户区左上角相对窗口左上角的偏移（比猜标题栏高度可靠）
    pt = wt.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(pt))
    off_x = pt.x - r.left
    off_y = pt.y - r.top

    new_x = int(x) - off_x
    # 窗口顶边不能推到屏幕外（系统会 clamp，反而把画面挤下去）
    new_y = max(int(y) - off_y, 0)
    user32.SetWindowPos(
        hwnd, 0, new_x, new_y, int(w) + deco_w, int(h) + deco_h,
        0x0004 | 0x0040,     # SWP_NOZORDER | SWP_SHOWWINDOW
    )
    time.sleep(0.25)
    r2 = wt.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r2))
    log.info(
        "窗口摆位：客户区 %dx%d @(%d,%d)，窗口 %dx%d @(%d,%d)，装饰 %dx%d",
        cli_w, cli_h, x, y,
        r2.right - r2.left, r2.bottom - r2.top, r2.left, r2.top, deco_w, deco_h,
    )
    return (r2.left, r2.top, r2.right - r2.left, r2.bottom - r2.top)


def is_covered(hwnd: int) -> bool:
    """窗口是否被别的窗口整个盖住。

    PrintWindow 对被遮挡的窗口并不可靠（实测会把覆盖在上面的窗口内容画进来），
    所以截图前先判断，被盖住就别截 —— 否则 OCR 读到的是别人的画面。
    """
    try:
        r = wt.RECT()
        if not user32.GetWindowRect(hwnd, ctypes.byref(r)):
            return True
        if r.right <= r.left or r.bottom <= r.top:
            return True
        w, h = r.right - r.left, r.bottom - r.top
        pts = [
            ((r.left + r.right) // 2, (r.top + r.bottom) // 2),
            (r.left + max(8, w // 8), r.top + max(8, h // 8)),
            (r.right - max(8, w // 8), r.bottom - max(8, h // 8)),
            (r.right - max(8, w // 8), r.top + max(8, h // 8)),
            (r.left + max(8, w // 8), r.bottom - max(8, h // 8)),
        ]
        GA_ROOT = 2
        for cx, cy in pts:
            pt = wt.POINT(int(cx), int(cy))
            top = user32.WindowFromPoint(pt)
            if not top:
                continue
            root = user32.GetAncestor(top, GA_ROOT) or top
            if root == hwnd or top == hwnd:
                return False  # 还有一块露在外面
            # 同一个窗口可能有两个句柄（外层壳 + 实际窗口），位置/尺寸几乎一致
            tr = wt.RECT()
            if user32.GetWindowRect(root, ctypes.byref(tr)):
                if (abs(tr.left - r.left) <= 8 and abs(tr.top - r.top) <= 8
                        and abs((tr.right - tr.left) - w) <= 14
                        and abs((tr.bottom - tr.top) - h) <= 14):
                    return False
        return True
    except Exception as exc:  # noqa: BLE001
        log.debug("遮挡检测失败：%s", exc)
        return False


def make_borderless(hwnd: int, width: int, height: int, x: int = 0, y: int = 0) -> bool:
    """去掉标题栏和边框，并把窗口钉到指定尺寸 —— 窗口捕获时不会带进标题栏。

    注意：只清 WS_CAPTION / WS_THICKFRAME 是没用的。Chrome 的 app 窗口会自己
    再画一条标题栏（左侧图标 + 标题 + 右侧三个按钮），那条是画在客户区里的，
    清样式根本碰不到它。所以这里直接把窗口样式整体换成 WS_POPUP ——
    没有非客户区，Chrome 也就没地方放它自绘的标题栏了。
    """
    GWL_STYLE = -16
    WS_POPUP = 0x80000000
    WS_VISIBLE = 0x10000000
    WS_CLIPSIBLINGS = 0x04000000
    WS_CLIPCHILDREN = 0x02000000
    SWP_FRAMECHANGED = 0x0020
    SWP_NOZORDER = 0x0004
    SWP_SHOWWINDOW = 0x0040
    try:
        # 0x80000000 超出 c_int 范围，必须先声明成无符号，否则 ctypes 直接抛
        user32.SetWindowLongW.argtypes = [wt.HWND, ctypes.c_int, ctypes.c_uint]
        user32.SetWindowLongW.restype = ctypes.c_int
        if is_minimized(hwnd):
            user32.ShowWindow(hwnd, SW_RESTORE)
        new_style = WS_POPUP | WS_VISIBLE | WS_CLIPSIBLINGS | WS_CLIPCHILDREN
        user32.SetWindowLongW(hwnd, GWL_STYLE, new_style)
        user32.SetWindowPos(
            hwnd, 0, int(x), int(y), int(width), int(height),
            SWP_FRAMECHANGED | SWP_NOZORDER | SWP_SHOWWINDOW,
        )
        time.sleep(0.35)
        r = wt.RECT()
        cr = wt.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(r))
        user32.GetClientRect(hwnd, ctypes.byref(cr))
        win_w, win_h = r.right - r.left, r.bottom - r.top
        cli_w, cli_h = cr.right - cr.left, cr.bottom - cr.top
        if (win_w, win_h) != (cli_w, cli_h):
            # 还有非客户区装饰，交给调用方退回「把标题栏推出屏幕」的老办法
            log.warning("去边框后仍有装饰：窗口 %dx%d / 客户区 %dx%d", win_w, win_h, cli_w, cli_h)
            return False
        log.info("窗口已无边框化：%dx%d @(%d,%d)", win_w, win_h, r.left, r.top)
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("去边框失败：%s", exc)
        return False


def move_away() -> None:
    """把鼠标挪到角落，避免挡住画面或被误点。"""
    try:
        user32.SetCursorPos(5, 1075)
    except Exception:  # noqa: BLE001
        pass


def window_pid(hwnd: int) -> int:
    pid = wt.DWORD(0)
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)
