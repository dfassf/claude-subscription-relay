import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.claude_runner import check_auth, run_claude, run_login


def _make_proc(stdout=b"", stderr=b"", returncode=0):
    proc = AsyncMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = returncode
    proc.stdout = AsyncMock()
    return proc


class TestRunClaude:
    @pytest.mark.asyncio
    async def test_returns_stdout(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        proc = _make_proc(stdout=b"Hello from Claude")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"Hello from Claude", b"")):
                result = await run_claude("test prompt")

        assert result == "Hello from Claude"

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
                result = await run_claude("check file", files=[test_file])

        assert result == "ok"
        # 프롬프트에 파일 경로 포함 확인
        cmd_str = " ".join(str(c) for c in captured_cmd)
        assert "/workspace/" in cmd_str

    @pytest.mark.asyncio
    async def test_workspace_cleaned_up(self, fresh_settings, tmp_path):
        fresh_settings.workspace_base = str(tmp_path)
        proc = _make_proc(stdout=b"ok")

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", return_value=(b"ok", b"")):
                await run_claude("test")

        # workspace_base 아래 임시 디렉토리가 정리되었는지 확인
        remaining = list(tmp_path.iterdir())
        assert len(remaining) == 0


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

        proc = AsyncMock()
        proc.returncode = None
        proc.stdout = AsyncMock()
        proc.stdout.readline = AsyncMock(side_effect=[line_bytes])
        proc.kill = AsyncMock()
        proc.communicate = AsyncMock()

        async def fake_wait_for(coro, *, timeout=None):
            return await coro

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", side_effect=fake_wait_for):
                result = await run_login()

        assert result == url

    @pytest.mark.asyncio
    async def test_raises_when_no_url_found(self):
        proc = AsyncMock()
        proc.returncode = None
        proc.stdout = AsyncMock()
        proc.stdout.readline = AsyncMock(return_value=b"")
        proc.kill = AsyncMock()
        proc.communicate = AsyncMock()

        async def fake_wait_for(coro, *, timeout=None):
            return await coro

        with patch("app.claude_runner.asyncio.create_subprocess_exec", return_value=proc):
            with patch("app.claude_runner.asyncio.wait_for", side_effect=fake_wait_for):
                with pytest.raises(RuntimeError, match="OAuth URL"):
                    await run_login()
