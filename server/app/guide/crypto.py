"""Application-layer envelope encryption and keyed hashing (ZST-SEC-PRIV-PUB-GUIDE-001 §9).

WRITE: fresh 256-bit DEK -> AES-256-GCM(plaintext) -> DEK wrapped by the master key ->
       one self-describing blob in the bytea column.
READ:  unwrap DEK -> decrypt in memory -> never logged.

Key providers (GUIDE_KEY_PROVIDER):
  * "local"   — master from GUIDE_LOCAL_MASTER_KEY. Development only; production refuses it
                (settings.production_problems): a key in an env file is not a KMS.
  * "gcp-kms" — production. The 64-byte key set (32-byte KEK + 32-byte HMAC key) exists only
                as Cloud KMS ciphertext in GUIDE_KMS_KEYSET; it is decrypted once per process
                by Cloud KMS, which requires the service's IAM grant on GUIDE_KMS_KEY_NAME. A
                database dump plus the environment alone therefore decrypts nothing (SP-AT-13).
                Rotation: put the new key set in GUIDE_KMS_KEYSET and the old one in
                GUIDE_KMS_PREVIOUS_KEYSETS; old rows still unwrap by their `key_id`, new rows
                use the new KEK. Generate a key set (the plaintext is never printed):
                    python -m app.guide.crypto generate-kms-keyset
`key_id` in every blob names which KEK wrapped its DEK, so neither rotation nor a provider
change needs a synchronous re-encryption.

Hashes (session capability, CSRF, Idempotency-Key, nonces) are HMAC-SHA256 under a key
derived separately from the same master, so a database dump alone cannot be brute-forced
against guessed tokens.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import struct
import sys
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


KEYSET_BYTES = 64  # 32-byte KEK || 32-byte HMAC key


class KmsKeyProvider:
    """KEKs unwrapped from Cloud KMS ciphertext. The current key set wraps new DEKs and keys
    every HMAC; previous key sets only unwrap DEKs of older rows."""

    def __init__(self, keysets: list[tuple[str, bytes]]):
        if not keysets:
            raise CryptoUnavailable("no KMS key set configured")
        self._keks = {kid: AESGCM(raw[:32]) for kid, raw in keysets}
        self.key_id = keysets[0][0]
        self.hash_key = keysets[0][1][32:]

    def wrap(self, dek: bytes) -> bytes:
        nonce = os.urandom(_NONCE)
        return nonce + self._keks[self.key_id].encrypt(nonce, dek, self.key_id.encode())

    def unwrap(self, key_id: str, wrapped: bytes) -> bytes:
        kek = self._keks.get(key_id)
        if kek is None:
            raise CryptoUnavailable(f"unknown key id {key_id!r}")
        return kek.decrypt(wrapped[:_NONCE], wrapped[_NONCE:], key_id.encode())


def keyset_id(ciphertext: bytes) -> str:
    """Stable, non-secret id of a key set: derived from its KMS ciphertext."""
    return "kms-" + hashlib.sha256(ciphertext).hexdigest()[:12]


def _kms_client():
    from google.cloud import kms  # deferred: only production needs it
    return kms.KeyManagementServiceClient()


def _kms_decrypt(key_name: str, ciphertext: bytes) -> bytes:
    return _kms_client().decrypt(request={"name": key_name, "ciphertext": ciphertext}).plaintext


def _kms_keysets(s) -> list[tuple[str, bytes]]:
    if not s.GUIDE_KMS_KEY_NAME or not s.GUIDE_KMS_KEYSET:
        raise CryptoUnavailable("GUIDE_KMS_KEY_NAME and GUIDE_KMS_KEYSET are required")
    out = []
    for encoded in [s.GUIDE_KMS_KEYSET, *s.previous_kms_keysets()]:
        try:
            ciphertext = base64.b64decode(encoded, validate=True)
            raw = _kms_decrypt(s.GUIDE_KMS_KEY_NAME, ciphertext)
        except Exception as exc:  # never echo key material; name the failure class only
            raise CryptoUnavailable(f"KMS key set could not be decrypted ({type(exc).__name__})")                 from None
        if len(raw) != KEYSET_BYTES:
            raise CryptoUnavailable("KMS key set has the wrong length")
        out.append((keyset_id(ciphertext), raw))
    return out


@lru_cache
def provider() -> LocalKeyProvider | KmsKeyProvider:
    s = get_settings()
    if s.GUIDE_KEY_PROVIDER == "gcp-kms":
        return KmsKeyProvider(_kms_keysets(s))
    if s.GUIDE_KEY_PROVIDER != "local":
        raise CryptoUnavailable(f"key provider {s.GUIDE_KEY_PROVIDER!r} is not implemented")
    master = s.master_key_bytes()
    if master is None:
        raise CryptoUnavailable("GUIDE_LOCAL_MASTER_KEY is missing or too short")
    return LocalKeyProvider(master)


def generate_kms_keyset(key_name: str) -> str:
    """A new random key set, encrypted by Cloud KMS. Returns base64 ciphertext only."""
    raw = os.urandom(KEYSET_BYTES)
    resp = _kms_client().encrypt(request={"name": key_name, "plaintext": raw})
    return base64.b64encode(resp.ciphertext).decode()


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


if __name__ == "__main__":
    if sys.argv[1:2] != ["generate-kms-keyset"]:
        raise SystemExit("usage: python -m app.guide.crypto generate-kms-keyset")
    name = get_settings().GUIDE_KMS_KEY_NAME
    if not name:
        raise SystemExit("set GUIDE_KMS_KEY_NAME to the Cloud KMS crypto key resource name")
    print(generate_kms_keyset(name))
