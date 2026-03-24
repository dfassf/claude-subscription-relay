import pytest

from app.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings(claude_code_oauth_token="tok", api_key="", _env_file=None)
        assert s.workspace_base == "/workspace"
        assert s.claude_timeout == 120
        assert s.task_retention == 3600
        assert s.api_key == ""

    def test_refresh_token_default_empty(self):
        s = Settings(claude_code_oauth_token="tok", _env_file=None)
        assert s.claude_code_refresh_token == ""

    def test_telegram_defaults(self):
        s = Settings(claude_code_oauth_token="tok", _env_file=None)
        assert s.telegram_bot_token == ""
        assert s.telegram_chat_id == 0
