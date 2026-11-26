"""Accommodation agent."""

from __future__ import annotations

from travel_planner.agents.base import Agent, AgentFallback, PlanContext, pick_recommended, system_prompt, trip_brief, unwrap
from travel_planner.fallback import fallback_lodging
from travel_planner.models import LodgingPlan

_EXAMPLE = (
    '{"options": [{"name": "Mid-range hotel near Baga Beach", "kind": "hotel", "area": "Baga", '
    '"price_per_night": 3500, "rating": 4.1, "amenities": ["wifi", "breakfast", "pool"], '
    '"why": "one short reason", "recommended": true}]}'
)


class LodgingAgent(Agent):
    key = "lodging"
    label = "Accommodation"

    async def generate(self, ctx: PlanContext) -> LodgingPlan:
        req = ctx.request
        dest = ctx.destination.name if ctx.destination else req.destination
        user = (
            f"Suggest 3 places to stay in {dest} at different price levels for {req.nights} night(s). "
            f"The group needs {req.rooms} room(s). price_per_night is the price of ONE room per night "
            f"in {req.currency}. Mark exactly one as recommended for the traveller's style. Only use a "
            "real property name if you are confident it exists; otherwise describe the type and area "
            "(for example 'Mid-range hotel near the old town').\n\n"
            f"{trip_brief(req, dest)}\n\nExample shape:\n{_EXAMPLE}"
        )
        data = await ctx.ask(system_prompt("accommodation specialist", req), user, max_tokens=1400)
        if isinstance(data, list):
            data = {"options": data}
        plan = LodgingPlan.model_validate(unwrap(data, "lodging", "accommodation"))

        options = [o for o in plan.options if 0 < o.price_per_night * req.nights * req.rooms <= req.budget * 10][:5]
        if not options:
            raise AgentFallback("the model gave no usable accommodation prices")
        for o in options:
            o.nights, o.rooms = req.nights, req.rooms
            if o.rating > 5:
                o.rating = round(o.rating / 2, 1) if o.rating <= 10 else 0.0
            o.rating = max(0.0, min(o.rating, 5.0))
        pick_recommended(options, req.style, lambda o: o.price_per_night)
        plan.options, plan.nights, plan.rooms, plan.source = options, req.nights, req.rooms, "ai"
        return plan

    def estimate(self, ctx: PlanContext) -> LodgingPlan:
        return fallback_lodging(ctx.request, ctx.targets)
