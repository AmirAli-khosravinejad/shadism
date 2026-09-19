from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, Optional, Union

if TYPE_CHECKING:
    from shadism.client import Client
    from shadism.types.chat import Chat
    from shadism.types.user import User


@dataclass
class Message:
    id: str
    text: str
    author_guid: str
    chat_guid: str
    message_type: str = "Text"
    reply_to_message_id: Optional[str] = None
    is_edited: bool = False
    raw: Dict[str, Any] = field(default_factory=dict, repr=False, compare=False)
    _client: Optional[Client] = field(default=None, repr=False, compare=False)

    def __getitem__(self, key: str) -> Any:
        if hasattr(self, key):
            return getattr(self, key)
        if key in self.raw:
            return self.raw[key]
        inner = self.raw.get("message")
        if isinstance(inner, dict) and key in inner:
            return inner[key]
        raise KeyError(key)

    def get(self, key: str, default: Any = None) -> Any:
        if hasattr(self, key):
            val = getattr(self, key)
            if val is not None:
                return val
        if key in self.raw:
            return self.raw[key]
        inner = self.raw.get("message")
        if isinstance(inner, dict) and key in inner:
            return inner[key]
        return default

    async def edit(self, text: str) -> "Message":
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        edited = await self._client.edit_message(
            self.chat_guid,
            self.id,
            text,
        )
        self.text = edited.text
        self.is_edited = True
        return self

    async def delete(self, delete_type: str = "Global") -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        return await self._client.delete_message(
            self.chat_guid,
            self.id,
            delete_type=delete_type,
        )

    async def reply(self, text: str) -> "Message":
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        return await self._client.send_message(
            self.chat_guid,
            text,
            reply_to_message_id=self.id,
        )

    async def reply_photo(
        self,
        photo: Union[str, bytes, Path],
        caption: Optional[str] = None,
    ) -> "Message":
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        return await self._client.send_photo(
            self.chat_guid,
            photo,
            caption=caption,
            reply_to_message_id=self.id,
        )

    async def reply_file(
        self,
        file: Union[str, bytes, Path],
        file_name: Optional[str] = None,
        caption: Optional[str] = None,
    ) -> "Message":
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        return await self._client.send_file(
            self.chat_guid,
            file,
            file_name=file_name,
            caption=caption,
            reply_to_message_id=self.id,
        )

    async def get_chat(self) -> "Chat":
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        return await self._client.get_chat_info(self.chat_guid)

    async def get_author(self) -> "User":
        if self._client is None:
            raise RuntimeError("Message is not bound to a Client instance.")
        return await self._client.get_user_info(self.author_guid)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], client: Optional["Client"] = None) -> "Message":
        inner_msg: Dict[str, Any] = data.get("message") if isinstance(data.get("message"), dict) else {}
        text = data.get("text") or inner_msg.get("text") or ""
        is_edited = bool(data.get("is_edited") or inner_msg.get("is_edited") or False)
        return cls(
            id=str(data.get("message_id") or inner_msg.get("message_id") or ""),
            text=text,
            author_guid=data.get("author_object_guid") or inner_msg.get("author_object_guid") or "",
            chat_guid=data.get("object_guid") or inner_msg.get("object_guid") or "",
            message_type=data.get("type") or inner_msg.get("type") or "Text",
            reply_to_message_id=data.get("reply_to_message_id") or inner_msg.get("reply_to_message_id"),
            is_edited=is_edited,
            raw=data,
            _client=client,
        )

    def __str__(self) -> str:
        return (
            f"Message(id={self.id!r}, chat={self.chat_guid!r}, "
            f"author={self.author_guid!r}, text={self.text!r})"
        )
