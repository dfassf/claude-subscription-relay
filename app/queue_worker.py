import asyncio
import logging
import shutil
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path

from app.claude_runner import run_claude
from app.config import settings
from app.schemas import TaskStatus

logger = logging.getLogger(__name__)


def _new_task_id() -> str:
    return uuid.uuid4().hex[:12]


TaskCallback = Callable[["Task"], Awaitable[None]]


@dataclass(slots=True)
class Task:
    prompt: str
    system_prompt: str | None = None
    output_format: str = "text"
    timeout: int | None = None
    files: list[Path] | None = None
    task_id: str = field(default_factory=_new_task_id)
    status: TaskStatus = TaskStatus.queued
    result: str | None = None
    error: str | None = None
    completed_at: float | None = None
    started_at: float | None = None
    duration: float | None = None
    on_complete: TaskCallback | None = None
    resume_session: str | None = None
    session_id: str | None = None
    workspace_dir: str | None = None
    cleanup_dir: Path | None = None

    def cleanup(self):
        if not self.cleanup_dir:
            return

        shutil.rmtree(self.cleanup_dir, ignore_errors=True)
        self.cleanup_dir = None


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
            task.started_at = time.time()
            try:
                result_text, session_id = await run_claude(
                    task.prompt,
                    system_prompt=task.system_prompt,
                    output_format=task.output_format,
                    timeout=task.timeout,
                    files=task.files,
                    resume_session=task.resume_session,
                    workspace_dir=task.workspace_dir,
                )
                task.result = result_text
                task.session_id = session_id
                task.status = TaskStatus.completed
            except Exception as e:
                task.error = str(e)
                task.status = TaskStatus.failed
            finally:
                task.completed_at = time.time()
                task.duration = round(task.completed_at - task.started_at, 2)
                self._current_task_id = None
                if task.on_complete:
                    try:
                        await task.on_complete(task)
                    except Exception as e:
                        logger.error("Task callback 실패: %s", e)
                try:
                    task.cleanup()
                except Exception as e:
                    logger.warning("Task 정리 실패: %s", e)
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
