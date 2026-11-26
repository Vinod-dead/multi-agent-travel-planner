"""Offline estimator.

Used whenever the language model is unreachable or returns something unusable, so the
application always produces *something* sensible instead of an error.

Important: nothing here knows real prices. Amounts are a suggested split of the user's
own budget, and every section built from it is tagged ``source="estimate"`` so the UI
can say so.
"""

from __future__ import annotations

from dataclasses import dataclass

from travel_planner.models import (
    Destination,
    FlightOption,
    FlightPlan,
    ItineraryDay,
    ItineraryItem,
    ItineraryPlan,
    LodgingOption,
    LodgingPlan,
    TransportItem,
    TransportPlan,
    TripRequest,
)

# Fraction of the budget the estimate aims to spend (the rest is the safety buffer).
_SPEND_RATIO = {"budget": 0.70, "balanced": 0.85, "luxury": 0.95}

# Shares of the spend, with and without flights. Each row sums to 1.0.
_SHARES_WITH_FLIGHTS = {"flights": 0.30, "lodging": 0.30, "food": 0.18, "activities": 0.12, "transport": 0.10}
_SHARES_NO_FLIGHTS = {"flights": 0.0, "lodging": 0.38, "food": 0.24, "activities": 0.20, "transport": 0.18}

_LODGING_NAMES = {
    "budget": ("Guesthouse / hostel", "Budget hotel", "Comfortable 3-star hotel"),
    "balanced": ("Budget hotel", "Comfortable 3-star hotel", "Upscale 4-star hotel"),
    "luxury": ("Comfortable 3-star hotel", "Upscale 4-star hotel", "Luxury hotel / resort"),
}
_RECOMMENDED_TIER = {"budget": 0, "balanced": 1, "luxury": 2}


@dataclass(frozen=True)
class Targets:
    """How a budget is split across trip components (all values are group totals)."""

    flights: float
    lodging: float
    food: float
    activities: float
    transport: float

    @classmethod
    def for_request(cls, req: TripRequest) -> Targets:
        spend = req.budget * _SPEND_RATIO[req.style]
        shares = _SHARES_WITH_FLIGHTS if req.include_flights else _SHARES_NO_FLIGHTS
        return cls(**{k: round(spend * v, 2) for k, v in shares.items()})


# --------------------------------------------------------------------------- destinations
# A tiny, hand-written list used only to pick a destination when the model is offline
# and the user left the field empty (or to give a few real highlights for common places).
_KNOWN = {
    "goa": ("India", "November to February", ["Baga and Calangute beaches", "Old Goa churches", "Fort Aguada", "Dudhsagar Falls"], "beach"),
    "jaipur": ("India", "October to March", ["Amber Fort", "City Palace", "Hawa Mahal", "Johari Bazaar"], "culture"),
    "kerala": ("India", "September to March", ["Alleppey backwaters", "Munnar tea gardens", "Fort Kochi", "Periyar wildlife sanctuary"], "nature"),
    "manali": ("India", "March to June, December to February for snow", ["Solang Valley", "Rohtang Pass", "Hadimba Temple", "Old Manali cafes"], "mountain"),
    "mysuru": ("India", "October to March", ["Mysore Palace", "Chamundi Hill", "Brindavan Gardens", "Devaraja Market"], "culture"),
    "coorg": ("India", "October to March", ["Abbey Falls", "Raja's Seat", "Dubare Elephant Camp", "Coffee plantation walk"], "nature"),
    "bali": ("Indonesia", "April to October", ["Ubud rice terraces", "Uluwatu Temple", "Seminyak beach", "Tegallalang"], "beach"),
    "bangkok": ("Thailand", "November to February", ["Grand Palace", "Wat Pho", "Chatuchak Market", "Chao Phraya river cruise"], "food"),
    "singapore": ("Singapore", "February to April", ["Gardens by the Bay", "Sentosa", "Hawker centres", "Marina Bay"], "city"),
    "dubai": ("United Arab Emirates", "November to March", ["Burj Khalifa", "Dubai Mall", "Desert safari", "Dubai Creek"], "city"),
    "paris": ("France", "April to June, September to October", ["Eiffel Tower", "Louvre Museum", "Montmartre", "Seine river walk"], "culture"),
    "tokyo": ("Japan", "March to May, October to November", ["Senso-ji Temple", "Shibuya Crossing", "Meiji Shrine", "Tsukiji outer market"], "food"),
}

_INDIA_PICKS = {"beach": "goa", "culture": "jaipur", "nature": "kerala", "mountain": "manali", "food": "mysuru", "city": "mysuru"}
_WORLD_PICKS = {"beach": "bali", "culture": "paris", "nature": "bali", "mountain": "bali", "food": "bangkok", "city": "singapore"}
_THEME_WORDS = {
    "beach": ("beach", "sea", "island", "surf", "snorkel", "diving"),
    "mountain": ("mountain", "trek", "hike", "hill", "snow", "adventure"),
    "nature": ("nature", "wildlife", "forest", "backwater", "relax", "plantation"),
    "culture": ("culture", "history", "heritage", "temple", "museum", "architecture"),
    "food": ("food", "cuisine", "street food", "cafe", "restaurant"),
    "city": ("city", "shopping", "nightlife", "urban"),
}


def _pick_theme(req: TripRequest) -> str:
    text = " ".join([*req.interests, req.notes]).lower()
    for theme, words in _THEME_WORDS.items():
        if any(w in text for w in words):
            return theme
    return "culture"


def fallback_destination(req: TripRequest, targets: Targets) -> Destination:
    name = req.destination.strip()
    key = name.lower()
    if not name:
        theme = _pick_theme(req)
        key = (_INDIA_PICKS if req.currency == "INR" else _WORLD_PICKS)[theme]
        name = key.title()
    known = _KNOWN.get(key)

    food = round(targets.food / max(req.travelers * (req.days or 1), 1), 2)
    if known:
        country, best_time, highlights, _ = known
        description = (
            f"{name.title()} is a popular destination in {country}. "
            "This overview comes from the built-in offline guide; connect a language model "
            "for a detailed, personalised plan."
        )
        return Destination(
            name=name.title(), country=country, description=description, best_time=best_time,
            highlights=highlights, local_tips=["Book stays early in peak season.", "Carry some cash for small vendors."],
            food_cost_per_person_per_day=food, source="estimate",
        )
    return Destination(
        name=name.title(),
        description=(
            f"Offline overview for {name.title()}. No language model was available, so there are no "
            "destination-specific facts here - the plan below is a suggested split of your budget."
        ),
        highlights=[], local_tips=["Check opening hours and local holidays before you go."],
        food_cost_per_person_per_day=food, source="estimate",
    )


# --------------------------------------------------------------------------- flights
def fallback_flights(req: TripRequest, targets: Targets) -> FlightPlan:
    if not req.include_flights:
        return FlightPlan(skipped=True, note="Flights were not requested.", source="estimate")
    base = targets.flights / max(req.travelers, 1)
    tiers = (("Low-cost carrier (estimate)", 0.8, 1), ("Full-service carrier (estimate)", 1.0, 1), ("Direct / premium economy (estimate)", 1.25, 0))
    pick = _RECOMMENDED_TIER[req.style]
    options = [
        FlightOption(
            airline=name, origin=req.origin, destination=req.destination, stops=stops,
            price_per_person=round(base * factor, 2), travelers=req.travelers,
            notes="Round-trip estimate. Compare live fares before booking.", recommended=i == pick,
        )
        for i, (name, factor, stops) in enumerate(tiers)
    ]
    return FlightPlan(options=options, note="Estimated fares - not live prices.", source="estimate")


# --------------------------------------------------------------------------- lodging
def fallback_lodging(req: TripRequest, targets: Targets) -> LodgingPlan:
    per_night = targets.lodging / max(req.nights * req.rooms, 1)
    factors = (0.7, 1.0, 1.4)
    pick = _RECOMMENDED_TIER[req.style]
    options = [
        LodgingOption(
            name=name, kind="hotel", area="Central area (suggested)", price_per_night=round(per_night * f, 2),
            nights=req.nights, rooms=req.rooms, why="Estimated price tier for your budget.", recommended=i == pick,
        )
        for i, (name, f) in enumerate(zip(_LODGING_NAMES[req.style], factors, strict=True))
    ]
    return LodgingPlan(options=options, nights=req.nights, rooms=req.rooms, source="estimate")


# --------------------------------------------------------------------------- transport
def fallback_transport(req: TripRequest, targets: Targets) -> TransportPlan:
    items: list[TransportItem] = []
    remaining = targets.transport
    if req.include_flights:
        transfer = round(remaining * 0.25, 2)
        items.append(TransportItem(mode="Airport transfers", description="Taxi or cab to and from the airport.", cost=transfer))
        remaining -= transfer
    items.append(TransportItem(mode="Local transport", description="Cabs, autos or public transport for getting around.", cost=round(remaining, 2)))
    return TransportPlan(items=items, tips=["Use ride-hailing apps or prepaid taxis to avoid overpaying."], source="estimate")


# --------------------------------------------------------------------------- itinerary
_THEME_ACTIVITIES = {
    "beach": ("Relax on the beach", "Water sports or a boat trip", "Sunset walk by the sea"),
    "mountain": ("Easy scenic trek", "Viewpoint visit", "Evening at a local cafe"),
    "nature": ("Nature walk or wildlife visit", "Visit a garden or waterfall", "Sunset viewpoint"),
    "culture": ("Guided heritage walk", "Visit a museum or palace", "Local market stroll"),
    "food": ("Street-food tour", "Cooking class or food market", "Dinner at a well-reviewed local restaurant"),
    "city": ("Landmark and skyline tour", "Shopping district", "Evening entertainment"),
}


def fallback_itinerary(req: TripRequest, destination: Destination, targets: Targets) -> ItineraryPlan:
    days = req.days or 1
    theme = _pick_theme(req)
    acts = _THEME_ACTIVITIES[theme]
    highlights = destination.highlights or []
    place = destination.name or "your destination"
    paid_per_day = round(targets.activities / days, 2)
    out: list[ItineraryDay] = []

    for d in range(1, days + 1):
        highlight = highlights[(d - 1) % len(highlights)] if highlights else f"a top-rated sight in {place}"
        items: list[ItineraryItem] = []
        if d == 1:
            items.append(ItineraryItem(time="Morning", title=f"Travel to {place}", description="Arrive, check in and rest.", category="transport"))
        items.append(ItineraryItem(time="Morning" if d > 1 else "Afternoon", title=f"Visit {highlight}", description="Plan around opening hours and crowds.", category="sightseeing", cost=round(paid_per_day * 0.6, 2)))
        items.append(ItineraryItem(time="Afternoon", title=acts[(d - 1) % len(acts)], description="Choose what fits your energy and the weather.", category="activity", cost=round(paid_per_day * 0.4, 2)))
        items.append(ItineraryItem(time="Evening", title="Dinner at a local favourite", description="Ask your host for a recommendation.", category="food"))
        if d == days and days > 1:
            items.append(ItineraryItem(time="Late", title=f"Depart from {place}", description="Leave time for the journey home.", category="transport"))
        out.append(ItineraryDay(day=d, date=req.day_date(d), title=highlight.capitalize() if highlights else "Explore the area", items=items))
    return ItineraryPlan(days=out, source="estimate")
