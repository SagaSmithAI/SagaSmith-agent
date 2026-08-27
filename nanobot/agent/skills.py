"""Skills loader for agent capabilities."""

from __future__ import annotations

import copy
import json
import os
import re
import shutil
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import yaml

from nanobot.utils.file_cache import (
    BoundedTextFileCache,
    CachedText,
    PathSignature,
    TextCacheKey,
    normalize_cache_path,
    path_signature,
)

BUILTIN_SKILLS_DIR = Path(__file__).parent.parent / "skills"

_STRIP_SKILL_FRONTMATTER = re.compile(
    r"^---\s*\r?\n(.*?)\r?\n---\s*\r?\n?",
    re.DOTALL,
)


@dataclass(frozen=True, slots=True)
class _SkillEntry:
    name: str
    path: str
    source: str

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "path": self.path, "source": self.source}


@dataclass(frozen=True, slots=True)
class _DirectorySnapshot:
    signature: PathSignature
    child_signatures: tuple[PathSignature, ...]
    entries: tuple[_SkillEntry, ...]


@dataclass(frozen=True, slots=True)
class _RequirementSpec:
    bins: tuple[str, ...] = ()
    env: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class _RequirementStatus:
    available: bool
    missing: str
    fingerprint: tuple[tuple[str, str, bool], ...]


@dataclass(frozen=True, slots=True)
class _SkillDocument:
    key: TextCacheKey
    content: str
    body: str
    metadata: dict[str, object] | None
    nanobot_metadata: dict[str, object]
    requirements: _RequirementSpec
    description: str | None
    always: bool
    byte_size: int


class SkillsLoader:
    """Load and index workspace, builtin, and external Agent Skills."""

    _MAX_DIRECTORY_SNAPSHOTS = 16
    _MAX_WATCHED_CHILDREN = 1024
    _MAX_DOCUMENT_ENTRIES = 256
    _MAX_DOCUMENT_BYTES = 8 * 1024 * 1024
    _MAX_SUMMARY_ENTRIES = 32

    def __init__(
        self,
        workspace: Path,
        builtin_skills_dir: Path | None = None,
        disabled_skills: set[str] | None = None,
        external_skill_dirs: list[str | Path] | None = None,
    ):
        self.workspace = workspace
        self.workspace_skills = workspace / "skills"
        self.builtin_skills = builtin_skills_dir or BUILTIN_SKILLS_DIR
        external = os.environ.get("SAGASMITH_EXTERNAL_SKILLS_DIRS", "")
        configured = list(external_skill_dirs or [])
        configured.extend(item for item in external.split(os.pathsep) if item.strip())
        self.external_skill_dirs = []
        known_paths: set[Path] = set()
        for item in configured:
            path = Path(item).expanduser().resolve(strict=False)
            if path in known_paths:
                continue
            known_paths.add(path)
            self.external_skill_dirs.append(path)
        self.disabled_skills = disabled_skills or set()

        # Caches are instance-owned: hosted workspaces and tenants cannot read
        # each other's prompt material through process-global state.
        self._lock = threading.RLock()
        self._text_cache = BoundedTextFileCache(
            max_entries=self._MAX_DOCUMENT_ENTRIES,
            max_bytes=self._MAX_DOCUMENT_BYTES,
        )
        self._directory_snapshots: OrderedDict[str, _DirectorySnapshot] = OrderedDict()
        self._documents: OrderedDict[TextCacheKey, _SkillDocument] = OrderedDict()
        self._document_bytes = 0
        self._summary_cache: OrderedDict[tuple[object, ...], str] = OrderedDict()

    def _skill_entries_from_dir(
        self,
        base: Path,
        source: str,
        *,
        skip_names: set[str] | None = None,
    ) -> list[dict[str, str]]:
        entries = self._cached_entries_from_dir(base, source)
        if skip_names:
            entries = [entry for entry in entries if entry.name not in skip_names]
        return [entry.as_dict() for entry in entries]

    def _cached_entries_from_dir(self, base: Path, source: str) -> list[_SkillEntry]:
        signature = path_signature(base, kind="directory")
        if signature is None:
            with self._lock:
                self._directory_snapshots.pop(normalize_cache_path(base), None)
            return []

        with self._lock:
            snapshot = self._directory_snapshots.get(signature.path)
            if snapshot is not None and self._snapshot_is_current(snapshot, signature):
                self._directory_snapshots.move_to_end(signature.path)
                return [_SkillEntry(entry.name, entry.path, source) for entry in snapshot.entries]

            entries, stable_signature, child_signatures = self._scan_directory(base, source)
            if (
                stable_signature is not None
                and not stable_signature.has_coarse_timestamps
                and len(child_signatures) <= self._MAX_WATCHED_CHILDREN
                and all(not child.has_coarse_timestamps for child in child_signatures)
            ):
                self._directory_snapshots[stable_signature.path] = _DirectorySnapshot(
                    signature=stable_signature,
                    child_signatures=child_signatures,
                    entries=tuple(entries),
                )
                self._directory_snapshots.move_to_end(stable_signature.path)
                while len(self._directory_snapshots) > self._MAX_DIRECTORY_SNAPSHOTS:
                    self._directory_snapshots.popitem(last=False)
            else:
                self._directory_snapshots.pop(signature.path, None)
            return entries

    @staticmethod
    def _snapshot_is_current(
        snapshot: _DirectorySnapshot,
        current_signature: PathSignature,
    ) -> bool:
        if snapshot.signature != current_signature or current_signature.has_coarse_timestamps:
            return False
        for expected in snapshot.child_signatures:
            current = path_signature(Path(expected.path), kind="directory")
            if current != expected or (current is not None and current.has_coarse_timestamps):
                return False
        return True

    @staticmethod
    def _scan_directory(
        base: Path,
        source: str,
    ) -> tuple[list[_SkillEntry], PathSignature | None, tuple[PathSignature, ...]]:
        """Scan once, retrying when the directory changes during discovery."""
        last_entries: list[_SkillEntry] = []
        last_children: tuple[PathSignature, ...] = ()
        for _attempt in range(2):
            before = path_signature(base, kind="directory")
            if before is None:
                return [], None, ()
            try:
                children = list(base.iterdir())
            except OSError:
                return [], None, ()

            entries: list[_SkillEntry] = []
            root_skill = base / "SKILL.md"
            if root_skill.is_file():
                entries.append(_SkillEntry(name=base.name, path=str(root_skill), source=source))

            child_signatures: list[PathSignature] = []
            for skill_dir in children:
                child_signature = path_signature(skill_dir, kind="directory")
                if child_signature is None:
                    continue
                child_signatures.append(child_signature)
                skill_file = skill_dir / "SKILL.md"
                if skill_file.is_file():
                    entries.append(
                        _SkillEntry(
                            name=skill_dir.name,
                            path=str(skill_file),
                            source=source,
                        )
                    )

            after = path_signature(base, kind="directory")
            last_entries = entries
            last_children = tuple(child_signatures)
            if before == after:
                return entries, after, last_children
        return last_entries, None, last_children

    def _all_entries(self, *, include_disabled: bool = False) -> list[_SkillEntry]:
        skills = self._cached_entries_from_dir(self.workspace_skills, "workspace")
        workspace_names = {entry.name for entry in skills}
        if self.builtin_skills:
            skills.extend(
                entry
                for entry in self._cached_entries_from_dir(self.builtin_skills, "builtin")
                if entry.name not in workspace_names
            )
        known_names = {entry.name for entry in skills}
        for root in self.external_skill_dirs:
            extra = [
                entry
                for entry in self._cached_entries_from_dir(root, "external")
                if entry.name not in known_names
            ]
            skills.extend(extra)
            known_names.update(entry.name for entry in extra)
        if include_disabled or not self.disabled_skills:
            return skills
        return [entry for entry in skills if entry.name not in self.disabled_skills]

    def list_skills(self, filter_unavailable: bool = True) -> list[dict[str, str]]:
        """List skills with stable workspace/builtin/external precedence."""
        skills = self._all_entries()
        if filter_unavailable:
            skills = [
                entry
                for entry in skills
                if self._requirement_status(self._document_for_entry(entry).requirements).available
            ]
        return [entry.as_dict() for entry in skills]

    def _find_entry(self, name: str) -> _SkillEntry | None:
        return next(
            (entry for entry in self._all_entries(include_disabled=True) if entry.name == name),
            None,
        )

    def load_skill(self, name: str) -> str | None:
        """Load a skill by name, using the in-memory text cache."""
        entry = self._find_entry(name)
        if entry is None:
            return None
        cached = self._text_cache.read(Path(entry.path))
        return cached.content if cached is not None else None

    def load_skills_for_context(self, skill_names: list[str]) -> str:
        """Load specific skills for inclusion in agent context."""
        parts: list[str] = []
        for name in skill_names:
            entry = self._find_entry(name)
            if entry is None:
                continue
            document = self._document_for_entry(entry)
            if not document.content:
                continue
            parts.append(f"### Skill: {name}\n\n{document.body}")
        return "\n\n---\n\n".join(parts)

    def build_skills_summary(self, exclude: set[str] | None = None) -> str:
        """Build a cached summary keyed by files and live requirements."""
        entries = self._all_entries()
        excluded = frozenset(exclude or ())
        rows: list[tuple[_SkillEntry, _SkillDocument, _RequirementStatus]] = []
        state: list[object] = [excluded]
        for entry in entries:
            if entry.name in excluded:
                continue
            document = self._document_for_entry(entry)
            status = self._requirement_status(document.requirements)
            rows.append((entry, document, status))
            state.append(
                (
                    entry.name,
                    entry.path,
                    entry.source,
                    document.key,
                    status.fingerprint,
                )
            )
        cache_key = tuple(state)
        with self._lock:
            if cache_key in self._summary_cache:
                cached = self._summary_cache[cache_key]
                self._summary_cache.move_to_end(cache_key)
                return cached
            lines: list[str] = []
            for entry, document, status in rows:
                description = document.description or entry.name
                if status.available:
                    lines.append(f"- **{entry.name}** — {description}  `{entry.path}`")
                else:
                    suffix = (
                        f" (unavailable: {status.missing})"
                        if status.missing
                        else " (unavailable)"
                    )
                    lines.append(
                        f"- **{entry.name}** — {description}{suffix}  `{entry.path}`"
                    )
            summary = "\n".join(lines)
            self._summary_cache[cache_key] = summary
            self._summary_cache.move_to_end(cache_key)
            while len(self._summary_cache) > self._MAX_SUMMARY_ENTRIES:
                self._summary_cache.popitem(last=False)
            return summary

    @staticmethod
    def _parse_nanobot_metadata(raw: object) -> dict[str, object]:
        """Extract nanobot/openclaw metadata from a frontmatter field."""
        if isinstance(raw, dict):
            data = raw
        elif isinstance(raw, str):
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                return {}
        else:
            return {}
        if not isinstance(data, dict):
            return {}
        payload = data.get("nanobot", data.get("openclaw", {}))
        if not isinstance(payload, dict):
            return {}
        return {str(key): value for key, value in payload.items()}

    @staticmethod
    def _requirement_spec(skill_meta: dict[str, object]) -> _RequirementSpec:
        requires = skill_meta.get("requires", {})
        if not isinstance(requires, dict):
            return _RequirementSpec()
        raw_bins = requires.get("bins", [])
        raw_env = requires.get("env", [])
        bins = tuple(str(value) for value in raw_bins) if isinstance(raw_bins, list) else ()
        env = tuple(str(value) for value in raw_env) if isinstance(raw_env, list) else ()
        return _RequirementSpec(bins=bins, env=env)

    @staticmethod
    def _requirement_status(requirements: _RequirementSpec) -> _RequirementStatus:
        bin_states = tuple(("bin", name, bool(shutil.which(name))) for name in requirements.bins)
        env_states = tuple(("env", name, bool(os.environ.get(name))) for name in requirements.env)
        missing = [f"CLI: {name}" for _kind, name, available in bin_states if not available]
        missing.extend(f"ENV: {name}" for _kind, name, available in env_states if not available)
        fingerprint = bin_states + env_states
        return _RequirementStatus(
            available=all(available for _kind, _name, available in fingerprint),
            missing=", ".join(missing),
            fingerprint=fingerprint,
        )

    def _document_for_entry(self, entry: _SkillEntry) -> _SkillDocument:
        cached = self._text_cache.read(Path(entry.path))
        if cached is None:
            # Discovery and reading raced a deletion. Missing metadata behaves
            # as empty, matching the previous loader behavior.
            return self._empty_document(entry.path)
        with self._lock:
            document = self._documents.get(cached.key)
            if document is not None:
                self._documents.move_to_end(cached.key)
                return document
            document = self._parse_document(cached)
            if document.byte_size <= self._MAX_DOCUMENT_BYTES:
                stale_keys = [key for key in self._documents if key.path == cached.key.path]
                for stale_key in stale_keys:
                    removed = self._documents.pop(stale_key)
                    self._document_bytes -= removed.byte_size
                self._documents[cached.key] = document
                self._document_bytes += document.byte_size
                self._documents.move_to_end(cached.key)
                while (
                    len(self._documents) > self._MAX_DOCUMENT_ENTRIES
                    or self._document_bytes > self._MAX_DOCUMENT_BYTES
                ):
                    _key, removed = self._documents.popitem(last=False)
                    self._document_bytes -= removed.byte_size
            return document

    @staticmethod
    def _empty_document(path: str) -> _SkillDocument:
        key = TextCacheKey(
            path=path,
            mtime_ns=0,
            size=0,
            change_ns=0,
            device=0,
            inode=0,
            sha256="",
        )
        return _SkillDocument(
            key=key,
            content="",
            body="",
            metadata=None,
            nanobot_metadata={},
            requirements=_RequirementSpec(),
            description=None,
            always=False,
            byte_size=0,
        )

    def _parse_document(self, cached: CachedText) -> _SkillDocument:
        content = cached.content
        metadata: dict[str, object] | None = None
        body = content
        match = _STRIP_SKILL_FRONTMATTER.match(content) if content.startswith("---") else None
        if match is not None:
            body = content[match.end():].strip()
            try:
                parsed = yaml.safe_load(match.group(1))
            except yaml.YAMLError:
                parsed = None
            if isinstance(parsed, dict):
                metadata = {str(key): value for key, value in parsed.items()}

        nanobot_metadata = self._parse_nanobot_metadata(
            metadata.get("metadata") if metadata else None
        )
        description = metadata.get("description") if metadata else None
        requirements = self._requirement_spec(nanobot_metadata)
        always = bool(
            nanobot_metadata.get("always")
            or (metadata.get("always") if metadata else False)
        )
        return _SkillDocument(
            key=cached.key,
            content=content,
            body=body,
            metadata=metadata,
            nanobot_metadata=nanobot_metadata,
            requirements=requirements,
            description=str(description) if description else None,
            always=always,
            byte_size=cached.byte_size,
        )

    def _get_missing_requirements(self, skill_meta: dict) -> str:
        """Get a description of missing requirements."""
        return self._requirement_status(self._requirement_spec(skill_meta)).missing

    def get_skill_availability(self, name: str) -> tuple[bool, str]:
        """Return whether a skill can run and why not when it cannot."""
        entry = self._find_entry(name)
        if entry is None:
            return True, ""
        status = self._requirement_status(self._document_for_entry(entry).requirements)
        return status.available, "" if status.available else status.missing

    def get_skill_requirements(self, name: str) -> dict[str, list[str]]:
        """Return explicit command/env requirements and currently missing entries."""
        entry = self._find_entry(name)
        requirements = (
            self._document_for_entry(entry).requirements if entry is not None else _RequirementSpec()
        )
        status = self._requirement_status(requirements)
        missing_bins = [
            requirement
            for kind, requirement, available in status.fingerprint
            if kind == "bin" and not available
        ]
        missing_env = [
            requirement
            for kind, requirement, available in status.fingerprint
            if kind == "env" and not available
        ]
        return {
            "bins": list(requirements.bins),
            "env": list(requirements.env),
            "missing_bins": missing_bins,
            "missing_env": missing_env,
        }

    def _get_skill_description(self, name: str) -> str:
        """Get the description of a skill from its frontmatter."""
        entry = self._find_entry(name)
        if entry is None:
            return name
        return self._document_for_entry(entry).description or name

    @staticmethod
    def _strip_frontmatter(content: str) -> str:
        """Remove YAML frontmatter from markdown content."""
        if not content.startswith("---"):
            return content
        match = _STRIP_SKILL_FRONTMATTER.match(content)
        if match:
            return content[match.end():].strip()
        return content

    def _check_requirements(self, skill_meta: dict) -> bool:
        """Check if skill requirements are met (bins, env vars)."""
        return self._requirement_status(self._requirement_spec(skill_meta)).available

    def _get_skill_meta(self, name: str) -> dict:
        """Get parsed nanobot metadata for a skill."""
        entry = self._find_entry(name)
        if entry is None:
            return {}
        return copy.deepcopy(self._document_for_entry(entry).nanobot_metadata)

    def get_always_skills(self) -> list[str]:
        """Get skills marked as always=true that meet requirements."""
        always: list[str] = []
        for entry in self._all_entries():
            document = self._document_for_entry(entry)
            if document.always and self._requirement_status(document.requirements).available:
                always.append(entry.name)
        return always

    def get_skill_metadata(self, name: str) -> dict | None:
        """Get a defensive copy of parsed skill frontmatter."""
        entry = self._find_entry(name)
        if entry is None:
            return None
        metadata = self._document_for_entry(entry).metadata
        return copy.deepcopy(metadata) if metadata is not None else None
