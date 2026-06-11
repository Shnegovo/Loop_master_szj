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
from src.core.keil.live_write import (  # noqa: E402
    KeilLiveVariableReadResult,
    KeilLiveVariableSmokeResult,
    KeilLiveVariableWriteResult,
    KeilResolvedVariable,
)
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
        command_write: bool = False,
    ) -> None:
        self.axf_exists = bool(axf_exists)
        self.connect_after = int(connect_after)
        self.write_ok = bool(write_ok)
        self.command_write = bool(command_write)
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
        include_breakpoints: bool = True,
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
        new_raw = int(request.value_text, 0).to_bytes(4, "little", signed=True)
        if self.command_write:
            return KeilLiveVariableWriteResult(
                attempted=True,
                written=self.write_ok,
                expression=request.expression,
                value_text=request.value_text,
                method="command",
                command=f"{request.expression} = {request.value_text}",
                readback_value=request.value_text if self.write_ok else "",
                error="" if self.write_ok else "fake command write failed",
            )
        return KeilLiveVariableWriteResult(
            attempted=True,
            written=self.write_ok,
            expression=request.expression,
            value_text=request.value_text,
            method="memory",
            resolved=_resolved(request.expression),
            old_raw=(5000).to_bytes(4, "little", signed=True),
            new_raw=new_raw,
            readback_raw=new_raw if self.write_ok else b"",
            old_value="5000",
            readback_value=request.value_text if self.write_ok else "",
            error="" if self.write_ok else "fake write failed",
        )

    def read_live_variable(self, request, *, require_debug: bool = True):
        self.calls.append(f"read:{request.expression}")
        return KeilLiveVariableReadResult(
            attempted=True,
            read=True,
            expression=request.expression,
            method="memory",
            resolved=_resolved(request.expression),
            raw=(5000).to_bytes(4, "little", signed=True),
            value="5000",
        )

    def run_live_variable_smoke(self, request, *, require_debug: bool = True, read_before_write: bool = True):
        self.calls.append(f"smoke:{request.expression}={request.value_text}:read={int(bool(read_before_write))}")
        read = None
        if read_before_write:
            read = self.read_live_variable(request, require_debug=require_debug)
        write = self.write_live_variable(request, require_debug=require_debug)
        return KeilLiveVariableSmokeResult(read=read, write=write)


def _assert(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _replace_profile_axf_missing(profile):
    from dataclasses import replace

    return replace(profile, axf_exists=False)


def _resolved(expression: str) -> KeilResolvedVariable:
    return KeilResolvedVariable(
        expression=expression,
        symbol=expression,
        address=0x20000008,
        size=4,
        type_name="int",
        source="probe",
        ram_checked=True,
    )


def _step_map(result):
    return {step.key: step for step in result.steps}


def main() -> int:
    clock = _Clock()
    backend = _FakeBackend(axf_exists=True, connect_after=2)
    request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        poll_interval=0.1,
        prefer_existing_session=False,
    )
    result = run_keil_auto_debug_transaction(
        backend,
        request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(result.succeeded, result.summary())
    _assert(result.write is not None and result.write.expression == "debug_setpoint", "F401 default write mismatch")
    _assert(result.read is not None and result.read.value == "5000", "F401 read-before-write mismatch")
    _assert("smoke:debug_setpoint=6000:read=1" in backend.calls, f"smoke call missing: {backend.calls!r}")
    _assert("write:debug_setpoint=6000" in backend.calls, f"write call missing: {backend.calls!r}")
    steps = _step_map(result)
    _assert(steps["build"].attempted is False, f"build should be skipped when AXF exists: {steps['build']!r}")
    _assert(steps["smoke"].succeeded and "5000" in steps["smoke"].detail, f"smoke step mismatch: {steps['smoke']!r}")
    _assert(steps["connect"].succeeded and "attempts=2" in steps["connect"].detail, f"connect polling mismatch: {steps['connect']!r}")
    rows = dict(result.diagnostic_rows())
    _assert(rows.get("自动调试") == "成功", f"diagnostics mismatch: {rows!r}")
    _assert(rows.get("自动写入前回读") == "5000", f"read diagnostic mismatch: {rows!r}")
    _assert(rows.get("自动写入变量") == "debug_setpoint", f"write diagnostic mismatch: {rows!r}")

    reuse_backend = _FakeBackend(axf_exists=True, connect_after=1)
    reuse_request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        poll_interval=0.1,
        prefer_existing_session=True,
    )
    reuse_result = run_keil_auto_debug_transaction(
        reuse_backend,
        reuse_request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(reuse_result.succeeded, reuse_result.summary())
    reuse_steps = _step_map(reuse_result)
    _assert(reuse_steps["reuse"].succeeded, f"reuse step mismatch: {reuse_steps['reuse']!r}")
    _assert(reuse_steps["launch"].attempted is False, f"reuse should skip launch: {reuse_steps['launch']!r}")
    _assert("launch" not in reuse_backend.calls, f"reuse must not launch: {reuse_backend.calls!r}")

    guarded_backend = _FakeBackend(axf_exists=True, connect_after=1)
    guarded_request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        expected_device="STM32F401",
        poll_interval=0.1,
    )
    guarded_result = run_keil_auto_debug_transaction(
        guarded_backend,
        guarded_request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(guarded_result.succeeded, guarded_result.summary())
    _assert(_step_map(guarded_result)["device_guard"].succeeded, "F401 device guard should pass")

    mismatch_backend = _FakeBackend(axf_exists=True, connect_after=1)
    mismatch_request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        expected_device="STM32F103",
        poll_interval=0.1,
    )
    mismatch_result = run_keil_auto_debug_transaction(
        mismatch_backend,
        mismatch_request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(not mismatch_result.succeeded, "device mismatch should block auto-debug")
    _assert(_step_map(mismatch_result)["device_guard"].succeeded is False, "device guard step should fail")
    _assert("smoke:debug_setpoint=6000:read=1" not in mismatch_backend.calls, f"mismatch must not smoke: {mismatch_backend.calls!r}")
    _assert("write:debug_setpoint=6000" not in mismatch_backend.calls, f"mismatch must not write: {mismatch_backend.calls!r}")

    allowed_backend = _FakeBackend(axf_exists=True, connect_after=1)
    allowed_request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        expected_device="STM32F103",
        allow_device_mismatch=True,
        poll_interval=0.1,
    )
    allowed_result = run_keil_auto_debug_transaction(
        allowed_backend,
        allowed_request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(allowed_result.succeeded, allowed_result.summary())
    _assert("smoke:debug_setpoint=6000:read=1" in allowed_backend.calls, f"allowed mismatch should smoke: {allowed_backend.calls!r}")
    _assert("write:debug_setpoint=6000" in allowed_backend.calls, f"allowed mismatch should continue: {allowed_backend.calls!r}")

    strict_backend = _FakeBackend(axf_exists=True, connect_after=1, command_write=True)
    strict_result = run_keil_auto_debug_transaction(
        strict_backend,
        request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(not strict_result.succeeded, "strict smoke should reject command-only write success")
    _assert(_step_map(strict_result)["smoke"].succeeded is False, "strict smoke step should fail")

    loose_request = KeilAutoDebugRequest(
        project_path=PROJECT,
        target_name=TARGET,
        poll_interval=0.1,
        prefer_existing_session=False,
        strict_write_smoke=False,
    )
    loose_backend = _FakeBackend(axf_exists=True, connect_after=1, command_write=True)
    loose_result = run_keil_auto_debug_transaction(
        loose_backend,
        loose_request,
        sleep=clock.sleep,
        monotonic=clock.monotonic,
    )
    _assert(loose_result.succeeded, loose_result.summary())

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
