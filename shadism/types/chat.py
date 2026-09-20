from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Union

if TYPE_CHECKING:
    from shadism.client import Client
    from shadism.types.message import Message


@dataclass
class Chat:
    guid: str
    title: str = ""
    type: str = "User"
    username: Optional[str] = None
    description: Optional[str] = None
    members_count: int = 0
    voice_chat_id: Optional[str] = None
    count_unseen: int = 0
    last_message: Optional[Dict[str, Any]] = None
    avatar_thumbnail: Optional[Dict[str, Any]] = None
    is_deleted: bool = False
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)
    _client: Optional[Client] = field(default=None, repr=False, compare=False)

    @property
    def chat_type(self) -> str:
        return self.type

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    async def send_message(
        self,
        text: str = "",
        reply_to_message_id: Optional[str] = None,
        file_inline: Optional[Dict[str, Any]] = None,
    ) -> "Message":
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        return await self._client.send_message(
            self.guid,
            text=text,
            reply_to_message_id=reply_to_message_id,
            file_inline=file_inline,
        )

    async def send_photo(
        self,
        photo: Union[str, bytes, Path],
        caption: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> "Message":
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        return await self._client.send_photo(
            self.guid,
            photo=photo,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    async def send_file(
        self,
        file: Union[str, bytes, Path],
        file_name: Optional[str] = None,
        mime: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> "Message":
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        return await self._client.send_file(
            self.guid,
            file=file,
            file_name=file_name,
            mime=mime,
            caption=caption,
            reply_to_message_id=reply_to_message_id,
        )

    async def get_chat_history(
        self,
        limit: int = 50,
        sort: str = "FromMax",
        max_id: Optional[str] = None,
        min_id: Optional[str] = None,
    ) -> List["Message"]:
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        return await self._client.get_chat_history(
            self.guid,
            limit=limit,
            max_id=max_id,
            min_id=min_id,
            sort=sort,
        )

    async def get_messages(
        self,
        limit: int = 50,
        sort: str = "FromMax",
        max_id: Optional[str] = None,
        min_id: Optional[str] = None,
    ) -> List["Message"]:
        return await self.get_chat_history(
            limit=limit,
            sort=sort,
            max_id=max_id,
            min_id=min_id,
        )

    async def delete_messages(
        self,
        message_ids: Union[str, int, List[Union[str, int]]],
        delete_type: str = "Global",
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        return await self._client.delete_messages(
            self.guid,
            message_ids=message_ids,
            delete_type=delete_type,
        )

    async def delete_message(
        self,
        message_id: Union[str, int],
        delete_type: str = "Global",
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        return await self._client.delete_message(
            self.guid,
            message_id=message_id,
            delete_type=delete_type,
        )

    async def join_voice_chat(
        self,
        voice_chat_id: Optional[str] = None,
        sdp_offer_data: str = "",
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        target_vc = voice_chat_id or self.voice_chat_id
        return await self._client.join_voice_chat(self.guid, target_vc, sdp_offer_data)

    async def leave_voice_chat(
        self,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        target_vc = voice_chat_id or self.voice_chat_id
        return await self._client.leave_voice_chat(self.guid, target_vc)

    async def get_voice_chat_participants(
        self,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self._client is None:
            raise RuntimeError("Chat is not bound to a Client instance.")
        target_vc = voice_chat_id or self.voice_chat_id
        return await self._client.get_voice_chat_participants(self.guid, target_vc)

    @classmethod
    def from_dict(cls, data: Dict[str, Any], client: Optional["Client"] = None) -> "Chat":
        if not isinstance(data, dict):
            return cls(guid="")
        raw = data.copy()
        if "data" in raw and isinstance(raw["data"], dict):
            inner_data = raw["data"]
            for k, v in inner_data.items():
                raw.setdefault(k, v)
        group = raw.get("group") if isinstance(raw.get("group"), dict) else None
        channel = raw.get("channel") if isinstance(raw.get("channel"), dict) else None
        user = raw.get("user") if isinstance(raw.get("user"), dict) else None
        bot_meta = raw.get("bot") if isinstance(raw.get("bot"), dict) else None
        chat_meta = raw.get("chat") if isinstance(raw.get("chat"), dict) else {}

        guid = ""
        title = ""
        chat_type = "User"
        username = None
        description = None
        members_count = 0
        voice_chat_id = None

        if group is not None:
            guid = group.get("group_guid") or raw.get("object_guid") or ""
            title = group.get("group_title") or group.get("title") or ""
            chat_type = "Group"
            username = group.get("username")
            description = group.get("description")
            members_count = int(group.get("count_members") or 0)
            voice_chat_id = (
                chat_meta.get("group_voice_chat_id")
                or group.get("voice_chat_id")
            )
        elif channel is not None:
            guid = channel.get("channel_guid") or raw.get("object_guid") or ""
            title = channel.get("channel_title") or channel.get("title") or ""
            chat_type = "Channel"
            username = channel.get("username")
            description = channel.get("description")
            members_count = int(channel.get("count_members") or 0)
            voice_chat_id = (
                chat_meta.get("channel_voice_chat_id")
                or channel.get("voice_chat_id")
            )
        elif bot_meta is not None:
            guid = bot_meta.get("bot_guid") or raw.get("object_guid") or ""
            title = bot_meta.get("bot_title") or bot_meta.get("title") or ""
            chat_type = "Bot"
            username = bot_meta.get("username")
            description = bot_meta.get("description")
            members_count = 0
            voice_chat_id = None
        elif user is not None:
            guid = user.get("user_guid") or raw.get("object_guid") or ""
            first = user.get("first_name") or ""
            last = user.get("last_name") or ""
            title = f"{first} {last}".strip() or first or user.get("name") or ""
            chat_type = "User"
            username = user.get("username")
            description = user.get("bio")
            members_count = 0
            voice_chat_id = None
        else:
            guid = raw.get("object_guid") or raw.get("guid") or ""
            if guid.startswith("g0"):
                chat_type = "Group"
            elif guid.startswith("c0"):
                chat_type = "Channel"
            elif guid.startswith("s0"):
                chat_type = "Service"
            elif guid.startswith("b0"):
                chat_type = "Bot"
            else:
                chat_type = "User"

            title = raw.get("title") or raw.get("first_name") or ""
            username = raw.get("username")
            description = raw.get("description") or raw.get("bio")
            members_count = int(raw.get("count_members") or 0)
            voice_chat_id = (
                raw.get("group_voice_chat_id")
                or raw.get("channel_voice_chat_id")
                or chat_meta.get("group_voice_chat_id")
                or chat_meta.get("channel_voice_chat_id")
            )

        count_unseen = int(raw.get("count_unseen") or chat_meta.get("count_unseen") or 0)
        last_message = raw.get("last_message") or chat_meta.get("last_message")
        avatar_thumbnail = (
            raw.get("avatar_thumbnail")
            or chat_meta.get("avatar_thumbnail")
            or (user.get("avatar_thumbnail") if isinstance(user, dict) else None)
            or (group.get("avatar_thumbnail") if isinstance(group, dict) else None)
            or (channel.get("avatar_thumbnail") if isinstance(channel, dict) else None)
        )
        is_deleted = bool(
            raw.get("is_deleted")
            or (user.get("is_deleted") if isinstance(user, dict) else False)
        )

        return cls(
            guid=guid,
            title=title,
            type=chat_type,
            username=username,
            description=description,
            members_count=members_count,
            voice_chat_id=voice_chat_id,
            count_unseen=count_unseen,
            last_message=last_message,
            avatar_thumbnail=avatar_thumbnail,
            is_deleted=is_deleted,
            raw=raw,
            _client=client,
        )

    def __str__(self) -> str:
        return (
            f"Chat(guid={self.guid!r}, title={self.title!r}, "
            f"type={self.type!r}, username={self.username!r}, "
            f"members_count={self.members_count})"
        )
