from __future__ import annotations

import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional, Tuple, Union

from shadism.dispatcher import Dispatcher, MessageHandler
from shadism.filters import Filter, ensure_filter
from shadism.methods import Methods
from shadism.network import Transport
from shadism.session import Session, SessionStorage
from shadism.types.message import Message
from shadism.types.user import User
from shadism.types.chat import Chat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)

logger = logging.getLogger("shadism.client")


class Client:
    def __init__(
        self,
        phone_number: str,
        session_directory: str = ".",
        messenger_host: Optional[str] = None,
    ) -> None:
        self._phone_number = phone_number
        self._storage = SessionStorage(phone_number, directory=session_directory)
        self.session: Session = self._storage.load(phone_number)
        self._pending_handlers: List[Tuple[MessageHandler, Optional[Filter]]] = []

        if messenger_host:
            self.session.messenger_host = messenger_host

        self._transport: Optional[Transport] = None
        self._methods: Optional[Methods] = None
        self._dispatcher: Optional[Dispatcher] = None

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            raise RuntimeError("Client not started. Call await bot.start() first.")
        return self._transport

    @property
    def methods(self) -> Methods:
        if self._methods is None:
            raise RuntimeError("Client not started. Call await bot.start() first.")
        return self._methods

    def _bootstrap(self) -> None:
        self._transport = Transport(self.session)
        self._methods = Methods(self.session, self._transport, self._storage, client=self)
        self._dispatcher = Dispatcher(self)
        for handler, filter_obj in self._pending_handlers:
            self._dispatcher.register_handler(handler, filter_obj=filter_obj)
        self._pending_handlers.clear()

    async def connect(self) -> None:
        self._bootstrap()
        methods = self.methods
        if not self.session.has_auth():
            logger.info(
                "No active session found for %s. Starting login flow...",
                self._phone_number,
            )
            await methods.login_flow(self._phone_number)
        else:
            try:
                await methods.register_device()
            except Exception as exc:
                if "INVALID_AUTH" in str(exc):
                    logger.warning("Session auth expired or invalidated. Starting login flow...")
                    self.session.auth = ""
                    self.session.decode_auth = ""
                    self.session.key_hex = ""
                    self._storage.save(self.session)
                    await methods.login_flow(self._phone_number)
                else:
                    logger.debug("Device registration check: %s", exc)

    async def start(self) -> None:
        if self._transport is None:
            await self.connect()

        logger.info("Client authenticated. Starting dispatcher...")
        if self._dispatcher is not None:
            self._dispatcher.start()

        loop = asyncio.get_running_loop()

        def _request_shutdown() -> None:
            logger.info("Shutdown signal received.")
            asyncio.create_task(self.stop())

        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, _request_shutdown)
            except (NotImplementedError, ValueError):
                pass

        logger.info("shadism client is running. Press Ctrl+C to stop.")
        await self._idle()

    async def _idle(self) -> None:
        while self._dispatcher is not None and self._dispatcher._running:
            await asyncio.sleep(1.0)

    async def stop(self) -> None:
        if self._dispatcher:
            await self._dispatcher.stop()
        if self._transport:
            await self._transport.close()
        self._storage.save(self.session)
        logger.info("Client stopped and session saved.")

    def on_message(
        self,
        filters_or_func: Optional[Union[Filter, Callable[..., Any], MessageHandler]] = None,
    ) -> Any:
        import inspect

        if inspect.iscoroutinefunction(filters_or_func):
            func = filters_or_func
            self._register_message_handler(func, filter_obj=None)
            return func

        filter_obj: Optional[Filter] = None
        if filters_or_func is not None:
            filter_obj = ensure_filter(filters_or_func)

        def decorator(func: MessageHandler) -> MessageHandler:
            self._register_message_handler(func, filter_obj=filter_obj)
            return func

        return decorator

    def _register_message_handler(
        self,
        func: MessageHandler,
        filter_obj: Optional[Filter] = None,
    ) -> None:
        if self._dispatcher is not None:
            self._dispatcher.register_handler(func, filter_obj=filter_obj)
        else:
            self._pending_handlers.append((func, filter_obj))

    async def send_message(
        self,
        object_guid: str,
        text: str = "",
        reply_to_message_id: Optional[str] = None,
        file_inline: Optional[Dict[str, Any]] = None,
    ) -> Message:
        return await self.methods.send_message(object_guid, text, reply_to_message_id, file_inline)

    async def edit_message(
        self,
        object_guid: str,
        message_id: str,
        text: str,
    ) -> Message:
        return await self.methods.edit_message(object_guid, message_id, text)

    async def delete_messages(
        self,
        object_guid: str,
        message_ids: Union[str, int, List[Union[str, int]]],
        delete_type: str = "Global",
    ) -> Dict[str, Any]:
        return await self.methods.delete_messages(object_guid, message_ids, delete_type)

    async def delete_message(
        self,
        object_guid: str,
        message_id: Union[str, int],
        delete_type: str = "Global",
    ) -> Dict[str, Any]:
        return await self.methods.delete_message(object_guid, message_id, delete_type)

    async def upload_file(
        self,
        file: Union[str, bytes, Path],
        file_name: Optional[str] = None,
        mime: Optional[str] = None,
        chunk_size: int = 131072,
    ) -> Dict[str, Any]:
        return await self.methods.upload_file(file, file_name, mime, chunk_size)

    async def send_photo(
        self,
        object_guid: str,
        photo: Union[str, bytes, Path],
        caption: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> Message:
        return await self.methods.send_photo(object_guid, photo, caption, reply_to_message_id, file_name)

    async def send_file(
        self,
        object_guid: str,
        file: Union[str, bytes, Path],
        file_name: Optional[str] = None,
        mime: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Message:
        return await self.methods.send_file(object_guid, file, file_name, mime, caption, reply_to_message_id)

    async def get_user_info(self, user_guid: Optional[str] = None) -> User:
        return await self.methods.get_user_info(user_guid)

    async def update_profile(
        self,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        bio: Optional[str] = None,
    ) -> bool:
        return await self.methods.update_profile(first_name, last_name, bio)

    async def get_chats(self, start_id: Optional[str] = None) -> Dict[str, Any]:
        return await self.methods.get_chats(start_id)

    async def get_messages(
        self,
        object_guid: str,
        limit: int = 50,
        sort: str = "FromMax",
        max_id: Optional[str] = None,
        min_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.methods.get_messages(object_guid, limit, sort, max_id, min_id)

    async def get_chats_updates(self, state: Optional[int] = None) -> Dict[str, Any]:
        return await self.methods.get_chats_updates(state)

    async def get_messages_updates(
        self,
        object_guid: str,
        state: Optional[int] = None,
    ) -> Dict[str, Any]:
        return await self.methods.get_messages_updates(object_guid, state)

    async def get_chat_history(
        self,
        object_guid: Optional[str] = None,
        limit: int = 50,
        max_id: Optional[str] = None,
        min_id: Optional[str] = None,
        sort: str = "FromMax",
        guid: Optional[str] = None,
        state: Optional[int] = None,
    ) -> List[Message]:
        return await self.methods.get_chat_history(
            object_guid=object_guid,
            limit=limit,
            max_id=max_id,
            min_id=min_id,
            sort=sort,
            guid=guid,
            state=state,
        )

    async def register_device(self) -> Dict[str, Any]:
        return await self.methods.register_device()

    async def get_chat_info(self, object_guid: str) -> Chat:
        return await self.methods.get_chat_info(object_guid)

    async def get_chat_info_by_username(self, username: str) -> Chat:
        return await self.methods.get_chat_info_by_username(username)

    async def join_voice_chat(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
        sdp_offer_data: str = "",
    ) -> Dict[str, Any]:
        return await self.methods.join_voice_chat(chat_guid, voice_chat_id, sdp_offer_data)

    async def leave_voice_chat(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.methods.leave_voice_chat(chat_guid, voice_chat_id)

    async def get_voice_chat_participants(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.methods.get_voice_chat_participants(chat_guid, voice_chat_id)

    async def create_voice_chat(self, chat_guid: str) -> Dict[str, Any]:
        return await self.methods.create_voice_chat(chat_guid)

    async def discard_voice_chat(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.methods.discard_voice_chat(chat_guid, voice_chat_id)

    async def set_voice_chat_state(
        self,
        chat_guid: str,
        voice_chat_id: str,
        activity: str = "Speaking",
        participant_object_guid: Optional[str] = None,
    ) -> Dict[str, Any]:
        return await self.methods.set_voice_chat_state(chat_guid, voice_chat_id, activity, participant_object_guid)

    def set_messenger_host(self, host: str) -> None:
        self.session.messenger_host = host
        self._storage.save(self.session)
        logger.info("Messenger host switched to: %s", host)
