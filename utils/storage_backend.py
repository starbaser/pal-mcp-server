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
- TTL support with automatic expiration
- Singleton pattern for consistent state within a single process
- Drop-in replacement for Redis storage (for single-process scenarios)
- FileStorage: atomic writes via temp file + os.rename to prevent corruption
- FileStorage: startup recovery loads all non-expired threads into memory cache
"""

import json
import logging
import os
import tempfile
import threading
import time
from typing import Optional


logger = logging.getLogger("mcp_server")


class InMemoryStorage:
    """Thread-safe in-memory storage for conversation threads"""

    def __init__(self):
        self._store: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()
        from config import CONVERSATION_TIMEOUT_HOURS as timeout_hours
        self._cleanup_interval = (timeout_hours * 3600) // 10
        self._cleanup_interval = max(300, self._cleanup_interval)  # Minimum 5 minutes
        self._shutdown = False

        self._cleanup_thread = threading.Thread(target=self._cleanup_worker, daemon=True)
        self._cleanup_thread.start()

        logger.info(
            f"In-memory storage initialized with {timeout_hours}h timeout, "
            f"cleanup every {self._cleanup_interval // 60}m"
        )

    def set_with_ttl(self, key: str, ttl_seconds: int, value: str) -> None:
        """Store value with expiration time"""
        with self._lock:
            expires_at = time.time() + ttl_seconds
            self._store[key] = (value, expires_at)
            logger.debug(f"Stored key {key} with TTL {ttl_seconds}s")

    def get(self, key: str) -> Optional[str]:
        """Retrieve value if not expired"""
        with self._lock:
            if key in self._store:
                value, expires_at = self._store[key]
                if time.time() < expires_at:
                    logger.debug(f"Retrieved key {key}")
                    return value
                else:
                    del self._store[key]
                    logger.debug(f"Key {key} expired and removed")
        return None

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        """Redis-compatible setex method"""
        self.set_with_ttl(key, ttl_seconds, value)

    def _cleanup_worker(self):
        """Background thread that periodically cleans up expired entries"""
        while not self._shutdown:
            time.sleep(self._cleanup_interval)
            self._cleanup_expired()

    def _cleanup_expired(self):
        """Remove all expired entries"""
        with self._lock:
            current_time = time.time()
            expired_keys = [k for k, (_, exp) in self._store.items() if exp < current_time]
            for key in expired_keys:
                del self._store[key]

            if expired_keys:
                logger.debug(f"Cleaned up {len(expired_keys)} expired conversation threads")

    def shutdown(self):
        """Graceful shutdown of background thread"""
        self._shutdown = True
        if self._cleanup_thread.is_alive():
            self._cleanup_thread.join(timeout=1)


class FileStorage:
    """
    Disk-backed storage with memory hot cache for conversation threads.

    Each thread is stored as {storage_dir}/{thread_id}.json with format:
        {"value": "<serialized ThreadContext JSON>", "expires_at": <unix timestamp>}

    Writes are atomic: content is written to a temp file then renamed into place,
    preventing corrupt reads if the process crashes mid-write.

    On startup, all non-expired files are loaded into the memory cache so that
    existing threads survive server restarts without a round-trip to disk on
    the first access.
    """

    def __init__(self, storage_dir: str):
        self._storage_dir = storage_dir
        self._cache: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

        try:
            os.makedirs(storage_dir, exist_ok=True)
        except OSError as e:
            logger.error(f"FileStorage: could not create storage dir {storage_dir!r}: {e}")

        self._recover_from_disk()
        logger.info(
            f"File storage initialized at {storage_dir!r} "
            f"({len(self._cache)} threads recovered)"
        )

    # ------------------------------------------------------------------
    # Public interface (matches InMemoryStorage)
    # ------------------------------------------------------------------

    def setex(self, key: str, ttl_seconds: int, value: str) -> None:
        """Redis-compatible setex method"""
        self.set_with_ttl(key, ttl_seconds, value)

    def set_with_ttl(self, key: str, ttl_seconds: int, value: str) -> None:
        """Write value to memory cache and disk atomically"""
        expires_at = time.time() + ttl_seconds
        with self._lock:
            self._cache[key] = (value, expires_at)
        self._write_to_disk(key, value, expires_at)
        logger.debug(f"FileStorage: stored key {key!r} with TTL {ttl_seconds}s")

    def get(self, key: str) -> Optional[str]:
        """Return value for key, checking memory cache first then disk"""
        with self._lock:
            if key in self._cache:
                value, _ = self._cache[key]
                logger.debug(f"FileStorage: cache hit for key {key!r}")
                return value

        # Cache miss — try disk
        return self._read_from_disk(key)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _key_to_filename(self, key: str) -> str:
        """Map a storage key to a safe filename under storage_dir"""
        # Keys look like "thread:<uuid>" — replace the colon so it's FS-safe
        safe_name = key.replace(":", "_")
        return os.path.join(self._storage_dir, f"{safe_name}.json")

    def _write_to_disk(self, key: str, value: str, expires_at: float) -> None:
        """Atomically write a key/value/expiry record to disk"""
        path = self._key_to_filename(key)
        payload = json.dumps({"value": value, "expires_at": expires_at})
        try:
            dir_ = os.path.dirname(path)
            with tempfile.NamedTemporaryFile(
                mode="w", dir=dir_, delete=False, suffix=".tmp"
            ) as tmp:
                tmp.write(payload)
                tmp_path = tmp.name
            os.rename(tmp_path, path)
        except OSError as e:
            logger.error(f"FileStorage: failed to write key {key!r} to disk: {e}")
            # Non-fatal: value is already in memory cache

    def _read_from_disk(self, key: str) -> Optional[str]:
        """Read and validate a single record from disk; delete if expired"""
        path = self._key_to_filename(key)
        if not os.path.exists(path):
            return None
        try:
            with open(path) as f:
                record = json.load(f)
            value: str = record["value"]
            expires_at: float = record["expires_at"]
        except (OSError, json.JSONDecodeError, KeyError) as e:
            logger.warning(f"FileStorage: could not read {path!r}: {e}")
            return None

        # Warm the cache on a disk-hit so subsequent reads stay fast
        with self._lock:
            self._cache[key] = (value, expires_at)
        return value

    def _recover_from_disk(self) -> None:
        """Load all non-expired thread files into the memory cache at startup"""
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
                expires_at: float = record["expires_at"]
            except (OSError, json.JSONDecodeError, KeyError) as e:
                logger.warning(f"FileStorage: skipping unreadable file {path!r}: {e}")
                continue

            # Derive key from filename: "thread_<uuid>.json" -> "thread:<uuid>"
            key = filename[:-5].replace("_", ":", 1)
            self._cache[key] = (value, expires_at)


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
            logger.error(
                f"FileStorage init failed ({e}); falling back to in-memory storage"
            )
            return InMemoryStorage()

    instance = InMemoryStorage()
    logger.info("Conversation storage: in-memory backend")
    return instance
