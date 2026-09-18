"""Shared shapes for LLM providers.

The contract is deliberately "give me a validated object", not "give me text". Where a
provider supports real schema-constrained output it uses it; where it only guarantees
valid JSON, the provider owns the validate-and-retry loop so callers never see a
half-parsed brief.
"""

from __future__ import annotations

import json
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMUnavailable(Exception):
    """The model could not produce a result (no credentials, API error, or bad output)."""


class LLMProvider(Protocol):
    key: str
    label: str

    @property
    def model(self) -> str: ...

    def credentials_available(self) -> bool: ...

    async def generate(
        self,
        system: str,
        payload: str,
        output_model: type[T],
        *,
        client: Any = None,
    ) -> T: ...


def schema_instructions(output_model: type[BaseModel]) -> str:
    """Render the schema as prompt text for providers without native schema support.

    DeepSeek's JSON mode guarantees syntactically valid JSON, not JSON that matches our
    shape, and their docs require the literal word "json" in the prompt. So the schema has
    to be stated here and enforced by validation afterwards.
    """
    schema = output_model.model_json_schema()
    fields = []
    for name, spec in schema.get("properties", {}).items():
        required = name in schema.get("required", [])
        desc = spec.get("description", "")
        kind = spec.get("type") or "string"
        if "enum" in spec:
            kind = "one of: " + ", ".join(repr(v) for v in spec["enum"])
        elif kind == "array":
            kind = "array of strings"
        elif "anyOf" in spec:
            kinds = [s.get("type", "?") for s in spec["anyOf"]]
            kind = " or ".join(k for k in kinds if k)
        flag = "required" if required else "optional, may be null"
        fields.append(f'  "{name}": {kind}  [{flag}]{" - " + desc if desc else ""}')
    return (
        "Reply with a single json object and nothing else. No markdown fence, no prose "
        "before or after. The json object must have exactly these keys:\n\n"
        + "\n".join(fields)
        + "\n\nEvery required key must be present. Arrays must be arrays of plain strings."
    )


def coerce[M: BaseModel](raw: str, output_model: type[M]) -> M:
    """Parse and validate, tolerating a fenced code block if one slips through."""
    text = (raw or "").strip()
    if not text:
        raise ValueError("model returned empty content")
    if text.startswith("```"):
        text = text.split("\n", 1)[-1]
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        text = text[start : end + 1]
    return output_model.model_validate(json.loads(text))
