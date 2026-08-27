"""Tests that the Jina Reader path never discloses visible URL credentials."""

from __future__ import annotations

import json
import socket
from unittest.mock import patch

import httpx
import pytest

from nanobot.agent.tools import web as web_module
from nanobot.agent.tools.web import (
    WebFetchTool,
    _redact_url_for_log,
    _url_carries_credentials,
)


def _fake_resolve_public(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


@pytest.fixture(autouse=True)
def _clear_proxy_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "http_proxy",
        "https_proxy",
        "all_proxy",
        "NO_PROXY",
        "no_proxy",
    ):
        monkeypatch.delenv(name, raising=False)


class _RecordingJinaClient:
    """Fake httpx.AsyncClient that records every requested URL."""

    requested: list[str] = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, url, **kwargs):
        self.requested.append(str(url))

        class _Response:
            status_code = 200

            def raise_for_status(self):
                return None

            def json(self):
                return {"data": {"title": "T", "content": "body", "url": url}}

        return _Response()


@pytest.fixture
def jina_client():
    _RecordingJinaClient.requested = []
    with patch("nanobot.agent.tools.web.httpx.AsyncClient", _RecordingJinaClient):
        yield _RecordingJinaClient


@pytest.mark.parametrize(
    "url",
    [
        "https://user:secret@example.com/report",
        "https://user@example.com/report",
        "https://example.com/download?ACCESS_TOKEN=secret",
        "https://example.com/download?API-KEY=secret",
        "https://example.com/download?api_token=secret",
        "https://example.com/download?Authorization=secret",
        "https://example.com/download?client_assertion=secret",
        "https://example.com/download?client_secret=secret",
        "https://example.com/callback?code=secret",
        "https://example.com/download?credential=secret",
        "https://example.com/download?id_token=secret",
        "https://example.com/download?jwt=secret",
        "https://example.com/download?key=secret",
        "https://example.com/download?password=secret",
        "https://example.com/download?private_key=secret",
        "https://example.com/download?refresh_token=secret",
        "https://example.com/download?SAMLResponse=secret",
        "https://example.com/download?session_id=secret",
        "https://example.com/download?session_token=secret",
        "https://example.com/download?sig=secret",
        "https://example.com/download?Signature=secret",
        "https://example.com/download?sso_token=secret",
        "https://example.com/download?ticket=secret",
        "https://example.com/download?token=secret",
        "https://bucket.s3.amazonaws.com/key?X-Amz-Credential=secret",
        "https://bucket.s3.amazonaws.com/key?X-Amz-Signature=secret",
        "https://storage.googleapis.com/o/file?X-Goog-Credential=secret",
        "https://storage.googleapis.com/o/file?X-Goog-Signature=secret",
        "https://example.com/download?file=report;token=secret",
        "https://example.com/download?%20ToKeN%20=secret",
        "https://[invalid",
    ],
)
def test_credential_urls_are_detected(url: str) -> None:
    assert _url_carries_credentials(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://example.com/",
        "https://example.com/watch?v=abc123",
        "https://example.com/search?q=token+design&page=2",
        "https://example.com/page?monkey=value&authentic=true",
        "https://example.com/page?signatureAlgorithm=sha256",
        "https://example.com/page#access_token=client-side-only",
    ],
)
def test_plain_urls_are_not_detected(url: str) -> None:
    assert _url_carries_credentials(url) is False


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://user:secret@example.com:8443/private/secret?token=secret#secret",
            "https://example.com:8443",
        ),
        (
            "https://user:secret@[2001:db8::1]:8443/private?token=secret",
            "https://[2001:db8::1]:8443",
        ),
        ("not a URL with secret material", "<redacted URL>"),
    ],
)
def test_log_label_contains_only_the_url_origin(url: str, expected: str) -> None:
    assert _redact_url_for_log(url) == expected


async def test_jina_is_skipped_for_credential_urls(jina_client) -> None:
    result = await WebFetchTool()._fetch_jina(
        "https://user:secret@example.com/private?token=query-secret#fragment-secret",
        max_chars=1000,
    )

    assert result is None
    assert jina_client.requested == []


async def test_jina_skip_log_exposes_only_origin(jina_client, monkeypatch) -> None:
    logged: list[tuple[object, ...]] = []
    monkeypatch.setattr(web_module.logger, "debug", lambda *args: logged.append(args))

    result = await WebFetchTool()._fetch_jina(
        "https://user:user-secret@example.com/private-path-secret"
        "?token=query-secret#fragment-secret",
        max_chars=1000,
    )

    assert result is None
    assert jina_client.requested == []
    rendered = " ".join(str(item) for call in logged for item in call)
    assert "https://example.com" in rendered
    for secret in (
        "user-secret",
        "private-path-secret",
        "query-secret",
        "fragment-secret",
    ):
        assert secret not in rendered


async def test_jina_still_used_for_plain_urls(jina_client) -> None:
    result = await WebFetchTool()._fetch_jina(
        "https://example.com/watch?v=abc123", max_chars=1000
    )

    assert result is not None
    assert json.loads(result)["extractor"] == "jina"
    assert jina_client.requested == ["https://r.jina.ai/https://example.com/watch?v=abc123"]


async def test_fragment_is_never_forwarded(jina_client) -> None:
    result = await WebFetchTool()._fetch_jina(
        "https://example.com/page?q=1#access_token=fragment-secret", max_chars=1000
    )

    assert result is not None
    assert jina_client.requested == ["https://r.jina.ai/https://example.com/page?q=1"]


async def test_jina_failure_log_exposes_only_origin(monkeypatch) -> None:
    logged: list[tuple[object, ...]] = []
    requested: list[str] = []

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def get(self, url, **kwargs):
            requested.append(str(url))
            raise RuntimeError("exception-secret")

    monkeypatch.setattr(web_module.httpx, "AsyncClient", FailingClient)
    monkeypatch.setattr(web_module.logger, "debug", lambda *args: logged.append(args))

    result = await WebFetchTool()._fetch_jina(
        "https://example.com/private-path-secret?view=query-secret#fragment-secret",
        max_chars=1000,
    )

    assert result is None
    assert requested == [
        "https://r.jina.ai/https://example.com/private-path-secret?view=query-secret"
    ]
    rendered = " ".join(str(item) for call in logged for item in call)
    assert "https://example.com" in rendered
    for secret in (
        "private-path-secret",
        "query-secret",
        "fragment-secret",
        "exception-secret",
    ):
        assert secret not in rendered


async def test_execute_fetches_credential_urls_locally(monkeypatch) -> None:
    tool = WebFetchTool()
    requested: list[str] = []

    class FakeStreamResponse:
        status_code = 200
        headers = {"content-type": "text/html"}
        url = "https://example.com/download"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeResponse:
        status_code = 200
        url = "https://example.com/download"
        text = "<html><head><title>T</title></head><body><p>ok</p></body></html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, **kwargs):
            requested.append(str(url))
            return FakeStreamResponse()

        async def get(self, url, headers=None, **kwargs):
            requested.append(str(url))
            return FakeResponse()

    async def unexpected_jina(*args, **kwargs):
        raise AssertionError("credential URL must not reach Jina")

    monkeypatch.setattr(tool, "_fetch_jina", unexpected_jina)
    monkeypatch.setattr(tool, "_extract_readable_html", lambda html, mode: "ok")
    monkeypatch.setattr(web_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(web_module, "_pinned_dns_transport", lambda: object())

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url="https://example.com/download?token=query-secret")

    assert json.loads(result)["extractor"] == "readability"
    assert all("r.jina.ai" not in url for url in requested)


async def test_execute_keeps_credential_redirect_chain_local(monkeypatch) -> None:
    tool = WebFetchTool()
    requested: list[str] = []
    short_url = "https://example.com/short"
    signed_url = "https://cdn.example.com/file?X-Amz-Signature=query-secret"

    class FakeStreamResponse:
        def __init__(self, url: str):
            self.url = url
            self.status_code = 302 if url == short_url else 200
            self.headers = (
                {"location": signed_url}
                if url == short_url
                else {"content-type": "text/html"}
            )

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeResponse:
        text = "<html><head><title>T</title></head><body><p>ok</p></body></html>"

        def __init__(self, url: str):
            self.url = url
            self.status_code = 302 if url == short_url else 200
            self.headers = (
                {"location": signed_url}
                if url == short_url
                else {"content-type": "text/html"}
            )

        async def aclose(self):
            return None

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, **kwargs):
            requested.append(str(url))
            return FakeStreamResponse(str(url))

        async def get(self, url, headers=None, **kwargs):
            requested.append(str(url))
            return FakeResponse(str(url))

    async def unexpected_jina(*args, **kwargs):
        raise AssertionError("credential redirect chain must not reach Jina")

    monkeypatch.setattr(tool, "_fetch_jina", unexpected_jina)
    monkeypatch.setattr(tool, "_extract_readable_html", lambda html, mode: "ok")
    monkeypatch.setattr(web_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(web_module, "_pinned_dns_transport", lambda: object())

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url=short_url)

    assert json.loads(result)["extractor"] == "readability"
    assert requested == [short_url, signed_url, short_url, signed_url]
    assert all("r.jina.ai" not in url for url in requested)


async def test_preflight_failure_stays_local_and_log_exposes_only_origin(monkeypatch) -> None:
    tool = WebFetchTool()
    logged: list[tuple[object, ...]] = []

    class FailingStream:
        async def __aenter__(self):
            raise RuntimeError("exception-secret")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeResponse:
        status_code = 200
        url = "https://example.com/final"
        text = "<html><head><title>T</title></head><body><p>ok</p></body></html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None, **kwargs):
            return FailingStream()

        async def get(self, url, headers=None, **kwargs):
            return FakeResponse()

    async def unexpected_jina(*args, **kwargs):
        raise AssertionError("Jina must stay disabled after an inconclusive preflight")

    monkeypatch.setattr(tool, "_fetch_jina", unexpected_jina)
    monkeypatch.setattr(tool, "_extract_readable_html", lambda html, mode: "ok")
    monkeypatch.setattr(web_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(web_module, "_pinned_dns_transport", lambda: object())
    monkeypatch.setattr(web_module.logger, "debug", lambda *args: logged.append(args))
    url = "https://example.com/private-path-secret?view=query-secret#fragment-secret"

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool.execute(url=url)

    assert json.loads(result)["extractor"] == "readability"
    rendered = " ".join(str(item) for call in logged for item in call)
    assert "https://example.com" in rendered
    for secret in (
        "private-path-secret",
        "query-secret",
        "fragment-secret",
        "exception-secret",
    ):
        assert secret not in rendered


async def test_readability_fallback_log_exposes_only_origin(monkeypatch) -> None:
    logged: list[tuple[object, ...]] = []

    class FakeResponse:
        status_code = 200
        url = "https://example.com/final"
        text = "<html><body><p>ok</p></body></html>"
        headers = {"content-type": "text/html"}

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, **kwargs):
            return FakeResponse()

    tool = WebFetchTool()
    monkeypatch.setattr(web_module.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(web_module, "_pinned_dns_transport", lambda: object())
    monkeypatch.setattr(web_module.logger, "warning", lambda *args: logged.append(args))
    monkeypatch.setattr(
        tool,
        "_extract_readable_html",
        lambda html, mode: (_ for _ in ()).throw(RuntimeError("exception-secret")),
    )
    url = "https://example.com/private-path-secret?token=query-secret#fragment-secret"

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = await tool._fetch_readability(url, "markdown", 1000)

    assert json.loads(result)["extractor"] == "html"
    rendered = " ".join(str(item) for call in logged for item in call)
    assert "https://example.com" in rendered
    for secret in (
        "private-path-secret",
        "query-secret",
        "fragment-secret",
        "exception-secret",
    ):
        assert secret not in rendered


@pytest.mark.parametrize(
    "failure",
    [
        httpx.ProxyError("exception-secret"),
        RuntimeError("exception-secret"),
    ],
)
async def test_readability_request_failure_log_exposes_only_origin(monkeypatch, failure) -> None:
    logged: list[tuple[object, ...]] = []

    class FailingClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, headers=None, **kwargs):
            raise failure

    monkeypatch.setattr(web_module.httpx, "AsyncClient", FailingClient)
    monkeypatch.setattr(web_module, "_pinned_dns_transport", lambda: object())
    monkeypatch.setattr(web_module.logger, "warning", lambda *args: logged.append(args))
    url = "https://example.com/private-path-secret?token=query-secret#fragment-secret"

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        await WebFetchTool()._fetch_readability(url, "markdown", 1000)

    rendered = " ".join(str(item) for call in logged for item in call)
    assert "https://example.com" in rendered
    for secret in (
        "private-path-secret",
        "query-secret",
        "fragment-secret",
        "exception-secret",
    ):
        assert secret not in rendered
