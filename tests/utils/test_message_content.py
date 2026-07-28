from __future__ import annotations

from nanobot.agent.context import ContextBuilder
from nanobot.agent.runner import AgentRunner
from nanobot.utils.message_content import merge_message_content


def test_context_and_runner_delegate_to_same_message_content_contract() -> None:
    cases = [
        ("left", ""),
        ("", "right"),
        ("left", "right"),
        (None, "right"),
        ([{"type": "text", "text": "left"}], "right"),
    ]

    for left, right in cases:
        expected = merge_message_content(left, right)
        assert ContextBuilder._merge_message_content(left, right) == expected
        assert AgentRunner._merge_message_content(left, right) == expected
