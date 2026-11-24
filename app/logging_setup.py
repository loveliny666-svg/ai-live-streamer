"""日志：控制台 + 滚动文件 + 内存环形缓冲（给控制台页面看）。"""
from __future__ import annotations

import logging
import sys
from collections import deque
from logging.handlers import RotatingFileHandler
from pathlib import Path

RING: deque[str] = deque(maxlen=1200)


class RingHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            RING.append(self.format(record))
        except Exception:  # noqa: BLE001
            pass


def setup(log_dir: Path, level: str = "INFO") -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    try:  # Windows 控制台默认 GBK，中文日志会炸
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        pass

    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))
    root.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s %(levelname)-5s %(name)-12s %(message)s", datefmt="%H:%M:%S"
    )
    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(fmt)
    root.addHandler(ch)

    fh = RotatingFileHandler(
        log_dir / "app.log", maxBytes=4 * 1024 * 1024, backupCount=6, encoding="utf-8"
    )
    fh.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-5s %(name)-12s %(message)s")
    )
    root.addHandler(fh)

    rh = RingHandler()
    rh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-5s %(name)-10s %(message)s", datefmt="%H:%M:%S"))
    root.addHandler(rh)

    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
