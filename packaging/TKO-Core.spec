# -*- mode: python ; coding: utf-8 -*-
# TKO-Core — trading process only (no tkinter GUI)
from pathlib import Path

block_cipher = None
# SPECPATH may be the .spec file or its directory (PyInstaller version-dependent)
_sp = Path(SPECPATH).resolve()
SPECDIR = _sp.parent if _sp.is_file() else _sp
ROOT = SPECDIR.parent

hiddenimports = [
    "tko",
    "tko.runtime",
    "tko.runtime.entrypoint",
    "tko.runtime.bot",
    "tko.runtime.backup",
    "tko.runtime.lifecycle",
    "tko.runtime.watchdog",
    "tko.runtime.instance_lock",
    "tko.ipc",
    "tko.ipc.protocol",
    "tko.ipc.transport",
    "tko.ipc.dispatcher",
    "tko.exchange.tokocrypto",
    "tko.strategy.btc",
    "tko.risk.engine",
    "tko.execution.engine",
    "tko.reconciliation.reconciler",
    "tko.notify.telegram",
    "tko.core.redact",
    "tko.core.credentials",
    "ccxt",
    "ccxt.tokocrypto",
]

a = Analysis(
    [str(ROOT / "src" / "tko" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["torch", "tensorflow", "matplotlib", "tkinter", "PyQt5", "PySide6"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TKO-Core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=True, name="TKO-Core")
