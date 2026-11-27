"""Work that goes on after the response that asked for it."""

from __future__ import annotations

import asyncio

from app.admin import tasks


def _running() -> set[asyncio.Task[None]]:
    return set(tasks._running)  # noqa: SLF001 - what is kept is what is shown


class TestStartingWork:
    async def test_runs_the_work_with_its_arguments(self):
        given: list[tuple[object, ...]] = []
        done = asyncio.Event()

        async def work(*args: object) -> None:
            given.append(args)
            done.set()

        tasks.start(work, 1, "a")
        await done.wait()

        assert given == [(1, "a")]

    async def test_keeps_the_task_until_it_is_done_and_then_lets_it_go(self):
        release = asyncio.Event()

        async def work() -> None:
            await release.wait()

        tasks.start(work)
        [task] = _running()
        release.set()
        await task

        assert _running() == set()


class TestCancellingAllWork:
    async def test_cancels_what_is_running_and_waits_for_it_to_stop(self):
        started, stopped = asyncio.Event(), asyncio.Event()

        async def work() -> None:
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                stopped.set()

        tasks.start(work)
        await started.wait()

        await tasks.cancel_all()

        assert stopped.is_set()
        assert _running() == set()

    async def test_does_nothing_when_nothing_is_running(self):
        await tasks.cancel_all()

        assert _running() == set()
