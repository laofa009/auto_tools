from __future__ import annotations

import argparse
import base64
import json
import os
import platform
import shutil
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from task_loader import TaskLoader
from uploader import TaskUploader, ensure_storage_state_file

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_FILE = PROJECT_ROOT / "agent_state.json"
RUNTIME_ROOT = Path(os.environ.get("RZAPPLY_AGENT_RUNTIME", PROJECT_ROOT / "agent_runtime"))
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("RZAPPLY_AGENT_WORKERS", "1")))


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _coerce_bool(value: Any, default: bool | None = None) -> bool | None:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        value = value.strip().lower()
        if value in {"1", "true", "yes", "on"}:
            return True
        if value in {"0", "false", "no", "off"}:
            return False
    return bool(value)


class TaskAgent:
    """Long-running worker that registers at a server and executes tasks sequentially."""

    def __init__(
        self,
        server_url: str,
        *,
        token: str | None = None,
        headless: Optional[bool] = None,
        heartbeat_interval: int = 30,
        poll_interval: int = 5,
        long_poll_timeout: int = 50,
    ) -> None:
        if not server_url:
            raise ValueError("server_url is required")

        self.server_url = server_url.rstrip("/")
        self.token = token or os.environ.get("RZAPPLY_AGENT_TOKEN") or ""
        self.heartbeat_interval = max(heartbeat_interval, 5)
        self.poll_interval = max(poll_interval, 5)
        self.long_poll_timeout = max(long_poll_timeout, 10)
        self.session = requests.Session()
        self.client_id = self._load_client_id()
        self._status = "idle"
        self._current_task_id: str | None = None
        self._stop_event = threading.Event()
        self._status_lock = threading.Lock()
        self.headless = (
            headless
            if headless is not None
            else _env_bool("RZAPPLY_AGENT_HEADLESS", _env_bool("RZAPPLY_HEADLESS", False))
        )

        RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
        ensure_storage_state_file()

    # ------------------------------------------------------------------ public API
    def run(self) -> None:
        self._log(f"Agent starting (headless={self.headless})")
        self._register()
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        try:
            self._task_loop()
        except KeyboardInterrupt:
            self._log("Received Ctrl+C, shutting down...")
            self._stop_event.set()
            heartbeat_thread.join(timeout=5)

    # ------------------------------------------------------------------ network helpers
    def _request_headers(self) -> Dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def _post_json(self, path: str, payload: Dict[str, Any], timeout: int = 10) -> Dict[str, Any]:
        url = f"{self.server_url}{path}"
        response = self.session.post(url, headers=self._request_headers(), json=payload, timeout=timeout)
        response.raise_for_status()
        if not response.content:
            return {}
        return response.json()

    def _get_json(self, path: str, params: Dict[str, Any], timeout: int) -> Optional[Dict[str, Any]]:
        url = f"{self.server_url}{path}"
        response = self.session.get(url, headers=self._request_headers(), params=params, timeout=timeout)
        if response.status_code == 204:
            return None
        response.raise_for_status()
        if not response.content:
            return None
        return response.json()

    # ------------------------------------------------------------------ lifecycle
    def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            payload = {
                "client_id": self.client_id,
                "status": self._status,
                "task_id": self._current_task_id,
                "timestamp": datetime.utcnow().isoformat(),
            }
            try:
                self._post_json("/heartbeat", payload)
            except Exception as exc:  # noqa: BLE001
                self._log(f"Heartbeat failed: {exc}")
            finally:
                self._stop_event.wait(self.heartbeat_interval)

    def _task_loop(self) -> None:
        while not self._stop_event.is_set():
            payload: Optional[Dict[str, Any]] = None
            try:
                payload = self._get_json(
                    "/task",
                    {"client_id": self.client_id},
                    timeout=self.long_poll_timeout,
                )
            except requests.RequestException as exc:
                self._log(f"Task polling error: {exc}")
                self._stop_event.wait(self.poll_interval)
                continue

            if not payload:
                continue

            task_data = payload.get("task") if "task" in payload else payload
            if not isinstance(task_data, dict):
                self._log("Server returned malformed task payload")
                continue

            self._handle_task(task_data)

    # ------------------------------------------------------------------ task processing
    def _handle_task(self, task_payload: Dict[str, Any]) -> None:
        task_id = str(task_payload.get("task_id") or uuid.uuid4().hex)
        run_dir = RUNTIME_ROOT / task_id
        run_dir.mkdir(parents=True, exist_ok=True)

        self._set_status("running", task_id)
        log_lines: list[str] = []

        def _task_log(message: str) -> None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            log_line = f"[{timestamp}] {message}"
            log_lines.append(log_line)

        task_headless = _coerce_bool(task_payload.get("headless"), self.headless)

        def _invoke_uploader() -> Dict[str, Any]:
            uploader = TaskUploader(headless=task_headless)
            return uploader.upload(task, log=_task_log)
        result_status = "success"
        result_reason = "任务完成"
        artifacts: Dict[str, Any] = {}

        try:
            task_zip = self._prepare_task_archive(task_payload, run_dir)
            loader = TaskLoader(run_dir)
            tasks = loader.load_tasks()
            if not tasks:
                raise RuntimeError("任务包中未发现 meta.json")

            task = tasks[0]
            overrides = task_payload.get("config") or task_payload.get("overrides") or {}
            if overrides:
                task.update_config(overrides)
            if not task.is_config_complete():
                raise RuntimeError("任务配置不完整，缺少著作权人信息或登录参数")

            _task_log(f"开始执行任务：{task.display_name()}，zip={task_zip.name}")
            artifacts = EXECUTOR.submit(_invoke_uploader).result()
            result_reason = "上传成功"
            self._log(f"[{task_id}] 执行成功")
        except Exception as exc:  # noqa: BLE001
            result_status = "failed"
            result_reason = f"{exc}"
            self._log(f"[{task_id}] 执行失败：{exc}")
        finally:
            cleanup = task_payload.get("cleanup", True)
            if cleanup:
                shutil.rmtree(run_dir, ignore_errors=True)

        self._submit_result(
            task_id=task_id,
            status=result_status,
            reason=result_reason,
            logs=log_lines,
            artifacts=artifacts,
        )
        self._set_status("idle", None)

    def _prepare_task_archive(self, payload: Dict[str, Any], run_dir: Path) -> Path:
        """Download or decode the task ZIP into run_dir and return its path."""
        zip_path = run_dir / (payload.get("zip_filename") or f"{uuid.uuid4().hex}.zip")
        if payload.get("zip_base64"):
            data = base64.b64decode(payload["zip_base64"])
            zip_path.write_bytes(data)
            return zip_path

        zip_url = payload.get("zip_url")
        if not zip_url:
            raise RuntimeError("任务缺少 zip_url 或 zip_base64 字段")

        resp = self.session.get(zip_url, timeout=60, stream=True)
        resp.raise_for_status()
        with zip_path.open("wb") as fp:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    fp.write(chunk)
        return zip_path

    def _submit_result(
        self,
        *,
        task_id: str,
        status: str,
        reason: str,
        logs: list[str],
        artifacts: Dict[str, Any],
    ) -> None:
        files_payload = {}
        for key, value in (artifacts or {}).items():
            if isinstance(value, Path):
                files_payload[key] = str(value)
            else:
                files_payload[key] = value

        payload = {
            "client_id": self.client_id,
            "task_id": task_id,
            "status": status,
            "reason": reason,
            "logs": logs,
            "artifacts": files_payload,
        }
        try:
            self._post_json("/task_result", payload, timeout=20)
        except Exception as exc:  # noqa: BLE001
            self._log(f"上报任务结果失败：{exc}")

    # ------------------------------------------------------------------ registration & state
    def _register(self) -> None:
        payload = {
            "client_id": self.client_id,
            "hostname": platform.node(),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
            "headless": self.headless,
        }
        try:
            data = self._post_json("/register", payload)
            assigned_id = data.get("client_id")
            if assigned_id:
                self.client_id = str(assigned_id)
                self._save_client_id()
            self._log(f"注册成功，client_id={self.client_id}")
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"注册客户端失败：{exc}") from exc

    def _load_client_id(self) -> str:
        if STATE_FILE.exists():
            try:
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                if data.get("client_id"):
                    return str(data["client_id"])
            except json.JSONDecodeError:
                pass
        client_id = uuid.uuid4().hex
        self._save_client_id(client_id)
        return client_id

    def _save_client_id(self, client_id: Optional[str] = None) -> None:
        STATE_FILE.write_text(json.dumps({"client_id": client_id or self.client_id}, ensure_ascii=False), encoding="utf-8")

    def _set_status(self, status: str, task_id: Optional[str]) -> None:
        with self._status_lock:
            self._status = status
            self._current_task_id = task_id

    # ------------------------------------------------------------------ logging
    def _log(self, message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        print(f"[agent {timestamp}] {message}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="rzapply automation agent")
    parser.add_argument(
        "--server",
        default=os.environ.get("RZAPPLY_AGENT_SERVER", "http://localhost:8000"),
        help="API server base URL, default from RZAPPLY_AGENT_SERVER",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get("RZAPPLY_AGENT_TOKEN", ""),
        help="Optional bearer token for authentication",
    )
    parser.add_argument("--headless", choices=["auto", "true", "false"], default="auto", help="Override headless mode")
    parser.add_argument("--heartbeat", type=int, default=int(os.environ.get("RZAPPLY_AGENT_HEARTBEAT", "30")))
    parser.add_argument("--poll", type=int, default=int(os.environ.get("RZAPPLY_AGENT_POLL", "5")))
    parser.add_argument("--long-poll", type=int, default=int(os.environ.get("RZAPPLY_AGENT_LONG_POLL", "50")))
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.headless == "auto":
        headless = None
    else:
        headless = args.headless == "true"

    agent = TaskAgent(
        server_url=args.server,
        token=args.token or None,
        headless=headless,
        heartbeat_interval=args.heartbeat,
        poll_interval=args.poll,
        long_poll_timeout=args.long_poll,
    )
    agent.run()


if __name__ == "__main__":
    main()
