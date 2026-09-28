"""Strongly typed base interface for reference resolvers."""

from abc import ABC, abstractmethod
from typing import Generic, TypeVar

InputT = TypeVar("InputT")
OutputT = TypeVar("OutputT")


class Resolver(ABC, Generic[InputT, OutputT]):
    """Resolve references in one typed input without prescribing a strategy."""

    @property
    @abstractmethod
    def resolution_description(self) -> str:
        """Describe the accepted input and references resolved by this resolver."""

    @abstractmethod
    def resolve(self, value: InputT) -> OutputT:
        """Resolve one complete input or raise when resolution cannot run."""
