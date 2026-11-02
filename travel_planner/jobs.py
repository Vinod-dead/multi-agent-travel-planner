"""Runs plans in the background and records their progress."""

from __future__ import annotations

import asyncio
import logging

from travel_planner.llm import LLMClient
from travel_planner.models import TripRequest
from travel_planner.planner import plan_trip
from travel_planner.store import PlanStore

log = logging.getLogger(__name__)

MAX_CONCURRENT_PLANS = 2


class JobManager:
    def __init__(self, store: PlanStore, llm: LLMClient):
        self.store = store
        self.llm = llm
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._slots = asyncio.Semaphore(MAX_CONCURRENT_PLANS)

    def submit(self, request: TripRequest) -> str:
        plan_id = self.store.create(request)
        task = asyncio.create_task(self._run(plan_id, request), name=f"plan-{plan_id}")
        self._tasks[plan_id] = task
        task.add_done_callback(lambda _t, pid=plan_id: self._tasks.pop(pid, None))
        return plan_id

    async def _run(self, plan_id: str, request: TripRequest) -> None:
        try:
            async with self._slots:
                self.store.set_status(plan_id, "running")
                plan = await plan_trip(
                    request,
                    self.llm,
                    plan_id=plan_id,
                    on_event=lambda agent, status, msg: self.store.update_progress(plan_id, agent, status, msg),
                )
                self.store.set_result(plan_id, plan)
        except asyncio.CancelledError:
            self.store.set_error(plan_id, "Cancelled.", status="cancelled")
            raise
        except Exception as exc:  # noqa: BLE001
            log.exception("plan %s failed", plan_id)
            self.store.set_error(plan_id, f"Planning failed: {type(exc).__name__}: {exc}")

    def cancel(self, plan_id: str) -> bool:
        task = self._tasks.get(plan_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    def is_active(self, plan_id: str) -> bool:
        return plan_id in self._tasks

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
