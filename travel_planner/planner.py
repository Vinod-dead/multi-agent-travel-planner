"""LangGraph orchestration: research first, then four agents in parallel, then the budget.

    START -> destination_research -+-> flight_search --------+
                                   +-> accommodation_search -+
                                   +-> transport_planning ---+--> budget_check -> END
                                   +-> activity_planning ----+
"""

from __future__ import annotations

import logging
import time
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from travel_planner.agents import (
    ActivityAgent,
    BudgetAgent,
    DestinationAgent,
    FlightAgent,
    LodgingAgent,
    PlanContext,
    TransportAgent,
)
from travel_planner.agents.base import EventSink, sentence
from travel_planner.config import Settings, get_settings
from travel_planner.fallback import Targets
from travel_planner.llm import LLMClient
from travel_planner.models import (
    BudgetSummary,
    Destination,
    FlightPlan,
    ItineraryPlan,
    LodgingPlan,
    TransportPlan,
    TripPlan,
    TripRequest,
)

log = logging.getLogger(__name__)

# Order shown in the UI.
STEPS = [
    ("planner", "Preparing"),
    ("research", "Destination research"),
    ("flights", "Flight search"),
    ("lodging", "Accommodation"),
    ("transport", "Local transport"),
    ("activities", "Activity planning"),
    ("budget", "Budget check"),
]


class PlanState(TypedDict, total=False):
    destination: Destination
    flights: FlightPlan
    lodging: LodgingPlan
    transport: TransportPlan
    itinerary: ItineraryPlan
    budget: BudgetSummary


def build_graph(ctx: PlanContext):
    """Compile the agent graph for one plan (agents share ``ctx``)."""
    research, flights, lodging = DestinationAgent(), FlightAgent(), LodgingAgent()
    transport, activities, budget = TransportAgent(), ActivityAgent(), BudgetAgent()

    async def n_research(_: PlanState) -> dict[str, Any]:
        return {"destination": await research.run(ctx)}

    async def n_flights(_: PlanState) -> dict[str, Any]:
        return {"flights": await flights.run(ctx)}

    async def n_lodging(_: PlanState) -> dict[str, Any]:
        return {"lodging": await lodging.run(ctx)}

    async def n_transport(_: PlanState) -> dict[str, Any]:
        return {"transport": await transport.run(ctx)}

    async def n_activities(_: PlanState) -> dict[str, Any]:
        return {"itinerary": await activities.run(ctx)}

    async def n_budget(state: PlanState) -> dict[str, Any]:
        summary = await budget.run(
            ctx, state["destination"], state["flights"], state["lodging"], state["transport"], state["itinerary"]
        )
        return {"budget": summary}

    graph = StateGraph(PlanState)
    graph.add_node("destination_research", n_research)
    graph.add_node("flight_search", n_flights)
    graph.add_node("accommodation_search", n_lodging)
    graph.add_node("transport_planning", n_transport)
    graph.add_node("activity_planning", n_activities)
    graph.add_node("budget_check", n_budget)

    graph.add_edge(START, "destination_research")
    parallel = ["flight_search", "accommodation_search", "transport_planning", "activity_planning"]
    for node in parallel:
        graph.add_edge("destination_research", node)
    graph.add_edge(parallel, "budget_check")
    graph.add_edge("budget_check", END)
    return graph.compile()


def _mode(plan_sections: list[tuple[str, bool]]) -> str:
    sources = {src for src, skipped in plan_sections if not skipped}
    if sources == {"ai"}:
        return "ai"
    if sources == {"estimate"}:
        return "estimate"
    return "mixed"


async def plan_trip(
    request: TripRequest,
    llm: LLMClient,
    *,
    settings: Settings | None = None,
    on_event: EventSink | None = None,
    plan_id: str = "",
) -> TripPlan:
    """Run the whole multi-agent workflow and return the finished plan. Never raises for
    model problems: those degrade to offline estimates and are listed in ``warnings``."""
    started = time.monotonic()
    settings = settings or llm.settings or get_settings()
    model = request.model or settings.llm_model
    ctx = PlanContext(
        request=request, llm=llm, settings=settings, targets=Targets.for_request(request),
        model=model, sink=on_event,
    )

    ctx.emit("planner", "running", "Checking the language model...")
    health = await llm.ping()
    if not health["ok"]:
        ctx.llm_ok = False
        ctx.warn(sentence(health["error"]) + " Showing offline estimates instead.")
        ctx.emit("planner", "done", "Language model unavailable - using offline estimates")
    else:
        ctx.emit("planner", "done", f"Using {model}")

    state: PlanState = await build_graph(ctx).ainvoke({})

    flights = state["flights"]
    sections = [
        (state["destination"].source, False),
        (flights.source, flights.skipped),
        (state["lodging"].source, False),
        (state["transport"].source, False),
        (state["itinerary"].source, False),
    ]
    mode = _mode(sections)
    return TripPlan(
        id=plan_id,
        request=request,
        mode=mode,  # type: ignore[arg-type]
        model=model if mode != "estimate" else "",
        destination=state["destination"],
        flights=flights,
        lodging=state["lodging"],
        transport=state["transport"],
        itinerary=state["itinerary"],
        budget=state["budget"],
        warnings=ctx.warnings,
        elapsed_seconds=round(time.monotonic() - started, 1),
    )
