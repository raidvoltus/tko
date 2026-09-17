# -*- mode: python ; coding: utf-8 -*-
"""TKO-Core — onedir for native DLL / ML reliability."""
from pathlib import Path
import os

ROOT = Path(os.getcwd()).resolve()

a = Analysis(
    [str(ROOT / "src" / "core" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=[],
    datas=[(str(ROOT / "config" / "config.yaml"), "config")],
    hiddenimports=[
        "yaml", "numpy", "websocket",
        "src", "src.core", "src.core.autopilot", "src.core.order_state",
        "src.ipc", "src.ipc.server", "src.ipc.token", "src.ipc.protocol", "src.ipc.client",
        "src.risk.engine", "src.execution.manager", "src.execution.filters",
        "src.tokocrypto.rest", "src.tokocrypto.auth", "src.tokocrypto.websocket",
        "src.portfolio.rotation", "src.features.engine", "src.decision.plane",
        "src.control.plane", "src.observability.cycle", "src.telegram.notifier",
        "src.utils.secure_config", "src.ml.base",
    ],
    excludes=["torch", "tensorflow", "matplotlib", "IPython", "notebook"],
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="TKO-Core",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name="TKO-Core",
)
