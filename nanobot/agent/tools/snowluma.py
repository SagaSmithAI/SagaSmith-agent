"""SnowLuma OneBot action tool — invoke any OneBot v11 action."""

from __future__ import annotations

from typing import Any

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.schema import (
    ObjectSchema,
    StringSchema,
    tool_parameters_schema,
)

# Module-level state set by SnowlumaChannel on start
_http_url: str = ""
_access_token: str = ""
_http_session: Any = None


def configure(url: str, token: str, session: Any) -> None:
    """Called by SnowlumaChannel on start to wire up HTTP access."""
    global _http_url, _access_token, _http_session
    _http_url = url.rstrip("/")
    _access_token = token
    _http_session = session


@tool_parameters(
    tool_parameters_schema(
        action=StringSchema(
            "OneBot v11 action name, e.g. send_msg, get_group_member_list, "
            "set_group_ban, get_group_info, get_friend_list, delete_msg, ... "
            "Full catalog at https://snowluma.github.io/zh/api/",
        ),
        params=ObjectSchema(
            description="Action parameters as a JSON object. "
            "For send_msg/send_group_msg: {\"message\": [{\"type\":\"text\",\"data\":{\"text\":\"hi\"}}], "
            "\"group_id\": 123}. Use message array format (not string). "
            "For get_group_member_list: {\"group_id\": 123}. "
            "For set_group_ban: {\"group_id\": 123, \"user_id\": 456, \"duration\": 600}. "
            "Refer to the OneBot v11 spec for each action's params.",
        ),
        required=["action", "params"],
        description="Invoke any OneBot v11 action via SnowLuma HTTP API.",
    )
)
class SnowlumaActionTool(Tool):
    """Invoke any OneBot v11 action via SnowLuma HTTP API."""

    _plugin_discoverable = True

    @property
    def name(self) -> str:
        return "snowluma_action"

    @property
    def description(self) -> str:
        return (
            "Invoke any OneBot v11 QQ bot action via SnowLuma HTTP API. "
            "Use this for QQ group management, sending messages with special segments, "
            "querying group/friend info, managing files, etc. "
            "For plain text replies, just answer naturally — this is only needed for "
            "non-text segments (images, rich messages) or administrative actions."
        )

    async def execute(
        self,
        action: str,
        params: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> str:
        if not _http_session or not _http_url:
            return ToolResult.error("SnowLuma channel not initialized")

        import aiohttp

        url = f"{_http_url}/{action}"
        headers = {"Content-Type": "application/json"}
        if _access_token:
            headers["Authorization"] = f"Bearer {_access_token}"

        try:
            async with _http_session.post(
                url,
                json=params or {},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                body = await resp.json()
                status = body.get("status")
                retcode = body.get("retcode")
                if (status and status != "ok") or (retcode not in (None, 0)):
                    return ToolResult.error(
                        f"OneBot action '{action}' failed: "
                        f"{body.get('wording', '')} (status={status}, retcode={retcode})"
                    )
                data = body.get("data")
                if data is not None:
                    import json as _json
                    return f"Success: {_json.dumps(data, ensure_ascii=False, default=str)}"
                return f"Action '{action}' completed successfully"
        except Exception as e:
            return ToolResult.error(f"SnowLuma HTTP call failed: {e}")
