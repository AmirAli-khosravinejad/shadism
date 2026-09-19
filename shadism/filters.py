from __future__ import annotations

import asyncio
import inspect
import re
from typing import TYPE_CHECKING, Any, Callable, Coroutine, List, Optional, Pattern, Union

if TYPE_CHECKING:
    from shadism.types.message import Message


class Filter:
    async def check(self, message: "Message") -> bool:
        raise NotImplementedError

    async def __call__(self, message: "Message") -> bool:
        res = self.check(message)
        if inspect.isawaitable(res):
            return bool(await res)
        return bool(res)

    def __and__(self, other: Union[Filter, Callable[["Message"], Any]]) -> "AndFilter":
        return AndFilter(self, ensure_filter(other))

    def __or__(self, other: Union[Filter, Callable[["Message"], Any]]) -> "OrFilter":
        return OrFilter(self, ensure_filter(other))

    def __rand__(self, other: Union[Filter, Callable[["Message"], Any]]) -> "AndFilter":
        return AndFilter(ensure_filter(other), self)

    def __ror__(self, other: Union[Filter, Callable[["Message"], Any]]) -> "OrFilter":
        return OrFilter(ensure_filter(other), self)

    def __invert__(self) -> "InvertFilter":
        return InvertFilter(self)


class AndFilter(Filter):
    def __init__(self, *filters: Filter) -> None:
        self.filters: List[Filter] = list(filters)

    async def check(self, message: "Message") -> bool:
        for f in self.filters:
            if not await f(message):
                return False
        return True

    def __str__(self) -> str:
        return f"({' and '.join(str(f) for f in self.filters)})"


class OrFilter(Filter):
    def __init__(self, *filters: Filter) -> None:
        self.filters: List[Filter] = list(filters)

    async def check(self, message: "Message") -> bool:
        for f in self.filters:
            if await f(message):
                return True
        return False

    def __str__(self) -> str:
        return f"({' or '.join(str(f) for f in self.filters)})"


class InvertFilter(Filter):
    def __init__(self, target: Filter) -> None:
        self.target: Filter = target

    async def check(self, message: "Message") -> bool:
        return not await self.target(message)

    def __str__(self) -> str:
        return f"(not {self.target})"


class CustomFilter(Filter):
    def __init__(self, func: Callable[["Message"], Any]) -> None:
        self.func: Callable[["Message"], Any] = func

    async def check(self, message: "Message") -> bool:
        res = self.func(message)
        if inspect.isawaitable(res):
            return bool(await res)
        return bool(res)

    def __str__(self) -> str:
        func_name = getattr(self.func, "__name__", "custom")
        return f"CustomFilter({func_name})"


def ensure_filter(item: Union[Filter, Callable[["Message"], Any]]) -> Filter:
    if isinstance(item, Filter):
        return item
    if callable(item):
        return CustomFilter(item)
    raise TypeError(f"Expected Filter or callable, got {type(item).__name__}")


class CommandFilter(Filter):
    def __init__(
        self,
        commands: Union[str, List[str]],
        prefixes: Union[str, List[str]] = "/",
        case_sensitive: bool = False,
    ) -> None:
        if isinstance(commands, str):
            self.commands: List[str] = [commands]
        else:
            self.commands = list(commands)

        if isinstance(prefixes, str):
            self.prefixes: List[str] = [prefixes]
        else:
            self.prefixes = list(prefixes)

        self.case_sensitive: bool = case_sensitive
        if not case_sensitive:
            self.commands = [cmd.lower() for cmd in self.commands]

    async def check(self, message: "Message") -> bool:
        text = (message.text or "").strip()
        if not text:
            return False

        parts = text.split()
        first_word = parts[0]
        if not self.case_sensitive:
            first_word = first_word.lower()

        for prefix in self.prefixes:
            p_len = len(prefix)
            if first_word.startswith(prefix):
                cmd_body = first_word[p_len:]
                if "@" in cmd_body:
                    cmd_body = cmd_body.split("@", 1)[0]
                if cmd_body in self.commands:
                    return True
        return False

    def __str__(self) -> str:
        return f"CommandFilter(commands={self.commands!r}, prefixes={self.prefixes!r})"


class RegexFilter(Filter):
    def __init__(self, pattern: Union[str, Pattern[str]], flags: int = 0) -> None:
        if isinstance(pattern, str):
            self.compiled: Pattern[str] = re.compile(pattern, flags)
        else:
            self.compiled = pattern

    async def check(self, message: "Message") -> bool:
        text = message.text or ""
        return bool(self.compiled.search(text))

    def __str__(self) -> str:
        return f"RegexFilter(pattern={self.compiled.pattern!r})"


class TextFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return bool(message.text and message.text.strip())

    def __str__(self) -> str:
        return "TextFilter()"


class PrivateFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return message.chat_guid.startswith("u0")

    def __str__(self) -> str:
        return "PrivateFilter()"


class GroupFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return message.chat_guid.startswith("g0")

    def __str__(self) -> str:
        return "GroupFilter()"


class ChannelFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return message.chat_guid.startswith("c0")

    def __str__(self) -> str:
        return "ChannelFilter()"


class ReplyFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return bool(message.reply_to_message_id)

    def __str__(self) -> str:
        return "ReplyFilter()"


class EditedFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return bool(message.is_edited)

    def __str__(self) -> str:
        return "EditedFilter()"


class AuthorFilter(Filter):
    def __init__(self, guids: Union[str, List[str]]) -> None:
        self.guids: List[str] = [guids] if isinstance(guids, str) else list(guids)

    async def check(self, message: "Message") -> bool:
        return message.author_guid in self.guids

    def __str__(self) -> str:
        return f"AuthorFilter(guids={self.guids!r})"


class ChatFilter(Filter):
    def __init__(self, guids: Union[str, List[str]]) -> None:
        self.guids: List[str] = [guids] if isinstance(guids, str) else list(guids)

    async def check(self, message: "Message") -> bool:
        return message.chat_guid in self.guids

    def __str__(self) -> str:
        return f"ChatFilter(guids={self.guids!r})"


class AllFilter(Filter):
    async def check(self, message: "Message") -> bool:
        return True

    def __str__(self) -> str:
        return "AllFilter()"


command = CommandFilter
regex = RegexFilter
author = AuthorFilter
chat = ChatFilter
create = CustomFilter

text = TextFilter()
private = PrivateFilter()
group = GroupFilter()
channel = ChannelFilter()
reply = ReplyFilter()
edited = EditedFilter()
all = AllFilter()
