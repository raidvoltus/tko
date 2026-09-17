# -*- mode: python ; coding: utf-8 -*-
"""TKO-GUI — onedir IPC client only."""
from pathlib import Path
import os

ROOT = Path(os.getcwd()).resolve()

a = Analysis(
    [str(ROOT / "src" / "gui" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[],
    hiddenimports=[
        "yaml", "tkinter",
        "src", "src.gui", "src.gui.main_window",
        "src.ipc", "src.ipc.client", "src.ipc.token", "src.ipc.protocol",
    ],
    excludes=["torch", "tensorflow", "sklearn", "xgboost", "matplotlib"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TKO-GUI",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="TKO-GUI",
)
