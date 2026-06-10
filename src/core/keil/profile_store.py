"""Persistent Keil debug profile records for LoopMaster."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.keil.profile import KeilDebugProfile, make_keil_debug_profile


PROFILE_STORE_VERSION = 1


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


def _norm_path(path: Path) -> str:
    try:
        return str(path.expanduser().resolve()).lower()
    except OSError:
        return str(path).lower()


def _now_text() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")
