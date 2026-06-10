"""Keil project variable presets for live write and scope workflows."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from src.core.keil.project import KeilProject, KeilTarget, parse_keil_project


@dataclass(frozen=True)
class KeilVariablePreset:
    expression: str
    label: str
    value_type: str
    default_value: str = ""
    range_hint: str = ""
    purpose: str = ""
    write_allowed: bool = False

    def summary(self) -> str:
        parts = [self.expression]
        if self.default_value:
            parts.append(f"默认 {self.default_value}")
        if self.range_hint:
            parts.append(self.range_hint)
        return " · ".join(parts)


@dataclass(frozen=True)
class KeilVariablePresetProfile:
    key: str
    display_name: str
    project_path: Path | None
    target_name: str
    write_presets: tuple[KeilVariablePreset, ...] = ()
    scope_presets: tuple[KeilVariablePreset, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def default_write(self) -> KeilVariablePreset | None:
        return self.write_presets[0] if self.write_presets else None

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        if self.key == "unknown" and not self.write_presets and not self.scope_presets:
            return ()
        return (
            ("变量预设", self.display_name),
            ("推荐写入", _join_expressions(self.write_presets)),
            ("推荐示波", _join_expressions(self.scope_presets)),
        )


def keil_variable_preset_profile(
    project_path: str | Path | None,
    target_name: str = "",
) -> KeilVariablePresetProfile:
    if project_path is None:
        return _unknown_profile(None, target_name)
    project_file = Path(project_path).expanduser()
    if not project_file.exists():
        return _unknown_profile(project_file, target_name)
    try:
        project = parse_keil_project(project_file)
    except Exception:
        return _unknown_profile(project_file, target_name)

    target = _select_target(project, target_name)
    source_text = _project_source_text(target)
    lowered_name = project.path.name.lower()
    if "f401variableprobe" in lowered_name or "debug_setpoint" in source_text:
        return _f401_probe_profile(project.path, target.name if target else target_name)
    if _looks_like_balance_car(source_text):
        return _balance_car_profile(project.path, target.name if target else target_name)
    return _unknown_profile(project.path, target.name if target else target_name)


def keil_live_write_seed(profile: KeilVariablePresetProfile | None) -> tuple[str, str]:
    if profile is not None and profile.default_write is not None:
        preset = profile.default_write
        return preset.expression, preset.default_value
    return "debug_setpoint", "6000"


def keil_live_write_prompt_hint(profile: KeilVariablePresetProfile | None, *, max_items: int = 4) -> str:
    if profile is None or not profile.write_presets:
        return "示例：debug_setpoint\\n6000，或 AnglePID.Kp\\n40.0。"
    examples = []
    for preset in profile.write_presets[: max(1, int(max_items))]:
        value = preset.default_value or "新值"
        examples.append(f"{preset.expression}\\n{value}")
    scope = _join_expressions(profile.scope_presets[:4])
    scope_text = f"；推荐示波：{scope}" if scope else ""
    return f"{profile.display_name} 推荐写入：{'; '.join(examples)}{scope_text}。"


def _select_target(project: KeilProject, target_name: str) -> KeilTarget | None:
    if target_name:
        for target in project.targets:
            if target.name == target_name:
                return target
    return project.default_target


def _project_source_text(target: KeilTarget | None) -> str:
    if target is None:
        return ""
    chunks: list[str] = []
    total = 0
    for file in sorted(target.files, key=_source_priority):
        if not file.exists or file.suffix not in {".c", ".h", ".cpp", ".hpp"}:
            continue
        try:
            text = file.path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        chunks.append(text[:200_000])
        total += len(chunks[-1])
        if total >= 1_000_000:
            break
    return "\n".join(chunks)


def _source_priority(file) -> tuple[int, str]:
    text = file.path_text.replace("\\", "/").lower()
    if "/user/" in text or text.startswith("user/") or "/hardware/" in text or text.startswith("hardware/"):
        return (0, text)
    if "/library/" in text or text.startswith("library/") or "/start/" in text or text.startswith("start/"):
        return (2, text)
    if "/system/" in text or text.startswith("system/"):
        return (3, text)
    return (1, text)


def _looks_like_balance_car(source_text: str) -> bool:
    required = ("SpeedLevel", "AnglePID", "SpeedPID", "TurnPID")
    sensor_or_motor = ("MPU6050", "Encoder_Get", "Motor_SetPWM")
    return all(item in source_text for item in required) and any(item in source_text for item in sensor_or_motor)


def _f401_probe_profile(project_path: Path, target_name: str) -> KeilVariablePresetProfile:
    return KeilVariablePresetProfile(
        key="f401_variable_probe",
        display_name="F401 变量写入探针",
        project_path=project_path,
        target_name=target_name,
        write_presets=(
            KeilVariablePreset(
                "debug_setpoint",
                "调试设定值",
                "int32_t",
                default_value="6000",
                range_hint="-200000..200000",
                purpose="Keil live write 烟测主变量",
                write_allowed=True,
            ),
            KeilVariablePreset(
                "debug_gain",
                "调试增益",
                "float",
                default_value="1.25",
                range_hint="0..20",
                purpose="浮点写入/回读验证",
                write_allowed=True,
            ),
        ),
        scope_presets=(
            KeilVariablePreset("debug_setpoint", "设定值", "int32_t", purpose="写入回读曲线"),
            KeilVariablePreset("debug_feedback", "反馈值", "int32_t", purpose="闭环反馈曲线"),
            KeilVariablePreset("debug_output", "输出值", "int32_t", purpose="输出曲线"),
            KeilVariablePreset("debug_gain", "增益", "float", purpose="浮点参数"),
        ),
        notes=("固定用于 LoopMaster/Keil/ST-Link 写变量烟测。",),
    )


def _balance_car_profile(project_path: Path, target_name: str) -> KeilVariablePresetProfile:
    return KeilVariablePresetProfile(
        key="balance_car_f103",
        display_name="平衡车 F103 调参工程",
        project_path=project_path,
        target_name=target_name,
        write_presets=(
            KeilVariablePreset(
                "SpeedLevel",
                "速度档位",
                "uint16_t",
                default_value="5",
                range_hint="0..20",
                purpose="相对安全的第一写入变量，源码默认值为 5",
                write_allowed=True,
            ),
            KeilVariablePreset(
                "AngleAcc_Offset",
                "角度计偏移",
                "float",
                default_value="0.0",
                range_hint="-20..20",
                purpose="校准/姿态偏置调试",
                write_allowed=True,
            ),
            KeilVariablePreset(
                "AnglePID.Kp",
                "角度环 Kp",
                "float",
                default_value="3.0",
                range_hint="0..20",
                purpose="姿态环比例参数，适合小步调参",
                write_allowed=True,
            ),
            KeilVariablePreset(
                "AnglePID.Kd",
                "角度环 Kd",
                "float",
                default_value="3.0",
                range_hint="0..20",
                purpose="姿态环微分参数，观察超调/抖动",
                write_allowed=True,
            ),
            KeilVariablePreset(
                "SpeedPID.Kp",
                "速度环 Kp",
                "float",
                default_value="2.0",
                range_hint="0..20",
                purpose="速度环比例参数",
                write_allowed=True,
            ),
        ),
        scope_presets=(
            KeilVariablePreset("Angle", "融合角度", "float", purpose="姿态主曲线"),
            KeilVariablePreset("AngleAcc", "加速度角度", "float", purpose="传感器角度"),
            KeilVariablePreset("AngleAcc_Filter", "滤波角度", "float", purpose="滤波质量观察"),
            KeilVariablePreset("AngleDelta", "角度变化", "float", purpose="姿态变化/扰动观察"),
            KeilVariablePreset("AveSpeed", "平均速度", "float", purpose="速度 PID 主反馈"),
            KeilVariablePreset("DifSpeed", "差速", "float", purpose="转向/扰动观察"),
            KeilVariablePreset("PWML", "左 PWM", "int16_t", purpose="执行器输出"),
            KeilVariablePreset("PWMR", "右 PWM", "int16_t", purpose="执行器输出"),
            KeilVariablePreset("AnglePID.Target", "角度环目标", "float", purpose="PID 目标"),
            KeilVariablePreset("AnglePID.Actual", "角度环反馈", "float", purpose="PID 反馈"),
            KeilVariablePreset("AnglePID.Out", "角度环输出", "float", purpose="PID 输出拆解"),
            KeilVariablePreset("AnglePID.POut", "角度环 P", "float", purpose="PID P 项"),
            KeilVariablePreset("AnglePID.IOut", "角度环 I", "float", purpose="PID I 项"),
            KeilVariablePreset("AnglePID.DOut", "角度环 D", "float", purpose="PID D 项"),
            KeilVariablePreset("SpeedPID.Target", "速度环目标", "float", purpose="PID 目标"),
            KeilVariablePreset("SpeedPID.Actual", "速度环反馈", "float", purpose="PID 反馈"),
            KeilVariablePreset("SpeedPID.Out", "速度环输出", "float", purpose="PID 输出拆解"),
            KeilVariablePreset("TurnPID.Out", "转向环输出", "float", purpose="转向扰动/差速观察"),
        ),
        notes=(
            "参考工程当前输出 AXF 通常需要先通过 Keil 构建生成 Objects\\Project.axf。",
            "姿态环增益会直接影响小车稳定性，写入前仍必须走显式确认。",
        ),
    )


def _unknown_profile(project_path: Path | None, target_name: str) -> KeilVariablePresetProfile:
    return KeilVariablePresetProfile(
        key="unknown",
        display_name="未识别 Keil 工程",
        project_path=project_path,
        target_name=target_name,
    )


def _join_expressions(presets: tuple[KeilVariablePreset, ...] | list[KeilVariablePreset]) -> str:
    if not presets:
        return "--"
    return ", ".join(preset.expression for preset in presets)
