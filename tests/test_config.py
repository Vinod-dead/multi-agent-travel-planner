import pytest

from travel_planner.config import get_settings
from travel_planner.models import TripRequest
from travel_planner.planner import plan_trip


def test_extra_body_is_parsed(monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    get_settings.cache_clear()
    assert get_settings().llm_extra_body == {"chat_template_kwargs": {"enable_thinking": False}}


@pytest.mark.parametrize("bad", ["{not json", "[1, 2]", '"text"'])
def test_bad_extra_body_gives_a_clear_error(monkeypatch, bad):
    monkeypatch.setenv("LLM_EXTRA_BODY", bad)
    get_settings.cache_clear()
    with pytest.raises(ValueError, match="LLM_EXTRA_BODY"):
        get_settings()


async def test_extra_body_reaches_the_server(make_llm, monkeypatch):
    monkeypatch.setenv("LLM_EXTRA_BODY", '{"chat_template_kwargs": {"enable_thinking": false}}')
    get_settings.cache_clear()
    llm, fake = make_llm("clean")
    llm.settings = get_settings()
    await plan_trip(TripRequest(origin="Pune", destination="Goa", budget=50000, days=3), llm)
    assert fake.state.calls and all(c.get("chat_template_kwargs") == {"enable_thinking": False} for c in fake.state.calls)
