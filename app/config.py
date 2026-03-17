import subprocess

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # Claude OAuth 토큰 (직접 설정 or macOS Keychain 자동 추출)
    claude_oauth_token: str = ""

    # 작업 디렉토리 (Docker 볼륨으로 API 서버 ↔ Claude 컨테이너 공유)
    workspace_base: str = "/workspace"

    # Claude CLI 타임아웃 (초)
    claude_timeout: int = 120

    # 완료된 작업 보관 시간 (초)
    task_retention: int = 3600

    # API 인증 키
    api_key: str = ""

    # Telegram 봇 (빈 문자열이면 비활성)
    telegram_bot_token: str = ""
    telegram_chat_id: int = 0

    model_config = {"env_file": ".env", "env_prefix": ""}

    def get_oauth_token(self) -> str | None:
        """토큰 반환. 설정 안 되어 있으면 macOS Keychain 시도, 실패하면 None."""
        if self.claude_oauth_token:
            return self.claude_oauth_token
        try:
            return self._extract_from_keychain()
        except Exception:
            return None

    @staticmethod
    def _extract_from_keychain() -> str:
        import json

        result = subprocess.run(
            ["security", "find-generic-password", "-s", "Claude Code-credentials", "-w"],
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError("Keychain 접근 실패")
        creds = json.loads(result.stdout.strip())
        return creds["claudeAiOauth"]["accessToken"]


settings = Settings()
