"""DeepSeek provider, via their OpenAI-format endpoint.

DeepSeek offers JSON *mode*, not JSON *schema*: `response_format={'type':'json_object'}`
guarantees syntactically valid JSON, not JSON matching our shape. Their docs also note the
API "may occasionally return empty content". So the schema guarantee that Claude's native
structured output provided has to be rebuilt here - state the schema in the prompt,
validate the result, and retry once with the error fed back.

DeepSeek also exposes an Anthropic-compatible endpoint. We deliberately do not use it: it
does not advertise structured-output support, so it would hit the same wall while adding a
compatibility shim between us and the model.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from pydantic import BaseModel, ValidationError

from .base import LLMUnavailable, coerce, schema_instructions

log = logging.getLogger(__name__)

BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-pro"
# Thinking is on by default at high effort; reasoning tokens bill as output. Worth it for
# a judgement task, and still a fraction of a cent per brief.
DEFAULT_EFFORT = "high"
MAX_ATTEMPTS = 2


class DeepSeekProvider:
    key = "deepseek"
    label = "DeepSeek"

    @property
    def model(self) -> str:
        return os.environ.get("SCRAPE_TEST_MODEL", DEFAULT_MODEL)

    @property
    def effort(self) -> str:
        return os.environ.get("SCRAPE_TEST_EFFORT", DEFAULT_EFFORT)

    def credentials_available(self) -> bool:
        return bool(os.environ.get("DEEPSEEK_API_KEY"))

    def _client(self) -> Any:
        from openai import AsyncOpenAI

        key = os.environ.get("DEEPSEEK_API_KEY")
        if not key:
            raise LLMUnavailable(
                "No DeepSeek credentials found. Set DEEPSEEK_API_KEY in the environment "
                "and restart the server."
            )
        return AsyncOpenAI(api_key=key, base_url=BASE_URL)

    async def generate[M: BaseModel](
        self,
        system: str,
        payload: str,
        output_model: type[M],
        *,
        client: Any = None,
    ) -> M:
        import openai

        client = client or self._client()
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": f"{system}\n\n{schema_instructions(output_model)}"},
            {"role": "user", "content": payload},
        ]

        last_error: Exception | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=int(os.environ.get("SCRAPE_TEST_MAX_TOKENS", "8000")),
                    response_format={"type": "json_object"},
                    reasoning_effort=self.effort,
                )
            except openai.AuthenticationError as exc:
                raise LLMUnavailable(f"DeepSeek rejected the credentials: {exc}") from exc
            except openai.RateLimitError as exc:
                raise LLMUnavailable(f"Rate limited by DeepSeek: {exc}") from exc
            except openai.APIStatusError as exc:
                raise LLMUnavailable(f"DeepSeek API error {exc.status_code}: {exc}") from exc
            except openai.APIConnectionError as exc:
                raise LLMUnavailable(f"Could not reach DeepSeek: {exc}") from exc

            raw = ""
            if response.choices:
                raw = response.choices[0].message.content or ""

            try:
                return coerce(raw, output_model)
            except (ValidationError, ValueError) as exc:
                last_error = exc
                log.warning(
                    "DeepSeek output failed validation (attempt %d/%d): %s",
                    attempt + 1,
                    MAX_ATTEMPTS,
                    str(exc)[:200],
                )
                if attempt + 1 >= MAX_ATTEMPTS:
                    break
                # Feed the failure back rather than silently retrying the same prompt.
                messages.append({"role": "assistant", "content": raw[:4000] or "(empty)"})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That response was not usable: "
                            f"{str(exc)[:600]}\n\n"
                            "Reply again with a single valid json object matching the "
                            "schema exactly. No markdown fence, no commentary."
                        ),
                    }
                )

        raise LLMUnavailable(
            f"DeepSeek did not return output matching the schema after {MAX_ATTEMPTS} "
            f"attempts: {str(last_error)[:300]}"
        )
