"""Telegram 봇 — long-polling으로 메시지를 수신하여 Claude relay에 전달."""

import asyncio
import logging

import httpx

from app.config import settings
from app.queue_worker import Task, worker

logger = logging.getLogger(__name__)

TELEGRAM_API = "https://api.telegram.org/bot{token}"


class TelegramBot:
    def __init__(self, token: str, allowed_chat_id: int):
        self._token = token
        self._allowed_chat_id = allowed_chat_id
        self._base_url = TELEGRAM_API.format(token=token)
        self._offset = 0
        self._client: httpx.AsyncClient | None = None

    async def start(self):
        """Long-polling 루프 시작."""
        self._client = httpx.AsyncClient(timeout=35)
        logger.info("Telegram 봇 polling 시작 (chat_id=%s)", self._allowed_chat_id)
        try:
            while True:
                try:
                    await self._poll_once()
                except httpx.HTTPError as e:
                    logger.warning("Telegram polling 오류: %s", e)
                    await asyncio.sleep(5)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    logger.exception("Telegram polling 예외")
                    await asyncio.sleep(10)
        finally:
            await self._client.aclose()

    async def _poll_once(self):
        resp = await self._client.get(
            f"{self._base_url}/getUpdates",
            params={"offset": self._offset, "timeout": 30},
        )
        resp.raise_for_status()
        data = resp.json()

        for update in data.get("result", []):
            self._offset = update["update_id"] + 1
            await self._handle_update(update)

    async def _handle_update(self, update: dict):
        message = update.get("message")
        if not message:
            return

        chat_id = message.get("chat", {}).get("id")
        text = (message.get("text") or "").strip()

        if chat_id != self._allowed_chat_id:
            return

        if not text:
            return

        # 슬래시 명령어 처리
        if text.startswith("/"):
            cmd = text.split()[0].lower().split("@")[0]
            if cmd == "/status":
                await self._send_message(
                    chat_id,
                    f"큐 대기: {worker.queue_size}건\n"
                    f"현재 작업: {worker.current_task_id or '없음'}",
                )
                return
            if cmd in ("/start", "/help"):
                await self._send_message(
                    chat_id,
                    "Claude on Cloud\n\n"
                    "메시지를 보내면 Claude가 답변합니다.\n"
                    "/status - 큐 상태 확인",
                )
                return

        # 일반 메시지 → Claude로 전달
        await self._forward_to_claude(chat_id, text)

    async def _forward_to_claude(self, chat_id: int, prompt: str):
        await self._send_message(chat_id, "처리 중...")

        task = Task(prompt=prompt)

        async def on_complete(t: Task):
            if t.error:
                await self._send_message(chat_id, f"오류: {t.error}")
            else:
                result = t.result or "(빈 응답)"
                for chunk in _split_message(result, 4096):
                    await self._send_message(chat_id, chunk)

        task.on_complete = on_complete
        worker.enqueue(task)

    async def _send_message(self, chat_id: int, text: str):
        try:
            resp = await self._client.post(
                f"{self._base_url}/sendMessage",
                json={"chat_id": chat_id, "text": text},
            )
            resp.raise_for_status()
        except Exception:
            logger.exception("Telegram 메시지 전송 실패")


def _split_message(text: str, max_length: int) -> list[str]:
    """긴 메시지를 줄바꿈 기준으로 분할."""
    if not text:
        return ["(빈 응답)"]
    chunks = []
    while text:
        if len(text) <= max_length:
            chunks.append(text)
            break
        split_at = text.rfind("\n", 0, max_length)
        if split_at == -1:
            split_at = max_length
        chunks.append(text[:split_at])
        text = text[split_at:].lstrip("\n")
    return chunks


bot: TelegramBot | None = None


def get_bot() -> TelegramBot | None:
    global bot
    if bot is None and settings.telegram_bot_token and settings.telegram_chat_id:
        bot = TelegramBot(
            token=settings.telegram_bot_token,
            allowed_chat_id=settings.telegram_chat_id,
        )
    return bot
