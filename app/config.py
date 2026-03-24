from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    claude_code_oauth_token: str = ""
    claude_code_refresh_token: str = ""

    workspace_base: str = "/workspace"
    claude_timeout: int = 120
    task_retention: int = 3600
    api_key: str = ""

    telegram_bot_token: str = ""
    telegram_chat_id: int = 0

    model_config = {"env_file": ".env", "env_prefix": ""}


settings = Settings()
