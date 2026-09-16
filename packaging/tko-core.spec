# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
import os

# build_windows_release.ps1 sets cwd to repo root before invoking PyInstaller
ROOT = Path(os.getcwd()).resolve()

block_cipher = None
a = Analysis(
    [str(ROOT / "src" / "core" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "config" / "config.yaml"), "config")],
    hiddenimports=[
        "yaml", "numpy", "websocket",
        "src", "src.core", "src.core.autopilot",
        "src.ipc", "src.ipc.server", "src.ipc.token", "src.ipc.protocol",
        "src.risk.engine", "src.execution.manager", "src.tokocrypto.rest",
        "src.tokocrypto.auth", "src.portfolio.rotation", "src.features.engine",
        "src.decision.plane", "src.control.plane", "src.observability.cycle",
        "src.telegram.notifier", "src.utils.secure_config",
    ],
    excludes=["torch", "tensorflow", "matplotlib", "IPython"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name="TKO-Core",
    debug=False,
    strip=False,
    upx=False,
    console=True,
)
