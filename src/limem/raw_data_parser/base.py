"""Define the strict replaceable RawDataParser contract."""

from abc import ABC, abstractmethod
from typing import TypeAlias

from pydantic import JsonValue

from .models import SessionInput

JsonObject: TypeAlias = dict[str, JsonValue]


class RawDataParseError(ValueError):
    """One source Record failed after the outer JSON schema was validated."""

    def __init__(self, path: tuple[str | int, ...], message: str) -> None:
        super().__init__(message)
        self.path = path


class RawDataParser(ABC):
    """Strictly convert one complete source Session JSON into Raw Events."""

    @property
    @abstractmethod
    def source(self) -> str:
        """Return the stable source identifier written to every Raw Event."""

    @property
    @abstractmethod
    def format_description(self) -> str:
        """Describe the source Session JSON accepted by this adapter."""

    @abstractmethod
    def parse(self, session_json: JsonObject) -> SessionInput:
        """Parse one complete Session without mutating the input.

        Each source record must produce one Raw Event in source order. The
        original record must remain unchanged in ``raw_payload``. Invalid input
        must raise an error instead of returning a partial SessionInput.
        """
