import pytest

from travel_planner.llm import LLMBadOutput


async def test_thinking_model_empty_reply_is_explained(make_llm):
    llm, _ = make_llm("thinking")
    with pytest.raises(LLMBadOutput) as info:
        await llm.chat([{"role": "user", "content": "hi"}])
    message = str(info.value)
    assert "finish_reason=length" in message and "enable_thinking" in message


async def test_plain_empty_reply_is_still_reported(make_llm):
    llm, _ = make_llm("empty")
    with pytest.raises(LLMBadOutput, match="empty reply"):
        await llm.chat([{"role": "user", "content": "hi"}])
