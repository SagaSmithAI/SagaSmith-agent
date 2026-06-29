"""ChromaDB client lifecycle and collection access.

The VectorStore is a lightweight singleton that lazily connects to ChromaDB.
When neither CHROMA_DB_URL nor CHROMA_DB_PATH is set, ChromaDB integration is
fully disabled and ``VectorStore().enabled`` returns ``False``.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from chromadb import Collection, HttpClient, PersistentClient
from chromadb.config import Settings

from nanobot.config.paths import get_runtime_subdir

logger = logging.getLogger(__name__)

_COLLECTION_NAMES = ("dnd_rules", "dnd_modules", "dnd_campaign_memories")
_COLLECTION_METADATA = {
    "hnsw:space": "cosine",
}


def _default_chroma_path() -> Path:
    return get_runtime_subdir("dnd") / "chroma_db"


class VectorStore:
    """Manage a ChromaDB client and provide collection access.

    Usage::

        store = VectorStore()
        if store.enabled:
            coll = store.collection("dnd_rules")
            coll.query(...)
    """

    def __init__(self) -> None:
        self._client: HttpClient | PersistentClient | None = None
        self._collections: dict[str, Collection] = {}
        self._enabled: bool | None = None

    @property
    def enabled(self) -> bool:
        """True when ChromaDB is configured and reachable."""
        if self._enabled is None:
            url = os.environ.get("CHROMA_DB_URL")
            path = os.environ.get("CHROMA_DB_PATH")
            self._enabled = bool(url or path)
        return self._enabled

    @staticmethod
    def configured_url() -> str | None:
        """Return the configured HTTP URL, if any."""
        return os.environ.get("CHROMA_DB_URL") or None

    @staticmethod
    def configured_path() -> Path | None:
        """Return the configured persistent path, if any."""
        raw = os.environ.get("CHROMA_DB_PATH")
        return Path(raw).expanduser().resolve() if raw else None

    def _connect(self) -> HttpClient | PersistentClient:
        url = self.configured_url()
        if url:
            logger.info("Connecting to ChromaDB HTTP server at %s", url)
            return HttpClient(host=url, settings=Settings(anonymized_telemetry=False))
        path = self.configured_path() or _default_chroma_path()
        path.mkdir(parents=True, exist_ok=True)
        logger.info("Opening ChromaDB persistent store at %s", path)
        return PersistentClient(path=str(path), settings=Settings(anonymized_telemetry=False))

    def _ensure_client(self):
        if self._client is None:
            self._client = self._connect()
        return self._client

    def collection(self, name: str) -> Collection:
        """Return a ChromaDB collection, creating it on first access."""
        if name not in _COLLECTION_NAMES:
            raise ValueError(
                f"unknown ChromaDB collection {name!r}; expected one of {_COLLECTION_NAMES}"
            )
        if name not in self._collections:
            client = self._ensure_client()
            self._collections[name] = client.get_or_create_collection(
                name=name,
                metadata=_COLLECTION_METADATA,
            )
        return self._collections[name]

    def collection_stats(self, name: str) -> dict:
        """Return approximate row count for a collection."""
        try:
            coll = self.collection(name)
            return {"name": name, "count": coll.count()}
        except Exception as exc:
            return {"name": name, "count": None, "error": str(exc)}

    def drop_collection(self, name: str) -> None:
        """Delete a collection entirely (used by reindex)."""
        if name in self._collections:
            self._collections.pop(name)
        try:
            self._ensure_client().delete_collection(name)
        except Exception:
            logger.debug("Collection %r does not exist or could not be deleted", name)

    def dispose(self) -> None:
        """Release client resources."""
        self._collections.clear()
        self._client = None
