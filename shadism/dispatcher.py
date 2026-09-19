from __future__ import annotations

import asyncio
from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List, Optional

from shadism.filters import Filter
from shadism.types.message import Message

if TYPE_CHECKING:
    from shadism.client import Client

logger = logging.getLogger("shadism.dispatcher")

_POLL_INTERVAL_SECONDS = 1.5
_ERROR_BACKOFF_SECONDS = 5.0
_MAX_CONSECUTIVE_ERRORS = 10

MessageHandler = Callable[[Message], Coroutine[Any, Any, None]]


@dataclass
class HandlerEntry:
    callback: MessageHandler
    filter: Optional["Filter"] = None


class Dispatcher:
    def __init__(self, client: "Client") -> None:
        self._client = client
        self._handlers: List[HandlerEntry] = []
        self._state: int = 0
        self._running: bool = False
        self._task: Optional[asyncio.Task[None]] = None

    def register_handler(
        self, handler: MessageHandler, filter_obj: Optional["Filter"] = None
    ) -> None:
        self._handlers.append(HandlerEntry(callback=handler, filter=filter_obj))

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._polling_loop(), name="shadism_dispatcher")
        logger.info("Dispatcher started.")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Dispatcher stopped.")

    async def _polling_loop(self) -> None:
        import time

        consecutive_errors = 0
        if self._client.session.state > 0:
            self._state = self._client.session.state
        else:
            self._state = int(time.time()) - 150
            self._client.session.state = self._state

        while self._running:
            try:
                updates = await self._client.methods.get_chats_updates(self._state)
                consecutive_errors = 0
                data_dict = (
                    updates.get("data")
                    if isinstance(updates.get("data"), dict)
                    else updates
                )
                new_state = int(
                    data_dict.get("state")
                    or data_dict.get("new_state")
                    or self._state
                )
                if new_state > self._state:
                    self._state = new_state
                    self._client.session.state = self._state

                chats: List[Dict[str, Any]] = data_dict.get("chats", [])
                for chat in chats:
                    last_message = chat.get("last_message")
                    if last_message:
                        await self._dispatch_message(
                            last_message, chat.get("object_guid", "")
                        )

                message_updates = data_dict.get("message_updates", [])
                for item in message_updates:
                    msg = item.get("message") if isinstance(item, dict) else None
                    if msg:
                        await self._dispatch_message(
                            msg, item.get("object_guid", "") or msg.get("object_guid", "")
                        )

                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if "NOT_REGISTERED" in str(exc):
                    try:
                        logger.info("Session not registered. Registering device...")
                        await self._client.methods.register_device()
                        logger.info("Device registered. Resuming polling...")
                        continue
                    except Exception as reg_exc:
                        logger.error("Auto device registration failed: %s", reg_exc)

                if "INVALID_AUTH" in str(exc):
                    logger.critical("Session authentication is invalid or revoked. Halting dispatcher.")
                    self._running = False
                    break

                consecutive_errors += 1
                logger.error(
                    "Polling error (%d/%d): %s",
                    consecutive_errors,
                    _MAX_CONSECUTIVE_ERRORS,
                    exc,
                )
                if consecutive_errors >= _MAX_CONSECUTIVE_ERRORS:
                    logger.critical(
                        "Dispatcher halted after %d consecutive errors.",
                        _MAX_CONSECUTIVE_ERRORS,
                    )
                    self._running = False
                    break
                await asyncio.sleep(_ERROR_BACKOFF_SECONDS)

    async def _dispatch_message(
        self, raw_message: Dict[str, Any], object_guid: str
    ) -> None:
        raw_message.setdefault("object_guid", object_guid)
        message = Message.from_dict(raw_message, client=self._client)

        if not self._handlers:
            return

        for entry in self._handlers:
            if entry.filter is not None:
                try:
                    matched = await entry.filter(message)
                    if not matched:
                        continue
                except Exception as filter_exc:
                    logger.error(
                        "Filter error for handler %r on message %s: %s",
                        getattr(entry.callback, "__name__", "handler"),
                        message.id,
                        filter_exc,
                    )
                    continue

            asyncio.create_task(
                self._safe_call(entry.callback, message),
                name=f"handler_{getattr(entry.callback, '__name__', 'handler')}_{message.id}",
            )

    async def _safe_call(self, handler: MessageHandler, message: Message) -> None:
        try:
            await handler(message)
        except Exception as exc:
            logger.error(
                "Unhandled exception in handler %r for message %s: %s",
                handler.__name__,
                message.id,
                exc,
            )
