"""Local transportation agent."""

from __future__ import annotations

from travel_planner.agents.base import Agent, AgentFallback, PlanContext, system_prompt, trip_brief, unwrap
from travel_planner.fallback import fallback_transport
from travel_planner.models import TransportPlan

_EXAMPLE = (
    '{"items": [{"mode": "Airport transfer", "description": "prepaid taxi both ways", "cost": 1800}, '
    '{"mode": "Local transport", "description": "scooter rental or cabs", "cost": 4000}], '
    '"tips": ["practical tip"]}'
)


class TransportAgent(Agent):
    key = "transport"
    label = "Local transport"

    async def generate(self, ctx: PlanContext) -> TransportPlan:
        req = ctx.request
        dest = ctx.destination.name if ctx.destination else req.destination
        flights_note = "including airport transfers" if req.include_flights and req.origin else "without flights"
        user = (
            f"List 2-4 transport needs for getting around {dest} during this trip ({flights_note}). "
            f"cost is the TOTAL for the whole group for the whole trip, in {req.currency}. Add 2-3 "
            f"short money-saving tips.\n\n{trip_brief(req, dest)}\n\nExample shape:\n{_EXAMPLE}"
        )
        data = await ctx.ask(system_prompt("local transport planner", req), user, max_tokens=900)
        if isinstance(data, list):
            data = {"items": data}
        plan = TransportPlan.model_validate(unwrap(data, "transport"))
        plan.items = [i for i in plan.items if i.mode and 0 <= i.cost <= req.budget][:6]
        if not plan.items or plan.total <= 0:
            raise AgentFallback("the model gave no usable transport costs")
        plan.source = "ai"
        return plan

    def estimate(self, ctx: PlanContext) -> TransportPlan:
        return fallback_transport(ctx.request, ctx.targets)
