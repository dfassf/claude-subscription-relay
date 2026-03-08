from unittest.mock import patch

import pytest

from app.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings(claude_oauth_token="tok", api_key="", _env_file=None)
        assert s.workspace_base == "/workspace"
        assert s.claude_timeout == 120
        assert s.task_retention == 3600
        assert s.api_key == ""

    def test_get_oauth_token_returns_set_token(self):
        s = Settings(claude_oauth_token="my-token")
        assert s.get_oauth_token() == "my-token"

    def test_get_oauth_token_tries_keychain_when_empty(self):
        s = Settings(claude_oauth_token="")
        with patch.object(s, "_extract_from_keychain", return_value="keychain-tok"):
            assert s.get_oauth_token() == "keychain-tok"

    def test_get_oauth_token_returns_none_on_keychain_failure(self):
        s = Settings(claude_oauth_token="")
        with patch.object(s, "_extract_from_keychain", side_effect=RuntimeError):
            assert s.get_oauth_token() is None
