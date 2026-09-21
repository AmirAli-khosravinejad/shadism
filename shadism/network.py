from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import sys
from typing import Any, Dict, Final, Optional

import httpx

from shadism.crypto import (
    compute_sign,
    decode_auth,
    decrypt_payload,
    decrypt_payload_probe,
    encrypt_payload,
    rsa_sign,
)
from shadism.session import Session

logger = logging.getLogger("shadism.network")

_DEFAULT_HEADERS: Final[Dict[str, str]] = {
    "Content-Type": "text/plain",
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "en-US,en;q=0.9",
    "Origin": "https://web.shad.ir",
    "Referer": "https://web.shad.ir/",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "cross-site",
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/153.0.0.0 Safari/537.36"
    ),
}

_CLIENT_META: Final[Dict[str, str]] = {
    "app_name": "Main",
    "app_version": "4.4.26",
    "platform": "Web",
    "package": "web.shad.ir",
    "lang_code": "fa",
}

_DEFAULT_MESSENGER_HOSTS: Final[list[str]] = [
    "shadmessenger60.iranlms.ir",
    "shadmessenger145.iranlms.ir",
    "shadmessenger40.iranlms.ir",
    "shadmessenger23.iranlms.ir",
    "shadmessenger57.iranlms.ir",
]


def _detect_local_address() -> Optional[str]:
    if sys.platform == "darwin":
        for iface in ("en0", "en1"):
            try:
                out = subprocess.check_output(
                    ["ifconfig", iface],
                    text=True,
                    stderr=subprocess.DEVNULL,
                    timeout=1.0,
                )
                match = re.search(r"inet (\d+\.\d+\.\d+\.\d+)", out)
                if match and not match.group(1).startswith("127."):
                    return match.group(1)
            except Exception:
                continue
    return None


class NetworkError(Exception):
    def __init__(self, status: str, message: str = "") -> None:
        self.status = status
        self.message = message
        super().__init__(f"Network error [{status}]: {message}")


class Transport:
    def __init__(self, session: Session, timeout: float = 30.0) -> None:
        self._session = session
        local_addr = _detect_local_address()
        http_transport = httpx.AsyncHTTPTransport(
            local_address=local_addr,
            http2=True,
        )
        self._client = httpx.AsyncClient(
            headers=_DEFAULT_HEADERS,
            timeout=httpx.Timeout(timeout, read=timeout),
            follow_redirects=True,
            transport=http_transport,
        )

    async def close(self) -> None:
        await self._client.aclose()

    async def upload_chunk(
        self,
        upload_url: str,
        file_id: str,
        access_hash_send: str,
        chunk_data: bytes,
        part_number: int,
        total_parts: int,
        max_retries: int = 3,
    ) -> Dict[str, Any]:
        auth_candidates = [
            tok for tok in [self._session.auth, self._session.decode_auth] if tok
        ]
        if not auth_candidates:
            auth_candidates = [self._session.auth]

        last_error = None
        for attempt in range(max_retries):
            current_auth = auth_candidates[min(attempt, len(auth_candidates) - 1)]
            headers = {
                "auth": current_auth,
                "file-id": str(file_id),
                "access-hash-send": str(access_hash_send),
                "chunk-size": str(len(chunk_data)),
                "part-number": str(part_number),
                "total-part": str(total_parts),
                "accept": "application/json, text/plain, */*",
                "user-agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/153.0.0.0 Safari/537.36"
                ),
                "origin": "https://web.shad.ir",
                "referer": "https://web.shad.ir/",
            }
            try:
                resp = await self._client.post(
                    upload_url,
                    headers=headers,
                    content=chunk_data,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    if isinstance(data, dict) and data.get("status") in ("OK", None):
                        return data
                    status_det = (
                        data.get("status_det")
                        or data.get("status")
                        if isinstance(data, dict)
                        else "UPLOAD_ERROR"
                    )
                    last_error = NetworkError(str(status_det), f"Upload error response: {data}")
                    if status_det == "NOT_REGISTERED" and attempt < len(auth_candidates) - 1:
                        continue
                    raise last_error
                raise NetworkError(
                    str(resp.status_code),
                    f"HTTP {resp.status_code} during chunk upload: {resp.text}",
                )
            except NetworkError:
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(0.5 * (attempt + 1))
            except Exception as exc:
                if attempt == max_retries - 1:
                    raise NetworkError("UPLOAD_FAILED", str(exc)) from exc
                await asyncio.sleep(1.0 * (attempt + 1))

        if last_error:
            raise last_error
        raise NetworkError("UPLOAD_FAILED", "Exceeded max upload retries")

    def _build_inner_payload(self, method: str, input_data: Dict[str, Any]) -> bytes:
        payload: Dict[str, Any] = {
            "client": _CLIENT_META,
            "method": method,
            "input": input_data,
        }
        logger.debug("Outgoing RPC payload (pre-encrypt): %s", payload)
        return json.dumps(payload, ensure_ascii=False).encode()

    def _build_envelope_a(self, data_enc: str) -> Dict[str, str]:
        auth_token = self._session.decode_auth or decode_auth(self._session.auth)
        envelope = {
            "api_version": "6",
            "auth": auth_token,
            "data_enc": data_enc,
        }
        if self._session.private_key_pem:
            envelope["sign"] = rsa_sign(self._session.private_key_pem, data_enc)
        else:
            envelope["sign"] = compute_sign(self._session.get_key(), data_enc)
        return envelope

    def _build_envelope_b(self, data_enc: str) -> Dict[str, str]:
        return {
            "api_version": "6",
            "tmp_session": self._session.tmp_session,
            "data_enc": data_enc,
        }

    def _build_envelope_c(self, data_enc: str) -> Dict[str, str]:
        return {"data_enc": data_enc}

    async def _post(self, envelope: Dict[str, Any]) -> Dict[str, Any]:
        body = json.dumps(envelope, ensure_ascii=False).encode()
        logger.debug("Outgoing envelope (keys): %s  body_len=%d", list(envelope.keys()), len(body))
        current_host = self._session.messenger_host or _DEFAULT_MESSENGER_HOSTS[0]
        candidates = [current_host] + [h for h in _DEFAULT_MESSENGER_HOSTS if h != current_host]
        last_exc: Optional[Exception] = None

        for host in candidates:
            url = f"https://{host}/"
            try:
                response = await self._client.post(url, content=body)
                await response.aread()
                logger.debug(
                    "Response HTTP/%s  status=%d  content_type=%s  body_len=%d from %s",
                    "2" if response.http_version == "HTTP/2" else "1.1",
                    response.status_code,
                    response.headers.get("content-type", ""),
                    len(response.content),
                    host,
                )
                if response.status_code == 200:
                    if host != self._session.messenger_host:
                        logger.debug("Messenger host rotated from %s to healthy host: %s", self._session.messenger_host, host)
                        self._session.messenger_host = host
                    return response.json()
                elif response.status_code in (500, 502, 503, 504, 408, 429):
                    logger.warning("Host %s returned HTTP %d, failing over to next candidate...", host, response.status_code)
                    last_exc = NetworkError(str(response.status_code), f"HTTP {response.status_code} from {url}")
                    continue
                else:
                    raise NetworkError(str(response.status_code), f"HTTP {response.status_code} from {url}")
            except (httpx.RemoteProtocolError, httpx.ReadError, httpx.TransportError, httpx.ConnectTimeout) as exc:
                logger.warning("Host %s connection error (%s), failing over to next candidate...", host, exc)
                last_exc = NetworkError("TRANSPORT_ERROR", str(exc))
                continue

        if last_exc is not None:
            raise last_exc
        raise NetworkError("NO_HOST_AVAILABLE", "All candidate messenger hosts failed.")

    def _decrypt_response_body(self, data_enc: str) -> bytes:
        try:
            result = decrypt_payload(
                self._session.get_key(), data_enc, self._session.get_iv()
            )
            return result
        except Exception as primary_exc:
            logger.warning(
                "Primary decryption failed (%s). Probing all key/IV derivations...",
                primary_exc,
            )
            tmp_session = self._session.tmp_session
            if not tmp_session:
                raise ValueError(
                    "Primary decryption failed and no tmp_session available for probe."
                ) from primary_exc
            plaintext, working_key, working_iv = decrypt_payload_probe(tmp_session, data_enc)
            self._session.set_key(working_key)
            self._session.set_iv(working_iv)
            logger.debug(
                "Session key/IV updated via probe.  key_prefix=%s  iv_prefix=%s",
                working_key.hex()[:8],
                working_iv.hex()[:8],
            )
            return plaintext

    def _unwrap_response(self, response: Dict[str, Any]) -> Dict[str, Any]:
        logger.debug("Raw server response: %s", response)
        outer_status = response.get("status", "")
        if outer_status and outer_status not in ("OK", "SendCode"):
            raise NetworkError(outer_status, response.get("status_det", ""))

        top_level: Dict[str, Any] = {
            k: v for k, v in response.items()
            if k not in ("data_enc", "status", "status_det")
        }

        data_enc = response.get("data_enc", "")
        if not data_enc:
            inner = response.get("data", {})
            merged = {**top_level, **inner}
            logger.debug("Unwrapped response (no encryption): %s", merged)
            return merged

        raw = self._decrypt_response_body(data_enc)
        inner = json.loads(raw.decode())
        logger.debug("Decrypted inner payload: %s", inner)

        inner_status = inner.get("status", "")
        if inner_status and inner_status not in ("OK", "SendCode"):
            raise NetworkError(inner_status, inner.get("status_det", ""))

        merged = {**top_level, **inner}
        logger.debug("Unwrapped response (merged): %s", merged)
        return merged

    async def send_authenticated(
        self, method: str, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        inner = self._build_inner_payload(method, input_data)
        data_enc = encrypt_payload(
            self._session.get_key(), inner, self._session.get_iv()
        )
        envelope = self._build_envelope_a(data_enc)
        response = await self._post(envelope)
        return self._unwrap_response(response)

    async def send_initial(
        self, method: str, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        inner = self._build_inner_payload(method, input_data)
        data_enc = encrypt_payload(
            self._session.get_key(), inner, self._session.get_iv()
        )
        envelope: Dict[str, str] = {
            "api_version": "6",
            "data_enc": data_enc,
        }
        response = await self._post(envelope)
        return self._unwrap_response(response)

    async def send_handshake(
        self, method: str, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        inner = self._build_inner_payload(method, input_data)
        data_enc = encrypt_payload(
            self._session.get_key(), inner, self._session.get_iv()
        )
        envelope = self._build_envelope_b(data_enc)
        response = await self._post(envelope)
        return self._unwrap_response(response)

    async def send_direct(
        self, method: str, input_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        auth_token = self._session.decode_auth or decode_auth(self._session.auth)
        outer_payload: Dict[str, Any] = {
            "client": _CLIENT_META,
            "auth": auth_token,
            "method": method,
            "input": input_data,
        }
        raw = json.dumps(outer_payload, ensure_ascii=False).encode()
        data_enc = encrypt_payload(
            self._session.get_key(), raw, self._session.get_iv()
        )
        envelope = self._build_envelope_c(data_enc)
        response = await self._post(envelope)
        return self._unwrap_response(response)
