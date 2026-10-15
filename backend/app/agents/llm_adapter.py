import json
import time
from typing import Any, Type

from pydantic import BaseModel, ValidationError

from app.core.config import Settings


class LLMAdapter:
    """Thin optional OpenAI adapter.

    The app remains fully usable without an API key. When enabled, this adapter
    uses compact JSON schemas and small product summaries to control cost.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.last_error: str | None = None
        self.last_input_tokens = 0
        self.last_output_tokens = 0
        self.last_called = False

    @property
    def enabled(self) -> bool:
        return bool(self.settings.llm_enabled and self.settings.openai_api_key)

    def sdk_available(self) -> bool:
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def generate_json(
        self,
        *,
        schema_model: Type[BaseModel],
        system_prompt: str,
        user_payload: dict[str, Any],
        tools: list[dict[str, Any]] | None = None,
        max_output_tokens: int | None = None,
    ) -> BaseModel | None:
        self.last_error = None
        self.last_called = False
        self.last_input_tokens = 0
        self.last_output_tokens = 0

        if not self.enabled:
            self.last_error = "LLM is disabled or API key is missing."
            return None

        try:
            from openai import OpenAI
        except ImportError:
            self.last_error = "OpenAI SDK is not installed."
            return None

        client = OpenAI(
            api_key=self.settings.openai_api_key,
            timeout=self.settings.llm_timeout_seconds,
            max_retries=self.settings.llm_max_retries,
        )
        schema = schema_model.model_json_schema()

        request_payload: dict[str, Any] = {
            "model": self.settings.openai_model,
            "input": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": json.dumps(user_payload, ensure_ascii=False),
                },
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": schema_model.__name__,
                    "schema": schema,
                    "strict": False,
                }
            },
            "max_output_tokens": max_output_tokens or self.settings.llm_max_output_tokens,
        }
        if tools:
            request_payload["tools"] = tools
        if self.settings.openai_model.startswith("gpt-5") and not tools:
            request_payload["reasoning"] = {"effort": "minimal"}

        compact_input = system_prompt + json.dumps(user_payload, ensure_ascii=False)
        self.last_input_tokens = self._estimate_tokens(compact_input)
        attempts = max(1, self.settings.llm_max_retries + 1)
        for attempt in range(1, attempts + 1):
            try:
                self.last_called = True
                response = client.responses.create(**request_payload)
                raw_text = response.output_text
                if not raw_text:
                    raise ValueError("empty model response")
                self.last_output_tokens = self._estimate_tokens(raw_text)
                result = schema_model.model_validate_json(raw_text)
                self.last_error = None
                return result
            except (ValidationError, json.JSONDecodeError, ValueError, Exception) as exc:
                self.last_error = f"{type(exc).__name__}: {str(exc)[:300]}"
                if attempt < attempts and self._retryable_error(exc):
                    time.sleep(0.4 * attempt)
                    continue
                return None
        return None

    def _retryable_error(self, exc: Exception) -> bool:
        name = type(exc).__name__.lower()
        text = str(exc).lower()
        return any(token in name or token in text for token in ["timeout", "connection", "rate", "temporarily"])

    def _estimate_tokens(self, text: str) -> int:
        ascii_chars = sum(1 for char in text if ord(char) < 128)
        non_ascii_chars = max(len(text) - ascii_chars, 0)
        return max(1, int(ascii_chars / 4 + non_ascii_chars / 1.8))
