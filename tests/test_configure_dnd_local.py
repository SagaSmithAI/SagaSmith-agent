import json
from pathlib import Path

from scripts.configure_dnd_local import LOOPBACK_CIDR, desired_server, updated_config


def test_local_dnd_config_preserves_unrelated_secrets() -> None:
    original = {
        "providers": {"openai": {"apiKey": "keep-me"}},
        "tools": {
            "ssrfWhitelist": ["100.64.0.0/10"],
            "mcpServers": {
                "other": {"command": "other-mcp"},
                "sagasmith_coc": {"command": "coc-mcp"},
                "sagasmith_dnd": {"command": "old.exe", "enabledTools": ["exposure"]},
            },
        },
    }

    updated = updated_config(original, "http://127.0.0.1:8767/mcp")

    assert updated["providers"] == original["providers"]
    assert updated["tools"]["mcpServers"]["other"] == {"command": "other-mcp"}
    assert "sagasmith_coc" not in updated["tools"]["mcpServers"]
    assert updated["tools"]["mcpServers"]["sagasmith_dnd"] == desired_server(
        "http://127.0.0.1:8767/mcp"
    )
    assert updated["tools"]["ssrfWhitelist"] == ["100.64.0.0/10", LOOPBACK_CIDR]


def test_local_dnd_config_is_idempotent(tmp_path: Path) -> None:
    value = updated_config({"tools": {}}, "http://127.0.0.1:8767/mcp")
    path = tmp_path / "config.json"
    path.write_text(json.dumps(value), encoding="utf-8")

    loaded = json.loads(path.read_text(encoding="utf-8"))

    assert updated_config(loaded, "http://127.0.0.1:8767/mcp") == loaded
