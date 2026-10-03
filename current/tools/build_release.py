from __future__ import annotations

import argparse
import hashlib
import io
import os
import sys
import tempfile
import zipfile
from collections.abc import Iterable
from contextlib import ExitStack
from pathlib import Path

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


try:
    from .release_safety import (
        has_financial_capture_body, has_financial_capture_path, private_capture_file,
        safe_parts, validate_member_inventory, validate_zip_metadata,
    )
except ImportError:  # direct script execution
    from release_safety import (
        has_financial_capture_body, has_financial_capture_path, private_capture_file,
        safe_parts, validate_member_inventory, validate_zip_metadata,
    )


try:
    from .release_payload import ReleaseSource
except ImportError:  # direct script execution
    from release_payload import ReleaseSource


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


def _freeze_canaries(canaries: Iterable[str]) -> tuple[str, ...]:
    return tuple(text for item in canaries if (text := str(item)))


def _scan_payload(raw: bytes, needles: tuple[bytes, ...]) -> None:
    if any(needle in raw for needle in needles):
        # Do not put secret values or payload content in diagnostics.
        raise RuntimeError("Release secret canary detected in payload")


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

    needles = [item.encode("utf-8") for item in _freeze_canaries(canaries)]
    if not needles:
        return
    findings: list[str] = []
    for path in files:
        try:
            with ReleaseSource(path.parent) as source:
                data = source.read(path)
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
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Release root is a symlink or reparse point")
    legacy = find_legacy_files(root)
    if legacy:
        names = ", ".join(path.name for path in legacy)
        raise RuntimeError(f"Legacy release files remain: {names}")

    output_resolved = output.resolve() if output is not None else None
    files: list[Path] = []
    for path in root.rglob("*"):
        if (path.is_symlink()
                or getattr(path.lstat(), "st_file_attributes", 0) & 0x400):
            raise RuntimeError(
                "Release tree contains a symbolic link: "
                + path.relative_to(root).as_posix()
            )
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if (any(part.casefold() in {n.casefold() for n in EXCLUDED_DIR_NAMES}
                for part in relative.parts[:-1])
                or has_financial_capture_path(relative.parts)):
            continue
        if path.name.casefold() in {n.casefold() for n in EXCLUDED_FILE_NAMES}:
            continue
        if path.suffix.lower() in EXCLUDED_SUFFIXES:
            continue
        if output_resolved is not None and path.resolve() == output_resolved:
            continue
        safe_parts(relative.as_posix())
        if private_capture_file(path):
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
    canaries = _freeze_canaries(secret_canaries)
    safe_parts(archive_root, allow_empty=True)
    try:
        # Output cleanup outlives source leases. Only a completely verified ZIP
        # crosses this boundary; no source is reopened after releasing leases.
        with ExitStack() as output_cleanup:
            with ReleaseSource(root) as source:
                members, temporary = _build_checked_zip(
                    source, output, archive_root, canaries,
                    required_empty_directories, output_cleanup,
                )
            os.replace(temporary, output.absolute())
            return members
    except OSError as exc:
        raise RuntimeError("Release build filesystem boundary failed") from exc


def _build_checked_zip(
    source: ReleaseSource, output: Path, archive_root: str,
    canaries: tuple[str, ...], required_empty_directories: Iterable[str],
    output_cleanup: ExitStack,
) -> tuple[list[str], Path]:
    root = source.root
    needles = tuple(value.encode("utf-8") for value in canaries)
    safe_parts(archive_root, allow_empty=True)
    if root.is_symlink() or getattr(root.lstat(), "st_file_attributes", 0) & 0x400:
        raise RuntimeError("Release root is a symbolic link")
    output = output.absolute()
    if output.is_symlink() or (output.exists() and getattr(output.lstat(), "st_file_attributes", 0) & 0x400):
        raise RuntimeError("Release destination is a link or reparse point")
    files = collect_release_files(root, output, secret_canaries=canaries)
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

    prefix = archive_root
    directory_members: list[str] = []
    for value in required_empty_directories:
        relative = Path(*safe_parts(value))
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
    manifest_member = f"{prefix}/ZIP_CONTENTS.txt" if prefix else "ZIP_CONTENTS.txt"
    validate_member_inventory([*payload_members, manifest_member])
    file_by_member = dict(zip(file_members, files, strict=True))
    output.parent.mkdir(parents=True, exist_ok=True)
    with ExitStack() as fd_owner:
        fd, temporary_name = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
        # Register ownership immediately, before any stream constructor/audit
        # call. The CRT stream borrows the fd; fd_owner always closes it first.
        fd_owner.callback(os.close, fd)
        temporary = Path(temporary_name)
        output_cleanup.callback(temporary.unlink, missing_ok=True)
        with os.fdopen(fd, "w+b", closefd=False) as temporary_stream, zipfile.ZipFile(temporary_stream, mode="w") as archive:
            for member in payload_members:
                path = file_by_member.get(member)
                if path is None:
                    write_directory(archive, member)
                else:
                    # Scan the same immutable buffer sent to writestr. Earlier
                    # checked advisory reads never authorise a later read.
                    raw = source.read(path)
                    _scan_payload(raw, needles)
                    _scan_payload(member.encode("utf-8"), needles)
                    if path.suffix.casefold() == ".json" and has_financial_capture_body(raw):
                        raise RuntimeError("Financial capture appeared during packaging")
                    write_bytes(archive, member, raw)
                members.append(member)
            manifest_member = (
                f"{prefix}/ZIP_CONTENTS.txt" if prefix else "ZIP_CONTENTS.txt"
            )
            manifest_raw = ("\n".join(members + [manifest_member]) + "\n").encode("utf-8")
            _scan_payload(manifest_raw, needles)
            write_bytes(archive, manifest_member, manifest_raw)
            members.append(manifest_member)
    with zipfile.ZipFile(temporary, "r") as completed:
        if completed.namelist() != members or completed.testzip() is not None:
            raise RuntimeError("Completed release ZIP verification failed.")
    zip_identity(temporary)
    return members, temporary


def zip_identity(path: str | Path) -> dict[str, object]:
    """Return deterministic member and content identities for a completed ZIP."""

    selected = Path(path)
    raw = selected.read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw), "r") as archive:
        validate_member_inventory(archive.namelist())
        for info in archive.infolist():
            validate_zip_metadata(info)
        if archive.testzip() is not None:
            raise RuntimeError("ZIP CRC validation failed.")
        members = archive.namelist()
        payload_members = members[:-1]
        manifest_member = members[-1] if members else ""
        if (
            payload_members != sorted(payload_members)
            or manifest_member.split("/")[-1] != "ZIP_CONTENTS.txt"
            or len(members) != len(set(members))
        ):
            raise RuntimeError("ZIP members are not unique and sorted.")
        prefix = manifest_member[:-len("ZIP_CONTENTS.txt")]
        if prefix and any(not n.startswith(prefix) for n in payload_members):
            raise RuntimeError("ZIP member escapes the declared archive root")
        for name in payload_members:
            if has_financial_capture_path(safe_parts(name, directory=name.endswith("/"))):
                raise RuntimeError("Financial capture archive member")
            if name.casefold().endswith(".json") and has_financial_capture_body(archive.read(name)):
                raise RuntimeError("Financial capture protocol document in archive")
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
