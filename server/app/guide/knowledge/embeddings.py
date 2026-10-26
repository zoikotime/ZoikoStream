"""Embedding profile (RAG §8).

Launch profile: BAAI/bge-small-en-v1.5 run locally through fastembed (ONNX, CPU, 384 dims).
It needs no provider key and sends no public-corpus or visitor text to a third party. The
profile id is recorded on every unit and index release, so changing models is a re-embed into
a NEW release, never an in-place switch (RAG §25).

"hash" is a deterministic test/dev stand-in: no model download, stable vectors. It is never
used for a production release (ingest refuses to activate a hash release outside tests).
"""
from __future__ import annotations

import hashlib
import math
import re
import threading
from functools import lru_cache

from .models import EMBEDDING_DIM

LOCAL_PROFILE = "fastembed:BAAI/bge-small-en-v1.5"
HASH_PROFILE = "hash:v1"
# bge models expect this prefix on QUERIES (not documents) for retrieval.
_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "

_lock = threading.Lock()


class LocalEmbedder:
    profile = LOCAL_PROFILE

    def __init__(self):
        from fastembed import TextEmbedding  # heavy import, deferred until first use
        self._model = TextEmbedding("BAAI/bge-small-en-v1.5")

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with _lock:
            return [list(map(float, v)) for v in self._model.embed(texts)]

    def embed_query(self, text: str) -> list[float]:
        with _lock:
            return [float(x) for x in next(iter(self._model.embed([_QUERY_PREFIX + text])))]


class HashEmbedder:
    """Bag-of-words feature hashing — lexical-ish similarity, good enough for tests."""
    profile = HASH_PROFILE

    def _vec(self, text: str) -> list[float]:
        v = [0.0] * EMBEDDING_DIM
        for word in re.findall(r"[a-z0-9]+", text.lower()):
            h = int.from_bytes(hashlib.sha256(word.encode()).digest()[:4], "big")
            v[h % EMBEDDING_DIM] += 1.0
        norm = math.sqrt(sum(x * x for x in v)) or 1.0
        return [x / norm for x in v]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@lru_cache
def get_embedder(profile: str | None = None):
    from ..settings import get_settings
    chosen = profile or get_settings().GUIDE_EMBEDDING_PROFILE
    if chosen == HASH_PROFILE:
        return HashEmbedder()
    if chosen == LOCAL_PROFILE:
        return LocalEmbedder()
    raise ValueError(f"unknown embedding profile {chosen!r}")


def document_input(title: str, heading_path: str, text: str, topic: str | None = None) -> str:
    """RAG §8 embedding input: a limited structural prefix (title, section, topic) + the
    faithful unit text. No private workflow metadata, hidden instructions or internal ids."""
    return (f"Title: {title}\nSection: {heading_path}\nTopic: {topic or heading_path}\n"
            f"Content:\n{text}")
