"""Persistent Keil debug profile records for LoopMaster."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.keil.profile import KeilDebugProfile, make_keil_debug_profile
from src.core.keil.presets import KeilVariablePresetProfile, keil_variable_preset_profile


PROFILE_STORE_VERSION = 2


@dataclass(frozen=True)
class KeilDebugProfileMetadata:
    device: str = ""
    cpu_core: str = ""
    debug_adapter: str = ""
    debug_protocol: str = ""
    flash_range: str = ""
    ram_range: str = ""
    flash_algorithm: str = ""
    runtime_suitability: str = ""
    preset_key: str = ""
    default_write_expression: str = ""
    default_write_value: str = ""
    write_presets: tuple[str, ...] = ()
    scope_presets: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    def to_record(self) -> dict[str, Any]:
        return {
            "device": self.device,
            "cpu_core": self.cpu_core,
            "debug_adapter": self.debug_adapter,
            "debug_protocol": self.debug_protocol,
            "flash_range": self.flash_range,
            "ram_range": self.ram_range,
            "flash_algorithm": self.flash_algorithm,
            "runtime_suitability": self.runtime_suitability,
            "preset_key": self.preset_key,
            "default_write_expression": self.default_write_expression,
            "default_write_value": self.default_write_value,
            "write_presets": list(self.write_presets),
            "scope_presets": list(self.scope_presets),
            "warnings": list(self.warnings),
        }

    def diagnostic_rows(self) -> tuple[tuple[str, str], ...]:
        rows = []
        if self.device:
            rows.append(("档案芯片", self.device))
        if self.debug_adapter:
            adapter = self.debug_adapter
            if self.debug_protocol:
                adapter += f" / {self.debug_protocol}"
            rows.append(("档案调试器", adapter))
        if self.runtime_suitability:
            rows.append(("档案适用性", self.runtime_suitability))
        if self.preset_key:
            rows.append(("档案预设", self.preset_key))
        if self.default_write_expression:
            default = self.default_write_expression
            if self.default_write_value:
                default += f"={self.default_write_value}"
            rows.append(("档案默认写入", default))
        if self.scope_presets:
            rows.append(("档案推荐示波", ", ".join(self.scope_presets[:8])))
        if self.warnings:
            rows.append(("档案警告", "；".join(self.warnings[:3])))
        return tuple(rows)

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "KeilDebugProfileMetadata":
        if not isinstance(record, dict):
            return cls()
        return cls(
            device=str(record.get("device", "") or ""),
            cpu_core=str(record.get("cpu_core", "") or ""),
            debug_adapter=str(record.get("debug_adapter", "") or ""),
            debug_protocol=str(record.get("debug_protocol", "") or ""),
            flash_range=str(record.get("flash_range", "") or ""),
            ram_range=str(record.get("ram_range", "") or ""),
            flash_algorithm=str(record.get("flash_algorithm", "") or ""),
            runtime_suitability=str(record.get("runtime_suitability", "") or ""),
            preset_key=str(record.get("preset_key", "") or ""),
            default_write_expression=str(record.get("default_write_expression", "") or ""),
            default_write_value=str(record.get("default_write_value", "") or ""),
            write_presets=tuple(str(item) for item in record.get("write_presets", ()) or ()),
            scope_presets=tuple(str(item) for item in record.get("scope_presets", ()) or ()),
            warnings=tuple(str(item) for item in record.get("warnings", ()) or ()),
        )


@dataclass(frozen=True)
class KeilDebugProfileRecord:
    name: str
    project_path: Path
    target_name: str = ""
    keil_root: Path | None = None
    uvsock_port: int = 4827
    axf_path: Path | None = None
    created_at: str = ""
    updated_at: str = ""
    notes: str = ""
    metadata: KeilDebugProfileMetadata = KeilDebugProfileMetadata()

    @property
    def key(self) -> str:
        return f"{_norm_path(self.project_path)}::{self.target_name}"

    @property
    def display_name(self) -> str:
        return self.name or self.project_path.stem

    def to_profile(self) -> KeilDebugProfile:
        return make_keil_debug_profile(
            root=self.keil_root,
            project_path=self.project_path,
            target_name=self.target_name,
            port=self.uvsock_port,
            axf_path=self.axf_path,
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "project_path": str(self.project_path),
            "target_name": self.target_name,
            "keil_root": str(self.keil_root or ""),
            "uvsock_port": int(self.uvsock_port),
            "axf_path": str(self.axf_path or ""),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "notes": self.notes,
            "metadata": self.metadata.to_record(),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> "KeilDebugProfileRecord":
        now = _now_text()
        project = Path(str(record.get("project_path", "") or "")).expanduser()
        root_text = str(record.get("keil_root", "") or "")
        axf_text = str(record.get("axf_path", "") or "")
        name = str(record.get("name", "") or project.stem or "Keil 调试档案")
        return cls(
            name=name,
            project_path=project,
            target_name=str(record.get("target_name", "") or ""),
            keil_root=Path(root_text).expanduser() if root_text else None,
            uvsock_port=max(1, min(65535, int(record.get("uvsock_port", 4827) or 4827))),
            axf_path=Path(axf_text).expanduser() if axf_text else None,
            created_at=str(record.get("created_at", "") or now),
            updated_at=str(record.get("updated_at", "") or now),
            notes=str(record.get("notes", "") or ""),
            metadata=KeilDebugProfileMetadata.from_record(record.get("metadata")),
        )


@dataclass(frozen=True)
class KeilDebugProfileStore:
    default_key: str = ""
    records: tuple[KeilDebugProfileRecord, ...] = ()

    @property
    def default(self) -> KeilDebugProfileRecord | None:
        if self.default_key:
            for record in self.records:
                if record.key == self.default_key:
                    return record
        return self.records[0] if self.records else None

    def upsert(self, record: KeilDebugProfileRecord, *, make_default: bool = True) -> "KeilDebugProfileStore":
        now = _now_text()
        next_record = KeilDebugProfileRecord(
            name=record.name,
            project_path=record.project_path.expanduser().resolve(),
            target_name=record.target_name,
            keil_root=record.keil_root.expanduser().resolve() if record.keil_root else None,
            uvsock_port=record.uvsock_port,
            axf_path=record.axf_path.expanduser().resolve() if record.axf_path else None,
            created_at=record.created_at or now,
            updated_at=now,
            notes=record.notes,
            metadata=record.metadata,
        )
        records = [item for item in self.records if item.key != next_record.key]
        records.insert(0, next_record)
        default_key = next_record.key if make_default else self.default_key
        return KeilDebugProfileStore(default_key=default_key, records=tuple(records[:16]))

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": PROFILE_STORE_VERSION,
            "default_key": self.default_key,
            "profiles": [record.to_record() for record in self.records],
        }

    @classmethod
    def from_record(cls, record: dict[str, Any] | None) -> "KeilDebugProfileStore":
        if not isinstance(record, dict):
            return cls()
        profiles = []
        for item in record.get("profiles", ()) or ():
            if not isinstance(item, dict):
                continue
            try:
                parsed = KeilDebugProfileRecord.from_record(item)
            except Exception:
                continue
            if str(parsed.project_path):
                profiles.append(parsed)
        return cls(
            default_key=str(record.get("default_key", "") or ""),
            records=tuple(profiles),
        )


def profile_record_from_debug_profile(
    profile: KeilDebugProfile,
    *,
    name: str = "",
    notes: str = "",
) -> KeilDebugProfileRecord:
    project_path = profile.project_path or Path("")
    return KeilDebugProfileRecord(
        name=name or _default_profile_name(profile),
        project_path=project_path,
        target_name=profile.target_name,
        keil_root=profile.discovery.root,
        uvsock_port=profile.port,
        axf_path=profile.axf_path,
        created_at=_now_text(),
        updated_at=_now_text(),
        notes=notes,
        metadata=metadata_from_debug_profile(profile),
    )


def metadata_from_debug_profile(profile: KeilDebugProfile) -> KeilDebugProfileMetadata:
    debug_options = profile.debug_options
    preset_profile = keil_variable_preset_profile(profile.project_path, profile.target_name)
    default_write = preset_profile.default_write
    device = debug_options.device if debug_options is not None else ""
    return KeilDebugProfileMetadata(
        device=device,
        cpu_core=_cpu_core_from_device(device),
        debug_adapter=debug_options.adapter_label if debug_options is not None else "",
        debug_protocol=debug_options.protocol_label if debug_options is not None else "",
        flash_range=debug_options.flash_range_label if debug_options is not None else "",
        ram_range=debug_options.ram_range_label if debug_options is not None else "",
        flash_algorithm=debug_options.flash_algorithm if debug_options is not None else "",
        runtime_suitability=_runtime_suitability(profile, preset_profile),
        preset_key=preset_profile.key,
        default_write_expression=default_write.expression if default_write is not None else "",
        default_write_value=default_write.default_value if default_write is not None else "",
        write_presets=tuple(item.expression for item in preset_profile.write_presets[:12]),
        scope_presets=tuple(item.expression for item in preset_profile.scope_presets[:18]),
        warnings=tuple(debug_options.warnings if debug_options is not None else ()),
    )


def load_keil_profile_store(path: str | Path) -> KeilDebugProfileStore:
    store_path = Path(path).expanduser()
    try:
        data = json.loads(store_path.read_text(encoding="utf-8-sig"))
    except Exception:
        return KeilDebugProfileStore()
    return KeilDebugProfileStore.from_record(data)


def save_keil_profile_store(path: str | Path, store: KeilDebugProfileStore) -> bool:
    store_path = Path(path).expanduser()
    try:
        store_path.parent.mkdir(parents=True, exist_ok=True)
        store_path.write_text(
            json.dumps(store.to_record(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        return False
    return True


def _default_profile_name(profile: KeilDebugProfile) -> str:
    project = profile.project_path.stem if profile.project_path else "Keil"
    return f"{project} / {profile.target_name or 'Target'}"


def _cpu_core_from_device(device: str) -> str:
    text = str(device or "").upper()
    if "STM32F0" in text:
        return "Cortex-M0"
    if "STM32F1" in text:
        return "Cortex-M3"
    if "STM32F3" in text or "STM32F4" in text:
        return "Cortex-M4"
    if "STM32F7" in text:
        return "Cortex-M7"
    return ""


def _runtime_suitability(profile: KeilDebugProfile, preset_profile: KeilVariablePresetProfile) -> str:
    device = ""
    if profile.debug_options is not None:
        device = profile.debug_options.device
    device_upper = str(device or "").upper()
    if preset_profile.key == "f401_variable_probe":
        return "connected_f401_smoke"
    if preset_profile.key == "balance_car_f103":
        return "reference_only_f103" if "STM32F103" in device_upper else "reference_only"
    if profile.axf_exists:
        return "candidate_with_axf"
    return "profile_only"


def _norm_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve()).lower()
    except OSError:
        return str(path).lower()


def _now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
