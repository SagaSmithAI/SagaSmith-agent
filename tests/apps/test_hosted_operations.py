from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from nanobot.apps.hosted_operations import JournaledTool


@pytest.mark.asyncio
async def test_journal_failure_prevents_dispatch():
    tool = SimpleNamespace(_original_name="combat_resolve_attack", execute=AsyncMock())
    client = SimpleNamespace(post=AsyncMock(side_effect=RuntimeError("host unavailable")))
    wrapped = JournaledTool(tool, client, {"url": "http://host/receipt", "token": "test"})
    with pytest.raises(RuntimeError):
        await wrapped.execute()
    tool.execute.assert_not_called()


@pytest.mark.asyncio
async def test_receipt_is_saved_before_returning_result():
    events = []

    async def post(*args, **kwargs):
        events.append(kwargs["json"]["state"])
        return SimpleNamespace(raise_for_status=lambda: None)

    async def execute(**kwargs):
        events.append("domain")
        return SimpleNamespace(structured_content={"resolution_id": "r1"}, is_error=False)

    tool = SimpleNamespace(_original_name="combat_resolve_attack", execute=execute)
    wrapped = JournaledTool(tool, SimpleNamespace(post=post),
                            {"url": "http://host/receipt", "token": "test"})
    result = await wrapped.execute()
    assert result.structured_content == {"resolution_id": "r1"}
    assert events == ["dispatched", "domain", "returned"]
