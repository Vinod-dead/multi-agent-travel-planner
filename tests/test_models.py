from datetime import date

import pytest
from pydantic import ValidationError

from travel_planner.models import Destination, FlightOption, ItineraryDay, LodgingPlan, TripRequest


def test_request_from_dates():
    r = TripRequest(budget=1000, start_date=date(2026, 12, 1), end_date=date(2026, 12, 5))
    assert (r.days, r.nights) == (5, 4)
    assert r.day_date(2) == "2026-12-02"


def test_request_from_days_and_start():
    r = TripRequest(budget=1000, start_date=date(2026, 12, 1), days=3)
    assert r.end_date == date(2026, 12, 3)


def test_request_defaults_to_five_days_and_rooms():
    r = TripRequest(budget=1000, travelers=3)
    assert r.days == 5 and r.rooms == 2


@pytest.mark.parametrize(
    "bad",
    [
        {"budget": 0},
        {"budget": -5},
        {"budget": 100, "travelers": 0},
        {"budget": 100, "travelers": 99},
        {"budget": 100, "currency": "RUPEES"},
        {"budget": 100, "currency": "12$"},
        {"budget": 100, "start_date": date(2026, 12, 5), "end_date": date(2026, 12, 1)},
        {"budget": 100, "days": 500},
    ],
)
def test_invalid_requests_are_rejected(bad):
    with pytest.raises(ValidationError):
        TripRequest(**bad)


def test_interests_are_cleaned_and_deduplicated():
    r = TripRequest(budget=10, interests=["  Food ", "food", "", "Beaches"])
    assert r.interests == ["Food", "Beaches"]


def test_messy_model_numbers_are_understood():
    f = FlightOption(price_per_person="₹ 12,500", stops="1", recommended="yes", travelers=2)
    assert f.price_per_person == 12500 and f.stops == 1 and f.recommended is True
    assert f.total_price == 25000
    assert FlightOption(price_per_person="about 1.5k").price_per_person == 1500
    assert FlightOption(price_per_person=None).price_per_person == 0
    assert FlightOption(price_per_person="free").price_per_person == 0


def test_messy_lists_and_nulls():
    d = Destination(name=None, highlights="Fort; Beach\n- Market", local_tips=None)
    assert d.name == "" and d.highlights == ["Fort", "Beach", "Market"] and d.local_tips == []
    assert Destination(highlights=[{"name": "Fort"}, "Beach", 7]).highlights == ["Fort", "Beach", "7"]


def test_single_object_instead_of_list():
    plan = LodgingPlan.model_validate({"options": {"name": "Only one", "price_per_night": 10}})
    assert len(plan.options) == 1


def test_day_cost_is_computed():
    day = ItineraryDay.model_validate({"day": "2", "items": [{"title": "a", "cost": "100"}, {"title": "b", "cost": 50}]})
    assert day.day == 2 and day.day_cost == 150


def test_unknown_fields_are_ignored():
    assert Destination.model_validate({"name": "X", "surprise": [1, 2]}).name == "X"
