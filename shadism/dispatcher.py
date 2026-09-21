from __future__ import annotations

import asyncio
from dataclasses import dataclass
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any, Callable, Coroutine, Dict, List, Optional, Union

from shadism.filters import Filter
from shadism.types.message import Message

if TYPE_CHECKING:
    from shadism.client import Client

logger = logging.getLogger("shadism.dispatcher")

_POLL_INTERVAL_SECONDS = 1.5
_ERROR_BACKOFF_SECONDS = 5.0
_MAX_CONSECUTIVE_ERRORS = 10

MessageHandler = Callable[[Message], Coroutine[Any, Any, None]]
UpdateHandler = Callable[[Dict[str, Any]], Coroutine[Any, Any, None]]


@dataclass
class HandlerEntry:
    callback: MessageHandler
    filter: Optional["Filter"] = None


class Dispatcher:
    def __init__(self, client: "Client") -> None:
        self._client = client
        self._message_handlers: List[HandlerEntry] = []
        self._edited_handlers: List[HandlerEntry] = []
        self._chat_handlers: List[UpdateHandler] = []
        self._seen_handlers: List[UpdateHandler] = []
        self._state: int = 0
        self._running: bool = False
        self._task: Optional[asyncio.Task[None]] = None
        self._processed_keys: Dict[str, float] = {}
        self._first_poll: bool = True

    @property
    def _handlers(self) -> List[HandlerEntry]:
        return self._message_handlers

    def register_handler(
        self, handler: MessageHandler, filter_obj: Optional["Filter"] = None
    ) -> None:
        self.register_message_handler(handler, filter_obj=filter_obj)

    def register_message_handler(
        self, handler: MessageHandler, filter_obj: Optional["Filter"] = None
    ) -> None:
        self._message_handlers.append(HandlerEntry(callback=handler, filter=filter_obj))

    def register_edited_handler(
        self, handler: MessageHandler, filter_obj: Optional["Filter"] = None
    ) -> None:
        self._edited_handlers.append(HandlerEntry(callback=handler, filter=filter_obj))

    def register_chat_handler(self, handler: UpdateHandler) -> None:
        self._chat_handlers.append(handler)

    def register_seen_handler(self, handler: UpdateHandler) -> None:
        self._seen_handlers.append(handler)

    def _mark_processed(self, key: str) -> bool:
        if key in self._processed_keys:
            return False
        self._processed_keys[key] = time.time()
        if len(self._processed_keys) > 10000:
            oldest_keys = list(self._processed_keys.keys())[:2000]
            for old_k in oldest_keys:
                self._processed_keys.pop(old_k, None)
        return True

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._task = asyncio.create_task(self._polling_loop(), name="shadism_dispatcher")
        logger.debug("Dispatcher started.")

    async def stop(self) -> None:
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.debug("Dispatcher stopped.")

    async def _polling_loop(self) -> None:
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
                if not isinstance(data_dict, dict):
                    await asyncio.sleep(_POLL_INTERVAL_SECONDS)
                    continue

                candidate_states: List[int] = []
                for state_key in ("state", "new_state"):
                    if state_key in data_dict and data_dict[state_key]:
                        try:
                            candidate_states.append(int(data_dict[state_key]))
                        except (ValueError, TypeError):
                            pass

                for item in data_dict.get("message_updates", []):
                    if isinstance(item, dict) and item.get("state"):
                        try:
                            candidate_states.append(int(item["state"]))
                        except (ValueError, TypeError):
                            pass

                if candidate_states:
                    highest_state = max(candidate_states)
                    if highest_state > self._state:
                        self._state = highest_state
                        self._client.session.state = self._state

                message_updates = data_dict.get("message_updates", [])
                for item in message_updates:
                    if not isinstance(item, dict):
                        continue
                    action = str(item.get("action") or item.get("type") or "New")
                    object_guid = str(item.get("object_guid") or "")
                    message_dict = (
                        item.get("message")
                        if isinstance(item.get("message"), dict)
                        else {}
                    )
                    if not object_guid and isinstance(message_dict, dict):
                        object_guid = str(message_dict.get("object_guid") or "")
                    message_id = str(
                        item.get("message_id")
                        or message_dict.get("message_id")
                        or ""
                    )
                    if not message_id:
                        continue

                    if action in ("New", "Add", "SendMessage") and not message_dict.get("is_edited"):
                        new_key = f"msg:new:{object_guid}:{message_id}"
                        if not self._mark_processed(new_key):
                            continue
                        await self._dispatch_message(message_dict or item, object_guid)

                    elif action in ("Edit", "EditMessage") or bool(message_dict.get("is_edited")):
                        text_content = str(message_dict.get("text") or item.get("text") or "")
                        text_hash = hashlib.md5(text_content.encode("utf-8")).hexdigest()[:8]
                        edit_key = f"msg:edit:{object_guid}:{message_id}:{text_hash}"
                        if not self._mark_processed(edit_key):
                            continue
                        await self._dispatch_edited_message(message_dict or item, object_guid)

                    elif action in ("Seen", "SeenMessage"):
                        performer = str(item.get("performer_object_guid") or "")
                        seen_key = f"msg:seen:{object_guid}:{message_id}:{performer}"
                        if not self._mark_processed(seen_key):
                            continue
                        await self._dispatch_seen(item, object_guid)

                if self._first_poll:
                    for chat in data_dict.get("chats", []):
                        if isinstance(chat, dict):
                            c_guid = str(chat.get("object_guid") or "")
                            last_msg = chat.get("last_message")
                            if isinstance(last_msg, dict):
                                m_id = str(last_msg.get("message_id") or "")
                                if c_guid and m_id:
                                    self._processed_keys[f"msg:new:{c_guid}:{m_id}"] = time.time()
                    self._first_poll = False
                else:
                    chats = data_dict.get("chats", [])
                    for chat in chats:
                        if not isinstance(chat, dict):
                            continue
                        chat_guid = str(chat.get("object_guid") or "")
                        last_msg = chat.get("last_message")
                        if isinstance(last_msg, dict):
                            mid = str(last_msg.get("message_id") or "")
                            if mid and chat_guid:
                                new_key = f"msg:new:{chat_guid}:{mid}"
                                if new_key not in self._processed_keys:
                                    self._mark_processed(new_key)
                                    await self._dispatch_message(last_msg, chat_guid)

                        last_seen_peer = str(chat.get("last_seen_peer_mid") or "")
                        if last_seen_peer and chat_guid:
                            seen_key = f"msg:seen:{chat_guid}:{last_seen_peer}:peer"
                            if self._mark_processed(seen_key):
                                await self._dispatch_seen(chat, chat_guid)

                chat_updates = data_dict.get("chat_updates", [])
                for cu in chat_updates:
                    if isinstance(cu, dict):
                        cu_guid = str(cu.get("object_guid") or "")
                        cu_type = str(cu.get("type") or cu.get("action") or "Update")
                        cu_state = str(cu.get("state") or "")
                        cu_key = f"chat_update:{cu_guid}:{cu_type}:{cu_state}"
                        if self._mark_processed(cu_key):
                            await self._dispatch_chat_update(cu)

                await asyncio.sleep(_POLL_INTERVAL_SECONDS)

            except asyncio.CancelledError:
                break
            except Exception as exc:
                if "NOT_REGISTERED" in str(exc):
                    try:
                        logger.debug("Session not registered. Registering device...")
                        await self._client.methods.register_device()
                        logger.debug("Device registered. Resuming polling...")
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

        if not self._message_handlers:
            return

        for entry in self._message_handlers:
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
                self._safe_call_message(entry.callback, message),
                name=f"msg_handler_{getattr(entry.callback, '__name__', 'handler')}_{message.id}",
            )

    async def _dispatch_edited_message(
        self, raw_message: Dict[str, Any], object_guid: str
    ) -> None:
        raw_message.setdefault("object_guid", object_guid)
        message = Message.from_dict(raw_message, client=self._client)

        targets = self._edited_handlers if self._edited_handlers else self._message_handlers
        if not targets:
            return

        for entry in targets:
            if entry.filter is not None:
                try:
                    matched = await entry.filter(message)
                    if not matched:
                        continue
                except Exception as filter_exc:
                    logger.error(
                        "Filter error for edit handler %r on message %s: %s",
                        getattr(entry.callback, "__name__", "handler"),
                        message.id,
                        filter_exc,
                    )
                    continue

            asyncio.create_task(
                self._safe_call_message(entry.callback, message),
                name=f"edit_handler_{getattr(entry.callback, '__name__', 'handler')}_{message.id}",
            )

    async def _dispatch_seen(
        self, seen_data: Dict[str, Any], object_guid: str
    ) -> None:
        seen_data.setdefault("object_guid", object_guid)
        for handler in self._seen_handlers:
            asyncio.create_task(
                self._safe_call_update(handler, seen_data),
                name=f"seen_handler_{getattr(handler, '__name__', 'handler')}",
            )

    async def _dispatch_chat_update(self, chat_update_data: Dict[str, Any]) -> None:
        for handler in self._chat_handlers:
            asyncio.create_task(
                self._safe_call_update(handler, chat_update_data),
                name=f"chat_handler_{getattr(handler, '__name__', 'handler')}",
            )

    async def _safe_call_message(self, handler: MessageHandler, message: Message) -> None:
        try:
            await handler(message)
        except Exception as exc:
            logger.error(
                "Unhandled exception in message handler %r for message %s: %s",
                getattr(handler, "__name__", "handler"),
                message.id,
                exc,
            )

    async def _safe_call_update(self, handler: UpdateHandler, data: Dict[str, Any]) -> None:
        try:
            await handler(data)
        except Exception as exc:
            logger.error(
                "Unhandled exception in update handler %r: %s",
                getattr(handler, "__name__", "handler"),
                exc,
            )
