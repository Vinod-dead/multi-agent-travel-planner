import pytest

from travel_planner.agents.budget import build_budget
from travel_planner.fallback import (
    Targets,
    fallback_destination,
    fallback_flights,
    fallback_itinerary,
    fallback_lodging,
    fallback_transport,
)
from travel_planner.models import TripRequest


def parts(req):
    t = Targets.for_request(req)
    dest = fallback_destination(req, t)
    return dest, fallback_flights(req, t), fallback_lodging(req, t), fallback_transport(req, t), fallback_itinerary(req, dest, t)


@pytest.mark.parametrize("style", ["budget", "balanced", "luxury"])
@pytest.mark.parametrize("flights", [True, False])
def test_estimates_fit_the_budget_and_add_up(style, flights):
    req = TripRequest(origin="Pune", destination="Goa", budget=80000, days=6, travelers=3, style=style, include_flights=flights)
    b = build_budget(req, *parts(req))
    assert b.status == "within"
    assert b.estimated_total <= req.budget
    assert b.estimated_total == pytest.approx(sum(line.amount for line in b.breakdown), abs=0.05)
    assert sum(line.percent for line in b.breakdown) == pytest.approx(100, abs=1)
    assert b.remaining == pytest.approx(req.budget - b.estimated_total, abs=0.05)
    if not flights:
        assert all(line.category != "Flights" for line in b.breakdown)


def test_exactly_one_recommended_option():
    req = TripRequest(origin="Pune", destination="Goa", budget=50000)
    _, flights, lodging, *_ = parts(req)
    assert sum(o.recommended for o in flights.options) == 1
    assert sum(o.recommended for o in lodging.options) == 1


def test_over_budget_plan_switches_to_cheapest_options_then_reports_overrun():
    req = TripRequest(origin="Pune", destination="Goa", budget=1000, days=5, travelers=2)
    t = Targets.for_request(TripRequest(origin="Pune", destination="Goa", budget=100000, days=5, travelers=2))
    dest = fallback_destination(req, t)
    flights, lodging = fallback_flights(req, t), fallback_lodging(req, t)
    transport, itinerary = fallback_transport(req, t), fallback_itinerary(req, dest, t)
    b = build_budget(req, dest, flights, lodging, transport, itinerary)
    assert b.status == "over" and b.remaining < 0
    assert [o for o in lodging.options if o.recommended][0].price_per_night == min(o.price_per_night for o in lodging.options)
    assert [o for o in flights.options if o.recommended][0].price_per_person == min(o.price_per_person for o in flights.options)
    assert any("over budget" in tip for tip in b.tips)


def test_surprise_me_picks_a_destination_offline():
    req = TripRequest(budget=50000, interests=["beaches"])
    assert fallback_destination(req, Targets.for_request(req)).name == "Goa"
    world = TripRequest(budget=2000, currency="USD", interests=["culture"])
    assert fallback_destination(world, Targets.for_request(world)).name == "Paris"


def test_itinerary_has_every_day():
    req = TripRequest(destination="Atlantis", budget=50000, days=9)
    days = fallback_itinerary(req, fallback_destination(req, Targets.for_request(req)), Targets.for_request(req)).days
    assert [d.day for d in days] == list(range(1, 10))
