"""DeepSeek provider tests.

DeepSeek guarantees valid JSON, not JSON matching our schema, and its docs warn it "may
occasionally return empty content". The validate-and-retry loop is the whole reason this
provider is more than a base-URL swap, so it is pinned here. A fake client stands in for
the API - none of this proves a live call works.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from conftest import sample_brief
from scrape_test.brief import Brief
from scrape_test.llm import get_provider, schema_instructions
from scrape_test.llm.base import LLMUnavailable, coerce
from scrape_test.llm.deepseek import DeepSeekProvider

VALID = sample_brief().model_dump_json()


def _completion(content):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content))])


class FakeCompletions:
    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _completion(item)


class FakeOpenAI:
    def __init__(self, *responses):
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))


@pytest.fixture
def provider():
    return DeepSeekProvider()


class TestDefaults:
    def test_registry_default_is_deepseek(self, monkeypatch):
        monkeypatch.delenv("SCRAPE_TEST_LLM", raising=False)
        assert get_provider().key == "deepseek"

    def test_default_model_is_v4_pro(self, monkeypatch, provider):
        monkeypatch.delenv("SCRAPE_TEST_MODEL", raising=False)
        assert provider.model == "deepseek-v4-pro"

    def test_model_is_overridable(self, monkeypatch, provider):
        monkeypatch.setenv("SCRAPE_TEST_MODEL", "deepseek-flash")
        assert provider.model == "deepseek-flash"

    def test_claude_still_selectable(self, monkeypatch):
        monkeypatch.setenv("SCRAPE_TEST_LLM", "claude")
        assert get_provider().key == "claude"

    def test_unknown_provider_is_rejected(self, monkeypatch):
        monkeypatch.setenv("SCRAPE_TEST_LLM", "nope")
        with pytest.raises(LLMUnavailable, match="unknown LLM provider"):
            get_provider()


class TestSchemaPrompt:
    def test_contains_the_literal_word_json(self):
        """DeepSeek's JSON mode requires the word 'json' in the prompt or it errors."""
        assert "json" in schema_instructions(Brief).lower()

    def test_lists_every_required_field(self):
        text = schema_instructions(Brief)
        for field in ("headline", "news_summary", "priority", "email_subject", "email_body"):
            assert f'"{field}"' in text

    def test_states_the_priority_enum(self):
        assert "pursue now" in schema_instructions(Brief)

    def test_marks_the_optional_field_optional(self):
        line = next(ln for ln in schema_instructions(Brief).splitlines() if '"news_hook"' in ln)
        assert "optional" in line


class TestCoerce:
    def test_plain_json(self):
        assert coerce(VALID, Brief).priority == "pursue now"

    def test_strips_a_markdown_fence(self):
        assert coerce(f"```json\n{VALID}\n```", Brief).headline

    def test_strips_surrounding_prose(self):
        assert coerce(f"Here you go:\n{VALID}\nHope that helps!", Brief).headline

    def test_empty_content_raises(self):
        with pytest.raises(ValueError, match="empty content"):
            coerce("", Brief)

    def test_schema_violation_raises(self):
        bad = json.loads(VALID)
        bad["priority"] = "call them immediately"
        with pytest.raises(ValidationError):
            coerce(json.dumps(bad), Brief)


class TestGenerate:
    async def test_happy_path(self, provider):
        client = FakeOpenAI(VALID)
        result = await provider.generate("SYS", "PAYLOAD", Brief, client=client)
        assert result.priority == "pursue now"
        call = client.chat.completions.calls[0]
        assert call["response_format"] == {"type": "json_object"}
        assert call["messages"][1]["content"] == "PAYLOAD"
        assert "SYS" in call["messages"][0]["content"]
        assert call["reasoning_effort"] == "high"

    async def test_retries_once_on_schema_violation_then_succeeds(self, provider):
        bad = json.loads(VALID)
        bad["priority"] = "nope"
        client = FakeOpenAI(json.dumps(bad), VALID)
        result = await provider.generate("SYS", "P", Brief, client=client)
        assert result.priority == "pursue now"
        assert len(client.chat.completions.calls) == 2

    async def test_retry_feeds_the_error_back(self, provider):
        client = FakeOpenAI("not json at all", VALID)
        await provider.generate("SYS", "P", Brief, client=client)
        retry_messages = client.chat.completions.calls[1]["messages"]
        assert retry_messages[-1]["role"] == "user"
        assert "not usable" in retry_messages[-1]["content"]

    async def test_empty_content_is_retried(self, provider):
        """Their docs explicitly warn this happens."""
        client = FakeOpenAI("", VALID)
        result = await provider.generate("SYS", "P", Brief, client=client)
        assert result.headline
        assert len(client.chat.completions.calls) == 2

    async def test_gives_up_after_two_attempts(self, provider):
        client = FakeOpenAI("garbage", "still garbage")
        with pytest.raises(LLMUnavailable, match="after 2 attempts"):
            await provider.generate("SYS", "P", Brief, client=client)
        assert len(client.chat.completions.calls) == 2

    async def test_no_credentials_is_actionable(self, monkeypatch, provider):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        with pytest.raises(LLMUnavailable, match="DEEPSEEK_API_KEY"):
            await provider.generate("SYS", "P", Brief)
