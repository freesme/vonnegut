"""
统一日志模块，替代聚宽 log 对象。
提供与聚宽 log.info / log.warning / log.error 兼容的调用方式。
"""
import logging
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s - %(message)s"
_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"
_initialized = False


def setup_logging(log_dir: Path | str | None = None, level: int = logging.INFO):
    global _initialized
    if _initialized:
        return
    _initialized = True

    root = logging.getLogger()
    root.setLevel(level)
    formatter = logging.Formatter(_LOG_FORMAT, datefmt=_DATE_FORMAT)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    root.addHandler(console)

    if log_dir is not None:
        log_dir = Path(log_dir)
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_dir / "strategy.log",
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)


def get_logger(name: str = "strategy") -> logging.Logger:
    """获取 logger 实例，首次调用时自动初始化。"""
    if not _initialized:
        from config import LOG_DIR
        setup_logging(LOG_DIR)
    return logging.getLogger(name)


# 模块级快捷引用，策略代码中 `from utils.logger import log` 即可直接使用
log = get_logger()
