"""Probe Keil backend BL use during breakpoint sync."""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.keil.backend import KeilBackendConfig, KeilUvSockBackendAdapter  # noqa: E402
from src.core.keil.breakpoint_sync import build_keil_breakpoint_sync_request_from_state  # noqa: E402
from src.core.keil.commands import KeilBreakpointIntent  # noqa: E402


class FakeSession:
    def __init__(self) -> None:
        self.commands: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def execute_command(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        return ""

    def try_execute_command_text(self, command: str, *, echo: bool = False):
        self.commands.append(command)
        return "Breakpoints\n7 enabled D:\\demo\\Core\\Src\\main.c:9\n", ""


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    import src.core.keil.backend as backend_module

    fake_session = FakeSession()
    original_live_session = backend_module.KeilUvscLiveSession
    try:
        backend_module.KeilUvscLiveSession = SimpleNamespace(connect_existing=lambda *_args, **_kwargs: fake_session)
        with tempfile.TemporaryDirectory(prefix="loopmaster-keil-backend-bplist-") as tmp:
            root = Path(tmp)
            main_c = root / "main.c"
            main_c.write_text("int main(void){return 0;}\n", encoding="utf-8")
            request = build_keil_breakpoint_sync_request_from_state(
                project_path=root / "Project.uvprojx",
                target_name="DebugDemo",
                local_breakpoints=(KeilBreakpointIntent(main_c, 9),),
                remote_breakpoints=(),
                source_paths=(main_c,),
                transaction_id="backend-bplist",
                remote_snapshot_complete=False,
            )
            adapter = KeilUvSockBackendAdapter(KeilBackendConfig(root=Path("D:/Keil"), port=4827))
            result = adapter.sync_breakpoints(request)
    finally:
        backend_module.KeilUvscLiveSession = original_live_session

    _assert(result.succeeded, result.summary())
    _assert(any(command.startswith("BS ") for command in fake_session.commands), fake_session.commands)
    _assert("BL" in fake_session.commands, fake_session.commands)
    _assert(result.remote_snapshot is not None and result.remote_snapshot.complete, f"snapshot mismatch: {result.remote_snapshot!r}")
    _assert(result.remote_snapshot.breakpoints[0].remote_id == "7", f"remote id mismatch: {result.remote_snapshot.breakpoints!r}")
    print("PASS Keil backend breakpoint list probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
