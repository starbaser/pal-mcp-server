"""
Storage backends for conversation threads

This module provides thread-safe storage backends for persisting conversation
contexts. Two backends are available:

- InMemoryStorage: Ephemeral, process-local storage. Data is lost on restart.
- FileStorage: Disk-backed storage with memory hot cache. Survives restarts.

The active backend is selected by the CONVERSATION_STORAGE_BACKEND environment
variable ("memory" or "file", default "file").

⚠️  PROCESS-SPECIFIC NOTE (InMemoryStorage): Storage is confined to a single
    Python process. Data stored in one process is NOT accessible from other
    processes or subprocesses. This is why simulator tests that run server.py
    as separate subprocesses cannot share conversation state between tool calls.
    Use FileStorage to enable cross-process persistence.

Key Features:
- Thread-safe operations using locks
- Singleton pattern for consistent state within a single process
- Drop-in replacement for Redis storage (for single-process scenarios)
- FileStorage: atomic writes via temp file + os.rename to prevent corruption
- FileStorage: startup recovery loads existing threads into memory cache
"""

import json
import logging
import os
import tempfile
import threading
from typing import Optional

logger = logging.getLogger("mcp_server")


class InMemoryStorage:
    """Thread-safe in-memory storage for conversation threads"""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._lock = threading.Lock()
        logger.info("In-memory storage initialized")

    def set(self, key: str, value: str) -> None:
        """Store a value."""
        with self._lock:
            self._store[key] = value
            logger.debug(f"Stored key {key}")

    def get(self, key: str) -> Optional[str]:
        """Retrieve a value."""
        with self._lock:
            value = self._store.get(key)
            if value is not None:
                logger.debug(f"Retrieved key {key}")
            return value


class FileStorage:
    """
    Disk-backed storage with memory hot cache for conversation threads.

    Each thread is stored as {storage_dir}/{thread_id}.json with format:
        {"value": "<serialized ThreadContext JSON>"}

    Writes are atomic: content is written to a temp file then renamed into place,
    preventing corrupt reads if the process crashes mid-write.

    On startup, all files are loaded into the memory cache so that
    existing threads survive server restarts without a round-trip to disk on
    the first access.
    """

    def __init__(self, storage_dir: str):
        self._storage_dir = storage_dir
        self._cache: dict[str, str] = {}
        self._lock = threading.Lock()

        try:
            os.makedirs(storage_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"FileStorage: could not create storage dir {storage_dir!r}: {e}")

        self._recover_from_disk()
        logger.info(f"File storage initialized at {storage_dir!r} " f"({len(self._cache)} threads recovered)")

    # ------------------------------------------------------------------
    # Public interface (matches InMemoryStorage)
    # ------------------------------------------------------------------

    def set(self, key: str, value: str) -> None:
        """Write value to memory cache and disk atomically."""
        with self._lock:
            self._cache[key] = value
        self._write_to_disk(key, value)
        logger.debug(f"FileStorage: stored key {key!r}")

    def get(self, key: str) -> Optional[str]:
        """Return value for key, checking memory cache first then disk."""
        with self._lock:
            if key in self._cache:
                logger.debug(f"FileStorage: cache hit for key {key!r}")
                return self._cache[key]

        return self._read_from_disk(key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key_to_filename(self, key: str) -> str:
        """Map a storage key to a safe filename under storage_dir"""
        # Keys look like "thread:<uuid>" — replace the colon so it's FS-safe
        safe_name = key.replace(":", "_")
        return os.path.join(self._storage_dir, f"{safe_name}.json")

    def _write_to_disk(self, key: str, value: str) -> None:
        """Atomically write a key/value record to disk."""
        path = self._key_to_filename(key)
        payload = json.dumps({"value": value})
        try:
            dir_ = os.path.dirname(path)
            with tempfile.NamedTemporaryFile(mode="w", dir=dir_, delete=False, suffix=".tmp") as tmp:
                tmp.write(payload)
                tmp_path = tmp.name
            os.rename(tmp_path, path)
        except OSError as e:
            logger.error(f"FileStorage: failed to write key {key!r} to disk: {e}")
            # Non-fatal: value is already in memory cache

    def _read_from_disk(self, key: str) -> Optional[str]:
        """Read a record from disk and warm the cache."""
        path = self._key_to_filename(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                record = json.load(f)
            value: str = record["value"]
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"FileStorage: could not read {path!r}: {e}")
            return None

        with self._lock:
            self._cache[key] = value
        return value

    def _recover_from_disk(self) -> None:
        """Load all thread files into the memory cache at startup."""
        try:
            entries = os.listdir(self._storage_dir)
        except OSError as e:
            logger.warning(f"FileStorage: could not list storage dir on recovery: {e}")
            return

        for filename in entries:
            if not filename.endswith(".json"):
                continue
            path = os.path.join(self._storage_dir, filename)
            try:
                with open(path) as f:
                    record = json.load(f)
                value: str = record["value"]
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"FileStorage: skipping unreadable file {path!r}: {e}")
                continue

            key = filename[:-5].replace("_", ":", 1)
            self._cache[key] = value


# ------------------------------------------------------------------
# Singleton factory
# ------------------------------------------------------------------

_storage_instance: Optional[InMemoryStorage | FileStorage] = None
_storage_lock = threading.Lock()


def get_storage_backend() -> InMemoryStorage | FileStorage:
    """
    Return the global storage backend instance (singleton).

    Backend is selected by CONVERSATION_STORAGE_BACKEND:
      "file"   -> FileStorage persisted to CONVERSATION_STORAGE_DIR
      "memory" -> InMemoryStorage (process-local, ephemeral)
    """
    global _storage_instance
    if _storage_instance is None:
        with _storage_lock:
            if _storage_instance is None:
                _storage_instance = _create_backend()
    return _storage_instance


def _create_backend() -> InMemoryStorage | FileStorage:
    """Instantiate the configured backend; fall back to memory on error"""
    # Import here to avoid circular imports at module load time
    from config import CONVERSATION_STORAGE_BACKEND, CONVERSATION_STORAGE_DIR

    backend = (CONVERSATION_STORAGE_BACKEND or "file").lower()

    if backend == "file":
        try:
            instance = FileStorage(CONVERSATION_STORAGE_DIR)
            logger.info(f"Conversation storage: file backend at {CONVERSATION_STORAGE_DIR!r}")
            return instance
        except Exception as e:
            logger.error(f"FileStorage init failed ({e}); falling back to in-memory storage")
            return InMemoryStorage()

    instance = InMemoryStorage()
    logger.info("Conversation storage: in-memory backend")
    return instance
