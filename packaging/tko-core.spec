# -*- mode: python ; coding: utf-8 -*-
# Build: pyinstaller packaging/tko-core.spec
block_cipher = None
a = Analysis(
    ['../src/core/entry.py'],
    pathex=['..'],
    binaries=[],
    datas=[('../config/config.yaml', 'config')],
    hiddenimports=['yaml', 'sklearn', 'numpy', 'websocket'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['torch', 'tensorflow'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)
exe = EXE(
    pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [],
    name='TKO-Core',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
)
