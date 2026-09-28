"""Offline behavior-consolidation baseline; not registered in the service pipeline."""

from .agent import run
from .client import QwenClient

BASELINE_ID = "behavior-react-20260923"
__all__ = ["BASELINE_ID", "QwenClient", "run"]
