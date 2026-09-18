"""CONTROL PLANE - file-based, fail-closed, Win7 compatible."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp_")
    try:
        os.write(fd, data)
        os.close(fd)
        fd = -1
        os.replace(tmp, str(path))
    finally:
        if fd >= 0:
            try:
                os.close(fd)
            except Exception:
                pass
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except Exception:
                pass


class ControlPlane:
    def __init__(self, root: Optional[Path] = None):
        from src.utils.paths import ensure_default_config, program_data_dir, resolve_config_path, state_root

        if root is not None:
            self.root = Path(root)
            self.config_path = self.root / "config" / "config.yaml"
            self.state_dir = self.root / "state"
            self.audit_dir = self.root / "audit"
        else:
            # Frozen-aware: config beside EXE or ProgramData; state always ProgramData
            data = state_root()
            self.root = data
            self.config_path = resolve_config_path()
            # materialize template on first run
            self.config_path = ensure_default_config(self.config_path)
            self.state_dir = data / "state"
            self.audit_dir = data / "audit"
        self.kill_path = self.state_dir / "kill_switch.flag"
        self.registry_path = self.state_dir / "model_registry.json"
        self.flags_path = self.state_dir / "feature_flags.json"
        self.config: Dict[str, Any] = {}
        self.config_hash: str = ""
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.audit_dir.mkdir(parents=True, exist_ok=True)

    def load_config(self) -> Dict[str, Any]:
        if not self.config_path.exists():
            logger.error("config.yaml missing — fail-closed")
            raise FileNotFoundError(str(self.config_path))
        raw = self.config_path.read_bytes()
        self.config_hash = _sha256_bytes(raw)
        if yaml is None:
            # minimal fallback: empty defaults if PyYAML missing
            logger.warning("PyYAML not installed; using empty config defaults")
            self.config = {}
            return self.config
        self.config = yaml.safe_load(raw) or {}
        mode = str(self.config.get("mode", "PAPER")).upper()
        if mode not in ("PAPER", "SHADOW", "LIVE"):
            raise ValueError(f"invalid mode: {mode}")
        self.config["mode"] = mode
        logger.info("Config loaded hash=%s mode=%s", self.config_hash[:12], mode)
        return self.config

    def is_kill_switch_active(self) -> bool:
        return self.kill_path.exists()

    def activate_kill_switch(self, reason: str = "manual") -> None:
        atomic_write(self.kill_path, f"ACTIVE reason={reason}\n".encode("utf-8"))
        logger.critical("KILL SWITCH FILE WRITTEN: %s", reason)

    def clear_kill_switch(self) -> None:
        """Manual only — never auto-clear."""
        if self.kill_path.exists():
            self.kill_path.unlink()
            logger.warning("Kill switch flag cleared manually")

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self.config
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return cur

    def load_model_registry(self) -> Dict[str, Any]:
        if not self.registry_path.exists():
            return {}
        try:
            return json.loads(self.registry_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.error("Corrupt model_registry: %s", e)
            return {}

    def save_model_registry(self, data: Dict[str, Any]) -> None:
        atomic_write(self.registry_path, json.dumps(data, indent=2).encode("utf-8"))

    def append_audit(self, name: str, record: Dict[str, Any]) -> None:
        path = self.audit_dir / f"{name}.jsonl"
        line = json.dumps(record, default=str) + "\n"
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
