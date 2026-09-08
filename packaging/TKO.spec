# -*- mode: python ; coding: utf-8 -*-
# PyInstaller one-folder for TKO (Windows 10+)

from pathlib import Path

block_cipher = None
SPECDIR = Path(SPECPATH).resolve().parent
ROOT = SPECDIR.parent

hiddenimports = [
    "tko",
    "tko.runtime",
    "tko.runtime.entrypoint",
    "tko.runtime.bot",
    "tko.exchange.tokocrypto",
    "tko.strategy.btc",
    "tko.risk.engine",
    "tko.execution.engine",
    "tko.notify.telegram",
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
    excludes=["torch", "tensorflow", "matplotlib", "tkinter"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TKO",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="TKO",
)
