"""FastAPI service that reuses rzapply core modules without modifying them."""
from __future__ import annotations

import asyncio
import base64
import json
import os
import shutil
import sys
import time
import uuid
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

# Ensure the existing rzapply modules are importable when running from api_server.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from models import Task  # noqa: E402
from task_loader import TaskLoader  # noqa: E402
from uploader import TaskUploader, ensure_storage_state_file  # noqa: E402

RUNTIME_DIR = Path(os.environ.get("RZAPPLY_API_RUNTIME", PROJECT_ROOT / "api_runtime"))
RUNTIME_DIR.mkdir(parents=True, exist_ok=True)

ARTIFACTS_ROOT = Path(os.environ.get("RZAPPLY_API_OUTPUT", PROJECT_ROOT / "tasks_output"))
ARTIFACTS_ROOT.mkdir(parents=True, exist_ok=True)

app = FastAPI(title="rzapply API", version="0.1.0")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


DEFAULT_HEADLESS = _env_flag("RZAPPLY_HEADLESS", False)

AGENT_TOKEN = (os.environ.get("RZAPPLY_AGENT_TOKEN") or "").strip()
AGENT_PENDING_TASKS = deque()
AGENT_RUNNING_TASKS: Dict[str, Dict[str, Any]] = {}
AGENT_CLIENTS: Dict[str, Dict[str, Any]] = {}
AGENT_RESULTS: Dict[str, Dict[str, Any]] = {}
AGENT_LOCK = asyncio.Lock()


def _persist_upload(file: UploadFile, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    filename = Path(file.filename or "upload.zip").name
    save_path = target_dir / filename
    with save_path.open("wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return save_path


def _apply_config_overrides(task: Task, overrides: Dict[str, Any]) -> None:
    cleaned = {k: v for k, v in overrides.items() if isinstance(v, str) and v.strip()}
    cleaned.update({k: v for k, v in overrides.items() if not isinstance(v, str)})
    if cleaned:
        task.update_config(cleaned)


def _relative_to_project(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path.resolve())


def _persist_artifacts(artifacts: Dict[str, Path | None], bucket: str) -> Dict[str, str]:
    saved: Dict[str, str] = {}
    if not artifacts:
        return saved

    target_root = ARTIFACTS_ROOT / bucket / "software_copyright_output"
    target_root.mkdir(parents=True, exist_ok=True)

    sign_pdf = artifacts.get("sign_page_pdf")
    if isinstance(sign_pdf, Path) and sign_pdf.exists():
        destination = target_root / sign_pdf.name
        shutil.copy2(sign_pdf, destination)
        saved["sign_page_pdf"] = _relative_to_project(destination)

    if not saved:
        # 如果没有可保存的产物，可以回收空目录
        try:
            target_root.rmdir()
        except OSError:
            pass
    return saved


def _resolve_sign_pdf_path(bucket: str, filename: str | None = None) -> Path | None:
    base = (ARTIFACTS_ROOT / bucket / "software_copyright_output").resolve()
    if not base.exists() or not base.is_dir():
        return None

    if filename:
        candidate = (base / filename).resolve()
        try:
            candidate.relative_to(base)
        except ValueError:
            raise HTTPException(status_code=400, detail="文件名非法")
        return candidate if candidate.exists() else None

    pdfs = sorted(base.glob("*_签章页.pdf"), key=lambda p: p.stat().st_mtime)
    return pdfs[-1] if pdfs else None


@app.on_event("startup")
def _prepare() -> None:
    ensure_storage_state_file()


def _api_response(data: Dict[str, Any] | None, message: str, success: bool = True, status_code: int = 200) -> JSONResponse:
    payload = {
        "code": 0 if success else 1,
        "messages": message,
        "data": data or {},
    }
    return JSONResponse(content=payload, status_code=status_code)


@app.exception_handler(HTTPException)
async def _http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    return _api_response(None, str(exc.detail), success=False, status_code=exc.status_code)


@app.exception_handler(Exception)
async def _generic_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: BLE001
    return _api_response(None, f"服务器内部错误：{exc}", success=False, status_code=500)


@app.get("/health")
def health() -> JSONResponse:
    return _api_response({"status": "ok"}, "ok")


@app.get("/files/sign-page/{bucket}")
def download_sign_page(bucket: str, filename: str | None = None) -> FileResponse:
    target = _resolve_sign_pdf_path(bucket, filename)
    if not target:
        raise HTTPException(status_code=404, detail="未找到签章页文件")
    return FileResponse(path=target, filename=target.name, media_type="application/pdf")


class RegisterPayload(BaseModel):
    client_id: str | None = None
    hostname: str | None = None
    platform: str | None = None
    python_version: str | None = None
    headless: bool | None = None


class HeartbeatPayload(BaseModel):
    client_id: str
    status: str
    task_id: str | None = None
    timestamp: str | None = None


class TaskResultPayload(BaseModel):
    client_id: str
    task_id: str
    status: str
    reason: str
    logs: List[str] = Field(default_factory=list)
    artifacts: Dict[str, Any] = Field(default_factory=dict)


class EnqueueTaskPayload(BaseModel):
    zip_url: str | None = None
    zip_base64: str | None = None
    zip_filename: str | None = None
    config: Dict[str, Any] = Field(default_factory=dict)
    cleanup: bool = True
    headless: bool | None = None
    task_id: str | None = None


def _require_agent_auth(request: Request) -> None:
    if not AGENT_TOKEN:
        return
    auth_header = request.headers.get("Authorization")
    if auth_header != f"Bearer {AGENT_TOKEN}":
        raise HTTPException(status_code=401, detail="unauthorized")


@app.post("/register")
async def register_agent(payload: RegisterPayload, request: Request) -> Dict[str, Any]:
    _require_agent_auth(request)
    client_id = payload.client_id or uuid.uuid4().hex
    client_data = {
        "client_id": client_id,
        "hostname": payload.hostname or "",
        "platform": payload.platform or "",
        "python_version": payload.python_version or "",
        "headless": payload.headless,
        "status": "idle",
        "last_seen": datetime.utcnow().isoformat(),
    }
    async with AGENT_LOCK:
        AGENT_CLIENTS[client_id] = client_data
    return {"client_id": client_id}


@app.post("/heartbeat")
async def heartbeat(payload: HeartbeatPayload, request: Request) -> Dict[str, Any]:
    _require_agent_auth(request)
    async with AGENT_LOCK:
        client = AGENT_CLIENTS.get(payload.client_id)
        if not client:
            raise HTTPException(status_code=404, detail="unknown client")
        client["status"] = payload.status
        client["task_id"] = payload.task_id
        client["last_seen"] = datetime.utcnow().isoformat()
    return {"ok": True}


@app.post("/tasks/enqueue")
async def enqueue_task(payload: EnqueueTaskPayload, request: Request) -> Dict[str, Any]:
    _require_agent_auth(request)
    if not payload.zip_url and not payload.zip_base64:
        raise HTTPException(status_code=400, detail="zip_url 或 zip_base64 至少提供一个")

    task_data = payload.dict()
    if not task_data.get("task_id"):
        task_data["task_id"] = uuid.uuid4().hex

    async with AGENT_LOCK:
        AGENT_PENDING_TASKS.append(task_data)
        pending = len(AGENT_PENDING_TASKS)
    return {"task_id": task_data["task_id"], "pending": pending}


@app.post("/tasks/enqueue/upload")
async def enqueue_upload_task(
    request: Request,
    file: UploadFile = File(..., description="包含 meta.json 的任务 ZIP，上传后将转为 base64 放入队列"),
    task_id: str = Form(
        "",
        description="可选的业务任务 ID；留空时由服务器自动生成",
    ),
    login_username: str = Form(
        "",
        description="版权中心官网登录账号；留空时由客户端回退到默认值或环境变量",
    ),
    login_password: str = Form(
        "",
        description="版权中心官网登录密码；留空时由客户端回退到默认值或环境变量",
    ),
    login_type: str = Form(
        "机构",
        description="登录入口，支持“机构”或“个人用户”",
    ),
    submit_role: str = Form(
        "申请人",
        description="办件身份，默认为“申请人”；如为“代理人”需在 config_json 中提供 owners",
    ),
    config_json: str | None = Form(
        None,
        description="额外的 JSON 覆盖字段，会合并到 config 中（例如 owners、meta 等）",
    ),
    cleanup: bool = Form(
        True,
        description="Agent 完成后是否删除解压目录",
    ),
    headless: bool | None = Form(
        None,
        description="覆盖 Agent 默认的 headless 运行模式；不填使用 Agent 自身配置",
    ),
) -> Dict[str, Any]:
    _require_agent_auth(request)
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传 zip 文件")

    blob = await file.read()
    if not blob:
        raise HTTPException(status_code=400, detail="上传文件为空")

    overrides: Dict[str, Any] = {
        "login_username": login_username,
        "login_password": login_password,
        "login_type": login_type,
        "submit_role": submit_role,
    }

    if config_json:
        try:
            extra = json.loads(config_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"config_json 不是合法 JSON：{exc}") from exc
        if not isinstance(extra, dict):
            raise HTTPException(status_code=400, detail="config_json 必须是 JSON 对象")
        overrides.update(extra)

    payload = {
        "task_id": task_id.strip() or uuid.uuid4().hex,
        "zip_base64": base64.b64encode(blob).decode("ascii"),
        "zip_filename": Path(file.filename).name,
        "config": overrides,
        "cleanup": cleanup,
    }
    if headless is not None:
        payload["headless"] = headless

    async with AGENT_LOCK:
        AGENT_PENDING_TASKS.append(payload)
        pending = len(AGENT_PENDING_TASKS)
    return {"task_id": payload["task_id"], "pending": pending}


async def _pop_agent_task(client_id: str, timeout: int) -> Dict[str, Any] | None:
    deadline = time.monotonic() + max(timeout, 0)
    while True:
        async with AGENT_LOCK:
            if AGENT_PENDING_TASKS:
                task = AGENT_PENDING_TASKS.popleft()
                task_id = str(task.get("task_id") or uuid.uuid4().hex)
                task["task_id"] = task_id
                AGENT_RUNNING_TASKS[task_id] = {
                    "client_id": client_id,
                    "task": task,
                    "started_at": datetime.utcnow().isoformat(),
                }
                return task
        if timeout <= 0 or time.monotonic() >= deadline:
            return None
        await asyncio.sleep(1)


@app.get("/task")
async def fetch_task(request: Request, client_id: str, timeout: int = 25):
    async with AGENT_LOCK:
        if client_id not in AGENT_CLIENTS:
            raise HTTPException(status_code=404, detail="unknown client")
    _require_agent_auth(request)
    task = await _pop_agent_task(client_id, timeout)
    if not task:
        return Response(status_code=204)
    return task


@app.post("/task_result")
async def task_result(payload: TaskResultPayload, request: Request) -> Dict[str, Any]:
    _require_agent_auth(request)
    async with AGENT_LOCK:
        AGENT_RESULTS[payload.task_id] = payload.dict()
        AGENT_RUNNING_TASKS.pop(payload.task_id, None)
        client = AGENT_CLIENTS.get(payload.client_id)
        if client:
            client["status"] = "idle"
            client["task_id"] = None
            client["last_seen"] = datetime.utcnow().isoformat()
    return {"ok": True}


@app.post("/tasks/upload")
async def upload_task(
    file: UploadFile = File(..., description="包含 meta.json 的任务 ZIP，上传后会解压并读取任务元数据"),
    task_id: str = Form(
        "",
        description="业务系统的任务 ID，便于调用方在前端展示进度或排查失败原因",
    ),
    login_username: str = Form(
        "",
        description="版权中心官网登录账号；留空时将回退到 TaskUploader 默认值或环境变量",
    ),
    login_password: str = Form(
        "",
        description="版权中心官网登录密码；留空时将回退到 TaskUploader 默认值或环境变量",
    ),
    login_type: str = Form(
        "机构",
        description="登录入口，支持“机构”或“个人用户”，将映射到官网登录页的 Tab",
    ),
    submit_role: str = Form(
        "申请人",
        description="办理身份，默认为“申请人”；如填写“代理人”则需在 config_json 中补齐 owners 信息",
    ),
    config_json: str | None = Form(
        None,
        description="可选 JSON 字符串，用于覆盖 Task.config（如 owners、meta 字段等）",
    ),
    cleanup: bool = Form(
        True,
        description="是否在任务完成后删除临时目录；设为 false 可保留解压出的文件以便排查",
    ),
) -> JSONResponse:
    if not file.filename or not file.filename.lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="请上传包含 meta.json 的 ZIP 文件")

    provided_task_id = task_id.strip()
    run_id = uuid.uuid4().hex
    run_dir = RUNTIME_DIR / run_id

    saved_zip = _persist_upload(file, run_dir)
    loader = TaskLoader(run_dir)
    tasks = loader.load_tasks()
    if not tasks:
        raise HTTPException(status_code=400, detail="未在 ZIP 中找到有效任务")

    task = tasks[0]
    overrides: Dict[str, Any] = {
        "login_username": login_username,
        "login_password": login_password,
        "login_type": login_type,
        "submit_role": submit_role,
    }

    if config_json:
        try:
            extra = json.loads(config_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=400, detail=f"config_json 不是合法 JSON：{exc}") from exc
        if not isinstance(extra, dict):
            raise HTTPException(status_code=400, detail="config_json 必须是 JSON 对象")
        overrides.update(extra)

    _apply_config_overrides(task, overrides)

    if not task.is_config_complete():
        raise HTTPException(status_code=400, detail="任务配置不完整：请提供 submit_role=申请人 或完整著作权人信息")

    log_lines: List[str] = []

    def _log(message: str) -> None:
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_lines.append(f"[{timestamp}] {message}")

    result_status = "success"
    result_message = "上传成功，已打印签章页"
    http_status = 200

    artifacts: Dict[str, Path | None] = {}
    saved_files: Dict[str, str] = {}

    def _run_upload() -> Dict[str, Path | None]:
        uploader = TaskUploader(headless=DEFAULT_HEADLESS)
        return uploader.upload(task, _log)

    try:
        artifacts = await run_in_threadpool(_run_upload)
        saved_files = _persist_artifacts(artifacts, provided_task_id or run_id)
    except Exception as exc:  # noqa: BLE001
        result_status = "failed"
        result_message = f"上传失败：{exc}"
        http_status = 500
    finally:
        if cleanup:
            shutil.rmtree(run_dir, ignore_errors=True)

    data = {
        "task_id": provided_task_id or None,
        "run_id": run_id,
        "task": task.display_name(),
        "status": result_status,
        "reason": result_message,
        "logs": log_lines,
        "files": saved_files,
    }
    return _api_response(data, result_message, success=(result_status == "success"), status_code=http_status)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server.app:app", host="0.0.0.0", port=8000, reload=False, workers=1)
