"""Probe automatic Keil debug transaction orchestration with a fake backend."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.debug_backend import DebugBackendDiagnostic, DebugBackendSessionSnapshot, now_iso  # noqa: E402
from src.core.debug_workbench import DebugBackendKind, DebugRuntimeState, make_debug_status  # noqa: E402
from src.core.keil.auto_debug import KeilAutoDebugRequest, run_keil_auto_debug_transaction  # noqa: E402
from src.core.keil.live_write import KeilLiveVariableWriteResult  # noqa: E402
from src.core.keil.profile import KeilBuildResult, make_keil_debug_profile  # noqa: E402
from src.core.keil.uvsock import UvscLaunchResult  # noqa: E402


PROJECT = ROOT / "firmware" / "keil_f401_variable_probe" / "F401VariableProbe.uvprojx"
TARGET = "STM32F401CCU6 Variable Probe"


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        self.now += 0.01
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(float(seconds))
        self.now += float(seconds)


class _FakeBackend:
    def __init__(
        self,
        *,
        axf_exists: bool = True,
        connect_after: int = 1,
        write_ok: bool = True,
    ) -> None:
        self.axf_exists = bool(axf_exists)
        self.connect_after = int(connect_after)
        self.write_ok = bool(write_ok)
        self.calls: list[str] = []
        self.connect_calls = 0

    def debug_profile(self, *, project_path=None, target_name: str = ""):
        self.calls.append("profile")
        profile = make_keil_debug_profile(
            root=Path("D:/Keil"),
            project_path=project_path or PROJECT,
            target_name=target_name or TARGET,
            port=4827,
        )
        return profile if self.axf_exists else _replace_profile_axf_missing(profile)

    def build_project(self, *, project_path=None, target_name: str = "", timeout: float = 180.0):
        self.calls.append("build")
        profile = make_keil_debug_profile(
            root=Path("D:/Keil"),
            project_path=project_path or PROJECT,
            target_name=target_name or TARGET,
            port=4827,
        )
        self.axf_exists = True
        return KeilBuildResult(
            plan=profile.build_plan,
            attempted=True,
            succeeded=True,
            returncode=0,
            log_path=profile.build_plan.log_path,
            output_tail="Program Size: Code=1 RO-data=1 RW-data=1 ZI-data=1\n0 Error(s), 0 Warning(s).",
            axf_path=profile.axf_path,
            axf_exists=True,
        )

    def launch_uvsock(self, *, project_path=None, target_name: str = ""):
        self.calls.append("launch")
        profile = self.debug_profile(project_path=project_path or PROJECT, target_name=target_name or TARGET)
        return UvscLaunchResult(plan=profile.launch_plan, launched=True, pid=12345)

    def read_only_session_snapshot(
        self,
        *,
        project_path=None,
        target_name: str = "",
        previous_status=None,
        attempt_connection: bool = True,
        query_status: bool = True,
    ):
        self.calls.append("connect")
        self.connect_calls += 1
        connected = self.connect_calls >= self.connect_after
        state = DebugRuntimeState.RUNNING if connected else DebugRuntimeState.KEIL_DISCOVERED
        status = make_debug_status(
            state=state,
            backend=DebugBackendKind.KEIL,
            detail="fake connected" if connected else "fake waiting",
            project_path=project_path or PROJECT,
            target_name=target_name or TARGET,
        )
        return DebugBackendSessionSnapshot(
            schema_version=1,
            backend=DebugBackendKind.KEIL,
            adapter_name="Fake Keil Auto Debug",
            snapshot_id=f"fake-auto-{self.connect_calls}",
            captured_at=now_iso(),
            status=status,
            diagnostics=(
                DebugBackendDiagnostic("连接结果", "已连接" if connected else "等待"),
                DebugBackendDiagnostic("目标运行", "运行中" if connected else "未知"),
            ),
            capabilities=(),
            read_only=True,
            connection_attempted=True,
            connection_established=connected,
            target_running=True if connected else None,
            port=4827,
            project_path=Path(project_path or PROJECT),
            target_name=target_name or TARGET,
        )

    def write_live_variable(self, request, *, require_debug: bool = True):
        self.calls.append(f"write:{request.expression}={request.value_text}")
        return KeilLiveVariableWriteResult(
            attempted=True,
            written=self.write_ok,
            expression=request.expression,
            value_text=request.value_text,
            method="fake",
            readback_value=request.value_text if self.write_ok else "",
            error="" if self.write_ok else "fake write failed",
        )


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _replace_profile_axf_missing(profile):
    from dataclasses import replace

    return replace(profile, axf_exists=False)


def _step_map(result):
    return {step.key: step for step in result.steps}


def main() -> int:
    clock = _Clock()
    backend = _FakeBackend(axf_exists=True, connect_after=2)
    request = KeilAutoDebugRequest(project_path=PROJECT, target_name=TARGET, poll_interval=0.1)
    result = run_keil_auto_debug_transaction(
        backend,
        request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(result.succeeded, result.summary())
    _assert(result.write is not None and result.write.expression == "debug_setpoint", "F401 default write mismatch")
    _assert("write:debug_setpoint=6000" in backend.calls, f"write call missing: {backend.calls!r}")
    steps = _step_map(result)
    _assert(steps["build"].attempted is False, f"build should be skipped when AXF exists: {steps['build']!r}")
    _assert(steps["connect"].succeeded and "attempts=2" in steps["connect"].detail, f"connect polling mismatch: {steps['connect']!r}")
    rows = dict(result.diagnostic_rows())
    _assert(rows.get("自动调试") == "成功", f"diagnostics mismatch: {rows!r}")
    _assert(rows.get("自动写入变量") == "debug_setpoint", f"write diagnostic mismatch: {rows!r}")

    build_backend = _FakeBackend(axf_exists=False, connect_after=1)
    build_result = run_keil_auto_debug_transaction(
        build_backend,
        request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(build_result.succeeded, build_result.summary())
    _assert("build" in build_backend.calls, f"missing build call: {build_backend.calls!r}")
    _assert(_step_map(build_result)["build"].attempted, "build step should be attempted when AXF missing")

    fail_backend = _FakeBackend(axf_exists=True, connect_after=999)
    fail_request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        wait_seconds=0.25,
        poll_interval=0.1,
        write_smoke=False,
    )
    fail_result = run_keil_auto_debug_transaction(
        fail_backend,
        fail_request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(not fail_result.succeeded, "connection timeout should fail")
    _assert(_step_map(fail_result)["connect"].succeeded is False, "connect step should fail")
    _assert("UVSOCK 连接未就绪" in fail_result.error or "attempts=" in fail_result.error, fail_result.error)

    print("PASS keil auto debug transaction probe")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
