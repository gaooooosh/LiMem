"""Create the concise Chinese service log."""

from datetime import datetime
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

_TIME_ZONE = ZoneInfo("Asia/Shanghai")


class _ChineseFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        occurred_at = datetime.fromtimestamp(record.created, _TIME_ZONE)
        return occurred_at.strftime(datefmt or "%Y-%m-%d %H:%M:%S")


def create_service_logger(log_path: Path) -> logging.Logger:
    """Return one non-propagating logger writing the agreed human format."""

    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setFormatter(
        _ChineseFormatter(
            fmt="%(asctime)s 【%(levelname)s】：%(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    logger = logging.getLogger("limem.service")
    logger.handlers.clear()
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    return logger
