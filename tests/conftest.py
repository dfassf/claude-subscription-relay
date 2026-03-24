import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.queue_worker import QueueWorker


@pytest.fixture(autouse=True)
def mock_token_manager(monkeypatch):
    """token_manager를 모킹하여 실제 HTTP 호출 방지."""
    monkeypatch.setattr("app.token_manager._access_token", "test-token")
    monkeypatch.setattr("app.token_manager._refresh_token", "")
    monkeypatch.setattr("app.token_manager._expires_at", float("inf"))


@pytest.fixture()
def fresh_settings(monkeypatch, tmp_path):
    """매 테스트마다 깨끗한 Settings 인스턴스를 사용한다."""
    s = Settings(
        claude_code_oauth_token="test-token",
        workspace_base=str(tmp_path / "workspace"),
        claude_timeout=30,
        task_retention=10,
        api_key="",
    )
    monkeypatch.setattr("app.config.settings", s)
    monkeypatch.setattr("app.claude_runner.settings", s)
    monkeypatch.setattr("app.queue_worker.settings", s)
    return s


@pytest.fixture()
def fresh_worker(monkeypatch):
    """매 테스트마다 새 QueueWorker 인스턴스."""
    w = QueueWorker()
    monkeypatch.setattr("app.queue_worker.worker", w)
    monkeypatch.setattr("app.main.worker", w)
    return w


@pytest.fixture()
def client(fresh_settings, fresh_worker):
    """FastAPI TestClient. run_claude는 모킹."""
    with patch("app.queue_worker.run_claude", new_callable=AsyncMock) as mock_run:
        mock_run.return_value = ("mocked response", None)
        from app.main import app
        yield TestClient(app, raise_server_exceptions=False)
