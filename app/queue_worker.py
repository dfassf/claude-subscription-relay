import asyncio
import time
import uuid
from pathlib import Path

from app.claude_runner import run_claude
from app.config import settings
from app.schemas import TaskStatus


class Task:
    __slots__ = ("task_id", "prompt", "system_prompt", "output_format", "timeout", "files", "status", "result", "error", "completed_at")

    def __init__(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        output_format: str = "text",
        timeout: int | None = None,
        files: list[Path] | None = None,
    ):
        self.task_id = uuid.uuid4().hex[:12]
        self.prompt = prompt
        self.system_prompt = system_prompt
        self.output_format = output_format
        self.timeout = timeout
        self.files = files
        self.status = TaskStatus.queued
        self.result: str | None = None
        self.error: str | None = None
        self.completed_at: float | None = None


class QueueWorker:
    def __init__(self):
        self._queue: asyncio.Queue[Task] = asyncio.Queue()
        self._tasks: dict[str, Task] = {}
        self._current_task_id: str | None = None

    async def start(self):
        """큐에서 작업을 하나씩 꺼내서 순차 실행한다."""
        while True:
            task = await self._queue.get()
            self._current_task_id = task.task_id
            task.status = TaskStatus.running
            try:
                task.result = await run_claude(
                    task.prompt,
                    system_prompt=task.system_prompt,
                    output_format=task.output_format,
                    timeout=task.timeout,
                    files=task.files,
                )
                task.status = TaskStatus.completed
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.failed
            finally:
                task.completed_at = time.time()
                self._current_task_id = None
                self._queue.task_done()

    async def cleanup_loop(self):
        """완료된 작업을 task_retention 시간 후 메모리에서 제거한다."""
        while True:
            await asyncio.sleep(600)  # 10분마다 확인
            now = time.time()
            expired = [
                tid for tid, t in self._tasks.items()
                if t.completed_at and now - t.completed_at > settings.task_retention
            ]
            for tid in expired:
                del self._tasks[tid]

    def enqueue(self, task: Task) -> str:
        self._tasks[task.task_id] = task
        self._queue.put_nowait(task)
        return task.task_id

    def get_task(self, task_id: str) -> Task | None:
        return self._tasks.get(task_id)

    @property
    def queue_size(self) -> int:
        return self._queue.qsize()

    @property
    def current_task_id(self) -> str | None:
        return self._current_task_id


worker = QueueWorker()
