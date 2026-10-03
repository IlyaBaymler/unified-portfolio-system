"""STQ-R2-IR-001/002: byte-bound scanning, read custody and device stems."""
from __future__ import annotations

import os
import zipfile
from pathlib import Path

import pytest

from tools import build_release as build
from tools import release_payload as payload
from tools.release_safety import safe_parts

CANARY = "SYNTHETIC_R3_EXPLICIT_CANARY_NEVER_DISTRIBUTE"


@pytest.mark.parametrize("kind", ["overwrite", "symlink", "hardlink", "parent_link"])
@pytest.mark.parametrize("existing", [False, True])
def test_late_source_substitution_never_commits_archive(tmp_path, monkeypatch, kind, existing):
    root = tmp_path / "source"; root.mkdir()
    parent = root / "docs"; parent.mkdir()
    notes = parent / "notes.txt"; notes.write_text("safe")
    foreign = tmp_path / "foreign"; foreign.mkdir()
    secret = foreign / "notes.txt"; secret.write_text(CANARY)
    output = tmp_path / "out.zip"
    prior = b"previous release must remain intact"
    if existing: output.write_bytes(prior)
    scan = build.scan_release_files_for_canaries
    hits = []

    def after_scan(files, *, canaries=()):
        scan(files, canaries=canaries)
        hits.append(True)
        if kind == "overwrite": notes.write_text(CANARY)
        elif kind == "parent_link":
            notes.unlink(); parent.rmdir(); parent.symlink_to(foreign, target_is_directory=True)
        else:
            notes.unlink()
            if kind == "symlink": notes.symlink_to(secret)
            else: os.link(secret, notes)

    monkeypatch.setattr(build, "scan_release_files_for_canaries", after_scan)
    with pytest.raises(RuntimeError):
        build.build_zip(root, output, "release", secret_canaries=[CANARY])
    assert hits == [True]
    assert output.read_bytes() == prior if existing else not output.exists()
    assert not list(tmp_path.glob("out.zip.*.tmp"))
    assert secret.read_text() == CANARY


def test_one_shot_canary_iterable_is_retained_after_early_scan(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    notes = root / "notes.txt"; notes.write_text("safe")
    scan = build.scan_release_files_for_canaries
    yielded = []

    def canaries():
        yielded.append(True)
        yield CANARY

    def late(files, *, canaries=()):
        scan(files, canaries=canaries)
        notes.write_text(CANARY)

    monkeypatch.setattr(build, "scan_release_files_for_canaries", late)
    output = tmp_path / "out.zip"
    with pytest.raises(RuntimeError, match="canary"):
        build.build_zip(root, output, "release", secret_canaries=canaries())
    assert yielded == [True] and not output.exists()


def test_canary_object_converted_once(tmp_path):
    root = tmp_path / "source"; root.mkdir(); (root / "notes.txt").write_text(CANARY)
    class OneValue:
        calls = 0
        def __str__(self):
            self.calls += 1
            return CANARY if self.calls == 1 else "DIFFERENT_VALUE"
    marker = OneValue()
    with pytest.raises(RuntimeError, match="canary"):
        build.build_zip(root, tmp_path / "out.zip", "release", secret_canaries=[marker])
    assert marker.calls == 1


def test_buffer_written_after_source_changes_is_the_scanned_buffer(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    notes = root / "notes.txt"; notes.write_text("safe original")
    real = build._scan_payload; seen = []
    def after_buffer_scan(raw, needles):
        real(raw, needles)
        if raw == b"safe original":
            seen.append(raw)
            notes.write_text(CANARY)
    monkeypatch.setattr(build, "_scan_payload", after_buffer_scan)
    output = tmp_path / "out.zip"
    build.build_zip(root, output, "release", secret_canaries=[CANARY])
    with zipfile.ZipFile(output) as z:
        assert z.read("release/notes.txt") == b"safe original"
        assert all(CANARY.encode() not in z.read(n) for n in z.namelist())
    assert seen == [b"safe original"] and notes.read_text() == CANARY


def test_mutation_during_single_handle_read_fails_closed(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    notes = root / "notes.txt"; notes.write_text("safe original")
    real = payload._read_buffer
    def after_read(stream, size):
        raw = real(stream, size)
        notes.write_text("another value of a different length")
        return raw
    monkeypatch.setattr(payload, "_read_buffer", after_read)
    output = tmp_path / "out.zip"
    with pytest.raises(RuntimeError): build.build_zip(root, output, "release")
    assert not output.exists() and not list(tmp_path.glob("out.zip.*.tmp"))


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor-relative race probe")
def test_file_open_replacement_uses_real_descriptor_identity(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    notes = root / "notes.txt"; notes.write_text("safe")
    donor = tmp_path / "donor"; donor.write_text("foreign otherwise-safe bytes")
    with payload.ReleaseSource(root) as source:
        real = os.open; hits = []
        def replaced(path, flags, *args, **kwargs):
            if path == "notes.txt":
                os.replace(donor, notes); hits.append(True)
            return real(path, flags, *args, **kwargs)
        monkeypatch.setattr(os, "open", replaced)
        with pytest.raises(RuntimeError, match="changed"):
            source.read(notes)
        assert hits == [True]


@pytest.mark.skipif(os.name != "posix", reason="POSIX anchored parent race probe")
def test_ancestor_swap_cannot_redirect_checked_read(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    docs = root / "docs"; docs.mkdir(); notes = docs / "notes.txt"; notes.write_text("safe")
    foreign = tmp_path / "foreign"; foreign.mkdir(); (foreign / "notes.txt").write_text(CANARY)
    real = os.open; hits = []
    with payload.ReleaseSource(root) as source:
        def replaced(path, flags, *args, **kwargs):
            if path == "notes.txt":
                os.rename(docs, root / "displaced")
                docs.symlink_to(foreign, target_is_directory=True); hits.append(True)
            return real(path, flags, *args, **kwargs)
        monkeypatch.setattr(os, "open", replaced)
        with pytest.raises(RuntimeError): source.read(notes)
        assert hits == [True]


@pytest.mark.skipif(os.name != "posix", reason="POSIX descriptor inventory")
def test_release_read_descriptors_closed_on_success_and_failure(tmp_path):
    root = tmp_path / "source"; root.mkdir(); p = root / "file"; p.write_text("safe")
    inventory = Path("/proc/self/fd")
    if not inventory.is_dir(): pytest.skip("descriptor inventory unavailable")
    before = len(list(inventory.iterdir()))
    for _ in range(10):
        with payload.ReleaseSource(root) as source:
            assert source.read(p) == b"safe"
            with pytest.raises(RuntimeError): source.read(tmp_path / "outside")
        with pytest.raises(RuntimeError): source.read(p)
    assert len(list(inventory.iterdir())) == before


@pytest.mark.parametrize("name", ["CON .txt", "AUX .log", "NUL .txt", "COM1 .txt", "LPT9 .log",
                                  "con  .TXT", "ＣＯＮ .txt", "COM¹ .txt", "safe/NUL .txt", "PRN .dat"])
def test_device_stem_spaces_rejected_without_sanitizing(name):
    with pytest.raises(RuntimeError): safe_parts(name)


@pytest.mark.parametrize("name", ["CON .txt", "AUX .log", "NUL .txt", "COM1 .txt", "LPT9 .log"])
def test_independent_raw_zip_verifier_rejects_device_stem_spaces(tmp_path, name):
    target = tmp_path / "hostile.zip"
    names = [name, "ZIP_CONTENTS.txt"]
    with zipfile.ZipFile(target, "w") as z:
        for member in names:
            info = zipfile.ZipInfo(member, (2020, 1, 1, 0, 0, 0))
            info.create_system = 3; info.external_attr = 0o644 << 16
            info.compress_type = zipfile.ZIP_DEFLATED
            raw = ("\n".join(names) + "\n").encode() if member == "ZIP_CONTENTS.txt" else b"synthetic"
            z.writestr(info, raw)
    with pytest.raises(RuntimeError): build.zip_identity(target)


@pytest.mark.parametrize("name", ["Quarterly notes.txt", "Report .txt", "CONgress.txt", "auxiliary.txt"])
def test_non_device_spaces_remain_valid(name):
    assert safe_parts(name) == (name,)


@pytest.mark.parametrize("stage", ["payload_write", "completed_verification"])
def test_write_failure_preserves_existing_destination(tmp_path, monkeypatch, stage):
    root = tmp_path / "source"; root.mkdir(); (root / "notes").write_text("safe")
    out = tmp_path / "out.zip"; out.write_bytes(b"previous")
    def fail(*args, **kwargs): raise RuntimeError("synthetic write/verify failure")
    if stage == "payload_write": monkeypatch.setattr(zipfile.ZipFile, "writestr", fail)
    else: monkeypatch.setattr(build, "zip_identity", fail)
    with pytest.raises(RuntimeError): build.build_zip(root, out, "release")
    assert out.read_bytes() == b"previous" and not list(tmp_path.glob("out.zip.*.tmp"))


@pytest.mark.parametrize("directory", [False, True])
def test_windows_payload_lease_requests_share_checked_read_rights(tmp_path, directory):
    from types import SimpleNamespace
    path = tmp_path / "entry"
    if directory: path.mkdir()
    else: path.write_text("safe")
    calls = []; closed = []
    source = object.__new__(payload._WindowsSource)
    source._ctypes = SimpleNamespace(c_void_p=lambda value: SimpleNamespace(value=-1))
    source._api = SimpleNamespace(CreateFileW=lambda *args: calls.append(args) or 42,
                                  CloseHandle=lambda handle: closed.append(handle))
    source._final_path = lambda handle: os.path.normcase(os.path.normpath(str(path)))
    handle, _ = source._open(path, directory)
    assert handle == 42 and closed == []
    args = calls[0]
    assert args[1] == 0x81 and args[2] == 0x1
    assert args[4] == 3 and args[5] & 0x00200000
    assert bool(args[5] & 0x02000000) == directory
    source._final_path = lambda handle: "wrong-path"
    with pytest.raises(RuntimeError): source._open(path, directory)
    assert closed == [42]


@pytest.mark.skipif(os.name != "nt", reason="Native Windows packaging read/share boundary")
@pytest.mark.parametrize("operation", ["overwrite", "rename_file", "rename_parent"])
def test_native_windows_payload_lease_prevents_mutation_during_read(tmp_path, monkeypatch, operation):
    root = tmp_path / "source"; root.mkdir()
    parent = root / "docs"; parent.mkdir()
    notes = parent / "notes.txt"; notes.write_text("safe")
    original = payload._read_buffer; blocked = []
    def read_with_attempt(stream, size):
        try:
            if operation == "overwrite": notes.write_text("foreign")
            elif operation == "rename_file": os.rename(notes, parent / "renamed.txt")
            else: os.rename(parent, root / "renamed")
        except OSError as exc:
            assert exc.winerror in {5, 32, 33}
            blocked.append(True)
        else: pytest.fail("Native Windows payload read lease did not block mutation")
        return original(stream, size)
    monkeypatch.setattr(payload, "_read_buffer", read_with_attempt)
    with payload.ReleaseSource(root) as source:
        assert source.read(notes) == b"safe"
    assert blocked == [True]
    notes.write_text("after close")
    assert notes.read_text() == "after close"
