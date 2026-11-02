"""A fake OpenAI-compatible server for tests and demos.

Run it standalone to try the web app's AI path without any real model::

    python -m tests.fake_llm --port 11500 --mode messy
    LLM_BASE_URL=http://localhost:11500/v1 LLM_MODEL=fake python -m travel_planner

Modes
-----
clean    valid JSON
messy    what small models really produce: Markdown fences, trailing commas, text around
         the JSON, numbers as strings with currency symbols
garbage  not JSON at all
cut      JSON that is cut off mid-way (token limit)
empty    empty replies
thinking a reasoning model that runs out of tokens while "thinking" (empty content)
missing  HTTP 404 "model not found"
down     HTTP 500
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


def _field(prompt: str, label: str) -> str:
    match = re.search(rf"^{label}:\s*(.+)$", prompt, re.MULTILINE)
    return match.group(1).strip() if match else ""


def build_reply(system: str, user: str) -> dict[str, Any]:
    """Return the JSON object the real agent in ``system`` would expect."""
    dest = _field(user, "To") or "Goa"
    dest = "Goa" if dest.startswith("(to be chosen)") else dest
    budget = float(re.sub(r"[^\d.]", "", (_field(user, "Total budget").split() or ["50000"])[0]) or 50000)

    if "destination researcher" in system:
        return {
            "name": dest, "country": "India", "description": f"{dest} is a lively coastal region.",
            "best_time": "November to February", "language": "Konkani, English",
            "currency_note": "Cards are common; carry cash for small shops.",
            "highlights": ["Baga Beach", "Fort Aguada", "Old Goa churches", "Spice plantation tour"],
            "local_tips": ["Rent a scooter for short trips.", "Avoid the monsoon months."],
            "food_cost_per_person_per_day": round(budget * 0.012),
        }
    if "flight search specialist" in system:
        base = budget * 0.12
        return {
            "options": [
                {"airline": "IndiGo", "origin": "Bengaluru", "destination": dest, "depart": "06:10", "arrive": "07:25",
                 "duration": "1h 15m", "stops": 0, "price_per_person": round(base), "notes": "cabin bag only", "recommended": False},
                {"airline": "Air India", "origin": "Bengaluru", "destination": dest, "depart": "10:00", "arrive": "11:20",
                 "duration": "1h 20m", "stops": 0, "price_per_person": round(base * 1.4), "notes": "meal included", "recommended": True},
                {"airline": "Akasa Air", "origin": "Bengaluru", "destination": dest, "depart": "18:30", "arrive": "21:10",
                 "duration": "2h 40m", "stops": 1, "price_per_person": round(base * 0.8), "notes": "one stop", "recommended": False},
            ],
            "note": "Estimates only.",
        }
    if "accommodation specialist" in system:
        per = budget * 0.3 / 4
        return {
            "options": [
                {"name": "Beachside guesthouse", "kind": "guesthouse", "area": "Calangute", "price_per_night": round(per * 0.6), "rating": 3.8, "amenities": ["wifi"], "why": "Cheap and close to the beach"},
                {"name": "Mid-range hotel near Baga", "kind": "hotel", "area": "Baga", "price_per_night": round(per), "rating": 4.2, "amenities": ["wifi", "pool", "breakfast"], "why": "Good balance", "recommended": True},
                {"name": "Resort with spa", "kind": "resort", "area": "Candolim", "price_per_night": round(per * 1.8), "rating": 4.6, "amenities": ["pool", "spa"], "why": "Treat yourself"},
            ]
        }
    if "local transport planner" in system:
        return {
            "items": [
                {"mode": "Airport transfer", "description": "Prepaid taxi both ways", "cost": round(budget * 0.03)},
                {"mode": "Scooter rental", "description": "Daily rental for exploring", "cost": round(budget * 0.06)},
            ],
            "tips": ["Use the GoaMiles app instead of street taxis."],
        }
    if "itinerary planner" in system:
        numbers = [int(n) for n in re.findall(r"^- Day (\d+)", user, re.MULTILINE)] or [1]
        return {
            "days": [
                {"day": n, "title": f"Day {n} in {dest}", "items": [
                    {"time": "09:00", "title": f"Morning visit #{n}", "description": "See a famous sight.", "category": "sightseeing", "cost": round(budget * 0.01)},
                    {"time": "13:00", "title": "Lunch at a beach shack", "description": "Fresh seafood.", "category": "food", "cost": 500},
                    {"time": "16:00", "title": f"Afternoon activity #{n}", "description": "Water sports.", "category": "activity", "cost": round(budget * 0.02)},
                    {"time": "20:00", "title": "Sunset dinner", "description": "Relax.", "category": "food", "cost": 0},
                ]}
                for n in numbers
            ]
        }
    return {"ok": True}


def _mess_up(obj: Any, mode: str) -> str:
    text = json.dumps(obj, indent=1)
    if mode == "clean":
        return text
    if mode == "messy":
        # numbers as currency strings, trailing commas, fences and chatter
        text = re.sub(r'("(?:price_per_person|price_per_night|cost|food_cost_per_person_per_day)"): (\d+)', r'\1: "₹\2"', text)
        text = re.sub(r"\n(\s*)([}\]])", r",\n\1\2", text)
        return f"Sure! Here is the plan:\n```json\n{text}\n```\nLet me know if you need anything else."
    if mode == "garbage":
        return "I'm sorry, I can't help with that request right now."
    if mode == "cut":
        return text[: int(len(text) * 0.7)]
    if mode in ("empty", "thinking"):
        return ""
    return text


def create_fake_llm(mode: str = "clean", delay: float = 0.0) -> FastAPI:
    app = FastAPI()
    app.state.mode = mode
    app.state.calls = []
    app.state.delay = delay

    @app.get("/v1/models")
    async def models():
        return {"object": "list", "data": [{"id": "fake", "object": "model", "created": 0, "owned_by": "test"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: Request):
        body = await request.json()
        app.state.calls.append(body)
        current = app.state.mode
        if current == "down":
            return JSONResponse({"error": {"message": "boom"}}, status_code=500)
        if current == "missing":
            return JSONResponse({"error": {"message": f"model '{body.get('model')}' not found, try pulling it first"}}, status_code=404)
        if app.state.delay:
            import asyncio

            await asyncio.sleep(app.state.delay)
        messages = body.get("messages", [])
        system = next((m["content"] for m in messages if m["role"] == "system"), "")
        user = next((m["content"] for m in messages if m["role"] == "user"), "")
        content = _mess_up(build_reply(system, user), current)
        if current == "thinking":
            return {
                "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": body.get("model", "fake"),
                "choices": [{"index": 0, "finish_reason": "length",
                             "message": {"role": "assistant", "content": None, "reasoning_content": "Let me think about this..."}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
            }
        return {
            "id": "chatcmpl-test", "object": "chat.completion", "created": 0, "model": body.get("model", "fake"),
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
        }

    return app


if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=11500)
    parser.add_argument("--mode", default="clean", choices=["clean", "messy", "garbage", "cut", "empty", "thinking", "missing", "down"])
    parser.add_argument("--delay", type=float, default=0.0)
    args = parser.parse_args()
    uvicorn.run(create_fake_llm(args.mode, args.delay), host="127.0.0.1", port=args.port, log_level="warning")
