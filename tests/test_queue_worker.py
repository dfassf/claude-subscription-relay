import asyncio
import time
from unittest.mock import AsyncMock, patch

import pytest

from app.queue_worker import QueueWorker, Task
from app.schemas import TaskStatus


class TestTask:
    def test_task_id_is_12_hex_chars(self):
        t = Task(prompt="hello")
        assert len(t.task_id) == 12
        assert t.task_id.isalnum()

    def test_unique_ids(self):
        ids = {Task(prompt="a").task_id for _ in range(50)}
        assert len(ids) == 50

    def test_defaults(self):
        t = Task(prompt="hello")
        assert t.status == TaskStatus.queued
        assert t.system_prompt is None
        assert t.output_format == "text"
        assert t.timeout is None
        assert t.files is None
        assert t.result is None
        assert t.error is None


class TestQueueWorker:
    def test_enqueue_and_get(self):
        w = QueueWorker()
        t = Task(prompt="test")
        tid = w.enqueue(t)
        assert tid == t.task_id
        assert w.get_task(tid) is t

    def test_get_nonexistent_returns_none(self):
        w = QueueWorker()
        assert w.get_task("nonexistent") is None

    def test_queue_size(self):
        w = QueueWorker()
        assert w.queue_size == 0
        w.enqueue(Task(prompt="a"))
        w.enqueue(Task(prompt="b"))
        assert w.queue_size == 2

    def test_current_task_id_default_none(self):
        w = QueueWorker()
        assert w.current_task_id is None

    @pytest.mark.asyncio
    async def test_start_processes_task(self):
        w = QueueWorker()
        t = Task(prompt="hello")
        w.enqueue(t)

        with patch("app.queue_worker.run_claude", new_callable=AsyncMock) as mock:
            mock.return_value = ("response", None)
            task = asyncio.create_task(w.start())
            await asyncio.sleep(0.1)
            task.cancel()

        assert t.status == TaskStatus.completed
        assert t.result == "response"
        assert t.duration is not None

    @pytest.mark.asyncio
    async def test_start_handles_failure(self):
        w = QueueWorker()
        t = Task(prompt="fail")
        w.enqueue(t)

        with patch("app.queue_worker.run_claude", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("boom")
            task = asyncio.create_task(w.start())
            await asyncio.sleep(0.1)
            task.cancel()

        assert t.status == TaskStatus.failed
        assert t.error == "boom"
        assert t.duration is not None

    @pytest.mark.asyncio
    async def test_cleanup_removes_expired(self, monkeypatch):
        w = QueueWorker()
        t = Task(prompt="old")
        t.status = TaskStatus.completed
        t.completed_at = time.time() - 9999
        w._tasks[t.task_id] = t

        fresh = Task(prompt="new")
        fresh.status = TaskStatus.completed
        fresh.completed_at = time.time()
        w._tasks[fresh.task_id] = fresh

        # task_retention = 10초 (conftest), 직접 설정
        monkeypatch.setattr("app.queue_worker.settings.task_retention", 10)

        # cleanup_loop의 sleep을 건너뛰고 한 번만 실행
        original_sleep = asyncio.sleep
        call_count = 0

        async def fake_sleep(seconds):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise asyncio.CancelledError
            await original_sleep(0)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        with pytest.raises(asyncio.CancelledError):
            await w.cleanup_loop()

        assert w.get_task(t.task_id) is None
        assert w.get_task(fresh.task_id) is fresh
