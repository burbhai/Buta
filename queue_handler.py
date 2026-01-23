from __future__ import annotations

import asyncio
import logging
from typing import List, Optional, Tuple

LOGGER = logging.getLogger(__name__)

LOCK = asyncio.Lock()
SUCCESS_COUNT = 0
FAIL_COUNT = 0
ACTIVE_TASKS = 0
QUEUE_LENGTH = 0
COMPLETED_TASKS: List[Tuple[str, float, bool]] = []
MONITOR_TASK: Optional[asyncio.Task] = None
REQUEST_EVENTS: dict[str, asyncio.Event] = {}


async def _monitor_queue(queue: asyncio.Queue, interval: float) -> None:
    """Periodically snapshot queue length without busy looping."""
    global QUEUE_LENGTH
    while True:
        try:
            async with LOCK:
                QUEUE_LENGTH = queue.qsize()
        except Exception:
            LOGGER.exception("Queue monitor failed.")
        await asyncio.sleep(interval)


def start_queue_monitor(queue: asyncio.Queue, *, interval: float = 2.0) -> asyncio.Task:
    """Start a background task that tracks queue length."""
    global MONITOR_TASK
    if MONITOR_TASK and not MONITOR_TASK.done():
        return MONITOR_TASK
    MONITOR_TASK = asyncio.create_task(_monitor_queue(queue, interval))
    return MONITOR_TASK


def register_request_event(request_id: str) -> asyncio.Event:
    """Register a queue wait event for a request id."""
    event = asyncio.Event()
    REQUEST_EVENTS[request_id] = event
    return event


def signal_request_started(request_id: str) -> None:
    """Signal that a request has started processing."""
    event = REQUEST_EVENTS.pop(request_id, None)
    if event:
        event.set()


async def mark_task_started(label: str) -> None:
    """Mark a queue task as started."""
    global ACTIVE_TASKS
    async with LOCK:
        ACTIVE_TASKS += 1


async def mark_task_completed(label: str, elapsed: float, *, success: bool = True) -> None:
    """Mark a queue task as completed with elapsed time."""
    global ACTIVE_TASKS, SUCCESS_COUNT, FAIL_COUNT
    async with LOCK:
        ACTIVE_TASKS = max(0, ACTIVE_TASKS - 1)
        if success:
            SUCCESS_COUNT += 1
        else:
            FAIL_COUNT += 1
        COMPLETED_TASKS.append((label, round(elapsed, 2), success))
        if len(COMPLETED_TASKS) > 50:
            COMPLETED_TASKS.pop(0)


async def get_queue_status() -> str:
    """Return a formatted status string for the queue."""
    async with LOCK:
        q_len = QUEUE_LENGTH
        active = ACTIVE_TASKS
        done = SUCCESS_COUNT
        failed = FAIL_COUNT
    return (
        "📊 Queue Status\n"
        f"• Active Tasks: {active}\n"
        f"• Queued Items: {q_len}\n"
        f"• Completed: {done}\n"
        f"• Failed: {failed}"
    )


async def get_queue_snapshot() -> dict:
    """Return queue metrics for health checks."""
    async with LOCK:
        return {
            "queue_length": QUEUE_LENGTH,
            "active_tasks": ACTIVE_TASKS,
            "success_count": SUCCESS_COUNT,
            "fail_count": FAIL_COUNT,
            "completed_samples": len(COMPLETED_TASKS),
        }


async def get_completed_tasks_summary() -> str:
    """Return a summary of recently completed tasks."""
    async with LOCK:
        if not COMPLETED_TASKS:
            return "No tasks completed yet."
        summary = "✅ Completed Tasks:\n"
        for idx, (label, elapsed, success) in enumerate(COMPLETED_TASKS, 1):
            status = "OK" if success else "FAIL"
            summary += f"{idx}. {label} | {elapsed}s | {status}\n"
        return summary
