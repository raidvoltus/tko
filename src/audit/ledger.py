"""
Append-only, hash-chained audit ledger for critical decisions.

Reconstructable chain: each event hashes (prev_hash || payload_canonical).
No secrets. No order submission capability.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


def _canonical(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)


@dataclass
class AuditEvent:
    seq: int
    ts_utc: float
    ts_mono: float
    kind: str
    payload: Dict[str, Any]
    prev_hash: str
    event_hash: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class AuditLedger:
    GENESIS = "0" * 64

    def __init__(self, path: Optional[str] = None, maxlen_memory: int = 10_000):
        self._lock = threading.RLock()
        self._events: List[AuditEvent] = []
        self._path = Path(path) if path else None
        self._maxlen = maxlen_memory
        self._seq = 0
        self._tip = self.GENESIS
        if self._path and self._path.exists():
            self._load()

    def append(self, kind: str, payload: Dict[str, Any], *, ts_utc: Optional[float] = None, ts_mono: Optional[float] = None) -> AuditEvent:
        with self._lock:
            self._seq += 1
            body = {
                "seq": self._seq,
                "kind": kind,
                "payload": payload,
                "prev_hash": self._tip,
            }
            raw = _canonical(body)
            eh = hashlib.sha256(raw.encode()).hexdigest()
            ev = AuditEvent(
                seq=self._seq,
                ts_utc=float(ts_utc if ts_utc is not None else time.time()),
                ts_mono=float(ts_mono if ts_mono is not None else time.monotonic()),
                kind=kind,
                payload=payload,
                prev_hash=self._tip,
                event_hash=eh,
            )
            self._events.append(ev)
            self._tip = eh
            if len(self._events) > self._maxlen:
                self._events = self._events[-self._maxlen :]
            if self._path:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                with self._path.open("a", encoding="utf-8") as f:
                    f.write(_canonical(ev.to_dict()) + "\n")
            return ev

    def tip_hash(self) -> str:
        with self._lock:
            return self._tip

    def verify_chain(self) -> bool:
        with self._lock:
            prev = self.GENESIS
            for ev in self._events:
                body = {"seq": ev.seq, "kind": ev.kind, "payload": ev.payload, "prev_hash": prev}
                expect = hashlib.sha256(_canonical(body).encode()).hexdigest()
                if ev.prev_hash != prev or ev.event_hash != expect:
                    return False
                prev = ev.event_hash
            return True

    def events(self) -> List[AuditEvent]:
        with self._lock:
            return list(self._events)

    def _load(self) -> None:
        assert self._path is not None
        prev = self.GENESIS
        for line in self._path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            d = json.loads(line)
            ev = AuditEvent(
                seq=int(d["seq"]),
                ts_utc=float(d["ts_utc"]),
                ts_mono=float(d["ts_mono"]),
                kind=str(d["kind"]),
                payload=dict(d["payload"]),
                prev_hash=str(d["prev_hash"]),
                event_hash=str(d["event_hash"]),
            )
            body = {"seq": ev.seq, "kind": ev.kind, "payload": ev.payload, "prev_hash": prev}
            expect = hashlib.sha256(_canonical(body).encode()).hexdigest()
            if ev.prev_hash != prev or ev.event_hash != expect:
                # corrupt → stop load, tip remains last good
                break
            self._events.append(ev)
            self._seq = ev.seq
            prev = ev.event_hash
            self._tip = prev
