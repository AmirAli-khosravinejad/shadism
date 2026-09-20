from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class User:
    guid: str
    name: str = ""
    first_name: str = ""
    last_name: str = ""
    username: str = ""
    bio: str = ""
    phone: str = ""
    is_verified: bool = False
    is_deleted: bool = False
    avatar_thumbnail: Optional[Dict[str, Any]] = None
    online_status: Optional[Dict[str, Any]] = None
    raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def is_online(self) -> bool:
        if isinstance(self.online_status, dict):
            return self.online_status.get("type") == "Exact"
        return False

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
            first_name=first,
            last_name=last,
            username=profile.get("username") or "",
            bio=profile.get("bio") or "",
            phone=profile.get("phone") or "",
            is_verified=bool(profile.get("is_verified", False)),
            is_deleted=bool(profile.get("is_deleted", False)),
            avatar_thumbnail=profile.get("avatar_thumbnail"),
            online_status=profile.get("online_status"),
            raw=profile,
        )

    def __str__(self) -> str:
        return f"User(guid={self.guid!r}, name={self.name!r}, username={self.username!r})"
