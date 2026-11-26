import pytest

from travel_planner.llm import LLMBadOutput, extract_json


def test_plain_json():
    assert extract_json('{"a": 1}') == {"a": 1}


def test_markdown_fence_and_chatter():
    text = 'Sure! Here you go:\n```json\n{"a": [1, 2]}\n```\nAnything else?'
    assert extract_json(text) == {"a": [1, 2]}


def test_text_around_json_without_fence():
    assert extract_json('The answer is {"x": "y"} - hope it helps') == {"x": "y"}


def test_trailing_commas_and_smart_quotes():
    assert extract_json('{"a": [1, 2,], "b": "t",}') == {"a": [1, 2], "b": "t"}
    assert extract_json("{“a”: 1}") == {"a": 1}


def test_python_literals():
    assert extract_json('{"ok": True, "none": None, "no": False}') == {"ok": True, "none": None, "no": False}


def test_think_block_is_ignored():
    assert extract_json('<think>maybe {"wrong": 1}</think>{"right": 2}') == {"right": 2}


def test_braces_inside_strings_do_not_confuse_the_scanner():
    assert extract_json('{"a": "a } tricky { string", "b": 1}') == {"a": "a } tricky { string", "b": 1}


def test_truncated_json_is_repaired():
    cut = '{"days": [{"day": 1, "title": "One"}, {"day": 2, "title": "Tw'
    data = extract_json(cut)
    assert data["days"][0] == {"day": 1, "title": "One"}


def test_truncated_in_the_middle_of_a_number_or_key():
    assert extract_json('{"a": 1, "b": [1, 2, 3')["a"] == 1
    assert extract_json('{"a": 1, "bb')["a"] == 1


def test_top_level_list():
    assert extract_json('[{"a": 1}, {"a": 2}]') == [{"a": 1}, {"a": 2}]


@pytest.mark.parametrize("bad", ["", "   ", None, "no json here at all", "I'm sorry, I can't do that."])
def test_unusable_replies_raise(bad):
    with pytest.raises(LLMBadOutput):
        extract_json(bad)
