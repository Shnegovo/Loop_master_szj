"""Keil debug profile, build, and launch helpers."""

from __future__ import annotations

import os
import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path

from src.core.keil.discovery import KeilDiscovery, discover_keil
from src.core.keil.project import KeilProject, KeilTarget, parse_keil_project
from src.core.keil.uvsock import UvscLaunchPlan, UvscLaunchResult, build_uvision_uvsock_command, start_uvision_uvsock


@dataclass(frozen=True)
class KeilBuildPlan:
    command: tuple[str, ...]
    cwd: Path | None
    log_path: Path | None
    ready: bool
    reasons: tuple[str, ...]

    @property
    def display_command(self) -> str:
        return " ".join(shlex.quote(part) for part in self.command)


@dataclass(frozen=True)
class KeilDebugProfile:
    discovery: KeilDiscovery
    project: KeilProject | None
    target: KeilTarget | None
    project_path: Path | None
    target_name: str
    port: int
    axf_path: Path | None
    axf_exists: bool
    build_plan: KeilBuildPlan
    launch_plan: UvscLaunchPlan
    ready: bool
    reasons: tuple[str, ...]

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        return (
            ("Keil 档案", "可用" if self.ready else "需要配置"),
            ("Keil 根目录", str(self.discovery.root or "--")),
            ("工程", str(self.project_path or "--")),
            ("Target", self.target_name or "--"),
            ("UVSOCK 端口", str(self.port)),
            ("AXF", str(self.axf_path or "--")),
            ("AXF 状态", "已存在" if self.axf_exists else "未生成"),
            ("构建命令", self.build_plan.display_command or "--"),
            ("构建状态", "可构建" if self.build_plan.ready else _reason_text(self.build_plan.reasons)),
            ("启动命令", self.launch_plan.display_command or "--"),
            ("启动状态", "可启动" if self.launch_plan.ready else _reason_text(self.launch_plan.reasons)),
        )


@dataclass(frozen=True)
class KeilBuildResult:
    plan: KeilBuildPlan
    attempted: bool
    succeeded: bool
    returncode: int | None = None
    log_path: Path | None = None
    output_tail: str = ""
    axf_path: Path | None = None
    axf_exists: bool = False
    error: str = ""

    def summary(self) -> str:
        if self.succeeded:
            return f"Keil 构建成功：{self.axf_path or '--'}"
        detail = self.error or self.output_tail or _reason_text(self.plan.reasons) or "构建失败"
        return f"Keil 构建失败：{detail}"


def make_keil_debug_profile(
    *,
    root: str | os.PathLike[str] | None,
    project_path: str | os.PathLike[str] | None,
    target_name: str = "",
    port: int = 4827,
    axf_path: str | os.PathLike[str] | None = None,
    build_log_path: str | os.PathLike[str] | None = None,
) -> KeilDebugProfile:
    discovery = discover_keil(root)
    reasons: list[str] = []
    project: KeilProject | None = None
    target: KeilTarget | None = None
    project_resolved = Path(project_path).expanduser().resolve() if project_path else None

    if not discovery.installed:
        reasons.append("Keil/uVision was not discovered")
    if project_resolved is None:
        reasons.append("No Keil project was provided")
    elif not project_resolved.exists():
        reasons.append(f"Keil project does not exist: {project_resolved}")
    else:
        try:
            project = parse_keil_project(project_resolved)
        except Exception as exc:
            reasons.append(f"Keil project parse failed: {exc}")

    if project is not None:
        target = _select_target(project, target_name)
        if target is None:
            reasons.append(f"Keil target does not exist: {target_name}")
    selected_target_name = target.name if target is not None else str(target_name or "")

    axf = _resolve_axf_path(axf_path, target)
    axf_exists = bool(axf and axf.exists())
    build_log = _default_build_log_path(project_resolved, build_log_path)
    build_plan = build_keil_project_command(
        discovery=discovery,
        project_path=project_resolved,
        target_name=selected_target_name,
        log_path=build_log,
    )
    launch_plan = build_uvision_uvsock_command(
        root=discovery.root or root,
        port=int(port),
        project=project_resolved,
        target=selected_target_name or None,
    )
    ready = bool(discovery.installed and project_resolved and project_resolved.exists() and target is not None)
    return KeilDebugProfile(
        discovery=discovery,
        project=project,
        target=target,
        project_path=project_resolved,
        target_name=selected_target_name,
        port=int(port),
        axf_path=axf,
        axf_exists=axf_exists,
        build_plan=build_plan,
        launch_plan=launch_plan,
        ready=ready and not reasons,
        reasons=tuple(reasons),
    )


def build_keil_project_command(
    *,
    discovery: KeilDiscovery,
    project_path: Path | None,
    target_name: str = "",
    log_path: Path | None = None,
) -> KeilBuildPlan:
    reasons: list[str] = []
    command: list[str] = []
    if not discovery.installed:
        reasons.append("Keil/uVision was not discovered")
    cli = discovery.uvision_com
    if cli is None or not cli.exists:
        reasons.append("uVision.com is missing")
    else:
        command.append(str(cli.path))
    if project_path is None:
        reasons.append("No Keil project was provided")
    elif not project_path.exists():
        reasons.append(f"Keil project does not exist: {project_path}")
    elif command:
        command.extend(["-b", str(project_path)])
    if command and target_name:
        command.extend(["-t", str(target_name)])
    if command:
        command.append("-j0")
    if command and log_path is not None:
        command.extend(["-o", str(log_path)])
    return KeilBuildPlan(
        command=tuple(command),
        cwd=discovery.uv4_dir,
        log_path=log_path,
        ready=bool(command and not reasons),
        reasons=tuple(reasons),
    )


def run_keil_project_build(profile: KeilDebugProfile, *, timeout: float = 180.0) -> KeilBuildResult:
    plan = profile.build_plan
    if not plan.ready:
        return KeilBuildResult(
            plan=plan,
            attempted=False,
            succeeded=False,
            log_path=plan.log_path,
            axf_path=profile.axf_path,
            axf_exists=profile.axf_exists,
            error=_reason_text(plan.reasons),
        )
    if plan.log_path is not None:
        try:
            plan.log_path.parent.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    try:
        result = subprocess.run(
            list(plan.command),
            cwd=str(plan.cwd) if plan.cwd else None,
            capture_output=True,
            text=True,
            timeout=max(1.0, float(timeout)),
            **_quiet_subprocess_kwargs(),
        )
    except Exception as exc:
        return KeilBuildResult(
            plan=plan,
            attempted=True,
            succeeded=False,
            log_path=plan.log_path,
            axf_path=profile.axf_path,
            axf_exists=bool(profile.axf_path and profile.axf_path.exists()),
            error=str(exc),
        )
    output = _read_build_output(plan.log_path, result.stdout, result.stderr)
    succeeded = result.returncode == 0 and _build_output_is_success(output)
    axf_exists = bool(profile.axf_path and profile.axf_path.exists())
    if result.returncode == 0 and not succeeded and not output:
        output = "uVision.com returned 0 but did not produce readable build output"
    return KeilBuildResult(
        plan=plan,
        attempted=True,
        succeeded=bool(succeeded),
        returncode=int(result.returncode),
        log_path=plan.log_path,
        output_tail=_tail(output),
        axf_path=profile.axf_path,
        axf_exists=axf_exists,
        error="" if succeeded else _build_error_text(result.returncode, output),
    )


def launch_keil_uvsock_from_profile(profile: KeilDebugProfile) -> UvscLaunchResult:
    if not profile.launch_plan.ready or profile.project_path is None:
        return UvscLaunchResult(
            plan=profile.launch_plan,
            launched=False,
            error=_reason_text(profile.launch_plan.reasons) or "Keil 调试档案未就绪",
        )
    return start_uvision_uvsock(
        root=profile.discovery.root,
        port=profile.port,
        project=profile.project_path,
        target=profile.target_name or None,
    )


def _select_target(project: KeilProject, target_name: str) -> KeilTarget | None:
    if target_name:
        for target in project.targets:
            if target.name == target_name:
                return target
    return project.default_target


def _resolve_axf_path(path: str | os.PathLike[str] | None, target: KeilTarget | None) -> Path | None:
    if path:
        return Path(path).expanduser().resolve()
    if target is not None and target.output_path is not None:
        return target.output_path
    return None


def _default_build_log_path(project_path: Path | None, path: str | os.PathLike[str] | None) -> Path | None:
    if path:
        return Path(path).expanduser().resolve()
    if project_path is None:
        return None
    return (project_path.parent / "loopmaster_keil_build.log").resolve()


def _reason_text(reasons: tuple[str, ...] | list[str]) -> str:
    if not reasons:
        return ""
    return "；".join(str(reason) for reason in reasons)


def _read_build_output(log_path: Path | None, stdout: str, stderr: str) -> str:
    parts = []
    if log_path is not None and log_path.exists():
        try:
            parts.append(log_path.read_text(encoding="utf-8-sig", errors="replace"))
        except OSError:
            pass
    if stdout:
        parts.append(stdout)
    if stderr:
        parts.append(stderr)
    return "\n".join(part for part in parts if part)


def _build_output_is_success(output: str) -> bool:
    text = output.lower()
    if "error(s)" in text:
        return "0 error(s)" in text
    return "error:" not in text and "fatal error" not in text


def _build_error_text(returncode: int, output: str) -> str:
    tail = _tail(output)
    if tail:
        return f"returncode={returncode}; {tail}"
    return f"uVision.com returncode={returncode}"


def _tail(text: str, max_lines: int = 12, max_chars: int = 2400) -> str:
    lines = [line.rstrip() for line in str(text or "").splitlines() if line.strip()]
    clipped = "\n".join(lines[-max_lines:])
    if len(clipped) > max_chars:
        return clipped[-max_chars:]
    return clipped


def _quiet_subprocess_kwargs() -> dict:
    if os.name != "nt":
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = 0
    return {
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
        "startupinfo": startupinfo,
    }
