import asyncio

import pytest

from nanobot.providers.base import LLMProvider, LLMResponse
from nanobot.providers.call_accounting import current_call_accounting


class Provider(LLMProvider):
    def __init__(self):
        super().__init__()
        self.calls = 0

    def get_default_model(self):
        return "test-model"

    async def chat(self, **kwargs):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content="rate limit", finish_reason="error", error_kind="rate_limit")
        return LLMResponse(content="ok", usage={"prompt_tokens": 10, "completion_tokens": 2})


class Accounting:
    def __init__(self, limit=10):
        self.authorized = []
        self.settled = []
        self.limit = limit

    async def authorize(self, provider, model, request):
        if len(self.authorized) >= self.limit:
            raise RuntimeError("budget exhausted")
        self.authorized.append((provider, model))
        return str(len(self.authorized))

    async def settle(self, reservation, response):
        self.settled.append((reservation, response.finish_reason))


@pytest.mark.asyncio
async def test_retry_requires_another_reservation(monkeypatch):
    async def no_sleep(*args, **kwargs):
        return None

    monkeypatch.setattr(Provider, "_sleep_with_heartbeat", no_sleep)
    provider = Provider()
    accounting = Accounting(limit=1)
    token = current_call_accounting.set(accounting)
    try:
        with pytest.raises(RuntimeError, match="budget exhausted"):
            await provider.chat_with_retry(messages=[{"role": "user", "content": "test"}])
    finally:
        current_call_accounting.reset(token)
    assert provider.calls == 1
    assert accounting.settled == [("1", "error")]


@pytest.mark.asyncio
async def test_context_does_not_leak_to_other_tasks():
    async def run(accounting):
        token = current_call_accounting.set(accounting)
        try:
            provider = Provider()
            provider.calls = 1
            await provider.chat_with_retry(messages=[{"role": "user", "content": "test"}])
        finally:
            current_call_accounting.reset(token)

    first, second = Accounting(), Accounting()
    await asyncio.gather(run(first), run(second))
    assert len(first.authorized) == len(second.authorized) == 1
    assert current_call_accounting.get() is None


def test_provider_parser_preserves_unknown_usage_and_cached_input():
    from nanobot.providers.openai_compat_provider import OpenAICompatProvider
    from nanobot.providers.openai_responses import parse_response_output

    assert OpenAICompatProvider._extract_usage({"usage": {"total_tokens": 20}}) == {
        "total_tokens": 20,
    }
    parsed = parse_response_output({
        "id": "resp-billed-request", "status": "completed", "output": [],
        "usage": {"input_tokens": 100, "output_tokens": 20,
                  "input_tokens_details": {"cached_tokens": 75}},
    })
    assert parsed.request_id == "resp-billed-request"
    assert parsed.usage["cached_tokens"] == 75
    assert parsed.usage["prompt_tokens"] == 100


def test_metered_request_never_hides_protocol_fallback():
    from types import SimpleNamespace

    from nanobot.providers.openai_compat_provider import OpenAICompatProvider

    error = SimpleNamespace(status_code=404, body="responses unsupported")
    assert OpenAICompatProvider._should_fallback_from_responses_error(error)
    token = current_call_accounting.set(Accounting())
    try:
        assert not OpenAICompatProvider._should_fallback_from_responses_error(error)
    finally:
        current_call_accounting.reset(token)
