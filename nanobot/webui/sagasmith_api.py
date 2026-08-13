"""Read-only status and owned configuration actions for the local SagaSmith stack."""

from __future__ import annotations

from typing import Any

from nanobot.sagasmith_local.configuration import configure_agent
from nanobot.sagasmith_local.model import StackLayout, normalize_modes
from nanobot.sagasmith_local.runtime import doctor, status


def sagasmith_payload() -> dict[str, Any]:
    layout = StackLayout.discover()
    payload = status(layout)
    payload["doctor"] = doctor(layout, include_runtime=False)
    return payload


def configure_sagasmith_modes(values: list[str]) -> dict[str, Any]:
    layout = StackLayout.discover()
    modes = normalize_modes(values)
    changed = configure_agent(layout, modes)
    state = layout.load_state()
    state.modes = [mode.value for mode in modes]
    state.workspace_root = str(layout.workspace_root)
    state.config_path = str(layout.config_path)
    state.revision += 1
    layout.save_state(state)
    return {**sagasmith_payload(), "changed": changed, "requires_restart": changed}
