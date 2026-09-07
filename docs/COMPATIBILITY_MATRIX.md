# Windows 7 SP1 64-bit Compatibility Matrix

**Target**: Windows 7 SP1 64-bit  
**Python**: 3.8.10 (last official CPython with Win7 support)  
**Audit date**: 2026-09-08

| Component              | Version     | Win7 Status      | Notes                                      |
|------------------------|-------------|------------------|--------------------------------------------|
| CPython                | 3.8.10      | PASS             | Official installer                         |
| requests               | 2.27.1      | PASS             | Pure Python + urllib3                      |
| urllib3                | 1.26.15     | PASS             |                                            |
| websocket-client       | 1.3.3       | PASS             | Pure Python                                |
| numpy                  | 1.21.6      | PASS             | Official Win wheels for py3.8              |
| pandas                 | 1.3.5       | PASS             |                                            |
| scipy                  | 1.7.3       | PASS             |                                            |
| scikit-learn           | 1.0.2       | PASS             | CPU only                                   |
| joblib                 | 1.1.0       | PASS             |                                            |
| xgboost                | 1.5.2       | PASS (verify)    | CPU hist; test binary on target machine    |
| cryptography           | 3.4.8       | PASS             | Older OpenSSL binding                      |
| PyYAML                 | 6.0         | PASS             |                                            |
| tkinter                | stdlib      | PASS             | Included with official Windows installer   |
| SQLite                 | stdlib      | PASS             |                                            |
| TensorFlow             | any modern  | FAIL             | Requires Win10+                            |
| PyTorch                | any modern  | FAIL             | Requires Win10+                            |
| LightGBM               | recent      | UNVERIFIED       | Avoid on Win7 runtime                      |
| CatBoost               | recent      | UNVERIFIED       | Avoid on Win7 runtime                      |

## Runtime requirements

- No GPU required
- No Windows 10+ APIs
- No mandatory cloud services
- ML inference is optional; bot runs in pure rule / PAPER mode without model

## Training vs Runtime

- Heavy training: modern machine / Colab using `requirements-training.txt`
- Export model package (pkl + metadata + checksum)
- Copy to Win7 machine under `models/`
- Bot loads for inference only

## Install notes

1. Install Python 3.8.10 64-bit from python.org (or archived installer).
2. `python -m pip install --upgrade pip setuptools wheel`
3. `pip install -r requirements-win7.txt`
4. Run `python scripts/startup_diagnostic.py`
5. Run `python main.py`
