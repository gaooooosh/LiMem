"""Call an OpenAI-compatible model once and validate its JSON response."""

from typing import TypeVar

from openai import OpenAI
from pydantic import BaseModel

from .config import LLMSettings, load_llm_settings

OutputModel = TypeVar("OutputModel", bound=BaseModel)


class LLMClient:
    """Synchronous JSON entry point shared by components that need an LLM."""

    def __init__(self, settings: LLMSettings | None = None) -> None:
        self.settings = settings if settings is not None else load_llm_settings()
        self._client = OpenAI(
            api_key=self.settings.api_key.get_secret_value(),
            base_url=str(self.settings.base_url),
            max_retries=0,
        )

    def generate_json(
        self,
        system_prompt: str,
        user_prompt: str,
        output_model: type[OutputModel],
    ) -> OutputModel:
        """Return one locally validated JSON response from Chat Completions."""

        completion = self._client.chat.completions.create(
            model=self.settings.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=self.settings.temperature,
            max_tokens=self.settings.max_tokens,
            response_format={"type": "json_object"},
            extra_body=(
                self.settings.extra_body.model_dump()
                if self.settings.extra_body is not None
                else None
            ),
        )
        content = completion.choices[0].message.content
        if not content:
            raise ValueError("LLM returned empty content")
        return output_model.model_validate_json(content)
