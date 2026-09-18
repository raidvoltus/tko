"""End-to-end data lineage records (immutable descriptors)."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any, Dict


def _h(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:32]


@dataclass(frozen=True)
class LineageNode:
    kind: str  # dataset | feature | model | calibration | evaluation | decision
    identity: str
    parent_ids: tuple = ()
    meta: Dict[str, Any] = field(default_factory=dict)

    def node_id(self) -> str:
        return _h({"kind": self.kind, "identity": self.identity, "parents": list(self.parent_ids), "meta": self.meta})

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["node_id"] = self.node_id()
        return d


def chain_lineage(
    dataset_hash: str,
    feature_schema_hash: str,
    model_hash: str = "",
    calibration_hash: str = "",
    evaluation_hash: str = "",
) -> Dict[str, Any]:
    nodes = []
    d = LineageNode("dataset", dataset_hash)
    nodes.append(d)
    f = LineageNode("feature", feature_schema_hash, parent_ids=(d.node_id(),))
    nodes.append(f)
    parents = (f.node_id(),)
    if model_hash:
        m = LineageNode("model", model_hash, parent_ids=parents)
        nodes.append(m)
        parents = (m.node_id(),)
    if calibration_hash:
        c = LineageNode("calibration", calibration_hash, parent_ids=parents)
        nodes.append(c)
        parents = (c.node_id(),)
    if evaluation_hash:
        e = LineageNode("evaluation", evaluation_hash, parent_ids=parents)
        nodes.append(e)
    return {"nodes": [n.to_dict() for n in nodes], "root": nodes[0].node_id() if nodes else ""}
