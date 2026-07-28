"""Canonical message-content composition for Agent context and runtime paths."""

from __future__ import annotations

from typing import Any


def merge_message_content(
    left: Any,
    right: Any,
) -> str | list[dict[str, Any]]:
    """Merge text and multimodal message content without empty separators."""

    if isinstance(left, str) and isinstance(right, str):
        if not left:
            return right
        if not right:
            return left
        return f"{left}\n\n{right}"

    def to_blocks(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [
                item
                if isinstance(item, dict)
                else {"type": "text", "text": str(item)}
                for item in value
            ]
        if value is None:
            return []
        return [{"type": "text", "text": str(value)}]

    return to_blocks(left) + to_blocks(right)
