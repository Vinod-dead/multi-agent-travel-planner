import pytest

from travel_planner.llm import LLMUnavailable
from travel_planner.models import TripPlan, TripRequest
from travel_planner.planner import plan_trip

REQ = dict(origin="Bengaluru", destination="Goa", budget=60000, days=5, travelers=2, interests=["beaches"])


def check_consistent(plan: TripPlan):
    b = plan.budget
    assert b.estimated_total == pytest.approx(sum(line.amount for line in plan.budget.breakdown), abs=0.05)
    assert [d.day for d in plan.itinerary.days] == list(range(1, plan.request.days + 1))
    assert sum(o.recommended for o in plan.lodging.options) == 1
    if not plan.flights.skipped:
        assert sum(o.recommended for o in plan.flights.options) == 1
    assert plan.destination.name


async def test_clean_model_output(make_llm):
    llm, fake = make_llm("clean")
    events = []
    plan = await plan_trip(TripRequest(**REQ), llm, on_event=lambda *e: events.append(e))
    assert plan.mode == "ai" and plan.model == "fake" and not plan.warnings
    assert plan.destination.source == "ai" and plan.destination.highlights
    check_consistent(plan)
    assert {e[0] for e in events} >= {"planner", "research", "flights", "lodging", "transport", "activities", "budget"}
    assert len(fake.state.calls) >= 5  # research, flights, lodging, transport, 2 itinerary batches


async def test_messy_output_is_repaired_not_replaced(make_llm):
    llm, _ = make_llm("messy")
    plan = await plan_trip(TripRequest(**REQ), llm)
    assert plan.mode == "ai"  # fences, trailing commas and "₹7200" strings all handled
    assert plan.flights.options[0].price_per_person > 1000
    check_consistent(plan)


async def test_truncated_output_still_yields_a_plan(make_llm):
    llm, _ = make_llm("cut")
    plan = await plan_trip(TripRequest(**REQ), llm)
    check_consistent(plan)


@pytest.mark.parametrize("mode", ["garbage", "empty"])
async def test_unusable_output_falls_back_per_agent(make_llm, mode):
    llm, _ = make_llm(mode)
    plan = await plan_trip(TripRequest(**REQ), llm)
    assert plan.mode == "estimate" and plan.model == ""
    assert plan.warnings and all(s == "estimate" for s in plan.sections().values())
    check_consistent(plan)


@pytest.mark.parametrize("mode", ["down", "missing"])
async def test_unavailable_model_gives_estimate_and_one_clear_warning(make_llm, mode, monkeypatch):
    monkeypatch.setenv("LLM_RETRIES", "0")
    from travel_planner.config import get_settings

    get_settings.cache_clear()
    llm, fake = make_llm(mode)
    llm.settings = get_settings()
    plan = await plan_trip(TripRequest(**REQ), llm)
    assert plan.mode == "estimate"
    assert len(plan.warnings) == 1 and "offline estimates" in plan.warnings[0]
    check_consistent(plan)


async def test_llm_disabled_never_calls_the_server(make_llm, monkeypatch):
    monkeypatch.setenv("LLM_ENABLED", "false")
    from travel_planner.config import get_settings

    get_settings.cache_clear()
    llm, fake = make_llm("clean")
    llm.settings = get_settings()
    plan = await plan_trip(TripRequest(**REQ), llm)
    assert plan.mode == "estimate" and fake.state.calls == []


async def test_no_origin_skips_flights(make_llm):
    llm, _ = make_llm("clean")
    plan = await plan_trip(TripRequest(**{**REQ, "origin": ""}), llm)
    assert plan.flights.skipped and "departure city" in plan.flights.note
    assert plan.mode == "ai"  # a skipped section does not downgrade the mode
    assert all(line.category != "Flights" for line in plan.budget.breakdown)


async def test_surprise_me_lets_the_model_choose(make_llm):
    llm, _ = make_llm("clean")
    plan = await plan_trip(TripRequest(**{**REQ, "destination": ""}), llm)
    assert plan.destination.name and plan.mode == "ai"


async def test_long_trip_is_planned_in_batches(make_llm):
    llm, fake = make_llm("clean")
    plan = await plan_trip(TripRequest(**{**REQ, "days": 8}), llm)
    itinerary_calls = [c for c in fake.state.calls if "itinerary planner" in c["messages"][0]["content"]]
    assert len(itinerary_calls) == 3  # 3 + 3 + 2 days
    check_consistent(plan)


async def test_model_override_is_sent_to_the_server(make_llm):
    llm, fake = make_llm("clean")
    await plan_trip(TripRequest(**REQ, model="my-model"), llm)
    assert {c["model"] for c in fake.state.calls} == {"my-model"}


async def test_unrealistic_prices_are_rejected(make_llm, monkeypatch):
    import tests.fake_llm as fl

    original = fl.build_reply

    def silly(system, user):
        data = original(system, user)
        if "flight search" in system:
            for o in data["options"]:
                o["price_per_person"] = 10**9
        return data

    monkeypatch.setattr(fl, "build_reply", silly)
    llm, _ = make_llm("clean")
    plan = await plan_trip(TripRequest(**REQ), llm)
    assert plan.flights.source == "estimate" and plan.mode == "mixed"


async def test_ping_reports_models_and_errors(make_llm):
    llm, _ = make_llm("clean")
    assert (await llm.ping(use_cache=False)) == {"ok": True, "models": ["fake"], "error": ""}


async def test_chat_maps_missing_model_to_unavailable(make_llm):
    llm, _ = make_llm("missing")
    with pytest.raises(LLMUnavailable, match="ollama pull"):
        await llm.chat([{"role": "user", "content": "hi"}])
