"""Deduplicate in-flight async work by key: only one task runs per key at a time."""

import asyncio
from collections.abc import Callable


class SingleFlight[T, K]:
    """
    Run a task keyed by K. If another call with the same key is in progress,
    await that task instead of starting a new one. Clear stored task on exception.
    """

    _key: K | None
    _task: asyncio.Task[T] | None

    def __init__(self) -> None:
        self._key = None
        self._task = None

    async def run(
        self,
        key: K,
        create_task_func: Callable[[], asyncio.Task[T]],
        *,
        use_dedup: bool = True,
    ) -> T:
        if not use_dedup or self._task is None or self._key != key or self._task.done():
            self._key = key
            self._task = create_task_func()
        try:
            return await self._task
        except BaseException:
            self._key = None
            self._task = None
            raise
