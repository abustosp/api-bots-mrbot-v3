"""CLI operativa JSON para la flota EC2 del autoscaler."""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from datetime import datetime, timezone
from typing import Sequence

from central_api.autoscaler.ec2 import Ec2Fleet, Ec2Settings, Ec2Worker


def _worker_json(worker: Ec2Worker) -> dict[str, object]:
    row = asdict(worker)
    row["launched_at"] = worker.launched_at.isoformat()
    row["node"] = worker.node
    return row


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Administra workers EC2 efímeros")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status", help="lista workers y costo aproximado")
    up = commands.add_parser("up", help="lanza workers (limitado por max_instances)")
    up.add_argument("count", type=int)
    down = commands.add_parser("down", help="termina workers gestionados")
    down.add_argument("--ids", nargs="+", help="IDs EC2 gestionados específicos")
    commands.add_parser("audit", help="audita instancias y recursos huérfanos taggeados")
    commands.add_parser("sg", help="asegura el security group de workers")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    fleet = Ec2Fleet(Ec2Settings.from_env())
    if args.command == "status":
        workers = fleet.list_managed()
        payload = {
            "workers": [_worker_json(worker) for worker in workers],
            "estimated_cost_usd": fleet.estimate_cost(workers, datetime.now(timezone.utc)),
        }
    elif args.command == "up":
        launched = fleet.launch(args.count)
        ready = fleet.wait_until_running(launched) if launched else []
        payload = {"workers": [_worker_json(worker) for worker in ready]}
    elif args.command == "down":
        ids = fleet.terminate(args.ids) if args.ids else fleet.terminate_all()
        payload = {"terminated": ids}
    elif args.command == "audit":
        payload = fleet.audit_orphans()
    else:
        payload = {"security_group_id": fleet.ensure_security_group()}
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised by module entrypoint
    raise SystemExit(main())
