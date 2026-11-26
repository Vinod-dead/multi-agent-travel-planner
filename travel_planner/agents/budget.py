"""Budget agent.

Deliberately *not* a language-model call: adding up costs and comparing them with the
budget is arithmetic, and models get arithmetic wrong. The agent also tries to bring an
over-budget plan back within budget by switching to cheaper options.
"""

from __future__ import annotations

from travel_planner.agents.base import PlanContext
from travel_planner.models import (
    BudgetLine,
    BudgetSummary,
    Destination,
    FlightPlan,
    ItineraryPlan,
    LodgingPlan,
    TransportPlan,
    TripRequest,
)

CONTINGENCY_RATE = 0.05


def _select_cheapest(options, price) -> None:
    if not options:
        return
    cheapest = min(options, key=price)
    for o in options:
        o.recommended = o is cheapest


def _lines(req: TripRequest, dest: Destination, flights: FlightPlan, lodging: LodgingPlan,
           transport: TransportPlan, itinerary: ItineraryPlan) -> list[tuple[str, float]]:
    flight = flights.selected
    stay = lodging.selected
    food = dest.food_cost_per_person_per_day * req.travelers * (req.days or 1)
    return [
        ("Flights", 0.0 if flights.skipped or not flight else flight.total_price),
        ("Accommodation", stay.total_price if stay else 0.0),
        ("Local transport", transport.total),
        ("Food", round(food, 2)),
        ("Activities", itinerary.total),
    ]


def build_budget(req: TripRequest, dest: Destination, flights: FlightPlan, lodging: LodgingPlan,
                 transport: TransportPlan, itinerary: ItineraryPlan) -> BudgetSummary:
    tips: list[str] = []

    def total_of(lines: list[tuple[str, float]]) -> float:
        subtotal = sum(v for _, v in lines)
        return subtotal * (1 + CONTINGENCY_RATE)

    lines = _lines(req, dest, flights, lodging, transport, itinerary)

    if total_of(lines) > req.budget:
        _select_cheapest(lodging.options, lambda o: o.price_per_night)
        lines = _lines(req, dest, flights, lodging, transport, itinerary)
        if total_of(lines) <= req.budget:
            tips.append("Switched to the cheapest accommodation option to fit your budget.")
    if total_of(lines) > req.budget and not flights.skipped:
        _select_cheapest(flights.options, lambda o: o.price_per_person)
        lines = _lines(req, dest, flights, lodging, transport, itinerary)
        if total_of(lines) <= req.budget:
            tips.append("Switched to the cheapest accommodation and flight options to fit your budget.")

    subtotal = round(sum(v for _, v in lines), 2)
    contingency = round(subtotal * CONTINGENCY_RATE, 2)
    total = round(subtotal + contingency, 2)
    breakdown = [BudgetLine(category=name, amount=round(v, 2)) for name, v in lines if v > 0]
    breakdown.append(BudgetLine(category="Buffer (5%)", amount=contingency))
    for line in breakdown:
        line.percent = round(100 * line.amount / total, 1) if total else 0.0

    remaining = round(req.budget - total, 2)
    status = "within" if remaining >= 0 else "over"
    if status == "over":
        biggest = max(breakdown[:-1], key=lambda b: b.amount, default=None)
        tips.append(f"This plan is {abs(remaining):,.0f} {req.currency} over budget even with the cheapest options.")
        if biggest:
            tips.append(f"{biggest.category} is the biggest cost - reducing it will help most.")
        tips.append("Shorter trip, fewer travellers per room, off-season dates or a closer destination all lower the total.")
    elif remaining > req.budget * 0.3:
        tips.append(f"You have about {remaining:,.0f} {req.currency} to spare - room for a nicer stay or extra activities.")
    return BudgetSummary(
        currency=req.currency, total_budget=req.budget, estimated_total=total, remaining=remaining,
        status=status, breakdown=breakdown, tips=tips,
    )


class BudgetAgent:
    key = "budget"
    label = "Budget check"

    async def run(self, ctx: PlanContext, dest: Destination, flights: FlightPlan, lodging: LodgingPlan,
                  transport: TransportPlan, itinerary: ItineraryPlan) -> BudgetSummary:
        ctx.emit(self.key, "running", "Adding up costs...")
        summary = build_budget(ctx.request, dest, flights, lodging, transport, itinerary)
        ctx.emit(self.key, "done", "Done")
        return summary
