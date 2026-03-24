from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import app
from app.queue_worker import QueueWorker, Task
from app.schemas import TaskStatus


@pytest.fixture()
def setup(monkeypatch, tmp_path):
    s = Settings(
        claude_code_oauth_token="tok",
        workspace_base=str(tmp_path),
        api_key="",
    )
    w = QueueWorker()
    monkeypatch.setattr("app.config.settings", s)
    monkeypatch.setattr("app.main.worker", w)
    monkeypatch.setattr("app.main.settings", s)
    return s, w


class TestHealthEndpoint:
    def test_returns_ok(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["queue_size"] == 0


class TestAskEndpoint:
    def test_enqueues_task(self, setup):
        _, w = setup
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/ask", json={"prompt": "hello"})
        assert resp.status_code == 200
        data = resp.json()
        assert "task_id" in data
        assert data["status"] == "queued"
        assert w.queue_size == 1

    def test_rejects_empty_prompt(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/ask", json={"prompt": ""})
        assert resp.status_code == 422

    def test_rejects_invalid_format(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/ask", json={"prompt": "ok", "output_format": "xml"})
        assert resp.status_code == 422


class TestTaskEndpoint:
    def test_returns_task_status(self, setup):
        _, w = setup
        t = Task(prompt="test")
        t.status = TaskStatus.completed
        t.result = "answer"
        t.duration = 1.5
        w._tasks[t.task_id] = t

        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(f"/task/{t.task_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["result"] == "answer"

    def test_returns_404_for_unknown(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/task/nonexistent")
        assert resp.status_code == 404


class TestApiKeyAuth:
    def test_rejects_without_key(self, setup):
        s, _ = setup
        s.api_key = "secret-key"
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/ask", json={"prompt": "hello"})
        assert resp.status_code == 401

    def test_accepts_valid_key(self, setup):
        s, _ = setup
        s.api_key = "secret-key"
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post(
            "/ask",
            json={"prompt": "hello"},
            headers={"X-API-Key": "secret-key"},
        )
        assert resp.status_code == 200

    def test_skips_auth_when_no_key_configured(self, setup):
        s, _ = setup
        s.api_key = ""
        client = TestClient(app, raise_server_exceptions=False)

        resp = client.post("/ask", json={"prompt": "hello"})
        assert resp.status_code == 200


class TestLoginEndpoint:
    def test_returns_oauth_url(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.main.run_login", new_callable=AsyncMock) as mock:
            mock.return_value = "https://claude.ai/oauth/authorize?code=abc"
            resp = client.post("/login")

        assert resp.status_code == 200
        assert "oauth_url" in resp.json()

    def test_returns_500_on_failure(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.main.run_login", new_callable=AsyncMock) as mock:
            mock.side_effect = RuntimeError("fail")
            resp = client.post("/login")

        assert resp.status_code == 500


class TestAuthEndpoint:
    def test_returns_auth_status(self, setup):
        client = TestClient(app, raise_server_exceptions=False)
        with patch("app.main.check_auth", new_callable=AsyncMock) as mock:
            mock.return_value = {"loggedIn": True}
            resp = client.get("/auth")

        assert resp.status_code == 200
        assert resp.json()["loggedIn"] is True
