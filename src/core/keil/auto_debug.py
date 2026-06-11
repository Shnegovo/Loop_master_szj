"""Automatic Keil debug transaction orchestration."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

from src.core.debug_backend import DebugBackendSessionSnapshot
from src.core.keil.live_write import KeilLiveVariableWriteRequest, KeilLiveVariableWriteResult
from src.core.keil.presets import keil_live_write_seed, keil_variable_preset_profile
from src.core.keil.profile import KeilBuildResult, KeilDebugProfile
from src.core.keil.uvsock import UvscLaunchResult


class KeilAutoDebugBackend(Protocol):
    def debug_profile(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
    ) -> KeilDebugProfile:
        ...

    def build_project(
        self,
        *,
        project_path: str | Path | None,
        target_name: str = "",
        timeout: float = 180.0,
    ) -> KeilBuildResult:
        ...

    def launch_uvsock(
        self,
        *,
        project_path: str | Path | None,
        target_name: str = "",
    ) -> UvscLaunchResult:
        ...

    def read_only_session_snapshot(
        self,
        *,
        project_path: str | Path | None = None,
        target_name: str = "",
        previous_status=None,
        attempt_connection: bool = True,
        query_status: bool = True,
    ) -> DebugBackendSessionSnapshot:
        ...

    def write_live_variable(
        self,
        request: KeilLiveVariableWriteRequest,
        *,
        require_debug: bool = True,
    ) -> KeilLiveVariableWriteResult:
        ...


@dataclass(frozen=True)
class KeilAutoDebugRequest:
    project_path: Path
    target_name: str = ""
    expected_device: str = ""
    allow_device_mismatch: bool = False
    prefer_existing_session: bool = True
    build_if_missing: bool = True
    launch_if_needed: bool = True
    wait_seconds: float = 20.0
    poll_interval: float = 0.75
    write_smoke: bool = True
    expression: str = ""
    value_text: str = ""
    prefer_memory: bool = True
    allow_command_fallback: bool = True
    build_timeout: float = 180.0
    connection_name: str = "LoopMasterAutoDebug"


@dataclass(frozen=True)
class KeilAutoDebugStep:
    key: str
    title: str
    attempted: bool
    succeeded: bool
    detail: str = ""
    elapsed_ms: float = 0.0
    diagnostics: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class KeilAutoDebugResult:
    request: KeilAutoDebugRequest
    profile: KeilDebugProfile | None = None
    build: KeilBuildResult | None = None
    launch: UvscLaunchResult | None = None
    snapshot: DebugBackendSessionSnapshot | None = None
    write: KeilLiveVariableWriteResult | None = None
    steps: tuple[KeilAutoDebugStep, ...] = ()
    error: str = ""

    @property
    def succeeded(self) -> bool:
        if self.error:
            return False
        if self.request.write_smoke:
            return bool(self.write and self.write.written)
        return bool(self.snapshot and self.snapshot.connection_established)

    def summary(self) -> str:
        if self.succeeded:
            if self.write is not None:
                return f"Keil 自动调试完成：{self.write.summary()}"
            return "Keil 自动调试完成：已连接并读取目标状态"
        return f"Keil 自动调试失败：{self.error or _last_failed_step(self.steps) or '未知错误'}"

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows: list[tuple[str, str]] = [
            ("自动调试", "成功" if self.succeeded else "失败"),
            ("自动调试工程", str(self.request.project_path)),
            ("自动调试 Target", self.request.target_name or "--"),
        ]
        if self.request.expected_device:
            rows.append(("期望芯片", self.request.expected_device))
        if self.profile is not None:
            if self.profile.debug_options is not None:
                rows.append(("工程芯片", self.profile.debug_options.device or "--"))
            rows.extend(
                [
                    ("自动调试 AXF", str(self.profile.axf_path or "--")),
                    ("自动调试 AXF 状态", "已存在" if self.profile.axf_exists else "未生成"),
                ]
            )
        for step in self.steps:
            rows.append((f"步骤 {step.title}", "成功" if step.succeeded else "失败" if step.attempted else "跳过"))
            if step.detail:
                rows.append((f"{step.title}详情", step.detail))
        if self.write is not None:
            rows.append(("自动写入变量", self.write.expression))
            rows.append(("自动写入结果", "成功" if self.write.written else "失败"))
            if self.write.readback_value:
                rows.append(("自动写入回读", self.write.readback_value))
            if self.write.error:
                rows.append(("自动写入错误", self.write.error))
        if self.error:
            rows.append(("自动调试错误", self.error))
        return tuple(rows)


@dataclass
class _AutoDebugState:
    steps: list[KeilAutoDebugStep] = field(default_factory=list)
    profile: KeilDebugProfile | None = None
    build: KeilBuildResult | None = None
    launch: UvscLaunchResult | None = None
    snapshot: DebugBackendSessionSnapshot | None = None
    write: KeilLiveVariableWriteResult | None = None
    error: str = ""


def run_keil_auto_debug_transaction(
    backend: KeilAutoDebugBackend,
    request: KeilAutoDebugRequest,
    *,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> KeilAutoDebugResult:
    state = _AutoDebugState()

    state.profile = _timed_step(
        state,
        "profile",
        "调试档案",
        monotonic,
        lambda: backend.debug_profile(project_path=request.project_path, target_name=request.target_name),
        lambda profile: profile.ready,
        lambda profile: _profile_detail(profile),
        skipped=False,
    )
    if state.profile is None or not state.profile.ready:
        state.error = _step_error(state.steps, "调试档案未就绪")
        return _result(request, state)

    if not _device_guard_passed(request, state):
        return _result(request, state)

    if request.build_if_missing and not state.profile.axf_exists:
        state.build = _timed_step(
            state,
            "build",
            "构建",
            monotonic,
            lambda: backend.build_project(
                project_path=request.project_path,
                target_name=state.profile.target_name or request.target_name,
                timeout=request.build_timeout,
            ),
            lambda build: build.succeeded and build.axf_exists,
            lambda build: build.summary(),
            skipped=False,
        )
        if state.build is None or not state.build.succeeded or not state.build.axf_exists:
            state.error = _step_error(state.steps, "构建未生成 AXF")
            return _result(request, state)
        state.profile = backend.debug_profile(
            project_path=request.project_path,
            target_name=state.profile.target_name or request.target_name,
        )
    else:
        state.steps.append(
            KeilAutoDebugStep(
                key="build",
                title="构建",
                attempted=False,
                succeeded=True,
                detail="AXF 已存在，跳过构建" if state.profile.axf_exists else "未启用缺失 AXF 自动构建",
            )
        )
        if not state.profile.axf_exists:
            state.error = "AXF 未生成，且当前事务未启用自动构建"
            return _result(request, state)

    if request.launch_if_needed:
        if request.prefer_existing_session:
            state.snapshot = _try_reuse_existing_connection(
                backend,
                request,
                state,
                target_name=state.profile.target_name or request.target_name,
            )
        if state.snapshot is not None and state.snapshot.connection_established:
            state.steps.append(
                KeilAutoDebugStep(
                    key="launch",
                    title="启动 Keil",
                    attempted=False,
                    succeeded=True,
                    detail="已有 UVSOCK 连接可用，跳过启动",
                )
            )
        else:
            state.launch = _timed_step(
                state,
                "launch",
                "启动 Keil",
                monotonic,
                lambda: backend.launch_uvsock(
                    project_path=request.project_path,
                    target_name=state.profile.target_name or request.target_name,
                ),
                lambda launch: launch.launched,
                lambda launch: f"PID={launch.pid or '--'}" if launch.launched else (launch.error or "启动失败"),
                skipped=False,
            )
            if state.launch is None or not state.launch.launched:
                state.error = _step_error(state.steps, "uVision/UVSOCK 启动失败")
                return _result(request, state)
    else:
        state.launch = _timed_step(
            state,
            "launch",
            "启动 Keil",
            monotonic,
            lambda: None,
            lambda _value: True,
            lambda _value: "按请求跳过启动，尝试连接现有 uVision",
            skipped=False,
        )

    if state.snapshot is None or not state.snapshot.connection_established:
        state.snapshot = _wait_for_connection(
            backend,
            request,
            state,
            monotonic=monotonic,
            sleep=sleep,
            target_name=state.profile.target_name or request.target_name,
        )
    if state.snapshot is None or not state.snapshot.connection_established:
        state.error = _step_error(state.steps, "UVSOCK 连接未就绪")
        return _result(request, state)

    if request.write_smoke:
        expression, value_text = _write_seed(request, state.profile)
        write_request = KeilLiveVariableWriteRequest(
            expression=expression,
            value_text=value_text,
            axf_path=state.profile.axf_path if state.profile and state.profile.axf_exists else None,
            prefer_memory=bool(request.prefer_memory and state.profile and state.profile.axf_exists),
            allow_command_fallback=request.allow_command_fallback,
            connection_name=request.connection_name,
        )
        state.write = _timed_step(
            state,
            "write",
            "写入回读",
            monotonic,
            lambda: backend.write_live_variable(write_request, require_debug=True),
            lambda write: write.written,
            lambda write: write.summary(),
            skipped=False,
        )
        if state.write is None or not state.write.written:
            state.error = _step_error(state.steps, "写入回读失败")

    return _result(request, state)


def _wait_for_connection(
    backend: KeilAutoDebugBackend,
    request: KeilAutoDebugRequest,
    state: _AutoDebugState,
    *,
    monotonic: Callable[[], float],
    sleep: Callable[[float], None],
    target_name: str,
) -> DebugBackendSessionSnapshot | None:
    started = monotonic()
    deadline = started + max(0.1, float(request.wait_seconds))
    attempts = 0
    last_snapshot: DebugBackendSessionSnapshot | None = None
    last_error = ""
    while monotonic() <= deadline:
        attempts += 1
        try:
            last_snapshot = _read_connection_snapshot(
                backend,
                request,
                target_name=target_name,
            )
        except Exception as exc:
            last_error = str(exc)
        else:
            if last_snapshot.connection_established:
                elapsed = (monotonic() - started) * 1000.0
                state.steps.append(
                    KeilAutoDebugStep(
                        key="connect",
                        title="连接",
                        attempted=True,
                        succeeded=True,
                        detail=f"attempts={attempts} state={last_snapshot.status.state.value}",
                        elapsed_ms=elapsed,
                    )
                )
                return last_snapshot
            last_error = _snapshot_error(last_snapshot)
        sleep(max(0.05, float(request.poll_interval)))

    elapsed = (monotonic() - started) * 1000.0
    state.steps.append(
        KeilAutoDebugStep(
            key="connect",
            title="连接",
            attempted=True,
            succeeded=False,
            detail=f"attempts={attempts} {last_error or 'timeout'}",
            elapsed_ms=elapsed,
        )
    )
    return last_snapshot


def _try_reuse_existing_connection(
    backend: KeilAutoDebugBackend,
    request: KeilAutoDebugRequest,
    state: _AutoDebugState,
    *,
    target_name: str,
) -> DebugBackendSessionSnapshot | None:
    try:
        snapshot = _read_connection_snapshot(
            backend,
            request,
            target_name=target_name,
        )
    except Exception as exc:
        state.steps.append(
            KeilAutoDebugStep(
                key="reuse",
                title="复用连接",
                attempted=True,
                succeeded=True,
                detail=f"现有连接不可用，继续启动：{exc}",
            )
        )
        return None
    if snapshot.connection_established:
        state.steps.append(
            KeilAutoDebugStep(
                key="reuse",
                title="复用连接",
                attempted=True,
                succeeded=True,
                detail=f"已复用现有 UVSOCK 连接：state={snapshot.status.state.value}",
            )
        )
        state.steps.append(
            KeilAutoDebugStep(
                key="connect",
                title="连接",
                attempted=True,
                succeeded=True,
                detail=f"reused state={snapshot.status.state.value}",
            )
        )
        return snapshot
    state.steps.append(
        KeilAutoDebugStep(
            key="reuse",
            title="复用连接",
            attempted=True,
            succeeded=True,
            detail=f"未发现可复用连接，继续启动：{_snapshot_error(snapshot) or 'not connected'}",
        )
    )
    return None


def _read_connection_snapshot(
    backend: KeilAutoDebugBackend,
    request: KeilAutoDebugRequest,
    *,
    target_name: str,
) -> DebugBackendSessionSnapshot:
    try:
        return backend.read_only_session_snapshot(
            project_path=request.project_path,
            target_name=target_name,
            attempt_connection=True,
            query_status=True,
            include_breakpoints=False,
        )
    except TypeError:
        return backend.read_only_session_snapshot(
            project_path=request.project_path,
            target_name=target_name,
            attempt_connection=True,
            query_status=True,
        )


def _timed_step(
    state: _AutoDebugState,
    key: str,
    title: str,
    monotonic: Callable[[], float],
    action: Callable[[], Any],
    success: Callable[[Any], bool],
    detail: Callable[[Any], str],
    *,
    skipped: bool,
):
    started = monotonic()
    if skipped:
        state.steps.append(KeilAutoDebugStep(key=key, title=title, attempted=False, succeeded=True))
        return None
    try:
        value = action()
    except Exception as exc:
        state.steps.append(
            KeilAutoDebugStep(
                key=key,
                title=title,
                attempted=True,
                succeeded=False,
                detail=str(exc),
                elapsed_ms=(monotonic() - started) * 1000.0,
            )
        )
        return None
    ok = bool(success(value))
    state.steps.append(
        KeilAutoDebugStep(
            key=key,
            title=title,
            attempted=True,
            succeeded=ok,
            detail=detail(value),
            elapsed_ms=(monotonic() - started) * 1000.0,
        )
    )
    return value


def _write_seed(request: KeilAutoDebugRequest, profile: KeilDebugProfile | None) -> tuple[str, str]:
    if request.expression:
        return request.expression, request.value_text
    preset_profile = keil_variable_preset_profile(
        request.project_path,
        profile.target_name if profile is not None else request.target_name,
    )
    expression, value_text = keil_live_write_seed(preset_profile)
    return expression, request.value_text or value_text


def _device_guard_passed(request: KeilAutoDebugRequest, state: _AutoDebugState) -> bool:
    expected = _normalize_device_text(request.expected_device)
    if not expected:
        state.steps.append(
            KeilAutoDebugStep(
                key="device_guard",
                title="芯片匹配",
                attempted=False,
                succeeded=True,
                detail="未设置期望芯片，跳过匹配护栏",
            )
        )
        return True

    actual = ""
    if state.profile is not None and state.profile.debug_options is not None:
        actual = state.profile.debug_options.device
    actual_norm = _normalize_device_text(actual)
    matched = bool(actual_norm and (expected in actual_norm or actual_norm in expected))
    if matched:
        state.steps.append(
            KeilAutoDebugStep(
                key="device_guard",
                title="芯片匹配",
                attempted=True,
                succeeded=True,
                detail=f"工程芯片 {actual or '--'} 匹配期望 {request.expected_device}",
            )
        )
        return True

    detail = f"工程芯片 {actual or '--'} 与期望 {request.expected_device} 不一致"
    if request.allow_device_mismatch:
        state.steps.append(
            KeilAutoDebugStep(
                key="device_guard",
                title="芯片匹配",
                attempted=True,
                succeeded=True,
                detail=detail + "，已按请求允许继续",
            )
        )
        return True

    state.steps.append(
        KeilAutoDebugStep(
            key="device_guard",
            title="芯片匹配",
            attempted=True,
            succeeded=False,
            detail=detail,
        )
    )
    state.error = detail
    return False


def _normalize_device_text(value: str) -> str:
    return "".join(ch for ch in str(value or "").upper() if ch.isalnum())


def _profile_detail(profile: KeilDebugProfile) -> str:
    if profile.ready:
        return f"{profile.target_name or '--'} AXF={'已存在' if profile.axf_exists else '未生成'}"
    return "; ".join(profile.reasons) or "档案不可用"


def _snapshot_error(snapshot: DebugBackendSessionSnapshot | None) -> str:
    if snapshot is None:
        return ""
    for diagnostic in snapshot.diagnostics:
        if diagnostic.key in {"连接错误", "预检原因", "状态"} and diagnostic.value and diagnostic.value != "--":
            return f"{diagnostic.key}={diagnostic.value}"
    return snapshot.status.detail


def _step_error(steps: list[KeilAutoDebugStep], fallback: str) -> str:
    for step in reversed(steps):
        if step.attempted and not step.succeeded:
            return step.detail or fallback
    return fallback


def _last_failed_step(steps: tuple[KeilAutoDebugStep, ...]) -> str:
    for step in reversed(steps):
        if step.attempted and not step.succeeded:
            return step.detail
    return ""


def _result(request: KeilAutoDebugRequest, state: _AutoDebugState) -> KeilAutoDebugResult:
    return KeilAutoDebugResult(
        request=request,
        profile=state.profile,
        build=state.build,
        launch=state.launch,
        snapshot=state.snapshot,
        write=state.write,
        steps=tuple(state.steps),
        error=state.error,
    )
