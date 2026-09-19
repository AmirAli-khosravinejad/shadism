from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class User:
    guid: str
    name: str = ""
    username: str = ""
    bio: str = ""
    phone: str = ""
    is_verified: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "User":
        profile = data.get("user", data) if isinstance(data, dict) else {}
        if not isinstance(profile, dict):
            profile = {}
        first = profile.get("first_name") or ""
        last = profile.get("last_name") or ""
        full_name = f"{first} {last}".strip()
        return cls(
            guid=profile.get("user_guid") or "",
            name=full_name,
            username=profile.get("username") or "",
            bio=profile.get("bio") or "",
            phone=profile.get("phone") or "",
            is_verified=bool(profile.get("is_verified", False)),
        )

    def __str__(self) -> str:
        return f"User(guid={self.guid!r}, name={self.name!r}, username={self.username!r})"
