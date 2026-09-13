# -*- mode: python ; coding: utf-8 -*-
# TKO-GUI — IPC client only. NO exchange, NO credentials decryption surface.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

block_cipher = None
_sp = Path(SPECPATH).resolve()
SPECDIR = _sp.parent if _sp.is_file() else _sp
ROOT = SPECDIR.parent

datas = []
hiddenimports = [
    "tko",
    "tko.gui",
    "tko.gui.app",
    "tko.ipc",
    "tko.ipc.protocol",
    "tko.ipc.transport",
]

try:
    datas += collect_data_files("tzdata")
except Exception:
    pass
try:
    hiddenimports += ["tzdata"]
except Exception:
    pass

a = Analysis(
    [str(ROOT / "src" / "tko" / "gui" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        "torch", "tensorflow", "matplotlib",
        "ccxt", "tko.exchange", "tko.execution", "tko.risk",
        "tko.reconciliation", "tko.strategy", "tko.ml",
        "keyring",
    ],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TKO-GUI",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, name="TKO-GUI")
