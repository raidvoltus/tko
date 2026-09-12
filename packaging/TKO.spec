# -*- mode: python ; coding: utf-8 -*-
# TKO unified onedir — trading process. Credentials/state NEVER bundled.
from pathlib import Path
from PyInstaller.utils.hooks import collect_submodules

block_cipher = None
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
    "tko.runtime.paths",
    "tko.runtime.metrics",
    "tko.runtime.windows_service",
    "tko.runtime.ipc_hooks",
    "tko.ipc",
    "tko.ipc.protocol",
    "tko.ipc.transport",
    "tko.ipc.dispatcher",
    "tko.exchange.tokocrypto",
    "tko.exchange.constraints",
    "tko.exchange.order_response",
    "tko.strategy.btc",
    "tko.risk.engine",
    "tko.risk.position_store",
    "tko.risk.pnl_tracker",
    "tko.risk.market_data",
    "tko.execution.engine",
    "tko.execution.intent",
    "tko.execution.fill_journal",
    "tko.reconciliation.reconciler",
    "tko.notify.telegram",
    "tko.core.redact",
    "tko.core.credentials",
    "tko.core.config",
    "tko.core.types",
    "tko.audit.audit_log",
    "tko.ml",
    "tko.ml.filter",
    "tko.ml.features",
    "tko.ml.models",
    "tko.ml.ohlcv_store",
    "tko.ml.shadow",
    "tko.ml.shadow_engine",
    "tko.ml.paper",
    "tko.ml.champion",
    "tko.ml.challenger",
    "tko.ml.compare",
    "tko.ml.observation",
    "tko.ml.promotion",
    "tko.ml.drift",
    "tko.ml.registry",
    "tko.ml.artifacts",
    "tko.ml.rollback",
    "ccxt",
    "ccxt.tokocrypto",
    "pydantic",
    "pydantic_settings",
    "keyring",
    "keyring.backends",
    "keyring.backends.Windows",
    "keyring.backends.fail",
]
try:
    hiddenimports += collect_submodules("ccxt")
except Exception:
    pass
try:
    hiddenimports += collect_submodules("tko.ml")
except Exception:
    pass

a = Analysis(
    [str(ROOT / "src" / "tko" / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=[],
    hiddenimports=sorted(set(hiddenimports)),
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[str(SPECDIR / "runtime_hook_multiprocessing.py")],
    excludes=["torch", "tensorflow", "matplotlib", "tkinter", "PyQt5", "PySide6", "IPython", "notebook"],
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="TKO",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name="TKO")
