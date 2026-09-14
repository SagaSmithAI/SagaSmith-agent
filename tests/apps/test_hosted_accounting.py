import json
from types import SimpleNamespace

import httpx
import pytest

from nanobot.apps.hosted_accounting import HostedCallAccounting

CALLBACK = {
    "authorize_url": "http://host/accounting/authorize",
    "settle_url": "http://host/accounting/settle",
    "token": "test-credential",
}


@pytest.mark.asyncio
async def test_hosted_accounting_sends_no_prompt_and_settles_usage():
    requests = []

    def handler(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert b"private player text" not in request.content
        return httpx.Response(200, json={"reservation_id": "reservation-1"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        accounting = HostedCallAccounting(client, {
            **CALLBACK,
        })
        reservation = await accounting.authorize("OpenAICompatProvider", "test-model", {
            "messages": [{"role": "user", "content": "private player text"}],
            "max_tokens": 4096,
        })
        await accounting.settle(reservation, SimpleNamespace(
            usage={"prompt_tokens": 20, "completion_tokens": 5}, finish_reason="stop",
        ))
    assert requests[0]["request_bytes"] > 0
    assert requests[1]["usage"]["prompt_tokens"] == 20


@pytest.mark.asyncio
async def test_hosted_accounting_fails_closed_on_host_error():
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda request: httpx.Response(503, text="private error details")
    )) as client:
        accounting = HostedCallAccounting(client, {
            **CALLBACK,
        })
        with pytest.raises(RuntimeError, match="rejected"):
            await accounting.authorize("OpenAICompatProvider", "test-model", {"max_tokens": 4096})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "provider",
    ("FallbackProvider", "AnthropicProvider", "OpenAICodexProvider"),
)
async def test_hosted_accounting_rejects_unsupported_provider_before_network(provider):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"reservation_id": "unexpected"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        accounting = HostedCallAccounting(client, CALLBACK)
        with pytest.raises(RuntimeError, match="OpenAICompatProvider only"):
            await accounting.authorize(provider, "", {})

    assert requests == []
