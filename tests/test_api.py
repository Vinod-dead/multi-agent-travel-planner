import asyncio

import httpx
import pytest

from travel_planner.api import create_app
from travel_planner.store import PlanStore

BODY = {"origin": "Bengaluru", "destination": "Goa", "days": 4, "budget": 50000, "travelers": 2, "interests": ["beaches"]}


@pytest.fixture
async def client(make_llm, settings):
    llm, _ = make_llm("messy")
    app = create_app(settings, llm=llm, store=PlanStore(":memory:"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            yield c


async def wait_done(client, plan_id, timeout=20):
    for _ in range(int(timeout / 0.1)):
        data = (await client.get(f"/api/plans/{plan_id}")).json()
        if data["status"] not in ("queued", "running"):
            return data
        await asyncio.sleep(0.1)
    raise AssertionError("plan did not finish")


async def test_health_and_config(client):
    health = (await client.get("/api/health")).json()
    assert health["status"] == "ok" and health["llm"]["ok"] and health["llm"]["models"] == ["fake"]
    assert "api_key" not in str(health).lower()
    assert (await client.get("/api/config")).json()["default_currency"] == "INR"


async def test_full_plan_lifecycle(client):
    created = await client.post("/api/plans", json=BODY)
    assert created.status_code == 202
    plan_id = created.json()["id"]

    data = await wait_done(client, plan_id)
    assert data["status"] == "done" and data["error"] is None
    result = data["result"]
    assert result["destination"]["name"] == "Goa" and len(result["itinerary"]["days"]) == 4
    assert all(step["status"] in ("done", "fallback") for step in data["progress"].values())

    listing = (await client.get("/api/plans")).json()
    assert listing[0]["id"] == plan_id and listing[0]["destination"] == "Goa"

    md = await client.get(f"/api/plans/{plan_id}/export?format=md")
    assert md.status_code == 200 and md.text.startswith("# Trip plan: Goa") and "## Budget" in md.text
    assert "attachment" in md.headers["content-disposition"]
    js = await client.get(f"/api/plans/{plan_id}/export?format=json")
    assert js.json()["id"] == plan_id

    assert (await client.delete(f"/api/plans/{plan_id}")).status_code == 204
    assert (await client.get(f"/api/plans/{plan_id}")).status_code == 404
    assert (await client.delete(f"/api/plans/{plan_id}")).status_code == 404


@pytest.mark.parametrize(
    "patch",
    [{"budget": 0}, {"budget": "lots"}, {"travelers": 0}, {"days": 0}, {"currency": "XX"}, {"start_date": "2026-12-09", "end_date": "2026-12-01"}, {"days": 9999}],
)
async def test_bad_requests_get_422(client, patch):
    response = await client.post("/api/plans", json={**BODY, **patch})
    assert response.status_code == 422


async def test_unknown_plan_and_unfinished_export(client):
    assert (await client.get("/api/plans/nope")).status_code == 404
    assert (await client.get("/api/plans/nope/export")).status_code == 404
    assert (await client.post("/api/plans/nope/cancel")).status_code == 404


async def test_bad_export_format_is_rejected(client):
    plan_id = (await client.post("/api/plans", json=BODY)).json()["id"]
    assert (await client.get(f"/api/plans/{plan_id}/export?format=exe")).status_code == 422
    await wait_done(client, plan_id)


async def test_cancel_a_running_plan(make_llm, settings):
    llm, fake = make_llm("clean")
    fake.state.delay = 0
    app = create_app(settings, llm=llm, store=PlanStore(":memory:"))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
            slow = asyncio.Event()

            async def hang(*_a, **_k):
                await slow.wait()

            llm.ping = hang  # the plan blocks in its first step
            plan_id = (await c.post("/api/plans", json=BODY)).json()["id"]
            await asyncio.sleep(0.2)
            assert (await c.post(f"/api/plans/{plan_id}/cancel")).json() == {"cancelled": True}
            data = await wait_done(c, plan_id)
            assert data["status"] == "cancelled"


async def test_web_ui_and_security_headers(client):
    index = await client.get("/")
    assert index.status_code == 200 and "Travel Planner" in index.text
    assert "script-src 'self'" in index.headers["content-security-policy"]
    assert index.headers["x-content-type-options"] == "nosniff"
    for asset in ("/static/app.js", "/static/style.css"):
        assert (await client.get(asset)).status_code == 200
    assert (await client.get("/api/health")).headers["cache-control"] == "no-store"
    assert (await client.get("/static/../config.py")).status_code in (404, 400)


async def test_restart_marks_interrupted_plans_failed(settings):
    store = PlanStore(":memory:")
    from travel_planner.models import TripRequest

    plan_id = store.create(TripRequest(budget=100))
    store.set_status(plan_id, "running")
    assert store.fail_stale() == 1
    assert store.get(plan_id)["status"] == "failed"
