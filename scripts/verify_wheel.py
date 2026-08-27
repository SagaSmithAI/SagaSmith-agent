"""Verify that a built wheel contains every Python module in the source tree."""

from __future__ import annotations

import argparse
from pathlib import Path
from zipfile import ZipFile


def resolve_wheel(candidate: Path) -> Path:
    """Resolve either one wheel path or a directory containing exactly one wheel."""
    if candidate.is_file():
        return candidate
    wheels = sorted(candidate.glob("*.whl")) if candidate.is_dir() else []
    if len(wheels) != 1:
        raise RuntimeError(f"expected exactly one wheel in {candidate}, found {len(wheels)}")
    return wheels[0]


def expected_python_members(source_root: Path) -> set[str]:
    """Return package-relative paths for every Python source module."""
    package_root = source_root / "nanobot"
    return {
        path.relative_to(source_root).as_posix()
        for path in package_root.rglob("*.py")
        if "__pycache__" not in path.parts
    }


def verify_wheel(wheel: Path, source_root: Path) -> None:
    """Raise when the wheel silently drops a Python package or module."""
    expected = expected_python_members(source_root)
    with ZipFile(wheel) as archive:
        packaged = set(archive.namelist())
    missing = sorted(expected - packaged)
    if missing:
        formatted = "\n".join(f"- {member}" for member in missing)
        raise RuntimeError(f"wheel is missing Python modules:\n{formatted}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheel", type=Path, help="wheel file or directory containing one wheel")
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    wheel = resolve_wheel(arguments.wheel.resolve())
    verify_wheel(wheel, arguments.source_root.resolve())
    print(f"verified Python package contents: {wheel}")


if __name__ == "__main__":
    main()
