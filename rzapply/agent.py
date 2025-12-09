from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
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
from typing import Any, Callable, Dict, Optional

import requests
import websockets

from task_loader import TaskLoader
from uploader import TaskUploader, ensure_storage_state_file

PROJECT_ROOT = Path(__file__).resolve().parent
STATE_FILE = PROJECT_ROOT / "agent_state.json"
RUNTIME_ROOT = Path(os.environ.get("RZAPPLY_AGENT_RUNTIME", PROJECT_ROOT / "agent_runtime"))
EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("RZAPPLY_AGENT_WORKERS", "1")))
# Dedicated executor for running synchronous uploader code when caller is inside an asyncio loop.
# This prevents calling Playwright's sync API from a thread that has a running asyncio event loop.
UPLOADER_EXECUTOR = ThreadPoolExecutor(max_workers=int(os.environ.get("RZAPPLY_UPLOADER_WORKERS", "1")))


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
        reg_info = self._register()
        heartbeat_thread = threading.Thread(target=self._heartbeat_loop, daemon=True)
        heartbeat_thread.start()

        # If server supports WebSocket, prefer WS mode; otherwise fall back to HTTP long-polling
        ws_thread = None
        try:
            if reg_info and isinstance(reg_info, dict) and reg_info.get("supports_ws") and reg_info.get("ws_url"):
                ws_url = reg_info.get("ws_url")
                self._log(f"Attempting WebSocket connection to {ws_url}")
                ws_thread = threading.Thread(target=self._ws_thread_entry, args=(ws_url,), daemon=True)
                ws_thread.start()

                # 主线程在此等待，直到收到停止信号（例如 Ctrl+C）或显式停止
                while not self._stop_event.is_set():
                    try:
                        time.sleep(0.5)
                    except KeyboardInterrupt:
                        self._log("Received Ctrl+C, shutting down...")
                        self._stop_event.set()
                        break
            else:
                # no ws support, continue with existing long-poll loop
                try:
                    self._task_loop()
                except KeyboardInterrupt:
                    self._log("Received Ctrl+C, shutting down...")
                    self._stop_event.set()
        finally:
            # ensure heartbeat thread is stopped
            self._stop_event.set()
            heartbeat_thread.join(timeout=5)
            if ws_thread and ws_thread.is_alive():
                # give websocket thread a chance to exit gracefully
                ws_thread.join(timeout=2)

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
            # If this code is running inside an asyncio event loop (e.g. the WebSocket client thread),
            # calling Playwright's sync API directly will raise an error. Detect a running loop and
            # dispatch the synchronous uploader to a separate thread pool to avoid invoking sync
            # Playwright APIs on the event loop thread.
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running event loop in current thread — safe to call directly.
                artifacts = _invoke_uploader()
            else:
                # Running inside an asyncio loop — run uploader in dedicated threadpool.
                artifacts = UPLOADER_EXECUTOR.submit(_invoke_uploader).result()
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
            return data
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

    # ------------------------------------------------------------------ websocket client
    def _ws_thread_entry(self, ws_url: str) -> None:
        """Thread entrypoint to run asyncio websocket client loop."""
        try:
            asyncio.run(self._ws_client_loop(ws_url))
        except Exception as exc:  # noqa: BLE001
            self._log(f"WebSocket thread exiting with error: {exc}")

    async def _ws_client_loop(self, ws_url: str) -> None:
        backoff = 1
        max_backoff = 60
        # We'll avoid passing extra_headers to websockets.connect (some event loops reject it).
        # Instead, append token as a query parameter if provided and let server accept it.
        headers = {}
        ws_connect_url = ws_url
        if self.token:
            # append token as query param
            from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

            parts = urlparse(ws_url)
            qs = dict(parse_qsl(parts.query))
            qs.setdefault("token", self.token)
            new_query = urlencode(qs)
            parts = parts._replace(query=new_query)
            ws_connect_url = urlunparse(parts)

        while not self._stop_event.is_set():
            try:
                # allow larger messages (e.g. task payloads containing base64 zips)
                async with websockets.connect(ws_connect_url, ping_interval=None, max_size=10_000_000) as ws:
                    self._log("WebSocket connected")
                    backoff = 1

                    # send register packet
                    register_pkt = {"type": "register", "payload": {
                        "client_id": self.client_id,
                        "hostname": os.uname().nodename if hasattr(os, "uname") else platform.node(),
                        "platform": platform.platform(),
                        "python_version": platform.python_version(),
                        "headless": self.headless,
                    }}
                    await ws.send(json.dumps(register_pkt))

                    # start heartbeat task
                    hb_task = asyncio.create_task(self._ws_heartbeat_task(ws))

                    # listen for messages
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        if not isinstance(msg, dict):
                            continue
                        msg_type = msg.get("type")
                        payload = msg.get("payload") or {}

                        if msg_type == "task":
                            # accept task and run in executor to avoid blocking loop
                            task_payload = payload
                            task_id = str(task_payload.get("task_id") or uuid.uuid4().hex)
                            # log receipt of the task for debugging
                            try:
                                self._log(f"ws: received task_id={task_id}")
                            except Exception:
                                pass
                            # send task_ack accepted
                            ack = {"type": "task_ack", "payload": {"task_id": task_id, "accepted": True}}
                            try:
                                await ws.send(json.dumps(ack))
                            except Exception:
                                pass

                            # schedule handling
                            try:
                                self._log(f"ws: scheduling task_id={task_id} to executor")
                                EXECUTOR.submit(self._handle_task, task_payload)
                            except Exception as exc:  # noqa: BLE001
                                self._log(f"ws: failed to schedule task_id={task_id}: {exc}")
                        elif msg_type == "heartbeat":
                            # server heartbeat request; respond with ack
                            try:
                                await ws.send(json.dumps({"type": "heartbeat_ack", "payload": {}}))
                            except Exception:
                                pass
                        elif msg_type == "heartbeat_ack":
                            # heartbeat acknowledgement from server — handle quietly to avoid noisy logs
                            # could update internal timestamp or metrics here if desired
                            continue
                        elif msg_type == "result":
                            # server pushed a result for some reason — record it via HTTP
                            try:
                                payload["client_id"] = self.client_id
                                # delegate to same recorder used by HTTP endpoint via executor
                                loop = asyncio.get_running_loop()
                                asyncio.create_task(loop.run_in_executor(None, self._post_json, "/task_result", payload, 20))
                            except Exception:
                                pass
                        elif msg_type == "register_ack":
                            # ignore or log
                            self._log("ws: register acknowledged")
                        else:
                            # unknown message
                            self._log(f"ws: unknown message type: {msg_type}")

                    hb_task.cancel()
            except Exception as exc:  # noqa: BLE001
                self._log(f"WebSocket connection error: {exc}")
                if self._stop_event.is_set():
                    break
                await asyncio.sleep(backoff)
                backoff = min(max_backoff, backoff * 2)

    async def _ws_heartbeat_task(self, ws: websockets.WebSocketClientProtocol) -> None:
        try:
            while not self._stop_event.is_set():
                payload = {"client_id": self.client_id, "status": self._status, "task_id": self._current_task_id}
                try:
                    await ws.send(json.dumps({"type": "heartbeat", "payload": payload}))
                except Exception:
                    return
                await asyncio.sleep(self.heartbeat_interval)
        except asyncio.CancelledError:
            return


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
