"""分级日志（需求书 §11）。

每个模块一个独立文件，单文件 10MB、保留 5 个备份；同时输出到控制台。
调用 ``setup_logging()`` 一次即可，其它模块用 ``get_logger("replacer")`` 取用。
"""

from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

MODULES = ("doc_parser", "replacer", "deepseek", "review", "main", "word")
MAX_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 5

_configured: dict[str, logging.Logger] = {}
_log_dir: Path | None = None


class _ConsoleFormatter(logging.Formatter):
    """控制台只显示关键信息，避免刷屏。"""

    def format(self, record: logging.LogRecord) -> str:
        return f"[{record.levelname:7s}] {record.getMessage()}"


def setup_logging(log_dir: str | Path, level: str = "INFO", console: bool = True) -> Path:
    """初始化日志目录与各模块 logger，返回日志目录。"""
    global _log_dir
    _log_dir = Path(log_dir)
    _log_dir.mkdir(parents=True, exist_ok=True)
    lvl = getattr(logging, str(level).upper(), logging.INFO)

    for name in MODULES:
        logger = logging.getLogger(f"tf.{name}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()

        file_handler = RotatingFileHandler(
            _log_dir / f"{name}.log", maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(
            logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")
        )
        logger.addHandler(file_handler)

        if console:
            console_handler = logging.StreamHandler(sys.stdout)
            console_handler.setLevel(lvl)
            console_handler.setFormatter(_ConsoleFormatter())
            logger.addHandler(console_handler)

        _configured[name] = logger
    return _log_dir


def get_logger(module: str) -> logging.Logger:
    """取模块 logger；未初始化时按默认目录初始化，保证独立调用也能落盘。"""
    if module not in _configured:
        base = _log_dir or (Path.cwd() / "logs")
        setup_logging(base)
    return _configured.get(module) or logging.getLogger(f"tf.{module}")


def log_dir() -> Path:
    return _log_dir or (Path.cwd() / "logs")
