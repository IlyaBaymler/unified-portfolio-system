from __future__ import annotations

from pathlib import Path
import hashlib
import zipfile

import pytest

from tools.build_release import build_zip, collect_release_files


def test_build_zip_excludes_runtime_and_cache_files(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("current", encoding="utf-8")
    (root / "app.py").write_text("print('ok')", encoding="utf-8")
    (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
    (root / "robot_state.json").write_text("{}", encoding="utf-8")
    (root / "runtime_bootstrap_report.json").write_text("{}", encoding="utf-8")
    (root / "runtime_bootstrap.lock").write_text("pid=1", encoding="utf-8")
    (root / "moex_robot_gui.lock").write_text("pid=1", encoding="utf-8")
    (root / "ZIP_CONTENTS.txt").write_text("stale", encoding="utf-8")
    cache = root / "__pycache__"
    cache.mkdir()
    (cache / "app.pyc").write_bytes(b"compiled")
    output = tmp_path / "release.zip"

    members = build_zip(root, output, "release-root")

    assert "release-root/README.md" in members
    assert "release-root/app.py" in members
    assert "release-root/ZIP_CONTENTS.txt" in members
    assert not any(".env" in member for member in members)
    assert not any("robot_state.json" in member for member in members)
    assert not any("runtime_bootstrap" in member for member in members)
    assert not any("moex_robot_gui.lock" in member for member in members)
    assert not any("__pycache__" in member for member in members)
    with zipfile.ZipFile(output) as archive:
        names = archive.namelist()
        assert names == members
        assert names.count("release-root/ZIP_CONTENTS.txt") == 1
        manifest = archive.read("release-root/ZIP_CONTENTS.txt").decode()
        assert "release-root/app.py" in manifest


def test_build_refuses_legacy_release_files(tmp_path: Path):
    (tmp_path / "README_HOTFIX_V3_4.txt").write_text("old", encoding="utf-8")
    with pytest.raises(RuntimeError, match="Legacy release files"):
        collect_release_files(tmp_path)


def test_build_zip_is_reproducible(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    (root / "README.md").write_text("same bytes", encoding="utf-8")
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    build_zip(root, first, "release-root")
    (root / "README.md").touch()
    build_zip(root, second, "release-root")

    assert hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(
        second.read_bytes()
    ).digest()


def test_build_zip_supports_rootless_hotfix_archive(tmp_path: Path):
    root = tmp_path / "hotfix"
    root.mkdir()
    (root / "module.py").write_text("value = 1", encoding="utf-8")
    output = tmp_path / "hotfix.zip"

    members = build_zip(root, output, "")

    assert members == ["module.py", "ZIP_CONTENTS.txt"]
    with zipfile.ZipFile(output) as archive:
        assert archive.namelist() == members


def test_build_refuses_known_secret_canary(tmp_path: Path):
    root = tmp_path / "source"
    root.mkdir()
    canary = "STABLE-SECRET-CANARY-123456789"
    (root / "accidental.txt").write_text(canary, encoding="utf-8")
    with pytest.raises(RuntimeError, match="secret canary"):
        collect_release_files(root, secret_canaries=[canary])
