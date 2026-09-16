# Compatibility Matrix — Windows 10+ (primary)

**Target OS**: Windows 10 / Windows 11 (64-bit)  
**Also OK**: modern Linux  
**Not targeted**: Windows 7 (dropped)  

**Python**: 3.10 – 3.12 (recommended **3.11**)  
**Hardware**: CPU-only; no GPU required. Works on modest PCs (e.g. i3 / 8GB).

| Component           | Version range        | Status   | Notes                          |
|---------------------|----------------------|----------|--------------------------------|
| CPython             | 3.10 – 3.12          | PASS     | Official installers            |
| requests            | 2.31+                | PASS     |                                |
| websocket-client    | 1.6+                 | PASS     |                                |
| numpy               | 1.24 – 2.0.x         | PASS     | float32 features               |
| pandas              | 2.0+                 | PASS     |                                |
| scikit-learn        | 1.3+                 | PASS     | CPU inference                  |
| xgboost             | 1.7 – 2.0.x          | PASS     | CPU hist                       |
| cryptography        | 41+                  | PASS     |                                |
| PyYAML              | 6.0+                 | PASS     |                                |
| tkinter             | stdlib               | PASS     | GUI                            |
| SQLite              | stdlib               | PASS     |                                |

## Legacy

`requirements-win7.txt` and `scripts/install_win7.bat` are **archived** for historical reference only.  
They are **not** supported going forward.
