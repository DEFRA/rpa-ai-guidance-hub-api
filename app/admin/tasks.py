"""Work an admin asked for, that goes on after the response that asked for it.

Not FastAPI's `BackgroundTasks`: those run inside the response cycle, and are not
cancelled cleanly at shutdown. A task here is kept by a strong reference until it is
done, as the event loop holds tasks only weakly, and is cancelled when the service
stops: work cut short by a restart is asked for again. `create_task` copies the
context, so the request's trace id goes with it.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

_running: set[asyncio.Task[None]] = set()


def start(work: Callable[..., Coroutine[Any, Any, None]], *args: Any) -> None:
    """Run `work(*args)` in the background, from inside the running event loop."""
    task = asyncio.create_task(work(*args))
    _running.add(task)
    task.add_done_callback(_running.discard)


async def cancel_all() -> None:
    """Cancel every task still running, and wait until each has stopped."""
    running = list(_running)
    for task in running:
        task.cancel()
    await asyncio.gather(*running, return_exceptions=True)
