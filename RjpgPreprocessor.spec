# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path


datas = []
icon_path = Path("rjpg_preprocessor_icon.ico")
if icon_path.exists():
    datas.append((str(icon_path), "."))
exiftool_dir = Path("tools/exiftool")
if exiftool_dir.exists():
    datas.append((str(exiftool_dir), "tools/exiftool"))


a = Analysis(
    ["run_app.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="RjpgPreprocessor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=str(icon_path) if icon_path.exists() else None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="RjpgPreprocessor",
)
