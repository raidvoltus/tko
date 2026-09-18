# -*- mode: python ; coding: utf-8 -*-
"""TKO-GUI — onedir IPC client only. Must bundle Tcl/Tk + _tkinter."""
from pathlib import Path
import os
import sys

ROOT = Path(os.getcwd()).resolve()

# Collect Tcl/Tk runtime so frozen EXE does not raise ModuleNotFoundError: _tkinter
datas = []
binaries = []
hiddenimports = [
    "yaml",
    "tkinter",
    "tkinter.ttk",
    "tkinter.messagebox",
    "tkinter.filedialog",
    "tkinter.font",
    "tkinter.constants",
    "_tkinter",
    "src",
    "src.gui",
    "src.gui.main_window",
    "src.gui.entry",
    "src.ipc",
    "src.ipc.client",
    "src.ipc.token",
    "src.ipc.protocol",
]

try:
    from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_dynamic_libs

    tmp_d, tmp_b, tmp_h = collect_all("tkinter")
    datas += tmp_d
    binaries += tmp_b
    hiddenimports += list(tmp_h)
except Exception:
    pass

# Explicit Tcl/Tk library trees from the build Python (Windows + some Linux builds)
def _tcl_tk_trees():
    out = []
    try:
        import tkinter

        root = tkinter.Tk()
        try:
            tcl_dir = root.tk.exprstring("$tcl_library")
            tk_dir = root.tk.exprstring("$tk_library")
        finally:
            root.destroy()
        if tcl_dir and os.path.isdir(tcl_dir):
            out.append((tcl_dir, "tcl"))
        if tk_dir and os.path.isdir(tk_dir):
            out.append((tk_dir, "tk"))
    except Exception as exc:
        print("WARNING: could not resolve tcl/tk library paths:", exc)
    # Fallback: common layout next to python.exe (Windows embed/official)
    try:
        base = Path(sys.base_prefix)
        for name, dest in (("tcl", "tcl"), ("tk", "tk")):
            for cand in (
                base / "tcl" / name,
                base / "lib" / name,
                base / "Library" / "lib" / name,
            ):
                # also versioned dirs e.g. tcl8.6
                parent = cand.parent if cand.name in ("tcl", "tk") else cand
                if parent.exists():
                    for child in parent.iterdir():
                        if child.is_dir() and child.name.lower().startswith(name):
                            out.append((str(child), child.name))
    except Exception:
        pass
    # dedupe
    seen = set()
    uniq = []
    for src, dest in out:
        key = (str(src), dest)
        if key not in seen:
            seen.add(key)
            uniq.append((str(src), dest))
    return uniq


datas += _tcl_tk_trees()

a = Analysis(
    [str(ROOT / "src" / "gui" / "entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
