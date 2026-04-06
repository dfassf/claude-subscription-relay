import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

import pytest

from app.claude_runner import check_auth, run_claude, run_login


def _make_proc(stdout=b"", stderr=b"", returncode=0):
    proc = Mock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.stdout = Mock()
    return proc


class TestRunClaude:
    @pytest.mark.asyncio
    async def test_returns_stdout(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        proc = _make_proc(stdout=b"Hello from Claude")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"Hello from Claude", b"")):
                result_text, session_id = await run_claude("test prompt")

        assert result_text == "Hello from Claude"
        assert session_id is None

    @pytest.mark.asyncio
    async def test_raises_on_nonzero_exit(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        proc = _make_proc(returncode=1, stderr=b"error msg")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"", b"error msg")):
                proc.returncode = 1
                with pytest.raises(RuntimeError, match="Claude 실행 실패"):
                    await run_claude("fail prompt")

    @pytest.mark.asyncio
    async def test_files_copied_to_workspace(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        test_file = tmp_path / "test.txt"
        test_file.write_text("content")

        captured_cmd = []

        async def capture_exec(*args, **kwargs):
            captured_cmd.extend(args)
            return _make_proc(stdout=b"ok")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", side_effect=capture_exec):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"ok", b"")):
                result_text, _ = await run_claude("check file", files=[test_file])

        assert result_text == "ok"
        cmd_str = " ".join(str(c) for c in captured_cmd)
        assert "/workspace/" in cmd_str

    @pytest.mark.asyncio
    async def test_workspace_cleaned_up(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        proc = _make_proc(stdout=b"ok")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"ok", b"")):
                await run_claude("test")

        remaining = list(tmp_path.iterdir())
        assert len(remaining) == 0

    @pytest.mark.asyncio
    async def test_json_output_returns_serialized_payload(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        payload = b'{"session_id": "sess-1", "result": "{\\"ok\\": true}"}'
        proc = _make_proc(stdout=payload)

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(payload, b"")):
                result_text, session_id = await run_claude("json prompt", output_format="json")

        assert session_id == "sess-1"
        assert json.loads(result_text) == {
            "session_id": "sess-1",
            "result": '{"ok": true}',
        }

    @pytest.mark.asyncio
    async def test_strips_memory_tags_and_persists_memory(self, fresh_settings, tmp_path, monkeypatch):
        fresh_settings.workspace_base = str(tmp_path)
        memory_file = tmp_path / "memory.md"
        payload = b'{"result": "answer<memory>remember this</memory>"}'
        proc = _make_proc(stdout=payload)

        monkeypatch.setattr("app.claude_runner.MEMORY_FILE", memory_file)

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(payload, b"")):
                result_text, _ = await run_claude("test prompt")

        assert result_text == "answer"
        assert memory_file.read_text(encoding="utf-8").strip() == "remember this"


class TestCheckAuth:
    @pytest.mark.asyncio
    async def test_parses_json_response(self, fresh_settings):
        proc = _make_proc(stdout=b'{"loggedIn": true}')

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b'{"loggedIn": true}', b"")):
                result = await check_auth()

        assert result == {"loggedIn": True}

    @pytest.mark.asyncio
    async def test_handles_non_json(self, fresh_settings):
        proc = _make_proc(stdout=b"not json")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"not json", b"")):
                result = await check_auth()

        assert result["loggedIn"] is False
        assert result["raw"] == "not json"


class TestRunLogin:
    @pytest.fixture(autouse=True)
    def _reset_login_proc(self):
        """테스트 간 전역 _login_proc 초기화."""
        import app.claude_runner as cr
        cr._login_proc = None
        yield
        cr._login_proc = None

    @pytest.mark.asyncio
    async def test_extracts_oauth_url(self):
        url = "https://claude.ai/oauth/authorize?code=abc123"
        line_bytes = f"Open this URL: {url}\n".encode()

        proc = Mock()
        proc.returncode = None
        proc.stdout = Mock()
        proc.stdout.readline = AsyncMock(side_effect=[line_bytes])
        proc.kill = Mock()
        proc.communicate = AsyncMock()

        async def fake_wait_for(coro, *, timeout=None):
            return await coro

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", side_effect=fake_wait_for):
                result = await run_login()

        assert result == url

    @pytest.mark.asyncio
    async def test_raises_when_no_url_found(self):
        proc = Mock()
        proc.returncode = None
        proc.stdout = Mock()
        proc.stdout.readline = AsyncMock(return_value=b"")
        proc.kill = Mock()
        proc.communicate = AsyncMock()

        async def fake_wait_for(coro, *, timeout=None):
            return await coro

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", side_effect=fake_wait_for):
                with pytest.raises(RuntimeError, match="OAuth URL"):
                    await run_login()
