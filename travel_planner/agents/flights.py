"""Flight agent: suggests realistic fare options (estimates, never live prices)."""

from __future__ import annotations

from travel_planner.agents.base import Agent, AgentFallback, PlanContext, pick_recommended, system_prompt, trip_brief, unwrap
from travel_planner.fallback import fallback_flights
from travel_planner.models import FlightPlan

_EXAMPLE = (
    '{"options": [{"airline": "IndiGo", "origin": "Delhi", "destination": "Goa", '
    '"depart": "06:10", "arrive": "09:05", "duration": "2h 55m", "stops": 0, '
    '"price_per_person": 9500, "notes": "round trip, cabin bag only", "recommended": true}], '
    '"note": "one short sentence"}'
)


class FlightAgent(Agent):
    key = "flights"
    label = "Flight search"

    def _skip_reason(self, ctx: PlanContext) -> str:
        req = ctx.request
        dest = ctx.destination.name if ctx.destination else req.destination
        if not req.include_flights:
            return "Flights were not requested."
        if not req.origin:
            return "Add a departure city to get flight estimates."
        if dest and req.origin.strip().lower() == dest.strip().lower():
            return "Origin and destination are the same, so no flight is needed."
        return ""

    async def generate(self, ctx: PlanContext) -> FlightPlan:
        req = ctx.request
        reason = self._skip_reason(ctx)
        if reason:
            return FlightPlan(skipped=True, note=reason, source="ai" if ctx.llm_ok else "estimate")

        dest = ctx.destination.name if ctx.destination else req.destination
        user = (
            f"Suggest 3 round-trip flight options (different price/duration trade-offs) from "
            f"{req.origin} to {dest} for {req.travelers} traveller(s). Mark exactly one as "
            "recommended for the traveller's style. price_per_person is the round-trip fare per "
            f"person in {req.currency}. These are estimates, not live fares.\n\n{trip_brief(req, dest)}\n\n"
            f"Example shape:\n{_EXAMPLE}"
        )
        data = await ctx.ask(system_prompt("flight search specialist", req), user, max_tokens=1200)
        if isinstance(data, list):
            data = {"options": data}
        plan = FlightPlan.model_validate(unwrap(data, "flights"))

        options = [o for o in plan.options if 0 < o.price_per_person * req.travelers <= req.budget * 10][:5]
        if not options:
            raise AgentFallback("the model gave no usable fare estimates")
        for o in options:
            o.travelers = req.travelers
            o.origin = o.origin or req.origin
            o.destination = o.destination or dest
        pick_recommended(options, req.style, lambda o: o.price_per_person)
        plan.options = options
        plan.note = plan.note or "Estimated fares - check live prices before booking."
        plan.source = "ai"
        return plan

    def estimate(self, ctx: PlanContext) -> FlightPlan:
        reason = self._skip_reason(ctx)
        if reason:
            return FlightPlan(skipped=True, note=reason, source="estimate")
        return fallback_flights(ctx.request.model_copy(update={"destination": ctx.destination.name if ctx.destination else ctx.request.destination}), ctx.targets)
