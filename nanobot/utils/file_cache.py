"""Small, process-local caches for frequently read text files.

The cache deliberately stores content only in memory.  Callers own an instance,
so workspace-scoped data is never shared through module globals.
"""

from __future__ import annotations

import hashlib
import os
import stat
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    class _FileBasicInfo(ctypes.Structure):
        _fields_ = [
            ("creation_time", ctypes.c_longlong),
            ("last_access_time", ctypes.c_longlong),
            ("last_write_time", ctypes.c_longlong),
            ("change_time", ctypes.c_longlong),
            ("file_attributes", wintypes.DWORD),
        ]

    _CreateFileW = ctypes.windll.kernel32.CreateFileW
    _CreateFileW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.LPVOID,
        wintypes.DWORD,
        wintypes.DWORD,
        wintypes.HANDLE,
    ]
    _CreateFileW.restype = wintypes.HANDLE
    _GetFileInformationByHandleEx = ctypes.windll.kernel32.GetFileInformationByHandleEx
    _GetFileInformationByHandleEx.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    ]
    _GetFileInformationByHandleEx.restype = wintypes.BOOL
    _CloseHandle = ctypes.windll.kernel32.CloseHandle
    _CloseHandle.argtypes = [wintypes.HANDLE]
    _CloseHandle.restype = wintypes.BOOL


def normalize_cache_path(path: Path) -> str:
    """Return a stable, platform-correct identity for *path*."""
    resolved = path.expanduser().resolve(strict=False)
    return os.path.normcase(os.path.normpath(str(resolved)))


def _change_time_ns(path: Path, fallback: int) -> int:
    """Return metadata change time, including real ChangeTime on Windows."""
    if os.name != "nt":
        return fallback
    # Python's st_ctime_ns is creation time on Windows.  FILE_BASIC_INFO's
    # ChangeTime tracks same-mtime rewrites and directory membership changes.
    handle = _CreateFileW(  # type: ignore[name-defined]
        str(path.expanduser().resolve(strict=False)),
        0,
        0x00000001 | 0x00000002 | 0x00000004,
        None,
        3,
        0x02000000,
        None,
    )
    invalid_handle = ctypes.c_void_p(-1).value  # type: ignore[name-defined]
    if handle in (None, invalid_handle):
        return fallback
    try:
        info = _FileBasicInfo()  # type: ignore[name-defined]
        ok = _GetFileInformationByHandleEx(  # type: ignore[name-defined]
            handle,
            0,
            ctypes.byref(info),  # type: ignore[name-defined]
            ctypes.sizeof(info),  # type: ignore[name-defined]
        )
        return int(info.change_time) * 100 if ok else fallback
    finally:
        _CloseHandle(handle)  # type: ignore[name-defined]


@dataclass(frozen=True, slots=True)
class PathSignature:
    """Cheap filesystem identity used before reading file contents."""

    path: str
    mtime_ns: int
    size: int
    change_ns: int
    device: int
    inode: int

    @property
    def has_coarse_timestamps(self) -> bool:
        """Whether metadata alone can plausibly collide after a quick rewrite."""
        one_second = 1_000_000_000
        return self.mtime_ns % one_second == 0 and self.change_ns % one_second == 0


@dataclass(frozen=True, slots=True)
class TextCacheKey:
    """Complete content identity retained with a cached text value."""

    path: str
    mtime_ns: int
    size: int
    change_ns: int
    device: int
    inode: int
    sha256: str

    @property
    def signature(self) -> PathSignature:
        return PathSignature(
            path=self.path,
            mtime_ns=self.mtime_ns,
            size=self.size,
            change_ns=self.change_ns,
            device=self.device,
            inode=self.inode,
        )


@dataclass(frozen=True, slots=True)
class CachedText:
    key: TextCacheKey
    content: str
    byte_size: int


@dataclass(frozen=True, slots=True)
class TextCacheInfo:
    hits: int
    misses: int
    entries: int
    bytes: int


def path_signature(path: Path, *, kind: str | None = None) -> PathSignature | None:
    """Return a normalized stat signature, optionally requiring a file kind."""
    normalized = normalize_cache_path(path)
    try:
        value = path.stat()
    except OSError:
        return None
    if kind == "file" and not stat.S_ISREG(value.st_mode):
        return None
    if kind == "directory" and not stat.S_ISDIR(value.st_mode):
        return None
    return PathSignature(
        path=normalized,
        mtime_ns=value.st_mtime_ns,
        size=value.st_size,
        change_ns=(
            _change_time_ns(path, value.st_ctime_ns)
            if kind == "directory"
            else value.st_ctime_ns
        ),
        device=value.st_dev,
        inode=value.st_ino,
    )


class BoundedTextFileCache:
    """Thread-safe UTF-8 text cache bounded by entry count and source bytes."""

    def __init__(self, *, max_entries: int, max_bytes: int) -> None:
        if max_entries <= 0 or max_bytes <= 0:
            raise ValueError("cache limits must be positive")
        self._max_entries = max_entries
        self._max_bytes = max_bytes
        self._entries: OrderedDict[str, CachedText] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._lock = threading.RLock()

    def read(self, path: Path) -> CachedText | None:
        """Read *path*, returning a cached value when its identity is unchanged."""
        with self._lock:
            signature = path_signature(path, kind="file")
            normalized = normalize_cache_path(path)
            if signature is None:
                self._discard_locked(normalized)
                self._misses += 1
                return None

            cached = self._entries.get(normalized)
            if cached is not None and cached.key.signature == signature:
                if not self._requires_hash_recheck(signature):
                    self._entries.move_to_end(normalized)
                    self._hits += 1
                    return cached
                # Coarse filesystem timestamps can rewrite same-sized content
                # without changing the cheap stat identity.
                # Hash in that uncommon case rather than serving stale text.
                try:
                    raw = path.read_bytes()
                except OSError:
                    self._discard_locked(normalized)
                    self._misses += 1
                    return None
                if hashlib.sha256(raw).hexdigest() == cached.key.sha256:
                    self._entries.move_to_end(normalized)
                    self._hits += 1
                    return cached

            self._misses += 1
            loaded = self._read_stable(path)
            if loaded is None:
                self._discard_locked(normalized)
                return None
            if loaded.byte_size > self._max_bytes:
                self._discard_locked(normalized)
                return loaded

            self._discard_locked(normalized)
            self._entries[normalized] = loaded
            self._bytes += loaded.byte_size
            self._prune_locked()
            return loaded

    @staticmethod
    def _requires_hash_recheck(signature: PathSignature) -> bool:
        one_second = 1_000_000_000
        if os.name == "nt":
            # st_ctime_ns is creation time for regular files on Windows.
            return signature.mtime_ns % one_second == 0
        return signature.has_coarse_timestamps

    def info(self) -> TextCacheInfo:
        with self._lock:
            return TextCacheInfo(
                hits=self._hits,
                misses=self._misses,
                entries=len(self._entries),
                bytes=self._bytes,
            )

    def _read_stable(self, path: Path) -> CachedText | None:
        """Read a stable snapshot, retrying once when a writer races us."""
        for _attempt in range(2):
            before = path_signature(path, kind="file")
            if before is None:
                return None
            try:
                raw = path.read_bytes()
            except OSError:
                return None
            after = path_signature(path, kind="file")
            if after is None:
                return None
            if before != after or len(raw) != after.size:
                continue
            content = raw.decode("utf-8")
            return CachedText(
                key=TextCacheKey(
                    path=after.path,
                    mtime_ns=after.mtime_ns,
                    size=after.size,
                    change_ns=after.change_ns,
                    device=after.device,
                    inode=after.inode,
                    sha256=hashlib.sha256(raw).hexdigest(),
                ),
                content=content,
                byte_size=len(raw),
            )
        return None

    def _discard_locked(self, normalized: str) -> None:
        previous = self._entries.pop(normalized, None)
        if previous is not None:
            self._bytes -= previous.byte_size

    def _prune_locked(self) -> None:
        while len(self._entries) > self._max_entries or self._bytes > self._max_bytes:
            _path, removed = self._entries.popitem(last=False)
            self._bytes -= removed.byte_size
