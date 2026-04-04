"""OAuth 토큰 생명주기 관리 — 자동 갱신, 영속 저장."""

import asyncio
import json
import logging
import time
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

OAUTH_ENDPOINT = "https://api.anthropic.com/v1/oauth/token"
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
SCOPE = "user:profile user:inference user:sessions:claude_code user:mcp_servers"
TOKEN_FILE = Path("/run/tokens/oauth.json")
REFRESH_MARGIN = 300  # 만료 5분 전에 갱신

_access_token: str = ""
_refresh_token: str = ""
_expires_at: float = 0.0
_refresh_dead: bool = False  # refresh token 무효화 시 재시도 중단
_lock = asyncio.Lock()


def init(access_token: str, refresh_token: str):
    """앱 시작 시 토큰 초기화. 파일에 더 최신 토큰이 있으면 그걸 사용."""
    global _access_token, _refresh_token, _expires_at

    if TOKEN_FILE.exists():
        try:
            saved = json.loads(TOKEN_FILE.read_text())
            if saved.get("expires_at", 0) > time.time():
                _access_token = saved["access_token"]
                _refresh_token = saved["refresh_token"]
                _expires_at = saved["expires_at"]
                logger.info("토큰 파일에서 복원 (%.0f초 후 만료)", _expires_at - time.time())
                return
        except Exception:
            logger.warning("토큰 파일 로드 실패, .env 값 사용")

    _access_token = access_token
    _refresh_token = refresh_token
    # .env에서 로드한 토큰은 만료 시점을 모르므로 무한대로 설정
    # → 실제 만료 시 claude_runner의 force_expire()가 갱신 트리거
    _expires_at = float("inf")


async def get_token() -> str | None:
    """유효한 access_token 반환. 만료 임박 시 자동 갱신."""
    if not _refresh_token or _refresh_dead:
        return _access_token or None

    if time.time() < _expires_at:
        return _access_token

    async with _lock:
        if time.time() < _expires_at:
            return _access_token
        await _do_refresh()

    return _access_token


def force_expire():
    """auth error 시 호출하여 다음 get_token()에서 강제 갱신."""
    global _expires_at
    _expires_at = 0.0


async def refresh_loop():
    """백그라운드: 1분마다 만료 체크, 선제적 갱신."""
    while True:
        await asyncio.sleep(60)
        if not _refresh_token or _refresh_dead:
            continue
        if time.time() >= _expires_at:
            try:
                async with _lock:
                    if time.time() >= _expires_at:
                        await _do_refresh()
            except Exception:
                logger.exception("백그라운드 토큰 갱신 실패")


async def _do_refresh():
    """refresh_token으로 새 토큰 발급."""
    global _access_token, _refresh_token, _expires_at, _refresh_dead

    async with httpx.AsyncClient() as client:
        resp = await client.post(
            OAUTH_ENDPOINT,
            json={
                "grant_type": "refresh_token",
                "refresh_token": _refresh_token,
                "client_id": CLIENT_ID,
                "scope": SCOPE,
            },
            timeout=15,
        )
        if resp.status_code == 400:
            logger.error("refresh token 무효화됨 (400). 재로그인 필요")
            _refresh_dead = True
            return
        resp.raise_for_status()
        data = resp.json()

    _access_token = data["access_token"]
    if "refresh_token" in data:
        _refresh_token = data["refresh_token"]
    _expires_at = time.time() + data.get("expires_in", 3600) - REFRESH_MARGIN

    logger.info("OAuth 토큰 갱신 완료 (%.0f초 후 만료)", _expires_at - time.time())
    _persist_tokens()


def _persist_tokens():
    """토큰을 파일에 영속 저장."""
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "access_token": _access_token,
        "refresh_token": _refresh_token,
        "expires_at": _expires_at,
    }))
