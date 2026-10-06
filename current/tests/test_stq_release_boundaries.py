"""STQ-C1D-002/003: privacy defaults and an independently hostile ZIP verifier."""
from __future__ import annotations

import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from tools import build_release as build
from tools.release_safety import FINANCIAL_CAPTURE_DIRECTORIES
from tools.verify_standalone_layout import REQUIRED_DIRS, verify_layout


@pytest.mark.parametrize("name", sorted(FINANCIAL_CAPTURE_DIRECTORIES))
@pytest.mark.parametrize("variant", ["plain", "upper", "nested"])
def test_every_current_capture_namespace_is_private(tmp_path, name, variant):
    root = tmp_path / "source"; root.mkdir()
    (root / "README.md").write_text("source")
    relative = ("nested/app/" if variant == "nested" else "") + (name.upper() if variant == "upper" else name)
    capture = root / relative / "plan.json"
    capture.parent.mkdir(parents=True); capture.write_text('{"only_synthetic":"private"}')
    archive = tmp_path / "result.zip"
    members = build.build_zip(root, archive, "release")
    assert members == ["release/README.md", "release/ZIP_CONTENTS.txt"]
    assert build.zip_identity(archive)["members"] == members


@pytest.mark.parametrize("domain", ["CL7_EXACT_CASH_COMPONENTS_V1", "V4_EXPLICIT_SOURCE_SELECTION_V1",
                                    "CL2_SAME_ID_FEE_CAPTURE_V1", "VERSIONED_ORDER_ADMISSION_V1"])
def test_renamed_financial_envelope_is_not_source(tmp_path, domain):
    root = tmp_path / "source"; root.mkdir()
    (root / "unassuming.json").write_text(json.dumps({"payload":{"domain":domain,"private":"synthetic"},"hmac_sha256":"0"*64}))
    (root / "fixture.json").write_text(json.dumps({"domain":"v3.10-synthetic-vectors","vectors":[]}))
    members = build.build_zip(root, tmp_path / "out.zip", "")
    assert members == ["fixture.json", "ZIP_CONTENTS.txt"]


BAD_NAMES = ["../escape", "a/../../b", "/absolute", "C:/escape", "C:relative", "\\\\host\\share",
             "a\\b", "a//b", "./a", "a/", "a/../b", "NUL.txt", "COM1", "lpt9.log", "COM¹.txt",
             "a.", "a ", " name", "a:stream", "a\x00b", "a\nb", "a\u202eb", "cafe\u0301",
             "a/CON/name", "ＣＯＮ", "a/／b", "LONGFI~1.TXT"]


@pytest.mark.parametrize("name", BAD_NAMES)
def test_builder_rejects_unsafe_root_before_any_output(tmp_path, name):
    source = tmp_path / "source"; source.mkdir(); (source / "ok.txt").write_text("safe")
    output = tmp_path / "new" / "out.zip"
    with pytest.raises(RuntimeError):
        build.build_zip(source, output, name)
    assert not output.parent.exists()


def _hostile_zip(path, members, *, link=False):
    # Handcrafted independent writer, never build_zip. Dangerous ZIPs are read
    # only by the verifier and are NEVER extracted.
    manifest = "ZIP_CONTENTS.txt"
    names = sorted(members) + [manifest]
    with zipfile.ZipFile(path, "w") as archive:
        for name in names:
            info = zipfile.ZipInfo(name, (2020,1,1,0,0,0))
            info.create_system = 3; info.compress_type = zipfile.ZIP_DEFLATED
            directory = name.endswith("/")
            info.external_attr = (((stat.S_IFLNK | 0o644) if link and name != manifest else
                                    0o40755 if directory else 0o644) << 16)
            data = ("\n".join(names)+"\n").encode() if name == manifest else b"" if directory else b"synthetic"
            archive.writestr(info, data)


@pytest.mark.parametrize("names", [["../escape"], ["C:/escape"], ["a\\b"], ["NUL"], ["bad."],
    ["Alpha.txt", "alpha.txt"], ["Dir/a", "dir/b"], ["a", "a/b"], ["a", "a/"],
    ["ｅ.txt", "e.txt"], ["a", "a"], ["x\u202ey"]])
def test_verifier_independently_rejects_hostile_members(tmp_path, names):
    path = tmp_path / "hostile.zip"; _hostile_zip(path, names)
    with pytest.raises(RuntimeError):build.zip_identity(path)


def test_verifier_rejects_symlink_typed_member(tmp_path):
    path=tmp_path/'link.zip'; _hostile_zip(path,['link'],link=True)
    with pytest.raises(RuntimeError, match='Symlink'): build.zip_identity(path)


@pytest.mark.skipif(os.name == 'nt', reason='case-distinct files require a case-sensitive filesystem')
@pytest.mark.parametrize('names', [('A.txt','a.txt'), ('Dir/a','dir/b'), ('ｅ.txt','e.txt')])
def test_builder_rejects_source_alias_collision(tmp_path,names):
    root=tmp_path/'source';root.mkdir()
    for name in names:
        path=root/name;path.parent.mkdir(exist_ok=True);path.write_text('synthetic')
    output=tmp_path/'out.zip'
    with pytest.raises(RuntimeError, match='collision|colliding|alias'):
        build.build_zip(root,output,'release')
    assert not output.exists()


def test_safe_unicode_spaces_rootless_and_portable_empty_dirs_survive(tmp_path):
    source=tmp_path/'source';source.mkdir()
    (source/'Пример документа.md').write_text('test')
    for name in ['runtime','backups','reports','logs','support']:(source/name).mkdir()
    a,b=tmp_path/'a.zip',tmp_path/'b.zip'
    for out in [a,b]:build.build_zip(source,out,'',required_empty_directories=['runtime','backups','reports','logs','support'])
    assert build.verify_deterministic_pair(a,b)['status']=='PASS'
    assert a.read_bytes()==b.read_bytes()


def _portable(root):
    for name in REQUIRED_DIRS:(root/name).mkdir(parents=True)
    (root/'MOEX Research Robot.bat').write_text('synthetic')
    (root/'app/MOEXResearchRobot.exe').write_bytes(b'placeholder, never run')
    (root/'app/build_manifest.json').write_text(json.dumps({'software_version':'0.3.10',
        'release_channel':'stable','sandbox_only':True,'real_account_execution':False}))


@pytest.mark.parametrize('namespace',sorted(FINANCIAL_CAPTURE_DIRECTORIES))
def test_layout_refuses_capture_even_in_app_subdirectory(tmp_path,namespace):
    root=tmp_path/'portable';_portable(root)
    path=root/'app'/namespace/'payload.json';path.parent.mkdir();path.write_text('{"private":"synthetic"}')
    assert any(namespace in e for e in verify_layout(root,expected_version='0.3.10'))


def test_layout_refuses_renamed_protocol_document(tmp_path):
    root=tmp_path/'portable';_portable(root)
    (root/'app/copy.json').write_text(json.dumps({'payload':{'domain':'V4_EXPLICIT_SOURCE_SELECTION_V1'}}))
    assert any('financial protocol' in e for e in verify_layout(root,expected_version='0.3.10'))
