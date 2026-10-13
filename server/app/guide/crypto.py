"""Application-layer envelope encryption and keyed hashing (ZST-SEC-PRIV-PUB-GUIDE-001 §9).

WRITE: fresh 256-bit DEK -> AES-256-GCM(plaintext) -> DEK wrapped by the master key ->
       one self-describing blob in the bytea column.
READ:  unwrap DEK -> decrypt in memory -> never logged.

Key provider "local" derives the master KEK from GUIDE_LOCAL_MASTER_KEY. It is a development
stand-in for a KMS and production refuses it (settings.production_problems). A KMS provider
plugs in behind `KeyProvider` without changing the blob format: `key_id` names which KEK
wrapped the DEK, so rotation never needs a synchronous re-encryption.

Hashes (session capability, CSRF, Idempotency-Key, nonces) are HMAC-SHA256 under a key
derived separately from the same master, so a database dump alone cannot be brute-forced
against guessed tokens.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import struct
from functools import lru_cache

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from .settings import get_settings

BLOB_VERSION = 1
_NONCE = 12


class CryptoUnavailable(RuntimeError):
    """No usable key material. Writes needing encryption must fail closed (no plaintext
    fallback — ZST-DB-PUB-GUIDE-001 §20)."""


def _derive(master: bytes, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=info).derive(master)


class LocalKeyProvider:
    key_id = "local-v1"

    def __init__(self, master: bytes):
        self._kek = AESGCM(_derive(master, b"zs-guide/kek/v1"))
        self.hash_key = _derive(master, b"zs-guide/hmac/v1")

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._kek.encrypt(nonce, dek, self.key_id.encode())

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        if key_id != self.key_id:
            raise CryptoUnavailable(f"unknown key id {key_id!r}")
        return self._kek.decrypt(wrapped[:_NONCE], wrapped[_NONCE:], key_id.encode())


@lru_cache
def provider() -> LocalKeyProvider:
    s = get_settings()
    if s.GUIDE_KEY_PROVIDER != "local":
        raise CryptoUnavailable(f"key provider {s.GUIDE_KEY_PROVIDER!r} is not implemented")
    master = s.master_key_bytes()
    if master is None:
        raise CryptoUnavailable("GUIDE_LOCAL_MASTER_KEY is missing or too short")
    return LocalKeyProvider(master)


def encrypt(plaintext: str, *, context: str) -> bytes:
    """`context` (e.g. "guide_messages.content") is bound as AAD, so a ciphertext copied into
    another column does not decrypt."""
    kp = provider()
    dek = AESGCM.generate_key(bit_length=256)
    nonce = os.urandom(_NONCE)
    ct = AESGCM(dek).encrypt(nonce, plaintext.encode("utf-8"), context.encode())
    wrapped = kp.wrap(dek)
    kid = kp.key_id.encode()
    return (struct.pack(">BBH", BLOB_VERSION, len(kid), len(wrapped)) + kid + wrapped
            + nonce + ct)


def decrypt(blob: bytes, *, context: str) -> str:
    version, kid_len, wrapped_len = struct.unpack(">BBH", blob[:4])
    if version != BLOB_VERSION:
        raise CryptoUnavailable(f"unsupported ciphertext version {version}")
    pos = 4
    kid = blob[pos:pos + kid_len].decode()
    pos += kid_len
    wrapped = blob[pos:pos + wrapped_len]
    pos += wrapped_len
    nonce, ct = blob[pos:pos + _NONCE], blob[pos + _NONCE:]
    dek = provider().unwrap(kid, wrapped)
    return AESGCM(dek).decrypt(nonce, ct, context.encode()).decode("utf-8")


def keyed_hash(value: str, *, purpose: str) -> bytes:
    """HMAC-SHA256(purpose || value). `purpose` separates domains so a CSRF token can never
    match a capability hash."""
    return hmac.new(provider().hash_key, f"{purpose}\x00{value}".encode(),
                    hashlib.sha256).digest()


def digest(value: str | bytes) -> bytes:
    """Unkeyed SHA-256 for integrity/dedup of public material (source digests, token
    fingerprints). Not for secrets a guesser could enumerate."""
    data = value.encode() if isinstance(value, str) else value
    return hashlib.sha256(data).digest()


def constant_time_equal(a: bytes, b: bytes) -> bool:
    return hmac.compare_digest(a, b)
