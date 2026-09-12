from __future__ import annotations

import argparse
import hashlib
import os
import sys
import zipfile
from collections.abc import Iterable
from pathlib import Path
from uuid import uuid4

try:
    from .release_cleanup import (
        PRIVATE_RUNTIME_DIRECTORIES,
        RUNTIME_NAMES,
        find_legacy_files,
    )
except ImportError:  # direct script execution
    from release_cleanup import (  # type: ignore[no-redef]
        PRIVATE_RUNTIME_DIRECTORIES,
        RUNTIME_NAMES,
        find_legacy_files,
    )


EXCLUDED_DIR_NAMES = {
    ".git",
    ".github",
    ".idea",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "risk_alpha_output",
    "risk_audit_output",
    "risk_beta_output",
    "risk_integration_output",
    "risk_stable_output",
    "qualification_output",
    "verification_output",
    "backups",
    "logs",
    "reports",
    "runtime",
    "support",
    "standalone_output",
    "cash_ledger_v3_10.sqlite3",
    *PRIVATE_RUNTIME_DIRECTORIES,
}
EXCLUDED_FILE_NAMES = {
    *RUNTIME_NAMES,
    ".DS_Store",
    "Thumbs.db",
    "ZIP_CONTENTS.txt",
    "HOTFIX_CONTENTS.txt",
}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".tmp", ".bak", ".lock"}


def scan_release_files_for_canaries(
    files: Iterable[Path],
    *,
    canaries: Iterable[str] = (),
) -> None:
    """Fail the build when a known secret canary is present in release files.

    The scanner intentionally uses explicit canaries rather than broad token
    heuristics because the source distribution contains security tests and
    regular expressions with synthetic token-shaped strings.
    """

    needles = [str(item).encode("utf-8") for item in canaries if str(item)]
    if not needles:
        return
    findings: list[str] = []
    for path in files:
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise RuntimeError(f"Cannot scan release file {path}: {exc}") from exc
        if any(needle in data for needle in needles):
            findings.append(str(path))
    if findings:
        raise RuntimeError("Release secret canary detected in: " + ", ".join(findings))


def collect_release_files(
    root: Path,
    output: Path | None = None,
    *,
    secret_canaries: Iterable[str] = (),
) -> list[Path]:
    legacy = find_legacy_files(root)
    if legacy:
        names = ", ".join(path.name for path in legacy)
        raise RuntimeError(f"Legacy release files remain: {names}")

    output_resolved = output.resolve() if output is not None else None
    files: list[Path] = []
    for path in root.rglob("*"):
        if path.is_symlink():
            raise RuntimeError(
                "Release tree contains a symbolic link: "
                + path.relative_to(root).as_posix()
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part in EXCLUDED_DIR_NAMES for part in relative.parts[:-1]):
            continue
        if path.name in EXCLUDED_FILE_NAMES:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        if output_resolved is not None and path.resolve() == output_resolved:
            continue
        files.append(path)
    files = sorted(files, key=lambda item: item.relative_to(root).as_posix())
    scan_release_files_for_canaries(files, canaries=secret_canaries)
    return files


def build_zip(
    root: Path,
    output: Path,
    archive_root: str,
    *,
    secret_canaries: Iterable[str] = (),
    required_empty_directories: Iterable[str] = (),
) -> list[str]:
    root = root.resolve()
    output = output.resolve()
    files = collect_release_files(root, output, secret_canaries=secret_canaries)
    output.parent.mkdir(parents=True, exist_ok=True)
    members: list[str] = []

    def write_bytes(archive: zipfile.ZipFile, member: str, data: bytes) -> None:
        info = zipfile.ZipInfo(member, date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = (0o644 & 0xFFFF) << 16
        info.create_system = 3
        archive.writestr(
            info, data, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
        )

    def write_directory(archive: zipfile.ZipFile, member: str) -> None:
        normalized = member.rstrip("/") + "/"
        info = zipfile.ZipInfo(normalized, date_time=(2020, 1, 1, 0, 0, 0))
        info.compress_type = zipfile.ZIP_DEFLATED
        info.external_attr = ((0o40755 & 0xFFFF) << 16) | 0x10
        info.create_system = 3
        archive.writestr(info, b"", compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)

    prefix = archive_root.strip("/")
    directory_members: list[str] = []
    for value in required_empty_directories:
        relative = Path(str(value).replace("\\", "/"))
        if relative.is_absolute() or not relative.parts or ".." in relative.parts:
            raise RuntimeError(f"Unsafe required empty directory: {value!r}")
        selected = root.joinpath(*relative.parts)
        if not selected.is_dir() or any(selected.iterdir()):
            raise RuntimeError(
                "Required release directory is missing or not empty: "
                + relative.as_posix()
            )
        member = relative.as_posix().rstrip("/") + "/"
        directory_members.append(f"{prefix}/{member}" if prefix else member)
    if len(directory_members) != len(set(directory_members)):
        raise RuntimeError("Required empty release directories are not unique.")

    file_members = [
        (
            f"{prefix}/{path.relative_to(root).as_posix()}"
            if prefix
            else path.relative_to(root).as_posix()
        )
        for path in files
    ]
    payload_members = sorted([*directory_members, *file_members])
    file_by_member = dict(zip(file_members, files, strict=True))
    temporary = output.with_name(f"{output.name}.{uuid4().hex}.tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(temporary, mode="w") as archive:
            for member in payload_members:
                path = file_by_member.get(member)
                if path is None:
                    write_directory(archive, member)
                else:
                    write_bytes(archive, member, path.read_bytes())
                members.append(member)
            manifest_member = (
                f"{prefix}/ZIP_CONTENTS.txt" if prefix else "ZIP_CONTENTS.txt"
            )
            write_bytes(
                archive,
                manifest_member,
                ("\n".join(members + [manifest_member]) + "\n").encode("utf-8"),
            )
            members.append(manifest_member)
        with zipfile.ZipFile(temporary, "r") as completed:
            if completed.namelist() != members or completed.testzip() is not None:
                raise RuntimeError("Completed release ZIP verification failed.")
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)
    return members


def zip_identity(path: str | Path) -> dict[str, object]:
    """Return deterministic member and content identities for a completed ZIP."""

    selected = Path(path)
    raw = selected.read_bytes()
    with zipfile.ZipFile(selected, "r") as archive:
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC validation failed.")
        members = archive.namelist()
        payload_members = members[:-1]
        manifest_member = members[-1] if members else ""
        if (
            payload_members != sorted(payload_members)
            or not manifest_member.endswith("ZIP_CONTENTS.txt")
            or len(members) != len(set(members))
        ):
            raise RuntimeError("ZIP members are not unique and sorted.")
        expected_contents = ("\n".join(members) + "\n").encode("utf-8")
        if archive.read(manifest_member) != expected_contents:
            raise RuntimeError("ZIP_CONTENTS.txt does not match archive members.")
        for info in archive.infolist():
            expected_mode = 0o755 if info.is_dir() else 0o644
            if (
                info.date_time != (2020, 1, 1, 0, 0, 0)
                or info.compress_type != zipfile.ZIP_DEFLATED
                or info.create_system != 3
                or (info.external_attr >> 16) & 0o777 != expected_mode
                or (info.is_dir() and archive.read(info.filename) != b"")
            ):
                raise RuntimeError("ZIP member metadata is not deterministic.")
        member_sha256 = {
            name: hashlib.sha256(archive.read(name)).hexdigest() for name in members
        }
    return {
        "sha256": hashlib.sha256(raw).hexdigest(),
        "size_bytes": len(raw),
        "members": members,
        "member_sha256": member_sha256,
    }


def verify_deterministic_pair(
    first: str | Path, second: str | Path
) -> dict[str, object]:
    first_identity = zip_identity(first)
    second_identity = zip_identity(second)
    return {
        "status": "PASS" if first_identity == second_identity else "FAIL",
        "first": first_identity,
        "second": second_identity,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build a sanitized, deterministic release ZIP."
    )
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument(
        "--secret-canary",
        action="append",
        default=[],
        help="Known secret value that must not occur in any release member.",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    output = Path(args.output)
    if not output.is_absolute():
        output = (Path.cwd() / output).resolve()
    if not root.is_dir():
        print(f"[ERROR] Release root is not a directory: {root}")
        return 2
    env_canary = os.getenv("MOEX_RELEASE_SECRET_CANARY", "").strip()
    canaries = [*args.secret_canary, *([env_canary] if env_canary else [])]
    try:
        files = collect_release_files(root, output, secret_canaries=canaries)
        if args.check_only:
            for path in files:
                print(path.relative_to(root).as_posix())
            print(f"Release build check: PASS ({len(files)} files)")
            return 0
        members = build_zip(
            root, output, str(args.archive_root), secret_canaries=canaries
        )
    except (OSError, RuntimeError, ValueError, zipfile.BadZipFile) as exc:
        print(f"[ERROR] {exc}")
        return 1

    print(f"Release ZIP: {output}")
    print(f"Members: {len(members)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
