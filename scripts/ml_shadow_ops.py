#!/usr/bin/env python3
"""Safe operational CLI for shadow/champion/challenger — no force-live/bypass."""
from __future__ import annotations
import argparse, json, sys
from pathlib import Path

def main() -> int:
    p = argparse.ArgumentParser(description="TKO ML shadow/champion ops (read-mostly)")
    p.add_argument("--state-dir", type=Path, default=Path("state/ml_shadow"))
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("shadow-status")
    sub.add_parser("champion-status")
    sub.add_parser("challenger-status")
    sub.add_parser("observation-status")
    sub.add_parser("promotion-history")
    sub.add_parser("audit-tail")
    args = p.parse_args()
    d = args.state_dir

    if args.cmd == "shadow-status":
        obs = d / "observation.jsonl"
        n = sum(1 for _ in obs.open()) if obs.exists() else 0
        print(json.dumps({"observation_events": n, "mode": "SHADOW"}))
    elif args.cmd == "champion-status":
        from tko.ml.champion import ChampionRegistry
        reg = ChampionRegistry(d / "champion.jsonl")
        a = reg.get_active()
        print(json.dumps(a.to_dict() if a else {"status": "NONE"}, indent=2))
    elif args.cmd == "challenger-status":
        from tko.ml.challenger import ChallengerRegistry
        reg = ChallengerRegistry(d / "challenger.jsonl")
        print(json.dumps({k: v.to_dict() for k, v in reg.all_latest().items()}, indent=2))
    elif args.cmd == "observation-status":
        from tko.ml.observation import ObservationWindow
        print(json.dumps(ObservationWindow(d / "observation.jsonl").status(), indent=2))
    elif args.cmd == "promotion-history":
        from tko.ml.champion import ChampionRegistry
        reg = ChampionRegistry(d / "champion.jsonl")
        print(json.dumps([h.to_dict() for h in reg.history()], indent=2))
    elif args.cmd == "audit-tail":
        from tko.ml.audit_ml import MlAuditLog
        rows = MlAuditLog(d / "audit.jsonl").read_all()
        print(json.dumps(rows[-20:], indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
