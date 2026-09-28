"""Public embedding client interface."""

from .client import EmbeddingClient
from .config import EmbeddingSettings, load_embedding_settings

__all__ = ["EmbeddingClient", "EmbeddingSettings", "load_embedding_settings"]
