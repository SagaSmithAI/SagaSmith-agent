"""Doctor, back up, verify, or recover one stopped local SagaSmith D&D system."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import shutil
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

FORMAT = "sagasmith-local-dnd-backup-v1"
RUNTIME_MARKER = ".sagasmith-runtime.json"


def _inside(path: Path, root: Path) -> bool:
    return path.resolve().is_relative_to(root.resolve())


def _require_stopped(workspace: Path) -> None:
    marker = workspace / RUNTIME_MARKER
    if marker.exists():
        raise ValueError(
            f"local runtime marker exists: {marker}; stop start.bat before this operation"
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if hasattr(ctypes, "windll"):
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True
    try:
        import os

        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _hash_stream(stream: Any) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    while chunk := stream.read(1024 * 1024):
        digest.update(chunk)
        size += len(chunk)
    return digest.hexdigest(), size


def _workspace_files(workspace: Path) -> list[Path]:
    return sorted(
        path
        for path in workspace.rglob("*")
        if path.is_file() and path.name != RUNTIME_MARKER
    )


def create_backup(agent_root: Path, output: Path) -> dict[str, Any]:
    workspace = (agent_root / "workspace").resolve()
    if not workspace.is_dir():
        raise ValueError(f"workspace does not exist: {workspace}")
    _require_stopped(workspace)
    output = output.resolve()
    if _inside(output, workspace):
        raise ValueError("backup output must be outside the workspace being archived")
    if output.exists():
        raise ValueError(f"backup output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    files: list[dict[str, Any]] = []
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=6,
        ) as archive:
            for source in _workspace_files(workspace):
                relative = source.relative_to(workspace).as_posix()
                with source.open("rb") as stream:
                    digest, size = _hash_stream(stream)
                archive.write(source, f"data/{relative}")
                files.append({"path": relative, "sha256": digest, "size": size})
            manifest = {
                "format": FORMAT,
                "created_at": datetime.now(UTC).isoformat(),
                "scope": "workspace",
                "files": files,
            }
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            )
        temporary.replace(output)
    finally:
        temporary.unlink(missing_ok=True)
    verify_backup(output)
    return manifest


def _safe_relative(value: str) -> Path:
    pure = PurePosixPath(value)
    if pure.is_absolute() or not pure.parts or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError(f"unsafe backup path: {value!r}")
    return Path(*pure.parts)


def verify_backup(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path) as archive:
        try:
            manifest = json.loads(archive.read("manifest.json"))
        except (KeyError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid backup manifest: {exc}") from exc
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ValueError("unsupported backup format")
        entries = manifest.get("files")
        if not isinstance(entries, list):
            raise ValueError("backup manifest files must be a list")
        expected_names = {"manifest.json"}
        for entry in entries:
            if not isinstance(entry, dict):
                raise ValueError("backup file entry must be an object")
            relative = _safe_relative(str(entry.get("path") or ""))
            name = f"data/{relative.as_posix()}"
            expected_names.add(name)
            try:
                with archive.open(name) as stream:
                    digest, size = _hash_stream(stream)
            except KeyError as exc:
                raise ValueError(f"backup is missing {name}") from exc
            if digest != entry.get("sha256") or size != entry.get("size"):
                raise ValueError(f"backup checksum mismatch: {relative.as_posix()}")
        extra = set(archive.namelist()) - expected_names
        if extra:
            raise ValueError(f"backup contains unmanifested entries: {sorted(extra)!r}")
        return manifest


def restore_backup(agent_root: Path, archive_path: Path) -> Path:
    agent_root = agent_root.resolve()
    workspace = (agent_root / "workspace").resolve()
    if not _inside(workspace, agent_root):
        raise ValueError("workspace escaped the Agent repository")
    _require_stopped(workspace)
    manifest = verify_backup(archive_path)
    staging = Path(tempfile.mkdtemp(prefix=".sagasmith-restore-", dir=agent_root))
    restored = staging / "workspace"
    restored.mkdir()
    previous: Path | None = None
    try:
        with zipfile.ZipFile(archive_path) as archive:
            for entry in manifest["files"]:
                relative = _safe_relative(entry["path"])
                target = (restored / relative).resolve()
                if not _inside(target, restored):
                    raise ValueError(f"restore path escaped staging: {relative}")
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(f"data/{relative.as_posix()}") as source:
                    with target.open("wb") as destination:
                        shutil.copyfileobj(source, destination)

        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
        previous = agent_root / f"workspace.pre-restore-{timestamp}"
        if workspace.exists():
            workspace.replace(previous)
        restored.replace(workspace)
        staging.rmdir()
        return previous
    except Exception:
        if previous is not None and previous.exists() and not workspace.exists():
            previous.replace(workspace)
        shutil.rmtree(staging, ignore_errors=True)
        raise


def doctor(agent_root: Path) -> list[str]:
    issues: list[str] = []
    workspace = agent_root / "workspace"
    checks = (
        (agent_root / "config" / "config.json", "Agent config"),
        (agent_root / "nanobot" / "web" / "dist" / "index.html", "Agent WebUI build"),
        (agent_root.parent / "SagaSmith-dnd-ui" / "dist" / "index.html", "D&D UI build"),
        (agent_root.parent / "SagaSmith-dnd-mcp" / ".venv" / "Scripts" / "sagasmith-dnd-mcp.exe", "D&D MCP executable"),
        (workspace / ".sagasmith-dnd-mcp", "D&D data home"),
    )
    for path, label in checks:
        if not path.exists():
            issues.append(f"{label} is missing: {path}")
    marker = workspace / RUNTIME_MARKER
    if marker.exists():
        try:
            runtime = json.loads(marker.read_text(encoding="utf-8"))
            pids = [
                runtime.get(name)
                for name in ("agent_pid", "dnd_gateway_pid", "dnd_mcp_pid")
            ]
            if not all(isinstance(pid, int) and _pid_alive(pid) for pid in pids):
                issues.append(f"runtime marker is stale or incomplete: {marker}")
        except (OSError, UnicodeError, json.JSONDecodeError, AttributeError):
            issues.append(f"runtime marker is invalid: {marker}")
    database = workspace / ".sagasmith-dnd-mcp" / "data" / "ttrpgbase.db"
    if database.exists():
        try:
            with sqlite3.connect(f"file:{database.as_posix()}?mode=ro", uri=True) as connection:
                result = connection.execute("PRAGMA quick_check").fetchone()
            if result != ("ok",):
                issues.append(f"D&D SQLite quick_check failed: {result!r}")
        except sqlite3.Error as exc:
            issues.append(f"cannot inspect D&D SQLite database: {exc}")
    return issues


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent-root", default=str(Path(__file__).resolve().parents[1]))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("doctor")
    backup = commands.add_parser("backup")
    backup.add_argument("output", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("archive", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("archive", type=Path)
    restore.add_argument("--yes", action="store_true")
    args = parser.parse_args()
    agent_root = Path(args.agent_root).expanduser().resolve()
    try:
        if args.command == "doctor":
            issues = doctor(agent_root)
            if issues:
                for issue in issues:
                    print(f"[ERROR] {issue}")
                return 1
            print("SagaSmith local D&D system: OK")
        elif args.command == "backup":
            manifest = create_backup(agent_root, args.output)
            print(f"Backup created and verified: {args.output.resolve()}")
            print(f"Files: {len(manifest['files'])}")
        elif args.command == "verify":
            manifest = verify_backup(args.archive.resolve())
            print(f"Backup verified: {args.archive.resolve()} ({len(manifest['files'])} files)")
        else:
            if not args.yes:
                raise ValueError("restore replaces workspace; rerun with --yes after stopping start.bat")
            previous = restore_backup(agent_root, args.archive.resolve())
            print(f"Workspace restored. Previous workspace retained at: {previous}")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        print(f"[ERROR] {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
