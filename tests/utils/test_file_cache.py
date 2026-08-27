"""Tests for the bounded process-local text cache."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from nanobot.utils.file_cache import BoundedTextFileCache


def _precise_mtime(path: Path, offset: int = 0) -> None:
    value = 1_700_000_000_123_456_789 + offset
    os.utime(path, ns=(value, value))


def test_unchanged_file_is_not_read_twice(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "AGENTS.md"
    path.write_text("steady", encoding="utf-8")
    _precise_mtime(path)
    cache = BoundedTextFileCache(max_entries=4, max_bytes=1024)

    first = cache.read(path)
    assert first is not None

    def unexpected_read(_path: Path) -> bytes:
        raise AssertionError("hot cache hit must not read file contents")

    monkeypatch.setattr(Path, "read_bytes", unexpected_read)
    second = cache.read(path)

    assert second == first
    assert cache.info().hits == 1


def test_coarse_same_size_mtime_collision_uses_content_hash(tmp_path: Path) -> None:
    path = tmp_path / "SOUL.md"
    path.write_text("first", encoding="utf-8")
    coarse = 1_700_000_000_000_000_000
    os.utime(path, ns=(coarse, coarse))
    cache = BoundedTextFileCache(max_entries=4, max_bytes=1024)

    assert cache.read(path).content == "first"  # type: ignore[union-attr]
    path.write_text("other", encoding="utf-8")
    os.utime(path, ns=(coarse, coarse))

    refreshed = cache.read(path)
    assert refreshed is not None
    assert refreshed.content == "other"
    assert refreshed.key.sha256 != ""


def test_cache_is_bounded_by_entries_and_bytes(tmp_path: Path) -> None:
    cache = BoundedTextFileCache(max_entries=2, max_bytes=7)
    paths = [tmp_path / f"{index}.md" for index in range(3)]
    for index, path in enumerate(paths):
        path.write_text(f"x{index}", encoding="utf-8")
        _precise_mtime(path, index)
        assert cache.read(path) is not None

    info = cache.info()
    assert info.entries == 2
    assert info.bytes <= 7

    oversized = tmp_path / "large.md"
    oversized.write_text("too-large", encoding="utf-8")
    _precise_mtime(oversized, 10)
    assert cache.read(oversized).content == "too-large"  # type: ignore[union-attr]
    assert cache.info().bytes <= 7


def test_paths_and_cache_instances_are_isolated(tmp_path: Path) -> None:
    left = tmp_path / "tenant-a" / "IDENTITY.md"
    right = tmp_path / "tenant-b" / "IDENTITY.md"
    left.parent.mkdir()
    right.parent.mkdir()
    left.write_text("tenant-a", encoding="utf-8")
    right.write_text("tenant-b", encoding="utf-8")
    _precise_mtime(left)
    _precise_mtime(right, 1)

    left_cache = BoundedTextFileCache(max_entries=2, max_bytes=1024)
    right_cache = BoundedTextFileCache(max_entries=2, max_bytes=1024)

    assert left_cache.read(left).content == "tenant-a"  # type: ignore[union-attr]
    assert right_cache.read(right).content == "tenant-b"  # type: ignore[union-attr]
    assert left_cache.info().entries == right_cache.info().entries == 1


def test_concurrent_reads_are_safe_and_share_one_entry(tmp_path: Path) -> None:
    path = tmp_path / "USER.md"
    path.write_text("thread-safe", encoding="utf-8")
    _precise_mtime(path)
    cache = BoundedTextFileCache(max_entries=4, max_bytes=1024)

    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(lambda _index: cache.read(path).content, range(32)))  # type: ignore[union-attr]

    assert values == ["thread-safe"] * 32
    assert cache.info().entries == 1
    assert cache.info().hits == 31
