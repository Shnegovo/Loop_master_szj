"""Probe the Keil backend adapter without requiring a live debug session."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Keil debug backend adapter.")
    parser.add_argument("--keil-root", default="D:\\Keil")
    parser.add_argument("--port", type=int, default=4827)
    parser.add_argument("--project", default="")
    parser.add_argument("--target", default="")
    parser.add_argument("--attempt-existing", action="store_true")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    adapter = KeilUvSockBackendAdapter(
        KeilBackendConfig(root=Path(args.keil_root), port=int(args.port))
    )
    if args.attempt_existing:
        snapshot = adapter.read_only_session_snapshot(
            project_path=args.project or None,
            target_name=args.target,
            attempt_connection=True,
            query_status=args.status,
        )
    else:
        snapshot = adapter.discover(
            project_path=args.project or None,
            target_name=args.target,
        )

    record = snapshot.to_record()
    json.dumps(record, ensure_ascii=False, sort_keys=True)
    if args.json:
        print(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"Adapter: {snapshot.adapter_name}")
        print(f"Snapshot: {snapshot.snapshot_id}")
        print(f"State: {snapshot.status.state.value} detail={snapshot.status.detail}")
        print(
            "Read-only: "
            f"attempted={snapshot.connection_attempted} "
            f"connected={snapshot.connection_established} "
            f"target_running={snapshot.target_running}"
        )
        for key, value in snapshot.diagnostic_rows():
            print(f"{key}: {value}")

    _assert(snapshot.backend.value == "keil", "backend kind mismatch")
    _assert(snapshot.read_only, "Keil adapter probe must remain read-only")
    _assert(not snapshot.status.capabilities.can_write_variables, "read-only adapter must not enable variable writes")
    _assert(not snapshot.status.capabilities.can_halt, "read-only adapter must not enable halt")
    _assert(not snapshot.status.capabilities.can_run, "read-only adapter must not enable run")
    _assert(not snapshot.status.capabilities.can_step, "read-only adapter must not enable step")
    _assert(not snapshot.status.capabilities.can_sync_breakpoints, "read-only adapter must not enable breakpoint sync")
    if not args.attempt_existing:
        _assert(not snapshot.connection_attempted, "discover probe must not attempt UVSOCK connection")

    print(
        "PASS keil backend adapter "
        f"state={snapshot.status.state.value} "
        f"attempted={snapshot.connection_attempted} "
        f"connected={snapshot.connection_established}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

