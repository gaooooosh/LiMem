"""Public strongly typed reference resolution interface."""

from .base import Resolver
from .models import QueryResolutionInput, ResolvedEntityMention, ResolvedQuery
from .query import QueryResolver

__all__ = [
    "QueryResolutionInput",
    "QueryResolver",
    "ResolvedEntityMention",
    "ResolvedQuery",
    "Resolver",
]
