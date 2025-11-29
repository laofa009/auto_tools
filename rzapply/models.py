from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, auto
from pathlib import Path
from typing import Any, Dict, List


class TaskStatus(Enum):
    PENDING = auto()
    CONFIGURED = auto()
    UPLOADING = auto()
    COMPLETED = auto()
    FAILED = auto()


OWNER_REQUIRED_FIELDS: List[str] = ["name", "province","city","card_input"]
DEFAULT_OWNER_TYPE = "企业法人"
OWNER_TYPE_ID_OPTIONS: Dict[str, List[str]] = {
    "自然人": ["居民身份证", "军人身份证明（军官证、士兵证等）", "户口本", "其他有效证件"],
    "企业法人": ["统一社会信用代码证书"],
    "机关法人": ["统一社会信用代码证书"],
    "事业单位法人": ["统一社会信用代码证书"],
    "社会团体法人": ["统一社会信用代码证书"],
}


@dataclass
class Task:
    zip_path: Path
    extract_dir: Path
    meta: Dict[str, Any] = field(default_factory=dict)
    config: Dict[str, Any] = field(default_factory=dict)
    status: TaskStatus = TaskStatus.PENDING

    def __post_init__(self) -> None:
        self.config.setdefault("owners", [])
        self.config.setdefault("login_username", "")
        self.config.setdefault("login_password", "")
        self.config.setdefault("login_type", "机构")
        self.config.setdefault("submit_role", "申请人")
        self._normalize_owners()
        if self.status in (TaskStatus.PENDING, TaskStatus.CONFIGURED):
            self._sync_status()

    def display_name(self) -> str:
        return str(
            self.meta.get("softwareName")
            or self.meta.get("software_name")
            or self.zip_path.stem
        )

    def update_config(self, new_config: Dict[str, Any]) -> None:
        self.config.update(new_config)
        self._normalize_owners()
        self._sync_status()

    def is_config_complete(self) -> bool:
        return self._owners_complete()

    def _normalize_owners(self) -> None:
        owners = self.config.get("owners", [])
        if not isinstance(owners, list):
            owners = []
        normalized: List[Dict[str, str]] = []
        for owner in owners:
            owner = owner or {}
            name_type = str(owner.get("name_type", DEFAULT_OWNER_TYPE)) or DEFAULT_OWNER_TYPE
            id_type_options = OWNER_TYPE_ID_OPTIONS.get(name_type, OWNER_TYPE_ID_OPTIONS[DEFAULT_OWNER_TYPE])
            id_type = str(owner.get("id_type") or id_type_options[0])
            normalized.append(
                {
                    "name": str(owner.get("name", "")),
                    "card_input": str(owner.get("card_input", "")),
                    "province": str(owner.get("province", "")),
                    "city": str(owner.get("city", "")),
                    "name_type": name_type,
                    "id_type": id_type,
                }
            )
        self.config["owners"] = normalized

    def _needs_owner_config(self) -> bool:
        submit_role = str(self.config.get("submit_role", "")).strip()
        return submit_role != "申请人"

    def _owners_complete(self) -> bool:
        if not self._needs_owner_config():
            return True
        owners = self.config.get("owners", [])
        if not owners:
            return False
        for owner in owners:
            if not all(owner.get(field, "").strip() for field in OWNER_REQUIRED_FIELDS):
                return False
        return True

    def _sync_status(self) -> None:
        self.status = TaskStatus.CONFIGURED if self._owners_complete() else TaskStatus.PENDING
