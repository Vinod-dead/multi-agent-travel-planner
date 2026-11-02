"""Activity planning agent: builds the day-by-day itinerary in small batches.

Small local models handle 2-3 days per request far better than 10+, so the trip is planned
in batches and each batch is told what earlier batches already covered.
"""

from __future__ import annotations

from travel_planner.agents.base import Agent, AgentFallback, PlanContext, system_prompt, trip_brief, unwrap
from travel_planner.fallback import fallback_itinerary
from travel_planner.models import ItineraryDay, ItineraryPlan

BATCH_DAYS = 3
MAX_ITEMS_PER_DAY = 7
_CATEGORIES = {"sightseeing", "food", "activity", "transport", "relax", "shopping", "culture", "nature"}

_EXAMPLE = (
    '{"days": [{"day": 1, "title": "short theme for the day", "items": ['
    '{"time": "09:00", "title": "Visit Fort Aguada", "description": "one sentence", '
    '"category": "sightseeing", "cost": 600}, '
    '{"time": "13:00", "title": "Lunch at a beach shack", "description": "one sentence", '
    '"category": "food", "cost": 0}]}]}'
)


class ActivityAgent(Agent):
    key = "activities"
    label = "Activity planning"

    async def generate(self, ctx: PlanContext) -> ItineraryPlan:
        req = ctx.request
        dest = ctx.destination.name if ctx.destination else req.destination
        highlights = ", ".join(ctx.destination.highlights) if ctx.destination and ctx.destination.highlights else "none known"
        total_days = req.days or 1
        planned: list[ItineraryDay] = []
        ai_days = 0
        fallback_pool = fallback_itinerary(req, ctx.destination or _blank(dest), ctx.targets).days

        for start in range(1, total_days + 1, BATCH_DAYS):
            numbers = list(range(start, min(start + BATCH_DAYS, total_days + 1)))
            try:
                days = await self._plan_batch(ctx, dest, highlights, numbers, planned)
                ai_days += len(days)
                planned.extend(days)
            except AgentFallback as exc:
                if not planned:
                    raise  # first batch failed: do not waste minutes retrying the rest
                ctx.warn(f"Activity planning: days {numbers[0]}-{numbers[-1]} use an offline template ({exc}).")
                planned.extend(d for d in fallback_pool if d.day in numbers)

        return ItineraryPlan(days=sorted(planned, key=lambda d: d.day), source="ai")

    async def _plan_batch(self, ctx, dest, highlights, numbers, planned) -> list[ItineraryDay]:
        req = ctx.request
        total_days = req.days or 1
        listing = "\n".join(f"- Day {n}" + (f" ({req.day_date(n)})" if req.day_date(n) else "") for n in numbers)
        covered = "; ".join(i.title for d in planned for i in d.items if i.category != "food")[:600] or "nothing yet"
        extras = []
        if 1 in numbers:
            extras.append("Day 1 starts with arrival, so keep it light.")
        if total_days in numbers and total_days > 1:
            extras.append(f"Day {total_days} is the departure day, so keep it short.")
        user = (
            f"Plan these days in {dest}:\n{listing}\n\n"
            f"{trip_brief(req, dest)}\n"
            f"Known highlights: {highlights}\n"
            f"Already planned (do not repeat): {covered}\n"
            f"{' '.join(extras)}\n"
            f"Give 3-5 items per day with a time, title, one-sentence description and category "
            f"({', '.join(sorted(_CATEGORIES))}). cost is the TOTAL for the group in {req.currency} "
            "for tickets/fees only; use 0 for free things and for meals (meals are budgeted "
            f"separately).\nExample shape:\n{_EXAMPLE}"
        )
        data = await ctx.ask(system_prompt("activity and itinerary planner", req), user, max_tokens=2200)
        if isinstance(data, list):
            data = {"days": data}
        plan = ItineraryPlan.model_validate(unwrap(data, "itinerary"))
        raw = [d for d in plan.days if d.items]
        if not raw:
            raise AgentFallback("no days in the model's answer")

        by_number = {d.day: d for d in raw}
        result: list[ItineraryDay] = []
        for position, number in enumerate(numbers):
            day = by_number.get(number) or (raw[position] if len(raw) == len(numbers) else None)
            if day is None:
                continue
            day.day = number
            day.date = req.day_date(number)
            day.title = day.title or f"Day {number}"
            day.items = day.items[:MAX_ITEMS_PER_DAY]
            for item in day.items:
                item.category = item.category.lower() if item.category.lower() in _CATEGORIES else "activity"
                if not 0 <= item.cost <= req.budget:
                    item.cost = 0.0
                if item.category == "food":
                    item.cost = 0.0  # meals are budgeted separately
            result.append(day)
        if not result:
            raise AgentFallback("the days in the answer did not match the request")
        return result

    def estimate(self, ctx: PlanContext) -> ItineraryPlan:
        dest = ctx.destination or _blank(ctx.request.destination)
        return fallback_itinerary(ctx.request, dest, ctx.targets)


def _blank(name: str):
    from travel_planner.models import Destination

    return Destination(name=name, source="estimate")
