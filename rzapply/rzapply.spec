# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path


spec_obj = globals().get("__spec__")
if spec_obj and getattr(spec_obj, "origin", None):
    spec_path = Path(spec_obj.origin).resolve()
else:
    spec_path = Path.cwd() / "rzapply.spec"
project_dir = spec_path.parent

a = Analysis(
    ['main.py'],
    pathex=[str(project_dir)],
    binaries=[],
    datas=[
        ('ms-playwright', 'playwright/driver/package/ms-playwright'),
        ('playwright/.auth', 'playwright/.auth'),
    ],
    hiddenimports=['uploader', 'task_loader', 'models'],
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
    name='rzapply',
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
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='rzapply',
)
