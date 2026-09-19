from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
import secrets
import string
from typing import Final, Optional

from cryptography.hazmat.primitives.ciphers.algorithms import AES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from cryptography.hazmat.primitives import padding as sym_padding
from cryptography.hazmat.backends import default_backend

_AES_BLOCK_SIZE: Final[int] = 16
_AES_KEY_BITS: Final[int] = 256
_IV: Final[bytes] = b"\x00" * _AES_BLOCK_SIZE
_BACKEND = default_backend()

logger = logging.getLogger("shadism.crypto")


def derive_passphrase(auth: str) -> str:
    if len(auth) != 32:
        raise ValueError("auth length should be 32 characters")
    chunks = [auth[i : i + 8] for i in range(0, 32, 8)]
    result_list = []
    for character in chunks[2] + chunks[0] + chunks[3] + chunks[1]:
        result_list.append(chr(((ord(character) - 97 + 9) % 26) + 97))
    return "".join(result_list)


def decode_auth(auth: str) -> str:
    result_list = []
    digits = "0123456789"
    translation_table_lower = str.maketrans(
        string.ascii_lowercase,
        "".join([chr(((32 - (ord(c) - 97)) % 26) + 97) for c in string.ascii_lowercase]),
    )
    translation_table_upper = str.maketrans(
        string.ascii_uppercase,
        "".join([chr(((29 - (ord(c) - 65)) % 26) + 65) for c in string.ascii_uppercase]),
    )
    for char in auth:
        if char in string.ascii_lowercase:
            result_list.append(char.translate(translation_table_lower))
        elif char in string.ascii_uppercase:
            result_list.append(char.translate(translation_table_upper))
        elif char in digits:
            result_list.append(chr(((13 - (ord(char) - 48)) % 10) + 48))
        else:
            result_list.append(char)
    return "".join(result_list)


def create_rsa_keys() -> tuple[str, str]:
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives import serialization

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=1024)
    pub_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    pub_b64 = base64.b64encode(pub_bytes).decode("utf-8")
    public_key_str = decode_auth(pub_b64)
    priv_bytes = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return public_key_str, priv_bytes.decode("utf-8")


def decrypt_rsa_oaep(private_key_pem: str, data: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
    from cryptography.hazmat.primitives import hashes, serialization

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError("Expected an RSA private key")
    decrypted = private_key.decrypt(
        base64.b64decode(data),
        asym_padding.OAEP(
            mgf=asym_padding.MGF1(algorithm=hashes.SHA1()),
            algorithm=hashes.SHA1(),
            label=None,
        ),
    )
    return decrypted.decode("utf-8")


def rsa_sign(private_key_pem: str, data: str) -> str:
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding, rsa
    from cryptography.hazmat.primitives import hashes, serialization

    private_key = serialization.load_pem_private_key(
        private_key_pem.encode("utf-8"), password=None
    )
    if not isinstance(private_key, rsa.RSAPrivateKey):
        raise TypeError("Expected an RSA private key")
    signature = private_key.sign(
        data.encode("utf-8"),
        asym_padding.PKCS1v15(),
        hashes.SHA256(),
    )
    return base64.b64encode(signature).decode("utf-8")


def derive_aes_key(raw_key: str) -> bytes:
    return hashlib.sha256(raw_key.encode()).digest()


def pkcs7_pad(data: bytes) -> bytes:
    padder = sym_padding.PKCS7(_AES_BLOCK_SIZE * 8).padder()
    return padder.update(data) + padder.finalize()


def pkcs7_unpad(data: bytes) -> bytes:
    unpadder = sym_padding.PKCS7(_AES_BLOCK_SIZE * 8).unpadder()
    return unpadder.update(data) + unpadder.finalize()


def aes_encrypt(key: bytes, plaintext: bytes, iv: bytes = _IV) -> bytes:
    cipher = Cipher(AES(key), modes.CBC(iv), backend=_BACKEND)
    encryptor = cipher.encryptor()
    padded = pkcs7_pad(plaintext)
    return encryptor.update(padded) + encryptor.finalize()


def aes_decrypt(key: bytes, ciphertext: bytes, iv: bytes = _IV) -> bytes:
    cipher = Cipher(AES(key), modes.CBC(iv), backend=_BACKEND)
    decryptor = cipher.decryptor()
    padded_plain = decryptor.update(ciphertext) + decryptor.finalize()
    logger.debug(
        "aes_decrypt raw bytes before unpad (first 32): %s",
        padded_plain[:32].hex(),
    )
    return pkcs7_unpad(padded_plain)


def encode_b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def decode_b64(data: str) -> bytes:
    return base64.b64decode(data)


def encrypt_payload(key: bytes, plaintext: bytes, iv: bytes = _IV) -> str:
    ciphertext = aes_encrypt(key, plaintext, iv)
    return encode_b64(ciphertext)


def decrypt_payload(key: bytes, data_enc: str, iv: bytes = _IV) -> bytes:
    ciphertext = decode_b64(data_enc)
    return aes_decrypt(key, ciphertext, iv)


def compute_sign(key: bytes, data_enc: str) -> str:
    raw = data_enc + encode_b64(key)
    return hashlib.sha256(raw.encode()).hexdigest()


def generate_random_key(length: int = 32) -> bytes:
    return os.urandom(length)


def generate_tmp_session() -> str:
    return "".join(secrets.choice(string.ascii_lowercase) for _ in range(32))


def derive_session_key(tmp_session: str) -> bytes:
    return derive_passphrase(tmp_session).encode("utf-8")


def _candidate_key_iv_pairs(tmp_session: str) -> list[tuple[bytes, bytes, str]]:
    passphrase_key = derive_passphrase(tmp_session).encode("utf-8")
    raw = tmp_session.encode("utf-8")
    sha = hashlib.sha256(raw).digest()
    zero_iv = b"\x00" * _AES_BLOCK_SIZE
    ascii_iv = b"0" * _AES_BLOCK_SIZE
    return [
        (passphrase_key, zero_iv, "key=passphrase(ts), iv=null_zeros"),
        (passphrase_key, ascii_iv, "key=passphrase(ts), iv=ascii_zeros"),
        (raw, zero_iv, "key=ts.utf8[32], iv=null_zeros"),
        (raw, ascii_iv, "key=ts.utf8[32], iv=ascii_zeros"),
        (sha, zero_iv, "key=sha256(ts), iv=null_zeros"),
        (sha, ascii_iv, "key=sha256(ts), iv=ascii_zeros"),
    ]


def decrypt_payload_probe(
    tmp_session: str, data_enc: str
) -> tuple[bytes, bytes, bytes]:
    ciphertext = decode_b64(data_enc)
    for key, iv, label in _candidate_key_iv_pairs(tmp_session):
        if len(key) not in (16, 24, 32):
            logger.debug("Skipping derivation [%s]: invalid key length %d", label, len(key))
            continue
        try:
            cipher = Cipher(AES(key), modes.CBC(iv), backend=_BACKEND)
            decryptor = cipher.decryptor()
            padded = decryptor.update(ciphertext) + decryptor.finalize()
            logger.debug(
                "Derivation [%s]: raw pre-unpad bytes (hex, first 64): %s",
                label,
                padded[:64].hex(),
            )
            plaintext = pkcs7_unpad(padded)
            logger.debug(
                "Derivation [%s]: plaintext preview: %s",
                label,
                plaintext[:80],
            )
            logger.info("Successful decryption with derivation: [%s]", label)
            return plaintext, key, iv
        except Exception as exc:
            logger.debug("Derivation [%s] failed: %s", label, exc)

    raise ValueError(
        "All key/IV derivation strategies exhausted. "
        "Server response cannot be decrypted. The protocol may have changed."
    )
