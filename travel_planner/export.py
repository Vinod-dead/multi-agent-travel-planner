"""Render a finished plan as Markdown (used by the API download and the CLI)."""

from __future__ import annotations

from travel_planner.models import TripPlan


def _money(amount: float, currency: str) -> str:
    return f"{amount:,.0f} {currency}"


def to_markdown(plan: TripPlan) -> str:
    req, cur = plan.request, plan.request.currency
    d = plan.destination
    out: list[str] = []
    title = d.name or "Your trip"
    out.append(f"# Trip plan: {title}" + (f", {d.country}" if d.country else ""))

    when = f"{req.start_date} to {req.end_date}" if req.start_date and req.end_date else f"{req.days} days"
    out.append(f"{when} · {req.travelers} traveller(s) · budget {_money(req.budget, cur)} · {req.style} style")
    if plan.mode != "ai":
        out.append(
            "\n> **Note:** parts of this plan are offline estimates (a suggested split of your budget), "
            "not real prices."
        )
    for w in plan.warnings:
        out.append(f"> ⚠ {w}")

    out.append("\n## Overview")
    if d.description:
        out.append(d.description)
    if d.best_time:
        out.append(f"\n**Best time to visit:** {d.best_time}")
    if d.highlights:
        out.append("\n**Highlights**\n" + "\n".join(f"- {h}" for h in d.highlights))
    if d.local_tips:
        out.append("\n**Local tips**\n" + "\n".join(f"- {t}" for t in d.local_tips))

    out.append("\n## Day by day")
    for day in plan.itinerary.days:
        date = f" ({day.date})" if day.date else ""
        out.append(f"\n### Day {day.day}{date} - {day.title}")
        for item in day.items:
            cost = f" - {_money(item.cost, cur)}" if item.cost else ""
            desc = f": {item.description}" if item.description else ""
            out.append(f"- **{item.time or '-'}** {item.title}{desc}{cost}")

    out.append("\n## Flights")
    if plan.flights.skipped:
        out.append(plan.flights.note or "No flights needed.")
    else:
        for o in plan.flights.options:
            star = " ⭐ recommended" if o.recommended else ""
            stops = "non-stop" if o.stops == 0 else f"{o.stops} stop" + ("s" if o.stops > 1 else "")
            out.append(f"- **{o.airline}** ({stops}) {_money(o.price_per_person, cur)} per person, {_money(o.total_price, cur)} total{star}")
        if plan.flights.note:
            out.append(f"\n_{plan.flights.note}_")

    out.append("\n## Where to stay")
    for o in plan.lodging.options:
        star = " ⭐ recommended" if o.recommended else ""
        where = f", {o.area}" if o.area else ""
        out.append(f"- **{o.name}**{where}: {_money(o.price_per_night, cur)} per room per night, {_money(o.total_price, cur)} total{star}")

    out.append("\n## Getting around")
    for i in plan.transport.items:
        out.append(f"- **{i.mode}**: {i.description} ({_money(i.cost, cur)})")
    for tip in plan.transport.tips:
        out.append(f"- Tip: {tip}")

    b = plan.budget
    out.append("\n## Budget")
    out.append("| Category | Amount | Share |\n|---|---:|---:|")
    for line in b.breakdown:
        out.append(f"| {line.category} | {_money(line.amount, cur)} | {line.percent:.0f}% |")
    verdict = "within budget" if b.status == "within" else "over budget"
    out.append(
        f"\n**Estimated total:** {_money(b.estimated_total, cur)} of {_money(b.total_budget, cur)} "
        f"({verdict}; {_money(abs(b.remaining), cur)} {'left' if b.remaining >= 0 else 'over'})"
    )
    for tip in b.tips:
        out.append(f"- {tip}")

    if d.sources:
        out.append("\n## Sources")
        out.extend(f"- [{s.title or s.url}]({s.url})" for s in d.sources)
    out.append("\n---\n_Prices are estimates. Check live prices and opening hours before you book._")
    return "\n".join(out) + "\n"
