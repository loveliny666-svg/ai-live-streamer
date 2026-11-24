"""OCR 封装（RapidOCR，离线、免费、带中文模型）。"""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Sequence

from PIL import Image

log = logging.getLogger("ocr")


@dataclass
class Box:
    text: str
    score: float
    x1: float
    y1: float
    x2: float
    y2: float

    @property
    def cx(self) -> float:
        return (self.x1 + self.x2) / 2

    @property
    def cy(self) -> float:
        return (self.y1 + self.y2) / 2

    @property
    def w(self) -> float:
        return self.x2 - self.x1

    @property
    def h(self) -> float:
        return self.y2 - self.y1

    def hit(self, text: str, fuzzy: bool = True) -> bool:
        if not text:
            return False
        return (text in self.text) if fuzzy else (text == self.text)


class OcrEngine:
    """单例封装，RapidOCR 初始化约 2-3 秒，只做一次。"""

    _inst = None
    _lock = threading.Lock()

    def __init__(self) -> None:
        self.ok = False
        self.err = ""
        self._ocr = None
        self._fast = None

    def _ensure(self) -> bool:
        if self._ocr is not None:
            return True
        with self._lock:
            if self._ocr is not None:
                return True
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._ocr = RapidOCR()
                self.ok = True
                log.info("OCR 引擎已加载")
            except Exception as exc:  # noqa: BLE001
                self.err = str(exc)[:200]
                self.ok = False
                log.error("OCR 初始化失败：%s", exc)
        return self._ocr is not None

    def _ensure_fast(self) -> bool:
        """弹幕面板专用的快速引擎。

        默认参数下 RapidOCR 会把短边放大到 736（`limit_type: min`），
        一条 377x210 的面板条带会被撑成 736x410 再推理，慢十倍。
        这里改成「只缩不放」+ 关掉角度分类，小图能压到几百毫秒。
        """
        if self._fast is not None:
            return True
        with self._lock:
            if self._fast is not None:
                return True
            try:
                from rapidocr_onnxruntime import RapidOCR

                self._fast = RapidOCR(
                    use_angle_cls=False,
                    det_model_path="",
                    det_limit_type="max",
                    det_limit_side_len=640,
                )
                log.info("OCR 快速引擎已加载（面板专用）")
            except Exception as exc:  # noqa: BLE001
                log.warning("快速 OCR 初始化失败，回退默认参数：%s", exc)
                self._fast = None
        return self._fast is not None

    def read(self, img: Image.Image, min_score: float = 0.0, fast: bool = False) -> list[Box]:
        engine = None
        if fast and self._ensure_fast():
            engine = self._fast
        elif self._ensure():
            engine = self._ocr
        if engine is None:
            return []
        try:
            import numpy as np

            arr = np.array(img.convert("RGB"))
            res, _ = engine(arr)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            log.warning("OCR 识别失败：%s", exc)
            return []
        out: list[Box] = []
        for item in res or []:
            try:
                pts, text, score = item[0], item[1], float(item[2])
            except Exception:  # noqa: BLE001
                continue
            if score < min_score or not str(text).strip():
                continue
            xs = [p[0] for p in pts]
            ys = [p[1] for p in pts]
            out.append(Box(str(text).strip(), score, min(xs), min(ys), max(xs), max(ys)))
        return out

    @staticmethod
    def find(boxes: Sequence[Box], target: str, min_score: float = 0.5) -> Box | None:
        """找到最像 target 的一行（优先完全相同，其次包含）。"""
        cands = [b for b in boxes if b.score >= min_score]
        exact = [b for b in cands if b.text.replace(" ", "") == target.replace(" ", "")]
        if exact:
            return max(exact, key=lambda b: b.score)
        part = [b for b in cands if target.replace(" ", "") in b.text.replace(" ", "")]
        if part:
            return max(part, key=lambda b: b.score)
        # 逐字相似度兜底（OCR 会把"开始直播"识别成"开始直潘"之类）
        best, best_ratio = None, 0.0
        tgt = set(target.replace(" ", ""))
        for b in cands:
            txt = b.text.replace(" ", "")
            if not txt:
                continue
            ratio = len(tgt & set(txt)) / max(1, len(tgt))
            if ratio > best_ratio:
                best, best_ratio = b, ratio
        if best and best_ratio >= 0.75 and len(best.text) <= len(target) + 3:
            return best
        return None


OCR = OcrEngine()
