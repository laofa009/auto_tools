from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List
from zipfile import ZipFile

from models import OWNER_REQUIRED_FIELDS, Task, TaskStatus


class TaskLoader:
    """Loads software tasks from ZIP archives that contain a meta.json file."""

    def __init__(self, files_dir: Path | str, metadata_filename: str = "meta.json"):
        self.files_dir = Path(files_dir)
        self.metadata_filename = metadata_filename

    def load_tasks(self) -> List[Task]:
        """Return Task objects discovered under files_dir."""
        self.files_dir.mkdir(parents=True, exist_ok=True)
        zip_paths = sorted(self.files_dir.glob("*.zip"))
        tasks: List[Task] = []

        for zip_path in zip_paths:
            extract_dir = self._ensure_extracted(zip_path)
            meta = self._load_meta_from_extract(extract_dir) or self._load_meta_from_zip(zip_path)
            config = self._build_initial_config(meta)
            status = TaskStatus.CONFIGURED if self._is_config_complete(config) else TaskStatus.PENDING
            tasks.append(
                Task(
                    zip_path=zip_path,
                    extract_dir=extract_dir,
                    meta=meta,
                    config=config,
                    status=status,
                )
            )

        return tasks

    def _ensure_extracted(self, zip_path: Path) -> Path:
        """Extract the archive if needed and return the extraction directory."""
        target_dir = self.files_dir / zip_path.stem
        target_dir.mkdir(parents=True, exist_ok=True)

        if not any(target_dir.iterdir()):
            with ZipFile(zip_path, "r") as archive:
                archive.extractall(target_dir)

        return target_dir

    def _load_meta_from_extract(self, extract_dir: Path) -> Dict:
        """Look for meta.json under the extracted directory tree."""
        meta_path = next(extract_dir.rglob(self.metadata_filename), None)
        if meta_path and meta_path.is_file():
            try:
                return json.loads(meta_path.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                return {}
        return {}

    def _load_meta_from_zip(self, zip_path: Path) -> Dict:
        """Fallback loader that reads meta.json directly from the archive."""
        with ZipFile(zip_path, "r") as archive:
            for item in archive.infolist():
                if item.filename.endswith(self.metadata_filename):
                    with archive.open(item) as fp:
                        try:
                            return json.loads(fp.read().decode("utf-8"))
                        except json.JSONDecodeError:
                            return {}
        return {}

    def _build_initial_config(self, meta: Dict) -> Dict[str, List[Dict[str, str]]]:
        _ = meta  # meta is currently unused but kept for potential future defaults
        return {
            "owners": [],
            "login_username": "",
            "login_password": "",
            "login_type": "机构",
            "submit_role": "申请人",
        }

    def _is_config_complete(self, config: Dict[str, List[Dict[str, str]]]) -> bool:
        owners = config.get("owners", [])
        if not owners:
            return False
        for owner in owners:
            if not all(owner.get(field, "").strip() for field in OWNER_REQUIRED_FIELDS):
                return False
        return True
