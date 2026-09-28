"""Create ordered document and query embeddings through DashScope."""

from typing import Literal

import dashscope
from pydantic import BaseModel, ConfigDict

from .config import EmbeddingSettings, load_embedding_settings


class _Embedding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["text"]
    text_index: int
    embedding: list[float]


class _EmbeddingOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    embeddings: list[_Embedding]


class EmbeddingClient:
    """Synchronous DashScope entry point for documents and queries."""

    def __init__(self, settings: EmbeddingSettings | None = None) -> None:
        self.settings = settings if settings is not None else load_embedding_settings()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Embed documents in deterministic batches while preserving input order."""

        return self._embed(texts, text_type="document")

    def embed_query(self, text: str) -> list[float]:
        """Embed one query using DashScope's query text type."""

        return self._embed([text], text_type="query")[0]

    def _embed(
        self,
        texts: list[str],
        *,
        text_type: Literal["document", "query"],
    ) -> list[list[float]]:
        vectors: list[list[float]] = []
        batch_size = self.settings.batch_size

        for start in range(0, len(texts), batch_size):
            batch = texts[start : start + batch_size]
            response = dashscope.TextEmbedding.call(
                api_key=self.settings.api_key.get_secret_value(),
                model=self.settings.model,
                input=batch,
                dimension=self.settings.dimension,
                text_type=text_type,
            )
            vectors.extend(self._read_batch(response, len(batch)))

        return vectors

    def _read_batch(self, response: object, expected_count: int) -> list[list[float]]:
        status_code = getattr(response, "status_code", None)
        if status_code != 200:
            raise RuntimeError(
                "DashScope embedding request failed: "
                f"status_code={status_code}, "
                f"code={getattr(response, 'code', None)}, "
                f"message={getattr(response, 'message', None)}, "
                f"request_id={getattr(response, 'request_id', None)}"
            )

        output = _EmbeddingOutput.model_validate(getattr(response, "output", None))
        ordered: list[list[float] | None] = [None] * expected_count

        for item in output.embeddings:
            index = item.text_index
            if index < 0 or index >= expected_count:
                raise ValueError(f"DashScope returned out-of-range text_index: {index}")
            if ordered[index] is not None:
                raise ValueError(f"DashScope returned duplicate text_index: {index}")
            if len(item.embedding) != self.settings.dimension:
                raise ValueError(
                    "DashScope returned an unexpected embedding dimension: "
                    f"expected {self.settings.dimension}, got {len(item.embedding)}"
                )
            ordered[index] = item.embedding

        if any(vector is None for vector in ordered):
            raise ValueError(
                "DashScope response is missing one or more text_index values"
            )

        return [vector for vector in ordered if vector is not None]
