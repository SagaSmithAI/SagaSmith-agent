from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.structured_output import StructuredOutputTool


def _tool() -> StructuredOutputTool:
    return StructuredOutputTool(
        name="submit_result",
        description="Submit the final result.",
        parameters={
            "type": "object",
            "additionalProperties": False,
            "properties": {"answer": {"type": "string", "minLength": 1}},
            "required": ["answer"],
        },
    )


async def test_structured_output_captures_one_valid_submission() -> None:
    registry = ToolRegistry()
    tool = _tool()
    registry.register(tool)

    result = await registry.execute("submit_result", {"answer": "ready"})

    assert not result.is_error
    assert tool.submission == {"answer": "ready"}
    copy = tool.submission
    assert copy is not None
    copy["answer"] = "changed"
    assert tool.submission == {"answer": "ready"}


async def test_structured_output_rejects_invalid_and_duplicate_submissions() -> None:
    registry = ToolRegistry()
    tool = _tool()
    registry.register(tool)

    invalid = await registry.execute("submit_result", {})
    assert invalid.is_error
    assert tool.submission is None

    first = await registry.execute("submit_result", {"answer": "first"})
    duplicate = await registry.execute("submit_result", {"answer": "second"})
    assert not first.is_error
    assert duplicate.is_error
    assert tool.submission == {"answer": "first"}


async def test_structured_output_validates_local_refs_and_discriminated_one_of() -> None:
    registry = ToolRegistry()
    tool = StructuredOutputTool(
        name="submit_union",
        description="Submit a typed block.",
        parameters={
            "type": "object",
            "$defs": {
                "text": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string", "const": "text"},
                        "text": {"type": "string", "minLength": 1},
                    },
                    "required": ["type", "text"],
                },
                "prompt": {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "type": {"type": "string", "const": "prompt"},
                        "text": {"type": "string", "minLength": 1},
                    },
                    "required": ["type", "text"],
                },
            },
            "properties": {
                "block": {
                    "oneOf": [
                        {"$ref": "#/$defs/text"},
                        {"$ref": "#/$defs/prompt"},
                    ]
                }
            },
            "required": ["block"],
        },
    )
    registry.register(tool)

    invalid = await registry.execute(
        "submit_union", {"block": {"type": "unknown", "text": "x"}}
    )
    valid = await registry.execute(
        "submit_union", {"block": {"type": "prompt", "text": "Choose."}}
    )

    assert invalid.is_error
    assert not valid.is_error
