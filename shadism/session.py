from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass
class Session:
    phone_number: str
    auth: str = ""
    decode_auth: str = ""
    private_key_pem: str = ""
    tmp_session: str = ""
    key_hex: str = ""
    iv_hex: str = ""
    messenger_host: str = "shadmessenger60.iranlms.ir"
    user_guid: str = ""
    state: int = 0

    def has_auth(self) -> bool:
        return bool(self.auth and self.key_hex)

    def get_key(self) -> bytes:
        return bytes.fromhex(self.key_hex)

    def set_key(self, raw_key: bytes) -> None:
        self.key_hex = raw_key.hex()

    def get_iv(self) -> bytes:
        if self.iv_hex:
            return bytes.fromhex(self.iv_hex)
        return b"\x00" * 16

    def set_iv(self, iv: bytes) -> None:
        self.iv_hex = iv.hex()

    def get_base_url(self) -> str:
        return f"https://{self.messenger_host}/"


class SessionStorage:
    def __init__(self, phone_number: str, directory: str = ".") -> None:
        self._path = os.path.join(directory, f"{phone_number}.session")

    def load(self, phone_number: str) -> Session:
        if not os.path.exists(self._path):
            return Session(phone_number=phone_number)
        with open(self._path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        fields = Session.__dataclass_fields__
        filtered = {k: v for k, v in data.items() if k in fields}
        return Session(**filtered)

    def save(self, session: Session) -> None:
        with open(self._path, "w", encoding="utf-8") as fh:
            json.dump(asdict(session), fh, indent=2)

    def exists(self) -> bool:
        return os.path.exists(self._path)
