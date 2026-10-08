"""Synchronize the independently installable skill's bundled Python source.

The source/destination are fixed relative to this file. --check is read-only
and exits 1 for source drift. No cache files or third-party dependencies copy.
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import tempfile


def _contained(path: Path, directory: Path) -> Path:
    resolved = path.resolve()
    if not resolved.is_relative_to(directory.resolve()):
        raise ValueError(f"Refusing a path outside the intended directory: {path}")
    return resolved


def _python_sources(directory: Path) -> dict[Path, Path]:
    if not directory.is_dir():
        return {}
    result = {}
    for path in directory.rglob("*.py"):
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts:
            continue
        if path.is_symlink():
            raise ValueError(f"Refusing a symlinked source file: {path}")
        _contained(path, directory)
        if path.is_file():
            result[relative] = path
    return result


def _replace_copy(original: Path, target: Path) -> None:
    """Replace the file atomically rather than following an existing hardlink."""
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix=".sync-skill-", delete=False) as temporary:
            temporary_path = Path(temporary.name)
            temporary.write(original.read_bytes())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def synchronize(*, check: bool) -> int:
    root = Path(__file__).resolve().parents[1]
    source = _contained(root / "sqlite_audit", root)
    skill_scripts = _contained(root / "skills" / "sqlite-migration-audit" / "scripts", root)
    destination = _contained(skill_scripts / "sqlite_audit", skill_scripts)
    license_source = _contained(root / "LICENSE", root)
    license_destination = _contained(skill_scripts / "LICENSE", skill_scripts)
    if not (root / "pyproject.toml").is_file() or not (source / "__init__.py").is_file():
        raise ValueError("Cannot find the fixed repository source package")
    if not license_source.is_file():
        raise ValueError("Cannot find the repository LICENSE")
    originals = _python_sources(source)
    bundled = _python_sources(destination)
    differences = []
    for relative in sorted(originals):
        target = _contained(destination / relative, destination)
        if not target.is_file() or target.read_bytes() != originals[relative].read_bytes():
            differences.append(f"different or missing: sqlite_audit/{relative.as_posix()}")
    obsolete = sorted(bundled.keys() - originals.keys())
    differences.extend(f"obsolete: sqlite_audit/{relative.as_posix()}" for relative in obsolete)
    if not license_destination.is_file() or license_destination.read_bytes() != license_source.read_bytes():
        differences.append("different or missing: LICENSE")
    if check:
        for difference in differences:
            print(difference)
        if differences:
            print("Run python scripts/sync_skill.py to update the bundled copy.")
            return 1
        print(f"Skill bundle matches {len(originals)} Python source files and LICENSE.")
        return 0
    destination.mkdir(parents=True, exist_ok=True)
    for relative, original in sorted(originals.items()):
        target = _contained(destination / relative, destination)
        _replace_copy(original, target)
    # Remove only stale Python files already verified to be inside this fixed bundle.
    # Never delete a directory recursively or touch files outside the source mirror.
    for relative in obsolete:
        _contained(bundled[relative], destination).unlink()
    _replace_copy(license_source, license_destination)
    print(f"Synchronized {len(originals)} Python source files and LICENSE.")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Report source drift without changing the bundle")
    args = parser.parse_args()
    try:
        return synchronize(check=args.check)
    except (OSError, ValueError) as error:
        print(f"sync_skill: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
