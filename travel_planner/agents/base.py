"""Shared plumbing for the planning agents."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from travel_planner.config import Settings
from travel_planner.fallback import Targets
from travel_planner.llm import LLMBadOutput, LLMClient, LLMUnavailable
from travel_planner.models import Destination, TripRequest

log = logging.getLogger(__name__)

EventSink = Callable[[str, str, str], None]


class AgentFallback(Exception):
    """Raised inside an agent to say "use the offline estimate instead".

    ``quiet`` means the cause was already reported once (for example the model server is
    down), so the agent should not add another warning for it.
    """

    def __init__(self, message: str, *, quiet: bool = False):
        super().__init__(message)
        self.quiet = quiet


@dataclass
class PlanContext:
    """Everything agents share while one plan is being built."""

    request: TripRequest
    llm: LLMClient
    settings: Settings
    targets: Targets
    model: str
    sink: EventSink | None = None
    llm_ok: bool = True
    destination: Destination | None = None
    warnings: list[str] = field(default_factory=list)

    def emit(self, agent: str, status: str, message: str = "") -> None:
        if self.sink:
            try:
                self.sink(agent, status, message)
            except Exception:  # noqa: BLE001 - progress reporting must never break a plan
                log.exception("progress sink failed")

    def warn(self, message: str) -> None:
        if message and message not in self.warnings:
            self.warnings.append(message)

    async def ask(self, system: str, user: str, *, max_tokens: int | None = None) -> Any:
        """Ask the model for JSON. Raises :class:`AgentFallback` when it cannot help."""
        if not self.llm_ok:
            raise AgentFallback("the language model is not available", quiet=True)
        try:
            return await self.llm.chat_json(
                system=system, user=user, model=self.model, max_tokens=max_tokens
            )
        except LLMUnavailable as exc:
            self.llm_ok = False  # stop every other agent from waiting on a dead server
            self.warn(sentence(str(exc)) + " Showing offline estimates instead.")
            raise AgentFallback(str(exc), quiet=True) from exc
        except LLMBadOutput as exc:
            raise AgentFallback(f"the model's reply could not be used ({exc})") from exc


def sentence(text: str) -> str:
    """Make sure ``text`` ends with a full stop (for joining warnings)."""
    text = text.strip()
    return text if text.endswith((".", "!", "?")) else text + "."


def trip_brief(req: TripRequest, destination: str | None = None) -> str:
    """A compact, model-friendly description of the trip."""
    dest = destination or req.destination or "(to be chosen)"
    when = (
        f"{req.start_date.isoformat()} to {req.end_date.isoformat()}"
        if req.start_date and req.end_date
        else "dates flexible"
    )
    lines = [
        f"From: {req.origin or 'not given'}",
        f"To: {dest}",
        f"When: {when} ({req.days} days, {req.nights} nights)",
        f"Travellers: {req.travelers} (rooms needed: {req.rooms})",
        f"Total budget: {req.budget:,.0f} {req.currency}",
        f"Style: {req.style}",
        f"Interests: {', '.join(req.interests) if req.interests else 'general sightseeing'}",
    ]
    if req.notes:
        lines.append(f"Notes from traveller: {req.notes}")
    return "\n".join(lines)


def system_prompt(role: str, req: TripRequest) -> str:
    return (
        f"You are the {role} in a team of travel-planning agents. "
        "Reply with ONLY one valid JSON object that follows the example shape - "
        "no Markdown, no commentary. "
        f"All money is in {req.currency}. Amounts are totals for the whole group unless "
        "the field name says otherwise. Be realistic and specific; if you are unsure of a "
        "fact, stay general instead of inventing details."
    )


def unwrap(data: Any, *keys: str) -> Any:
    """Models sometimes wrap the answer ({"flights": {...}}). Peel one layer if so."""
    if isinstance(data, dict):
        for key in keys:
            if key in data and isinstance(data[key], (dict, list)):
                return data[key]
    return data


class Agent:
    """Template: try the model, validate, and fall back to the estimator on any problem."""

    key = "agent"
    label = "Agent"

    async def run(self, ctx: PlanContext) -> Any:
        ctx.emit(self.key, "running", f"{self.label} is working...")
        try:
            result = await self.generate(ctx)
            ctx.emit(self.key, "done", "Done")
            return result
        except AgentFallback as exc:
            reason = str(exc)
            if exc.quiet:
                ctx.emit(self.key, "fallback", "Used offline estimate")
                return self.estimate(ctx)
        except (ValidationError, KeyError, TypeError, ValueError, AttributeError) as exc:
            reason = f"the model's answer did not match the expected format ({type(exc).__name__})"
            log.warning("%s: %s", self.key, exc)
        except Exception as exc:  # noqa: BLE001 - a plan should survive any single agent failing
            reason = f"unexpected error ({type(exc).__name__})"
            log.exception("%s failed", self.key)

        ctx.warn(f"{self.label}: used an offline estimate because {reason}.")
        ctx.emit(self.key, "fallback", "Used offline estimate")
        return self.estimate(ctx)

    async def generate(self, ctx: PlanContext) -> Any:  # pragma: no cover - interface
        raise NotImplementedError

    def estimate(self, ctx: PlanContext) -> Any:  # pragma: no cover - interface
        raise NotImplementedError


def pick_recommended(options: list[Any], style: str, price: Callable[[Any], float]) -> None:
    """Make sure exactly one option is flagged ``recommended`` (by travel style)."""
    if not options:
        return
    flagged = [o for o in options if o.recommended]
    if len(flagged) == 1:
        return
    for o in options:
        o.recommended = False
    ranked = sorted(options, key=price)
    index = {"budget": 0, "balanced": len(ranked) // 2, "luxury": len(ranked) - 1}[style]
    ranked[index].recommended = True
