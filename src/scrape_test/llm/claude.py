"""Claude provider, via the Anthropic SDK's native structured output.

Kept as a switchable alternative to DeepSeek. Unlike JSON mode, `messages.parse()`
constrains the response to the schema at the API level, so there is no validate-and-retry
loop here - if it returns, it matches.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from .base import LLMUnavailable

DEFAULT_MODEL = "claude-opus-5"


class ClaudeProvider:
    key = "claude"
    label = "Claude"

    @property
    def model(self) -> str:
        return os.environ.get("SCRAPE_TEST_MODEL", DEFAULT_MODEL)

    def credentials_available(self) -> bool:
        if os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"):
            return True
        # The SDK also resolves an `ant auth login` profile from disk.
        return (Path.home() / ".config" / "anthropic").exists()

    async def generate[M: BaseModel](
        self,
        system: str,
        payload: str,
        output_model: type[M],
        *,
        client: Any = None,
    ) -> M:
        import anthropic

        if client is None:
            if not self.credentials_available():
                raise LLMUnavailable(
                    "No Anthropic credentials found. Set ANTHROPIC_API_KEY in the "
                    "environment (or run `ant auth login`) and restart the server."
                )
            client = anthropic.AsyncAnthropic()

        try:
            response = await client.messages.parse(
                model=self.model,
                max_tokens=int(os.environ.get("SCRAPE_TEST_MAX_TOKENS", "8000")),
                system=system,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": payload}],
                output_format=output_model,
            )
        except anthropic.AuthenticationError as exc:
            raise LLMUnavailable(f"Anthropic rejected the credentials: {exc}") from exc
        except anthropic.RateLimitError as exc:
            raise LLMUnavailable(f"Rate limited by the Anthropic API: {exc}") from exc
        except anthropic.APIStatusError as exc:
            raise LLMUnavailable(f"Anthropic API error {exc.status_code}: {exc}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMUnavailable(f"Could not reach the Anthropic API: {exc}") from exc

        if getattr(response, "stop_reason", None) == "refusal":
            raise LLMUnavailable("The model declined to generate this brief.")

        parsed = getattr(response, "parsed_output", None)
        if parsed is None:
            raise LLMUnavailable("The model returned no parseable structured output.")
        return parsed
