"""抖音直播伴侣控制：自动开播 / 收播 / 掉线自愈。

思路：不逆向协议、不装驱动，用「截图 + OCR 找字 + 鼠标点击」这套最通用的方式。
所有动作都留证据（截图存 data/evidence/），出问题能复盘。
"""
from __future__ import annotations

import asyncio
import logging
import subprocess
import time
from pathlib import Path
from typing import Callable

from PIL import Image

from . import win
from .ocr import OCR, Box

log = logging.getLogger("companion")

START_TEXTS = ["开始直播", "开始直播（", "立即开播", "开播"]
STOP_TEXTS = ["结束直播", "关闭直播", "关播"]
# "关播" 是 v13 版直播伴侣上的文案（比"结束直播"短），漏了它会误判成没开播
LIVE_TEXTS = ["结束直播", "直播中", "直播时长", "关播"]
SCENE_TEXTS = ["场景一", "场景二", "场景三"]


class Companion:
    def __init__(self, cfg, stage_hwnd_provider: Callable[[], int | None] | None = None):
        self.cfg = cfg
        self.stage_hwnd = stage_hwnd_provider or (lambda: None)
        self.hwnd: int | None = None
        self.is_live = False
        self.last_action = 0.0
        self.last_result: dict = {}
        self.evidence_dir: Path = cfg.resolve("evidence")
        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        self._consecutive_fail = 0
        self._last_start_attempt = 0.0

    # ---------------- 窗口 ----------------
    def find(self) -> win.WinInfo | None:
        kw = ["直播伴侣", "DouyinLiveAssistant", "抖音直播"]
        info = win.find_window(kw)
        if info:
            self.hwnd = info.hwnd
        return info

    def hwnd_or_none(self) -> int | None:
        if self.hwnd and win.user32.IsWindow(self.hwnd):
            return self.hwnd
        info = self.find()
        return info.hwnd if info else None

    @staticmethod
    def is_elevated() -> bool:
        try:
            import ctypes

            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:  # noqa: BLE001
            return False

    def launch(self) -> bool:
        exe = str(self.cfg.get("companion.exe_path", "") or "")
        if not exe:
            # 开源版默认留空：每个人把直播伴侣装在哪不一样，写死一个路径没意义。
            # 留空就当「你自己已经开好了」，只打一条 info，不要刷 ERROR 吓人。
            log.info("没配 companion.exe_path，跳过自动启动（请自行打开直播伴侣）")
            return False
        if not Path(exe).exists():
            log.warning("直播伴侣路径不存在：%s", exe)
            return False
        try:
            subprocess.Popen([exe], close_fds=True)
            log.info("已启动直播伴侣：%s", exe)
            return True
        except Exception as exc:  # noqa: BLE001
            code = getattr(exc, "winerror", None)
            if code == 740:
                # 直播伴侣的清单要求管理员权限：本程序没提权时只能走 UAC
                log.warning("直播伴侣需要管理员权限，尝试提权启动（会弹一次 UAC）")
                try:
                    import ctypes

                    r = ctypes.windll.shell32.ShellExecuteW(
                        None, "runas", exe, None, str(Path(exe).parent), 1
                    )
                    if r > 32:
                        log.info("已通过 UAC 启动直播伴侣")
                        return True
                    log.error("提权被拒绝或被取消（ShellExecute=%s）", r)
                    self.last_result = {
                        "ok": False,
                        "error": "直播伴侣需要管理员权限，提权被拒绝",
                        "hint": "请右键 run.bat → 以管理员身份运行；或先手动打开直播伴侣",
                    }
                except Exception as exc2:  # noqa: BLE001
                    log.error("提权启动失败：%s", exc2)
                return False
            log.error("启动直播伴侣失败：%s", exc)
            return False

    async def ensure_running(self) -> bool:
        if self.hwnd_or_none():
            return True
        if not self.cfg.get("companion.auto_launch", True):
            return False
        self.launch()
        for _ in range(40):
            await asyncio.sleep(1.0)
            if self.hwnd_or_none():
                await asyncio.sleep(2.0)
                return True
        return False

    # ---------------- 截图 / OCR ----------------
    async def grab(self) -> tuple[Image.Image | None, tuple[int, int, int, int], list[Box]]:
        hwnd = self.hwnd_or_none()
        if not hwnd:
            return None, (0, 0, 0, 0), []
        loop = asyncio.get_running_loop()
        img, rect = await loop.run_in_executor(None, lambda: win.capture_window(hwnd, False))
        if img is None:
            return None, rect, []
        boxes = await loop.run_in_executor(None, lambda: OCR.read(img, 0.4))
        return img, rect, boxes

    def save_evidence(self, img: Image.Image | None, tag: str) -> str:
        if img is None:
            return ""
        name = f"{time.strftime('%Y%m%d-%H%M%S')}-{tag}.png"
        p = self.evidence_dir / name
        try:
            img.save(p)
        except Exception:  # noqa: BLE001
            return ""
        return str(p)

    def _zoom_read(self, img: Image.Image, frac: tuple[float, float, float, float],
                   scale: float = 2.0, min_score: float = 0.3) -> list[Box]:
        """把某个区域放大后再 OCR，救小字（场景标签、按钮文字）。坐标会映射回整图。"""
        x1 = int(img.width * frac[0])
        y1 = int(img.height * frac[1])
        x2 = int(img.width * frac[2])
        y2 = int(img.height * frac[3])
        if x2 - x1 < 20 or y2 - y1 < 12:
            return []
        crop = img.crop((x1, y1, x2, y2))
        crop = crop.resize((int(crop.width * scale), int(crop.height * scale)), Image.LANCZOS)
        boxes = OCR.read(crop, min_score)
        for b in boxes:
            b.x1 = b.x1 / scale + x1
            b.x2 = b.x2 / scale + x1
            b.y1 = b.y1 / scale + y1
            b.y2 = b.y2 / scale + y1
        return boxes

    def _find_any(self, boxes: list[Box], texts: list[str], min_score: float) -> tuple[str, Box] | None:
        for t in texts:
            b = OCR.find(boxes, t, min_score)
            if b:
                return t, b
        return None

    async def _click_box(self, rect, box: Box, tag: str) -> None:
        x = int(rect[0] + box.cx)
        y = int(rect[1] + box.cy)
        await self._to_front()
        win.click(x, y)
        log.info("点击「%s」@(%d,%d)", box.text, x, y)
        await asyncio.sleep(1.2)

    async def _to_front(self) -> None:
        """把直播伴侣拉到前台；必要时先把舞台窗口让开。"""
        hwnd = self.hwnd_or_none()
        if hwnd:
            win.focus(hwnd)
            await asyncio.sleep(0.35)

    def _back_to_stage(self) -> None:
        h = self.stage_hwnd()
        if h:
            try:
                win.focus(h)
            except Exception:  # noqa: BLE001
                pass

    # ---------------- 开播 ----------------
    async def start_live(self, force: bool = False, dry_run: bool = False) -> dict:
        min_score = float(self.cfg.get("companion.ocr_min_score", 0.55))
        retries = int(self.cfg.get("companion.click_retries", 3))
        detail = {"action": "start_live", "steps": [], "dry_run": dry_run}

        if not self.cfg.get("companion.enabled", True):
            return {"ok": False, "error": "companion.enabled=false"}

        if not await self.ensure_running():
            detail.update(ok=False, error="直播伴侣没启动")
            self.last_result = detail
            return detail

        # 防止高频重试
        cd = float(self.cfg.get("companion.restart_cooldown_sec", 120))
        if not force and time.time() - self._last_start_attempt < cd:
            detail.update(ok=False, error=f"冷却中（{int(cd)}s）")
            return detail
        self._last_start_attempt = time.time()

        for attempt in range(1, retries + 1):
            img, rect, boxes = await self.grab()
            if img is None:
                detail["steps"].append({"attempt": attempt, "error": "截图失败"})
                await asyncio.sleep(2)
                continue

            # 没有 OCR 就没有坐标，后面所有点击都无从谈起。直接说清楚，别重试三次。
            if not OCR.ok:
                detail.update(
                    ok=False,
                    error=f"OCR 不可用，识别不了「开始直播」按钮（{OCR.err or '未知原因'}）",
                )
                self.last_result = detail
                log.error("开播中止：OCR 不可用")
                return detail

            texts = [b.text for b in boxes]
            # 1) 权限拦截
            perm = self._find_any(boxes, list(self.cfg.get("companion.permission_keywords", [])), 0.4)
            if perm:
                ev = self.save_evidence(img, "no-permission")
                detail.update(
                    ok=False,
                    blocked=True,
                    reason=f"页面出现「{perm[0]}」，账号还没有开播权限，需要先在直播伴侣里申请并通过",
                    evidence=ev,
                )
                self.last_result = detail
                log.error("开播被权限拦住：%s", perm[0])
                return detail

            # 2) 已经在播
            live = self._find_any(boxes, LIVE_TEXTS, min_score)
            if live and live[0] in ("结束直播", "直播中"):
                self.is_live = True
                detail.update(ok=True, already=True, note=f"已经在播（看到「{live[0]}」）")
                self.last_result = detail
                return detail

            # 3) 切场景（可选）
            scene = str(self.cfg.get("companion.scene_name", "") or "")
            if scene and attempt == 1:
                sc = OCR.find(boxes, scene, 0.45)
                if not sc:
                    zb = self._zoom_read(img, (0.0, 0.0, 0.40, 0.28), 2.0, 0.3)
                    sc = OCR.find(zb, scene, 0.4)
                if sc:
                    detail["steps"].append({"click": scene})
                    if not dry_run:
                        await self._click_box(rect, sc, "scene")
                        await asyncio.sleep(0.8)
                        img, rect, boxes = await self.grab()
                        if img is None:
                            continue

            # 4) 点开始直播
            hit = self._find_any(boxes, START_TEXTS, min_score)
            if not hit:
                zb = self._zoom_read(img, (0.5, 0.66, 1.0, 1.0), 2.0, 0.3)
                hit = self._find_any(zb, START_TEXTS, min_score)
            if not hit:
                zb2 = self._zoom_read(img, (0.0, 0.66, 1.0, 1.0), 1.6, 0.3)
                hit = self._find_any(zb2, START_TEXTS, min_score)
            if not hit:
                ev = self.save_evidence(img, f"no-start-button-{attempt}")
                detail["steps"].append(
                    {"attempt": attempt, "error": "没找到「开始直播」按钮", "ocr_sample": texts[:12], "evidence": ev}
                )
                await asyncio.sleep(1.5)
                continue

            label, box = hit
            detail["steps"].append({"click": label, "score": round(box.score, 2)})
            if dry_run:
                detail.update(ok=True, dry_run=True, note=f"（演练）会点击「{label}」")
                self.last_result = detail
                return detail

            self.save_evidence(img, f"before-start-{attempt}")
            await self._click_box(rect, box, "start")
            await asyncio.sleep(2.5)

            # 5) 可能还有二次确认弹窗
            img2, rect2, boxes2 = await self.grab()
            if img2 is not None:
                confirm = self._find_any(boxes2, ["开始直播", "确认开播", "确定"], min_score)
                if confirm and confirm[0] in ("开始直播", "确认开播", "确定"):
                    detail["steps"].append({"click": f"确认:{confirm[0]}"})
                    await self._click_box(rect2, confirm[1], "confirm")
                    await asyncio.sleep(2.5)
                    img2, rect2, boxes2 = await self.grab()

            # 6) 判定是否成功
            if boxes2:
                live2 = self._find_any(boxes2, ["结束直播", "直播中"], 0.5)
                if live2:
                    self.is_live = True
                    ev = self.save_evidence(img2, "live-started")
                    detail.update(ok=True, note=f"已开播（看到「{live2[0]}」）", evidence=ev)
                    self.last_result = detail
                    log.info("自动开播成功")
                    self._back_to_stage()
                    return detail
                # 仍在准备页，重试
                still = self._find_any(boxes2, START_TEXTS, min_score)
                if still:
                    detail["steps"].append({"attempt": attempt, "note": "点了但还在准备页"})
                    await asyncio.sleep(2)
                    continue

        ev = self.save_evidence(img if "img" in dir() else None, "start-failed")
        detail.update(ok=False, error="多次尝试仍未开播", evidence=ev)
        self.last_result = detail
        self._back_to_stage()
        return detail

    # ---------------- 收播 ----------------
    async def stop_live(self, dry_run: bool = False) -> dict:
        min_score = float(self.cfg.get("companion.ocr_min_score", 0.55))
        detail: dict = {"action": "stop_live", "steps": [], "dry_run": dry_run}
        if not self.hwnd_or_none():
            detail.update(ok=False, error="没找到直播伴侣窗口")
            return detail

        for attempt in range(1, 4):
            img, rect, boxes = await self.grab()
            if img is None:
                await asyncio.sleep(1.5)
                continue
            hit = self._find_any(boxes, STOP_TEXTS, min_score)
            if not hit:
                detail.update(ok=False, error="没找到「结束直播」")
                return detail
            detail["steps"].append({"click": hit[0]})
            if dry_run:
                detail.update(ok=True, note="（演练）会点击结束直播")
                return detail
            self.save_evidence(img, f"before-stop-{attempt}")
            await self._click_box(rect, hit[1], "stop")
            await asyncio.sleep(1.5)
            # 二次确认
            img2, rect2, boxes2 = await self.grab()
            if img2 is not None:
                confirm = self._find_any(boxes2, ["结束直播", "确定", "确认"], 0.5)
                if confirm:
                    await self._click_box(rect2, confirm[1], "stop-confirm")
                    await asyncio.sleep(2)
                boxes3 = await self.grab()
                if boxes3[2]:
                    left = self._find_any(boxes3[2], LIVE_TEXTS, 0.5)
                    if not left:
                        self.is_live = False
                        detail.update(ok=True, note="已结束直播")
                        self._back_to_stage()
                        return detail
        self._back_to_stage()
        detail.update(ok=False, error="结束直播未确认")
        return detail

    # ---------------- 巡检 / 自愈 ----------------
    async def health(self) -> dict:
        """不抢焦点，只做后台截图判断。"""
        # 手动开播模式：OCR 装不上（比如 Python 3.13 没 wheel）时，靠人点「开始直播」。
        # 这时候截图 + OCR 全是白费，还会让 watchdog 误判「掉播」反复去点按钮。
        # 直接认「在播」，把开播这件事交给人。
        if self.cfg.get("companion.assume_live", False):
            self.is_live = True
            return {
                "ok": True,
                "is_live": True,
                "assumed": True,
                "sees_live_btn": False,
                "sees_start_btn": False,
                "recover_hint": "",
            }

        img, rect, boxes = await self.grab()
        if img is None:
            return {"ok": False, "error": "截图失败", "is_live": self.is_live}
        # OCR 引擎没加载起来时 boxes 一定是空的。这时候不能据此断言「没在直播」——
        # 否则 watchdog 每两分钟就会去点一次「开始直播」，直播间明明在播也被当成掉了。
        # 返回 ok=False 让调用方跳过判断，比猜一个错答案安全。
        if not OCR.ok:
            return {
                "ok": False,
                "error": f"OCR 不可用，无法判断直播状态（{OCR.err or '未知原因'}）",
                "is_live": self.is_live,
                "unknown": True,
            }
        texts = [b.text for b in boxes]
        # 用常量表，别在这里写死。直播伴侣版本更新后「结束直播」改叫「关播」了，
        # 只认前者的结果是：明明在播，巡检却一直判成没播 → 反复去点「开始直播」
        # → 点不到（当前界面是「关播」）→ 控制台挂一条「多次尝试仍未开播」的报错。
        live = self._find_any(boxes, LIVE_TEXTS, 0.5)
        offline = self._find_any(boxes, START_TEXTS, 0.5)
        # 后台 PrintWindow 截 Electron 窗口时会掉内容（实测同一时刻 31~51 个文字框波动），
        # 所以这里只做 debug，别刷 INFO。判据本身用「看到开始直播才算没播」的保守逻辑：
        # 读不到就当还在播，绝不去乱点按钮。
        log.debug(
            "巡检：%d 个文字框，live=%s offline=%s",
            len(boxes), live[0] if live else None, offline[0] if offline else None,
        )
        recover = self._find_any(boxes, list(self.cfg.get("companion.recover_keywords", [])), 0.5)
        if live:
            self.is_live = True
        elif offline:
            self.is_live = False
        return {
            "ok": True,
            "is_live": self.is_live,
            "sees_live_btn": bool(live),
            "sees_start_btn": bool(offline),
            "recover_hint": recover[0] if recover else "",
            "sample": texts[:8],
        }

    async def recover(self) -> bool:
        """掉线弹窗：点「重新连接 / 确定」。"""
        img, rect, boxes = await self.grab()
        if img is None:
            return False
        hit = self._find_any(boxes, list(self.cfg.get("companion.recover_keywords", [])), 0.5)
        if not hit:
            return False
        self.save_evidence(img, "recover-before")
        await self._click_box(rect, hit[1], "recover")
        log.warning("检测到掉线提示「%s」，已尝试点击恢复", hit[0])
        await asyncio.sleep(2)
        return True

    def snapshot(self) -> dict:
        return {
            "enabled": bool(self.cfg.get("companion.enabled", True)),
            "window": bool(self.hwnd_or_none()),
            "is_live": self.is_live,
            "last_result": self.last_result,
        }
