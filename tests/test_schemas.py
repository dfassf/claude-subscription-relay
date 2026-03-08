import pytest
from pydantic import ValidationError

from app.schemas import AskRequest, AskResponse, HealthResponse, TaskResult, TaskStatus


class TestTaskStatus:
    def test_enum_values(self):
        assert TaskStatus.queued == "queued"
        assert TaskStatus.running == "running"
        assert TaskStatus.completed == "completed"
        assert TaskStatus.failed == "failed"

    def test_all_values_count(self):
        assert len(TaskStatus) == 4


class TestAskRequest:
    def test_valid_minimal(self):
        req = AskRequest(prompt="hello")
        assert req.prompt == "hello"
        assert req.system_prompt is None
        assert req.output_format == "text"
        assert req.timeout is None

    def test_valid_full(self):
        req = AskRequest(
            prompt="test",
            system_prompt="be helpful",
            output_format="json",
            timeout=60,
        )
        assert req.output_format == "json"
        assert req.timeout == 60

    def test_empty_prompt_rejected(self):
        with pytest.raises(ValidationError):
            AskRequest(prompt="")

    def test_too_long_prompt_rejected(self):
        with pytest.raises(ValidationError):
            AskRequest(prompt="x" * 10001)

    def test_invalid_output_format_rejected(self):
        with pytest.raises(ValidationError):
            AskRequest(prompt="ok", output_format="xml")

    def test_timeout_below_min_rejected(self):
        with pytest.raises(ValidationError):
            AskRequest(prompt="ok", timeout=5)

    def test_timeout_above_max_rejected(self):
        with pytest.raises(ValidationError):
            AskRequest(prompt="ok", timeout=601)


class TestAskResponse:
    def test_defaults(self):
        resp = AskResponse(task_id="abc123")
        assert resp.status == TaskStatus.queued


class TestTaskResult:
    def test_completed_task(self):
        r = TaskResult(
            task_id="abc",
            status=TaskStatus.completed,
            result="answer",
            duration=1.5,
        )
        assert r.result == "answer"
        assert r.error is None

    def test_failed_task(self):
        r = TaskResult(
            task_id="abc",
            status=TaskStatus.failed,
            error="timeout",
        )
        assert r.result is None
        assert r.error == "timeout"


class TestHealthResponse:
    def test_defaults(self):
        h = HealthResponse()
        assert h.status == "ok"
        assert h.queue_size == 0
        assert h.current_task is None
