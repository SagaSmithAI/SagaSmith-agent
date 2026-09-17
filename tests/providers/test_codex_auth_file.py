import json

import pytest

from nanobot.providers.openai_codex_provider import OpenAICodexProvider, _read_codex_auth


def test_factory_auth_source_is_explicit_and_part_of_signature(tmp_path):
    from nanobot.config.schema import Config
    from nanobot.providers.factory import make_provider, provider_signature

    config = Config()
    config.agents.defaults.model = "openai-codex/gpt-5.6-luna"
    config.agents.defaults.provider = "openai_codex"
    before = provider_signature(config)
    path = tmp_path / "auth.json"
    config.providers.openai_codex.codex_auth_file = str(path)
    assert make_provider(config).auth_file == path
    assert provider_signature(config) != before
    config.agents.defaults.provider = "anthropic"
    config.providers.anthropic.codex_auth_file = str(path)
    with pytest.raises(ValueError, match="only supported for OpenAI Codex"):
        make_provider(config)


def test_auth_file_rotation_is_read_only(tmp_path):
    path = tmp_path / "auth.json"
    for access in ("first", "rotated"):
        raw = json.dumps({"tokens": {"account_id": "account", "access_token": access}})
        path.write_text(raw, encoding="utf-8")
        assert _read_codex_auth(path) == ("account", access)
        assert path.read_text(encoding="utf-8") == raw


@pytest.mark.parametrize("raw", ["secret-not-json", "null", "[]", '{"tokens":null}',
                                      '{"tokens":{"account_id":"secret","access_token":null}}'])
def test_invalid_auth_does_not_leak_contents(tmp_path, raw):
    path = tmp_path / "auth.json"
    path.write_text(raw, encoding="utf-8")
    with pytest.raises(ValueError, match="codex_auth_file") as error:
        _read_codex_auth(path)
    assert "secret" not in str(error.value)


@pytest.mark.asyncio
async def test_explicit_auth_bypasses_oauth_cache(tmp_path, monkeypatch):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"tokens": {"account_id": "account", "access_token": "fresh"}}))

    def stale_cache(**kwargs):
        pytest.fail("Explicit auth must not read or refresh OAuth cache")

    async def request(url, headers, body, **kwargs):
        assert headers["Authorization"] == "Bearer fresh"
        assert body["model"] == "gpt-5.6-luna"
        return "ok", [], "stop", {}, None

    monkeypatch.setattr("nanobot.providers.openai_codex_provider.get_codex_token", stale_cache)
    monkeypatch.setattr("nanobot.providers.openai_codex_provider._request_codex", request)
    response = await OpenAICodexProvider(auth_file=str(path)).chat([], model="gpt-5.6-luna")
    assert response.content == "ok"
