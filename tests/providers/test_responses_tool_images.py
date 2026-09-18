from nanobot.providers.openai_responses.converters import convert_messages


def test_tool_source_image_is_native_image_input_not_json_text():
    _, items = convert_messages([{
        "role": "tool", "tool_call_id": "call_review|fc_review",
        "content": [
            {"type": "text", "text": "Source page 181"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ],
    }])
    assert items == [{
        "type": "function_call_output", "call_id": "call_review",
        "output": [
            {"type": "input_text", "text": "Source page 181"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAAA", "detail": "auto"},
        ],
    }]
