from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from shadism.crypto import (
    compute_sign,
    create_rsa_keys,
    decode_auth,
    decrypt_payload,
    decrypt_rsa_oaep,
    derive_session_key,
    encode_b64,
    encrypt_payload,
    generate_tmp_session,
)
from shadism.network import Transport
from shadism.session import Session, SessionStorage
from shadism.types.message import Message
from shadism.types.user import User
from shadism.types.chat import Chat

logger = logging.getLogger("shadism.methods")

_DEFAULT_MESSENGER_HOSTS = [
    "shadmessenger60.iranlms.ir",
    "shadmessenger145.iranlms.ir",
    "shadmessenger40.iranlms.ir",
    "shadmessenger57.iranlms.ir",
    "shadmessenger23.iranlms.ir",
]


def _pick_host() -> str:
    import random
    return random.choice(_DEFAULT_MESSENGER_HOSTS)


def _split_phone(phone: str) -> tuple[str, str]:
    phone = phone.strip().lstrip("+")
    if phone.startswith("00"):
        phone = phone[2:]
    if phone.startswith("98") and len(phone) >= 12:
        return phone[2:], "98"
    if phone.startswith("0") and len(phone) == 11:
        return phone[1:], "98"
    if len(phone) == 10 and phone.startswith("9"):
        return phone, "98"
    return phone, "98"


def _prepare_file(
    file: Union[str, bytes, Path],
    file_name: Optional[str] = None,
    mime: Optional[str] = None,
) -> tuple[bytes, str, str]:
    if isinstance(file, (str, Path)):
        path_obj = Path(file)
        if not path_obj.is_file():
            raise FileNotFoundError(f"File not found: {file}")
        data = path_obj.read_bytes()
        resolved_name = file_name or path_obj.name
    elif isinstance(file, bytes):
        data = file
        resolved_name = file_name or ""
    else:
        raise TypeError("file must be a file path or raw bytes.")

    if mime is not None:
        resolved_mime = mime.lstrip(".").lower()
    elif "." in resolved_name:
        resolved_mime = resolved_name.rsplit(".", 1)[-1].lower()
    elif data.startswith(b"\x89PNG\r\n\x1a\n"):
        resolved_mime = "png"
    elif data.startswith(b"\xff\xd8\xff"):
        resolved_mime = "jpg"
    elif data.startswith(b"GIF87a") or data.startswith(b"GIF89a"):
        resolved_mime = "gif"
    elif data.startswith(b"%PDF"):
        resolved_mime = "pdf"
    elif data.startswith(b"OggS"):
        resolved_mime = "ogg"
    elif data.startswith(b"ID3") or data.startswith(b"\xff\xfb") or data.startswith(b"\xff\xf3"):
        resolved_mime = "mp3"
    else:
        resolved_mime = "bin"

    if not resolved_name:
        resolved_name = f"file.{resolved_mime}"

    return data, resolved_name, resolved_mime


def _extract_image_meta(data: bytes) -> tuple[int, int, Optional[str]]:
    try:
        from PIL import Image
        import io
        import base64

        with Image.open(io.BytesIO(data)) as im:
            w, h = im.size
            if h > w:
                th = 40
                tw = max(1, round(th * w / max(h, 1)))
            else:
                tw = 40
                th = max(1, round(tw * h / max(w, 1)))
            resample_filter = getattr(getattr(Image, "Resampling", Image), "LANCZOS", Image.LANCZOS if hasattr(Image, "LANCZOS") else 1)
            thumb_im = im.resize((tw, th), resample_filter)
            buf = io.BytesIO()
            thumb_im.save(buf, format="PNG")
            thumb_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
            return w, h, thumb_b64
    except Exception:
        return 720, 720, None


class Methods:
    def __init__(
        self,
        session: Session,
        transport: Transport,
        storage: SessionStorage,
        client: Optional[Any] = None,
    ) -> None:
        self._session = session
        self._transport = transport
        self._storage = storage
        self._client = client

    async def login_flow(self, phone_number: str) -> None:
        subscriber, country_code = _split_phone(phone_number)
        full_phone = f"{country_code}{subscriber}"
        logger.info(
            "Starting login flow for %s -> phone=%s",
            phone_number,
            full_phone,
        )

        tmp_session = generate_tmp_session()
        self._session.tmp_session = tmp_session
        self._session.set_key(derive_session_key(tmp_session))
        self._session.messenger_host = _pick_host()
        logger.info(
            "tmp_session generated: %s  host: %s",
            tmp_session,
            self._session.messenger_host,
        )
        self._storage.save(self._session)

        send_code_response = await self._send_code(full_phone)
        logger.debug("sendCode full response: %s", send_code_response)

        phone_code_hash = send_code_response.get("phone_code_hash") or send_code_response.get("data", {}).get("phone_code_hash", "")
        if not phone_code_hash:
            raise RuntimeError(
                f"sendCode failed: no phone_code_hash in server response. "
                f"Full response: {send_code_response}"
            )
        logger.debug("phone_code_hash: %s", phone_code_hash)

        public_key_str, private_key_pem = create_rsa_keys()
        self._session.private_key_pem = private_key_pem

        otp = input("Enter OTP code: ").strip()

        sign_in_response = await self._sign_in(full_phone, otp, phone_code_hash, public_key_str)
        logger.debug("signIn full response: %s", sign_in_response)

        raw_auth = sign_in_response.get("auth") or sign_in_response.get("data", {}).get("auth", "")
        user_data = sign_in_response.get("user") or sign_in_response.get("data", {}).get("user", {})
        user_guid = user_data.get("user_guid", "")

        if not raw_auth:
            raise RuntimeError(
                f"Authentication failed: server did not return auth token. "
                f"Full response: {sign_in_response}"
            )

        decrypted_auth = decrypt_rsa_oaep(private_key_pem, raw_auth)
        permanent_key = derive_session_key(decrypted_auth)

        self._session.auth = decrypted_auth
        self._session.decode_auth = decode_auth(decrypted_auth)
        self._session.set_key(permanent_key)
        self._session.user_guid = user_guid
        self._storage.save(self._session)
        logger.info("Login successful. User GUID: %s", user_guid)

        await self.register_device()
        logger.info("Device registered successfully.")

    async def register_device(self) -> Dict[str, Any]:
        import random
        device_hash = "".join(random.choices("0123456789", k=26))
        input_data: Dict[str, Any] = {
            "app_version": "WB_4.4.26",
            "device_hash": device_hash,
            "device_model": "Chrome 153",
            "is_multi_account": False,
            "lang_code": "fa",
            "system_version": "Mac/iOS",
            "token": "",
            "token_type": "Web",
        }
        return await self._transport.send_authenticated("registerDevice", input_data)

    async def _send_code(self, full_phone: str) -> Dict[str, Any]:
        input_data = {
            "phone_number": full_phone,
            "send_type": "SMS",
        }
        return await self._transport.send_handshake("sendCode", input_data)

    async def _sign_in(
        self, full_phone: str, otp: str, phone_code_hash: str, public_key: str
    ) -> Dict[str, Any]:
        input_data = {
            "phone_code": otp,
            "phone_number": full_phone,
            "phone_code_hash": phone_code_hash,
            "public_key": public_key,
        }
        return await self._transport.send_handshake("signIn", input_data)

    async def send_message(
        self,
        object_guid: str,
        text: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        file_inline: Optional[Dict[str, Any]] = None,
    ) -> Message:
        input_data: Dict[str, Any] = {
            "object_guid": object_guid,
            "rnd": str(int.from_bytes(os.urandom(4), "big")),
        }
        if text:
            input_data["text"] = text.strip()
        elif file_inline is None:
            input_data["text"] = ""
        if reply_to_message_id is not None:
            input_data["reply_to_message_id"] = reply_to_message_id
        if file_inline is not None:
            input_data["file_inline"] = file_inline

        response = await self._transport.send_authenticated("sendMessage", input_data)
        data_dict: Dict[str, Any] = response.get("data") if isinstance(response.get("data"), dict) else response
        msg_update: Dict[str, Any] = data_dict.get("message_update", {}) if isinstance(data_dict, dict) else {}
        inner_msg: Dict[str, Any] = (
            msg_update.get("message", {})
            if isinstance(msg_update, dict)
            else (data_dict.get("message", {}) if isinstance(data_dict, dict) else {})
        )

        msg_id = (
            msg_update.get("message_id")
            or inner_msg.get("message_id")
            or data_dict.get("message_id")
            or ""
        )
        msg_text = inner_msg.get("text") or data_dict.get("text") or text

        raw_msg: Dict[str, Any] = {
            "message_id": str(msg_id),
            "text": msg_text,
            "object_guid": msg_update.get("object_guid") or data_dict.get("object_guid") or object_guid,
            "author_object_guid": self._session.user_guid,
            "message": inner_msg,
        }
        return Message.from_dict(raw_msg, client=self._client)

    async def edit_message(
        self,
        object_guid: str,
        message_id: str,
        text: str,
    ) -> Message:
        input_data: Dict[str, Any] = {
            "object_guid": object_guid,
            "message_id": message_id,
            "text": text,
        }
        response = await self._transport.send_authenticated("editMessage", input_data)
        data_dict: Dict[str, Any] = response.get("data") if isinstance(response.get("data"), dict) else response
        msg_update: Dict[str, Any] = data_dict.get("message_update", {}) if isinstance(data_dict, dict) else {}
        inner_msg: Dict[str, Any] = msg_update.get("message", {}) if isinstance(msg_update, dict) else {}

        raw_msg: Dict[str, Any] = {
            "message_id": str(msg_update.get("message_id") or message_id),
            "text": inner_msg.get("text") or text,
            "object_guid": msg_update.get("object_guid") or object_guid,
            "author_object_guid": self._session.user_guid,
            "is_edited": True,
        }
        return Message.from_dict(raw_msg, client=self._client)

    async def delete_messages(
        self,
        object_guid: str,
        message_ids: Union[str, int, List[Union[str, int]]],
        delete_type: str = "Global",
    ) -> Dict[str, Any]:
        if delete_type not in ("Global", "Local"):
            raise ValueError('delete_type must be either "Global" or "Local"')
        if isinstance(message_ids, (str, int)):
            ids_list = [str(message_ids)]
        else:
            ids_list = [str(mid) for mid in message_ids]

        input_data: Dict[str, Any] = {
            "object_guid": object_guid,
            "message_ids": ids_list,
            "type": delete_type,
        }
        return await self._transport.send_authenticated("deleteMessages", input_data)

    async def delete_message(
        self,
        object_guid: str,
        message_id: Union[str, int],
        delete_type: str = "Global",
    ) -> Dict[str, Any]:
        return await self.delete_messages(object_guid, [message_id], delete_type)

    async def request_send_file(
        self,
        file_name: str,
        size: int,
        mime: str,
    ) -> Dict[str, Any]:
        input_data: Dict[str, Any] = {
            "file_name": file_name,
            "size": size,
            "mime": mime,
        }
        return await self._transport.send_authenticated("requestSendFile", input_data)

    async def upload_file(
        self,
        file: Union[str, bytes, Path],
        file_name: Optional[str] = None,
        mime: Optional[str] = None,
        chunk_size: int = 131072,
    ) -> Dict[str, Any]:
        data, resolved_name, resolved_mime = _prepare_file(file, file_name, mime)
        file_size = len(data)

        req_res = await self.request_send_file(resolved_name, file_size, resolved_mime)
        req_data = req_res.get("data") if isinstance(req_res.get("data"), dict) else req_res

        raw_file_id = req_data.get("id") or req_data.get("file_id") or ""
        raw_dc_id = req_data.get("dc_id") or ""
        upload_url = req_data.get("upload_url") or ""
        access_hash_send = req_data.get("access_hash_send") or ""

        if not upload_url or not access_hash_send or not raw_file_id:
            raise RuntimeError(f"Failed to acquire upload slot: {req_res}")

        if not upload_url.startswith("http"):
            upload_url = f"https://{upload_url}"
        if not upload_url.endswith(".ashx"):
            upload_url = upload_url.rstrip("/") + "/UploadFile.ashx"

        total_parts = (file_size + chunk_size - 1) // chunk_size if file_size > 0 else 1

        last_resp: Dict[str, Any] = {}
        for part_number in range(1, total_parts + 1):
            start_idx = (part_number - 1) * chunk_size
            end_idx = min(start_idx + chunk_size, file_size)
            chunk_data = data[start_idx:end_idx]
            try:
                last_resp = await self._transport.upload_chunk(
                    upload_url=upload_url,
                    file_id=str(raw_file_id),
                    access_hash_send=access_hash_send,
                    chunk_data=chunk_data,
                    part_number=part_number,
                    total_parts=total_parts,
                )
            except Exception as exc:
                if "NOT_REGISTERED" in str(exc):
                    logger.info("Upload server returned NOT_REGISTERED. Re-registering device and slot...")
                    await self.register_device()
                    req_res = await self.request_send_file(resolved_name, file_size, resolved_mime)
                    req_data = req_res.get("data") if isinstance(req_res.get("data"), dict) else req_res
                    raw_file_id = req_data.get("id") or req_data.get("file_id") or raw_file_id
                    raw_dc_id = req_data.get("dc_id") or raw_dc_id
                    upload_url = req_data.get("upload_url") or upload_url
                    access_hash_send = req_data.get("access_hash_send") or access_hash_send
                    if not upload_url.startswith("http"):
                        upload_url = f"https://{upload_url}"
                    if not upload_url.endswith(".ashx"):
                        upload_url = upload_url.rstrip("/") + "/UploadFile.ashx"
                    last_resp = await self._transport.upload_chunk(
                        upload_url=upload_url,
                        file_id=str(raw_file_id),
                        access_hash_send=access_hash_send,
                        chunk_data=chunk_data,
                        part_number=part_number,
                        total_parts=total_parts,
                    )
                else:
                    raise

        resp_data = last_resp.get("data") if isinstance(last_resp.get("data"), dict) else last_resp
        access_hash_rec = resp_data.get("access_hash_rec") if isinstance(resp_data, dict) else None

        if not access_hash_rec:
            raise RuntimeError(f"Upload failed to return access_hash_rec: {last_resp}")

        dc_id = int(raw_dc_id) if str(raw_dc_id).isdigit() else raw_dc_id
        file_id = int(raw_file_id) if str(raw_file_id).isdigit() else str(raw_file_id)

        return {
            "dc_id": dc_id,
            "file_id": file_id,
            "file_name": resolved_name,
            "size": file_size,
            "mime": resolved_mime,
            "access_hash_rec": access_hash_rec,
            "raw_bytes": data,
        }

    async def send_photo(
        self,
        object_guid: str,
        photo: Union[str, bytes, Path],
        caption: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
        file_name: Optional[str] = None,
    ) -> Message:
        data, resolved_name, resolved_mime = _prepare_file(photo, file_name, None)
        if resolved_mime not in ("png", "jpg", "jpeg", "webp", "gif"):
            resolved_mime = "png" if data.startswith(b"\x89PNG") else "jpg"
            if "." not in resolved_name or resolved_name.endswith(".bin"):
                resolved_name = f"photo.{resolved_mime}"

        upload_info = await self.upload_file(data, file_name=resolved_name, mime=resolved_mime)
        w, h, thumb_b64 = _extract_image_meta(upload_info["raw_bytes"])

        raw_dc_id = upload_info.get("dc_id")
        dc_id = int(raw_dc_id) if raw_dc_id is not None and str(raw_dc_id).isdigit() else raw_dc_id

        raw_file_id = upload_info.get("file_id")
        file_id = int(raw_file_id) if raw_file_id is not None and str(raw_file_id).isdigit() else str(raw_file_id or "")

        file_inline: Dict[str, Any] = {
            "dc_id": dc_id,
            "file_id": file_id,
            "file_name": upload_info["file_name"],
            "size": int(upload_info["size"]),
            "mime": upload_info["mime"],
            "access_hash_rec": upload_info["access_hash_rec"],
            "type": "Image",
            "is_spoil": False,
            "width": max(w, 1),
            "height": max(h, 1),
        }
        if thumb_b64:
            file_inline["thumb_inline"] = thumb_b64

        return await self.send_message(
            object_guid=object_guid,
            text=caption,
            reply_to_message_id=reply_to_message_id,
            file_inline=file_inline,
        )

    async def send_file(
        self,
        object_guid: str,
        file: Union[str, bytes, Path],
        file_name: Optional[str] = None,
        mime: Optional[str] = None,
        caption: Optional[str] = None,
        reply_to_message_id: Optional[str] = None,
    ) -> Message:
        upload_info = await self.upload_file(file, file_name=file_name, mime=mime)

        raw_dc_id = upload_info.get("dc_id")
        dc_id = int(raw_dc_id) if raw_dc_id is not None and str(raw_dc_id).isdigit() else raw_dc_id

        raw_file_id = upload_info.get("file_id")
        file_id = int(raw_file_id) if raw_file_id is not None and str(raw_file_id).isdigit() else str(raw_file_id or "")

        file_inline: Dict[str, Any] = {
            "dc_id": dc_id,
            "file_id": file_id,
            "file_name": upload_info["file_name"],
            "size": int(upload_info["size"]),
            "mime": upload_info["mime"],
            "access_hash_rec": upload_info["access_hash_rec"],
            "type": "File",
            "is_spoil": False,
        }

        return await self.send_message(
            object_guid=object_guid,
            text=caption,
            reply_to_message_id=reply_to_message_id,
            file_inline=file_inline,
        )

    async def get_user_info(self, user_guid: Optional[str] = None) -> User:
        input_data: Dict[str, Any] = {"user_guid": user_guid} if user_guid else {}
        response = await self._transport.send_authenticated("getUserInfo", input_data)
        user_dict = response.get("user", {}) or response.get("data", {}).get("user", {}) or response
        return User.from_dict(user_dict)

    async def update_profile(
        self,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
        bio: Optional[str] = None,
    ) -> bool:
        input_data: Dict[str, Any] = {"updated_parameters": []}
        if first_name is not None:
            input_data["first_name"] = first_name
            input_data["updated_parameters"].append("first_name")
        if last_name is not None:
            input_data["last_name"] = last_name
            input_data["updated_parameters"].append("last_name")
        if bio is not None:
            input_data["bio"] = bio
            input_data["updated_parameters"].append("bio")

        response = await self._transport.send_authenticated("updateProfile", input_data)
        return response.get("status") == "OK"

    async def get_chats(self, start_id: Optional[str] = None) -> Dict[str, Any]:
        input_data: Dict[str, Any] = {}
        if start_id is not None:
            input_data["start_id"] = start_id
        return await self._transport.send_authenticated("getChats", input_data)

    async def get_updates(self, state: int) -> Dict[str, Any]:
        input_data = {
            "state": str(state),
            "limit": 200,
        }
        return await self._transport.send_authenticated("getChats", input_data)

    async def get_messages(
        self,
        object_guid: str,
        limit: int = 50,
        sort: str = "FromMax",
        max_id: Optional[str] = None,
        min_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        input_data: Dict[str, Any] = {
            "object_guid": object_guid,
            "sort": sort,
            "limit": limit,
        }
        if max_id is not None:
            input_data["max_id"] = max_id
        if min_id is not None:
            input_data["min_id"] = min_id
        return await self._transport.send_authenticated("getMessages", input_data)

    async def get_chats_updates(self, state: Optional[int] = None) -> Dict[str, Any]:
        import time
        current_state = state if state and state > 0 else int(time.time()) - 150
        input_data = {"state": current_state}
        return await self._transport.send_authenticated("getChatsUpdates", input_data)

    async def get_messages_updates(
        self,
        object_guid: str,
        state: Optional[int] = None,
    ) -> Dict[str, Any]:
        import time
        current_state = state if state and state > 0 else int(time.time()) - 150
        input_data = {
            "object_guid": object_guid,
            "state": current_state,
        }
        return await self._transport.send_authenticated("getMessagesUpdates", input_data)

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
        target_guid = object_guid or guid or ""
        if not target_guid:
            raise ValueError("object_guid (or guid) must be provided.")

        target_limit = max(1, limit)
        collected_messages: List[Message] = []
        current_max_id = max_id
        seen_message_ids: set[str] = set()

        while len(collected_messages) < target_limit:
            batch_size = min(target_limit - len(collected_messages), 50)
            try:
                res = await self.get_messages(
                    object_guid=target_guid,
                    limit=batch_size,
                    sort=sort,
                    max_id=current_max_id,
                    min_id=min_id,
                )
            except Exception as exc:
                logger.debug("get_messages call error: %s", exc)
                break

            data_dict: Dict[str, Any] = res.get("data") if isinstance(res.get("data"), dict) else res
            raw_list: List[Any] = (
                data_dict.get("messages")
                or data_dict.get("updated_messages")
                or []
            ) if isinstance(data_dict, dict) else []

            if not raw_list:
                break

            new_in_batch: List[Message] = []
            lowest_id_in_batch: Optional[str] = None

            for item in raw_list:
                if not isinstance(item, dict):
                    continue
                mid = str(item.get("message_id") or (item.get("message") or {}).get("message_id") or "")
                if not mid or mid in seen_message_ids:
                    continue
                seen_message_ids.add(mid)
                lowest_id_in_batch = mid

                if not item.get("object_guid") and not (item.get("message") or {}).get("object_guid"):
                    item["object_guid"] = target_guid

                msg_obj = Message.from_dict(item, client=self._client)
                new_in_batch.append(msg_obj)

            if not new_in_batch:
                break

            collected_messages.extend(new_in_batch)

            if len(raw_list) < batch_size or not lowest_id_in_batch:
                break

            if current_max_id == lowest_id_in_batch:
                break

            current_max_id = lowest_id_in_batch

        if not collected_messages:
            try:
                updates_res = await self.get_messages_updates(object_guid=target_guid, state=state)
                u_data: Dict[str, Any] = updates_res.get("data") if isinstance(updates_res.get("data"), dict) else updates_res
                u_list: List[Any] = (
                    u_data.get("updated_messages")
                    or u_data.get("messages")
                    or []
                ) if isinstance(u_data, dict) else []
                for item in u_list:
                    if not isinstance(item, dict):
                        continue
                    mid = str(item.get("message_id") or (item.get("message") or {}).get("message_id") or "")
                    if not mid or mid in seen_message_ids:
                        continue
                    seen_message_ids.add(mid)
                    if not item.get("object_guid") and not (item.get("message") or {}).get("object_guid"):
                        item["object_guid"] = target_guid
                    collected_messages.append(Message.from_dict(item, client=self._client))
                    if len(collected_messages) >= target_limit:
                        break
            except Exception as exc:
                logger.debug("get_messages_updates fallback error: %s", exc)

        return collected_messages[:target_limit]

    async def get_chat_info(self, object_guid: str) -> Chat:
        if object_guid.startswith("g0"):
            method = "getGroupInfo"
            input_data = {"group_guid": object_guid}
        elif object_guid.startswith("c0"):
            method = "getChannelInfo"
            input_data = {"channel_guid": object_guid}
        else:
            method = "getUserInfo"
            input_data = {"user_guid": object_guid}
        response = await self._transport.send_authenticated(method, input_data)
        data_dict = response.get("data") if isinstance(response.get("data"), dict) else response
        return Chat.from_dict(data_dict, client=self._client)

    async def get_chat_info_by_username(self, username: str) -> Chat:
        clean_username = username.lstrip("@").strip()
        try:
            response = await self._transport.send_authenticated(
                "getObjectByUsername",
                {"username": clean_username},
            )
        except Exception:
            response = await self._transport.send_authenticated(
                "getObjectInfoByUsername",
                {"username": clean_username},
            )
        data_dict = response.get("data") if isinstance(response.get("data"), dict) else response
        return Chat.from_dict(data_dict, client=self._client)

    async def join_voice_chat(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
        sdp_offer_data: str = "",
    ) -> Dict[str, Any]:
        resolved_voice_chat_id = voice_chat_id
        if not resolved_voice_chat_id:
            chat_info = await self.get_chat_info(chat_guid)
            resolved_voice_chat_id = chat_info.voice_chat_id
            if not resolved_voice_chat_id:
                raise ValueError(f"No active voice chat found in {chat_guid}")

        method = "joinGroupVoiceChat" if chat_guid.startswith("g0") else "joinChannelVoiceChat"
        input_data = {
            "chat_guid": chat_guid,
            "voice_chat_id": resolved_voice_chat_id,
            "sdp_offer_data": sdp_offer_data,
            "self_object_guid": self._session.user_guid,
        }
        return await self._transport.send_authenticated(method, input_data)

    async def leave_voice_chat(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_voice_chat_id = voice_chat_id
        if not resolved_voice_chat_id:
            chat_info = await self.get_chat_info(chat_guid)
            resolved_voice_chat_id = chat_info.voice_chat_id
            if not resolved_voice_chat_id:
                raise ValueError(f"No active voice chat found in {chat_guid}")

        method = "leaveGroupVoiceChat" if chat_guid.startswith("g0") else "leaveChannelVoiceChat"
        input_data = {
            ("group_guid" if chat_guid.startswith("g0") else "channel_guid"): chat_guid,
            "voice_chat_id": resolved_voice_chat_id,
        }
        return await self._transport.send_authenticated(method, input_data)

    async def get_voice_chat_participants(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_voice_chat_id = voice_chat_id
        if not resolved_voice_chat_id:
            chat_info = await self.get_chat_info(chat_guid)
            resolved_voice_chat_id = chat_info.voice_chat_id
            if not resolved_voice_chat_id:
                raise ValueError(f"No active voice chat found in {chat_guid}")

        method = "getGroupVoiceChatParticipants" if chat_guid.startswith("g0") else "getChannelVoiceChatParticipants"
        input_data = {
            "chat_guid": chat_guid,
            "voice_chat_id": resolved_voice_chat_id,
        }
        return await self._transport.send_authenticated(method, input_data)

    async def create_voice_chat(
        self,
        chat_guid: str,
    ) -> Dict[str, Any]:
        method = "createGroupVoiceChat" if chat_guid.startswith("g0") else "createChannelVoiceChat"
        input_data = {
            ("group_guid" if chat_guid.startswith("g0") else "channel_guid"): chat_guid,
        }
        return await self._transport.send_authenticated(method, input_data)

    async def discard_voice_chat(
        self,
        chat_guid: str,
        voice_chat_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        resolved_voice_chat_id = voice_chat_id
        if not resolved_voice_chat_id:
            chat_info = await self.get_chat_info(chat_guid)
            resolved_voice_chat_id = chat_info.voice_chat_id
            if not resolved_voice_chat_id:
                raise ValueError(f"No active voice chat found in {chat_guid}")

        method = "discardGroupVoiceChat" if chat_guid.startswith("g0") else "discardChannelVoiceChat"
        input_data = {
            ("group_guid" if chat_guid.startswith("g0") else "channel_guid"): chat_guid,
            "voice_chat_id": resolved_voice_chat_id,
        }
        return await self._transport.send_authenticated(method, input_data)

    async def set_voice_chat_state(
        self,
        chat_guid: str,
        voice_chat_id: str,
        activity: str = "Speaking",
        participant_object_guid: Optional[str] = None,
    ) -> Dict[str, Any]:
        method = "setGroupVoiceChatState" if chat_guid.startswith("g0") else "setChannelVoiceChatState"
        input_data = {
            "chat_guid": chat_guid,
            "voice_chat_id": voice_chat_id,
            "action": activity,
            "participant_object_guid": participant_object_guid or self._session.user_guid,
        }
        return await self._transport.send_authenticated(method, input_data)

    async def check_user_username(self, username: str) -> Dict[str, Any]:
        cleaned = username.lstrip("@").strip()
        return await self._transport.send_authenticated("checkUserUsername", {"username": cleaned})

    async def check_channel_username(self, username: str) -> Dict[str, Any]:
        cleaned = username.lstrip("@").strip()
        return await self._transport.send_authenticated("checkChannelUsername", {"username": cleaned})

    async def set_block_user(self, user_guid: str, action: str = "Block") -> Dict[str, Any]:
        if action not in ("Block", "Unblock"):
            raise ValueError("action must be either 'Block' or 'Unblock'")
        return await self._transport.send_authenticated(
            "setBlockUser", {"user_guid": user_guid, "action": action}
        )

    async def get_avatars(self, object_guid: str) -> Dict[str, Any]:
        return await self._transport.send_authenticated("getAvatars", {"object_guid": object_guid})

    async def send_chat_activity(self, object_guid: str, activity: str = "Typing") -> Dict[str, Any]:
        if activity not in ("Typing", "Uploading", "Recording"):
            raise ValueError("activity must be one of 'Typing', 'Uploading', 'Recording'")
        return await self._transport.send_authenticated(
            "sendChatActivity", {"object_guid": object_guid, "activity": activity}
        )

    async def seen_chats(self, seen_list: Dict[str, str]) -> Dict[str, Any]:
        return await self._transport.send_authenticated("seenChats", {"seen_list": seen_list})

    async def delete_chat_history(
        self, object_guid: str, last_message_id: Optional[str] = None
    ) -> Dict[str, Any]:
        input_data: Dict[str, Any] = {"object_guid": object_guid}
        if last_message_id is not None:
            input_data["last_message_id"] = last_message_id
        return await self._transport.send_authenticated("deleteChatHistory", input_data)

    async def get_group_link(self, group_guid: str) -> Dict[str, Any]:
        return await self._transport.send_authenticated("getGroupLink", {"group_guid": group_guid})

    async def get_channel_link(self, channel_guid: str) -> Dict[str, Any]:
        return await self._transport.send_authenticated("getChannelLink", {"channel_guid": channel_guid})

    async def join_channel_action(self, channel_guid: str, action: str = "Join") -> Dict[str, Any]:
        if action not in ("Join", "Remove"):
            raise ValueError("action must be either 'Join' or 'Remove'")
        return await self._transport.send_authenticated(
            "joinChannelAction", {"channel_guid": channel_guid, "action": action}
        )

    async def join_channel_by_link(self, join_hash: str) -> Dict[str, Any]:
        token = join_hash.split("/")[-1].strip()
        return await self._transport.send_authenticated("joinChannelByLink", {"hash": token})

    async def join_group(self, join_hash: str) -> Dict[str, Any]:
        token = join_hash.split("/")[-1].strip()
        return await self._transport.send_authenticated("joinGroup", {"hash": token})

    async def leave_group(self, group_guid: str) -> Dict[str, Any]:
        return await self._transport.send_authenticated("leaveGroup", {"group_guid": group_guid})

    async def add_contact(
        self, phone: str, first_name: str, last_name: str = ""
    ) -> Dict[str, Any]:
        cleaned = phone.strip().replace(" ", "").replace("-", "").lstrip("+")
        if cleaned.startswith("98"):
            cleaned = "0" + cleaned[2:]
        elif not cleaned.startswith("0") and len(cleaned) == 10:
            cleaned = "0" + cleaned
        input_data = {
            "phone": cleaned,
            "first_name": str(first_name),
            "last_name": str(last_name),
        }
        return await self._transport.send_authenticated("addAddressBook", input_data)

    async def delete_contact(self, user_guid: str) -> Dict[str, Any]:
        return await self._transport.send_authenticated("deleteContact", {"user_guid": user_guid})

    async def get_contacts(self, start_id: Optional[str] = None) -> Dict[str, Any]:
        input_data = {"start_id": str(start_id) if start_id else None}
        return await self._transport.send_authenticated("getContacts", input_data)

    async def get_user_by_phone(
        self, phone: str, auto_delete: bool = True
    ) -> Optional[User]:
        res = await self.add_contact(phone=phone, first_name="ContactLookup", last_name="")
        data = res.get("data") if isinstance(res.get("data"), dict) else res
        user_dict = data.get("user")
        if not user_dict and isinstance(data.get("contact"), dict):
            user_dict = data["contact"].get("user")

        if not user_dict or not isinstance(user_dict, dict):
            return None

        user_obj = User.from_dict(user_dict)
        if auto_delete and user_obj.guid:
            try:
                await self.delete_contact(user_obj.guid)
            except Exception:
                pass

        return user_obj

    async def create_group(
        self,
        title: str,
        member_guids: Optional[Union[str, List[str]]] = None,
        description: Optional[str] = None,
    ) -> Chat:
        guids: List[str] = []
        if member_guids:
            if isinstance(member_guids, str):
                guids = [member_guids.strip()]
            else:
                guids = [str(g).strip() for g in member_guids if str(g).strip()]

        input_data: Dict[str, Any] = {
            "title": title.strip(),
            "member_guids": guids,
        }
        if description:
            input_data["description"] = description.strip()

        response = await self._transport.send_authenticated("addGroup", input_data)
        data_dict = response.get("data") if isinstance(response.get("data"), dict) else response
        return Chat.from_dict(data_dict, client=self._client)

    async def add_group(
        self,
        title: str,
        member_guids: Optional[Union[str, List[str]]] = None,
        description: Optional[str] = None,
    ) -> Chat:
        return await self.create_group(title, member_guids, description)
