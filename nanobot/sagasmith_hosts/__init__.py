"""Trusted external-Host mappings and the SagaSmith MCP auth bridge."""

from nanobot.sagasmith_hosts.contract import (
    BridgeLaunch,
    TrustedHostContext,
    adapt_claude_code,
    adapt_codex,
    adapt_hermes,
    adapt_nanobot,
    adapt_openclaw,
    adapt_sagasmith_agent,
    adapt_service_worker,
)

__all__ = [
    "BridgeLaunch",
    "TrustedHostContext",
    "adapt_claude_code",
    "adapt_codex",
    "adapt_hermes",
    "adapt_nanobot",
    "adapt_openclaw",
    "adapt_sagasmith_agent",
    "adapt_service_worker",
]
