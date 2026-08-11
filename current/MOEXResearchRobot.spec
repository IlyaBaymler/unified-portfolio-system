# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path

ROOT = Path(SPECPATH).resolve()

datas = [
    (str(ROOT / ".env.example"), "."),
    (str(ROOT / "build_manifest.json"), "."),
    (str(ROOT / "README.md"), "."),
    (str(ROOT / "START_HERE_WINDOWS.md"), "."),
    (str(ROOT / "V3_7_ALPHA3_RECOVERY_RUNBOOK_RU.md"), "."),
]

hiddenimports = [
    "tkinter",
    "tkinter.ttk",
    "matplotlib.backends.backend_tkagg",
    "certifi",
    "truststore",
]

a = Analysis(
    [str(ROOT / "desktop_gui.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["pytest", "ruff"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="MOEXResearchRobot",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="MOEXResearchRobot",
)
