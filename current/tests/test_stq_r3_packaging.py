"""STQ-R2-IR-001/002: byte-bound scanning, read custody and device stems."""
from __future__ import annotations

import errno
import json
import os
import subprocess
import sys
import time
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest

from tools import build_release as build
from tools import release_payload as payload
from tools import release_safety as safety
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

    def after_scan(files, *, canaries=(), source=None):
        scan(files, canaries=canaries, source=source)
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

    def late(files, *, canaries=(), source=None):
        scan(files, canaries=canaries, source=source)
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
    metadata = payload._WindowsInfo((7, 11), 0x10 if directory else 0x20, 1, 4, 1, 2, 3)
    source._snapshot = lambda path, directory: metadata
    source._metadata = lambda handle, directory: metadata
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
            assert (getattr(exc, "winerror", None) in {5, 32, 33}
                    or (operation == "overwrite" and isinstance(exc, PermissionError)
                        and exc.errno == errno.EACCES and getattr(exc, "winerror", None) is None))
            assert notes.read_bytes() == b"safe"
            blocked.append(True)
        else: pytest.fail("Native Windows payload read lease did not block mutation")
        return original(stream, size)
    monkeypatch.setattr(payload, "_read_buffer", read_with_attempt)
    with payload.ReleaseSource(root) as source:
        assert source.read(notes) == b"safe"
        assert notes.read_bytes() == b"safe"
    assert blocked == [True]
    notes.write_text("after close")
    assert notes.read_text() == "after close"


def test_r4_advisory_content_decisions_use_checked_reads(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir()
    private = root / "renamed.json"
    private.write_bytes(b'{"payload":{"domain":"CL7_EXACT_CASH_COMPONENTS_V1"}}')
    safe = root / "safe.txt"; safe.write_bytes(CANARY.encode())
    original = payload.ReleaseSource.read
    reads = []

    def checked(source, path):
        raw = original(source, path)
        reads.append(path)
        return raw

    def unchecked(path):
        raise AssertionError("Advisory content decisions must not call Path.read_bytes")

    monkeypatch.setattr(payload.ReleaseSource, "read", checked)
    monkeypatch.setattr(Path, "read_bytes", unchecked)
    assert build.private_capture_file(private)
    assert build.collect_release_files(root) == [safe]
    with pytest.raises(RuntimeError, match="canary"):
        build.scan_release_files_for_canaries([safe], canaries=[CANARY])
    assert reads.count(private) == 2 and reads.count(safe) == 1


def test_r4_fdopen_constructor_failure_closes_fd_and_removes_temp(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir(); (root / "safe").write_bytes(b"safe")
    output = tmp_path / "output.zip"; output.write_bytes(b"previous")
    original_mkstemp, original_fdopen = build.tempfile.mkstemp, os.fdopen
    captured = []

    def make_temp(*args, **kwargs):
        result = original_mkstemp(*args, **kwargs)
        captured.append(result)
        return result

    def fail_stream(fd, mode, *args, **kwargs):
        if mode == "w+b":
            raise OSError("Synthetic stream constructor failure")
        return original_fdopen(fd, mode, *args, **kwargs)

    monkeypatch.setattr(build.tempfile, "mkstemp", make_temp)
    monkeypatch.setattr(os, "fdopen", fail_stream)
    with pytest.raises(RuntimeError, match="filesystem boundary"):
        build.build_zip(root, output, "")
    assert len(captured) == 1
    with pytest.raises(OSError) as error:
        os.fstat(captured[0][0])
    assert error.value.errno == errno.EBADF
    assert not Path(captured[0][1]).exists()
    assert output.read_bytes() == b"previous"
    assert not list(tmp_path.glob("output.zip.*.tmp"))


def test_r4_real_audit_policy_failure_closes_fd_and_removes_temp(tmp_path):
    # Irreversible process-local audit hooks stay in a short-lived child. This
    # exercises real FileIO construction, without mocking fdopen or system policy.
    script = r'''
import errno, json, os, sys
from pathlib import Path
from tools import build_release as build
root = Path(sys.argv[1]); root.mkdir(); (root / "safe.txt").write_bytes(b"safe")
out = root.parent / "output.zip"; out.write_bytes(b"previous")
captured = []
def policy(event, args):
    if event == "open" and isinstance(args[0], int) and str(args[1]).startswith("w"):
        captured.append(args[0])
        raise OSError("Synthetic process-local audit-policy denial")
sys.addaudithook(policy)
try:
    build.build_zip(root, out, "")
except RuntimeError:
    rejected = True
else:
    rejected = False
closed = []
for fd in captured:
    try:
        os.fstat(fd)
    except OSError as error:
        closed.append(error.errno == errno.EBADF)
    else:
        closed.append(False)
result = dict(rejected=rejected, captured=len(captured), closed=closed,
              temporary_files=[p.name for p in out.parent.glob("output.zip.*.tmp")],
              destination_preserved=out.read_bytes() == b"previous",
              builtin_fdopen_mocked=False)
print(json.dumps(result))
'''
    child = subprocess.run([sys.executable, "-B", "-c", script, str(tmp_path / "source")],
                           capture_output=True, text=True, timeout=30, check=False)
    assert child.returncode == 0, child.stderr
    result = json.loads(child.stdout)
    assert result == {"rejected": True, "captured": 1, "closed": [True],
                      "temporary_files": [], "destination_preserved": True,
                      "builtin_fdopen_mocked": False}


def test_r4_verified_output_promoted_only_after_all_source_leases_close(tmp_path, monkeypatch):
    root = tmp_path / "source"; root.mkdir(); (root / "safe.txt").write_bytes(b"safe")
    output = tmp_path / "trusted" / "output.zip"
    output.parent.mkdir(); output.write_bytes(b"previous")
    original_source, original_replace = build.ReleaseSource, os.replace
    sources, promotions = [], []

    class TrackedSource(original_source):
        def __init__(self, selected):
            super().__init__(selected)
            sources.append(self)

    def promote(temporary, destination):
        assert sources and all(source._closed for source in sources)
        assert build.zip_identity(temporary)["members"] == ["safe.txt", "ZIP_CONTENTS.txt"]
        promotions.append((temporary, destination))
        return original_replace(temporary, destination)

    monkeypatch.setattr(build, "ReleaseSource", TrackedSource)
    monkeypatch.setattr(os, "replace", promote)
    assert build.build_zip(root, output, "") == ["safe.txt", "ZIP_CONTENTS.txt"]
    assert len(promotions) == 1
    with zipfile.ZipFile(output) as archive:
        assert archive.read("safe.txt") == b"safe"
    assert not list(output.parent.glob("output.zip.*.tmp"))


@pytest.mark.parametrize("name", ["BUILD_RELEASE.bat", "launcher.cmd", "placeholder.exe", "README.md"])
@pytest.mark.parametrize("rewritten", [False, True])
def test_r4_ordinary_payload_names_and_completed_prelease_rewrite(tmp_path, monkeypatch, name, rewritten):
    root = tmp_path / "source"; root.mkdir()
    path = root / name; path.write_bytes(b"before")
    expected = b"before"
    if rewritten:
        time.sleep(0.025)
        expected = b"legitimate rewrite completed before lease"
        path.write_bytes(expected)
    if os.name == "nt":
        def incompatible(value):
            raise AssertionError("Native Windows must not compare CPython stat fingerprints")
        monkeypatch.setattr(payload, "_fingerprint", incompatible)
    with payload.ReleaseSource(root) as source:
        assert source.read(path) == expected
    assert path.read_bytes() == expected


@pytest.mark.parametrize("field", ["identity", "attributes", "links", "size", "creation", "write", "change"])
def test_r4_windows_native_metadata_change_before_lease_fails_closed(tmp_path, field):
    from types import SimpleNamespace
    path = tmp_path / "safe.txt"
    initial = payload._WindowsInfo((7, 11), 0x20, 1, 4, 1, 2, 3)
    changed = replace(initial, **{field: (7, 12) if field == "identity" else getattr(initial, field) + 1})
    source = object.__new__(payload._WindowsSource)
    source._ctypes = SimpleNamespace(c_void_p=lambda value: SimpleNamespace(value=-1))
    closed = []
    source._api = SimpleNamespace(CreateFileW=lambda *args: 42,
                                  CloseHandle=lambda handle: closed.append(handle))
    source._snapshot = lambda path, directory: initial
    source._metadata = lambda handle, directory: changed
    source._final_path = lambda handle: os.path.normcase(os.path.normpath(str(path)))
    with pytest.raises(RuntimeError, match="changed"):
        source._open(path, False)
    assert closed == [42]


def test_r4_unavailable_native_metadata_never_falls_back_to_path_reads():
    import ctypes
    from types import SimpleNamespace
    source = object.__new__(payload._WindowsSource)
    source._ctypes = ctypes
    source._basic_info = source._standard_info = source._id_info = ctypes.c_int
    source._api = SimpleNamespace(GetFileType=lambda handle: 1,
                                  GetFileInformationByHandleEx=lambda *args: 0)
    with pytest.raises(RuntimeError, match="metadata is unavailable"):
        source._metadata(42, False)


def test_r4q_c1_privacy_root_aba_never_reads_foreign_bytes(tmp_path, monkeypatch):
    root = tmp_path / "source"
    root.mkdir()
    safe = root / "safe.json"
    original_raw = b'{"description":"safe original synthetic payload"}'
    foreign_raw = b'{"payload":{"domain":"CL7_EXACT_CASH_COMPONENTS_V1"}}'
    safe.write_bytes(original_raw)
    donor = tmp_path / "foreign"
    donor.mkdir()
    (donor / safe.name).write_bytes(foreign_raw)
    displaced = tmp_path / "displaced"
    original_identity = (root.stat().st_dev, root.stat().st_ino)
    donor_identity = (donor.stat().st_dev, donor.stat().st_ino)
    assert original_identity != donor_identity
    assert not build.has_financial_capture_body(original_raw)
    assert build.has_financial_capture_body(foreign_raw)
    output = tmp_path / "output.zip"
    prior = b"previous release must remain intact"
    output.write_bytes(prior)
    owners, reads, decisions, attacks = [], [], [], []
    source_type = payload.ReleaseSource
    private, read_buffer = build.private_capture_file, payload._read_buffer

    class TrackedSource(source_type):
        def __init__(self, selected):
            super().__init__(selected)
            owners.append(self)

    def observe_buffer(stream, size):
        raw = read_buffer(stream, size)
        reads.append(raw)
        return raw

    def during_privacy(path, *, source=None):
        assert source is owners[0] and source.root == root
        assert path == safe
        try:
            os.rename(root, displaced)
        except OSError as error:
            # Windows ancestry leases block the real rename. POSIX must reach
            # the original ordinary-directory ABA, not a simulated replacement.
            assert os.name == "nt" and error.winerror in {5, 32, 33}
            attacks.append("native_windows_root_rename_blocked")
            result = private(path, source=source)
        else:
            try:
                os.rename(donor, root)
                attacks.append("native_posix_root_aba")
                assert os.name == "posix"
                assert (root.stat().st_dev, root.stat().st_ino) == donor_identity
                result = private(path, source=source)
            finally:
                if root.exists():
                    os.rename(root, donor)
                os.rename(displaced, root)
        decisions.append(result)
        return result

    monkeypatch.setattr(build, "ReleaseSource", TrackedSource)
    monkeypatch.setattr(safety, "ReleaseSource", TrackedSource)
    monkeypatch.setattr(payload, "_read_buffer", observe_buffer)
    monkeypatch.setattr(build, "private_capture_file", during_privacy)
    try:
        members = build.build_zip(root, output, "")
    except RuntimeError:
        outcome = "fail_closed_before_promotion"
        assert output.read_bytes() == prior
    else:
        outcome = "original_owner_bytes_preserved"
        assert "safe.json" in members
        with zipfile.ZipFile(output) as archive:
            assert archive.read("safe.json") == original_raw
    assert len(owners) == 1 and owners[0]._closed
    assert foreign_raw not in reads and True not in decisions
    assert attacks == (["native_posix_root_aba"] if os.name == "posix"
                       else ["native_windows_root_rename_blocked"])
    assert (root.stat().st_dev, root.stat().st_ino) == original_identity
    assert safe.read_bytes() == original_raw
    assert (donor / safe.name).read_bytes() == foreign_raw
    assert not list(tmp_path.glob("output.zip.*.tmp"))
    print("C1_ROOT_ABA=" + json.dumps({"outcome": outcome, "attack": attacks[0],
          "foreign_read_count": reads.count(foreign_raw), "privacy_decisions": decisions,
          "owner_count": len(owners), "original_root_restored": True}))


@pytest.mark.parametrize("contains_canary", [False, True])
def test_r4q_c1_build_content_custody_uses_one_original_owner(
    tmp_path, monkeypatch, contains_canary,
):
    root = tmp_path / "source"
    root.mkdir()
    safe_json = root / "safe.json"
    safe_raw = b'{"description":"safe original synthetic payload"}'
    safe_json.write_bytes(safe_raw)
    notes = root / "notes.txt"
    notes_raw = CANARY.encode() if contains_canary else b"ordinary safe text"
    notes.write_bytes(notes_raw)
    output = tmp_path / "output.zip"
    owners, reads, privacy_owners, canary_owners, final_buffers = [], [], [], [], []
    source_type = payload.ReleaseSource
    private = build.private_capture_file
    scanner = build.scan_release_files_for_canaries
    final_scan, path_read = build._scan_payload, Path.read_bytes

    class TrackedSource(source_type):
        def __init__(self, selected):
            super().__init__(selected)
            owners.append(self)

        def read(self, path):
            raw = super().read(path)
            reads.append((self, path, raw))
            return raw

    def privacy(path, *, source=None):
        privacy_owners.append(source)
        return private(path, source=source)

    def early_canary(files, *, canaries=(), source=None):
        canary_owners.append(source)
        return scanner(files, canaries=canaries, source=source)

    def immutable_scan(raw, needles):
        final_scan(raw, needles)
        final_buffers.append(raw)

    def no_unchecked_source_read(path):
        assert not path.absolute().is_relative_to(root), "Unchecked release-source read"
        return path_read(path)  # Completed immutable ZIP verification only.

    monkeypatch.setattr(build, "ReleaseSource", TrackedSource)
    monkeypatch.setattr(safety, "ReleaseSource", TrackedSource)
    monkeypatch.setattr(build, "private_capture_file", privacy)
    monkeypatch.setattr(build, "scan_release_files_for_canaries", early_canary)
    monkeypatch.setattr(build, "_scan_payload", immutable_scan)
    monkeypatch.setattr(Path, "read_bytes", no_unchecked_source_read)
    if contains_canary:
        with pytest.raises(RuntimeError, match="canary"):
            build.build_zip(root, output, "", secret_canaries=[CANARY])
        assert not output.exists() and final_buffers == []
    else:
        assert build.build_zip(root, output, "", secret_canaries=[CANARY]) == [
            "notes.txt", "safe.json", "ZIP_CONTENTS.txt",
        ]
        assert safe_raw in final_buffers and notes_raw in final_buffers
        with zipfile.ZipFile(output) as archive:
            assert archive.read("safe.json") == safe_raw
            assert archive.read("notes.txt") == notes_raw
    assert len(owners) == 1 and owners[0]._closed
    assert privacy_owners == [owners[0], owners[0]]
    assert canary_owners == [owners[0]]
    assert reads and all(owner is owners[0] for owner, _, _ in reads)
    assert not list(tmp_path.glob("output.zip.*.tmp"))
    print("C1_OWNER_CUSTODY=" + json.dumps({"owner_count": len(owners),
          "privacy_original_owner": True, "canary_original_owner": True,
          "all_release_content_reads_original_owner": True, "content_read_count": len(reads),
          "explicit_canary_enforced": contains_canary,
          "final_immutable_payload_scan_retained": not contains_canary}))
