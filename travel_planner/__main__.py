"""Command line: ``python -m travel_planner [serve|plan|check]``."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import webbrowser

from travel_planner import __version__
from travel_planner.config import get_settings


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=getattr(logging, level, logging.INFO), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    for noisy in ("httpx", "httpx2", "httpcore", "httpcore2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    settings = get_settings()
    host, port = args.host or settings.host, args.port or settings.port
    url = f"http://{'localhost' if host in ('0.0.0.0', '127.0.0.1') else host}:{port}"
    print(f"Travel Planner {__version__} -> {url}")
    if args.open:
        webbrowser.open(url)
    uvicorn.run("travel_planner.api:get_app", factory=True, host=host, port=port, reload=args.reload, log_level=settings.log_level.lower())
    return 0


def cmd_check(_: argparse.Namespace) -> int:
    from travel_planner.llm import LLMClient

    async def run() -> int:
        settings = get_settings()
        client = LLMClient(settings)
        print(f"Endpoint: {settings.llm_base_url}\nModel:    {settings.llm_model}")
        result = await client.ping(use_cache=False)
        if not result["ok"]:
            print(f"\n[FAIL] {result['error']}")
            print("The app still works without a model (offline estimates).")
            await client.aclose()
            return 1
        print(f"\n[OK] server reachable, {len(result['models'])} model(s) listed")
        try:
            reply = await client.chat([{"role": "user", "content": "Reply with the single word: ready"}], max_tokens=256)
            print(f"[OK] model replied: {reply.strip()[:60]!r}")
            code = 0
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {exc}")
            code = 1
        await client.aclose()
        return code

    return asyncio.run(run())


def cmd_models(_: argparse.Namespace) -> int:
    from travel_planner.llm import LLMClient

    async def run() -> int:
        client = LLMClient(get_settings())
        result = await client.ping(use_cache=False)
        await client.aclose()
        if not result["ok"]:
            print(f"[FAIL] {result['error']}")
            return 1
        print(f"{len(result['models'])} models available at {client.host}:\n")
        print("\n".join(result["models"]))
        return 0

    return asyncio.run(run())


def cmd_plan(args: argparse.Namespace) -> int:
    from travel_planner.export import to_markdown
    from travel_planner.llm import LLMClient
    from travel_planner.models import TripRequest
    from travel_planner.planner import plan_trip

    settings = get_settings()
    request = TripRequest(
        origin=args.origin, destination=args.destination, days=args.days, travelers=args.travelers,
        budget=args.budget, currency=args.currency or settings.default_currency, style=args.style,
        interests=args.interests or [], notes=args.notes, include_flights=not args.no_flights, model=args.model,
    )

    async def run() -> str:
        client = LLMClient(settings)
        try:
            plan = await plan_trip(request, client, settings=settings, on_event=lambda a, s, m: print(f"  [{s:>8}] {a}: {m}", file=sys.stderr))
        finally:
            await client.aclose()
        return plan.model_dump_json(indent=2) if args.json else to_markdown(plan)

    print(asyncio.run(run()))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="travel_planner", description="Multi-agent travel planner")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    serve = sub.add_parser("serve", help="start the web app (default)")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    serve.add_argument("--open", action="store_true", help="open the browser")
    serve.set_defaults(func=cmd_serve)

    sub.add_parser("check", help="test the connection to the language model").set_defaults(func=cmd_check)

    sub.add_parser("models", help="list the models your server offers").set_defaults(func=cmd_models)

    plan = sub.add_parser("plan", help="plan a trip from the terminal")
    plan.add_argument("--origin", default="")
    plan.add_argument("--destination", default="")
    plan.add_argument("--days", type=int, default=5)
    plan.add_argument("--travelers", type=int, default=2)
    plan.add_argument("--budget", type=float, required=True)
    plan.add_argument("--currency")
    plan.add_argument("--style", choices=["budget", "balanced", "luxury"], default="balanced")
    plan.add_argument("--interests", nargs="*")
    plan.add_argument("--notes", default="")
    plan.add_argument("--model")
    plan.add_argument("--no-flights", action="store_true")
    plan.add_argument("--json", action="store_true", help="print JSON instead of Markdown")
    plan.set_defaults(func=cmd_plan)

    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to a legacy code page
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass

    args = parser.parse_args(argv)
    _setup_logging(get_settings().log_level)
    if not getattr(args, "func", None):
        args = parser.parse_args([*(argv or []), "serve"])
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
