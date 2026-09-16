# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

ROOT = Path(os.getcwd()).resolve()

block_cipher = None
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
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="TKO-GUI",
    debug=False,
    strip=False,
    upx=False,
    console=False,
)
