"""FastAPI service that reuses rzapply core modules without modifying them."""
from __future__ import annotations

import json
import os
import shutil
import sys
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse
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

app = FastAPI(title="rzapply API", version="0.1.0")


def _env_flag(name: str, default: bool = False) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


uploader = TaskUploader(headless=_env_flag("RZAPPLY_HEADLESS", False))


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


@app.on_event("startup")
def _prepare() -> None:
    ensure_storage_state_file()


@app.get("/health")
def health() -> Dict[str, str]:
    return {"status": "ok"}


@app.post("/tasks/upload")
async def upload_task(
    file: UploadFile = File(..., description="包含 meta.json 的任务 ZIP，上传后会解压并读取任务元数据"),
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

    try:
        await run_in_threadpool(uploader.upload, task, _log)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"上传失败：{exc}") from exc
    finally:
        if cleanup:
            shutil.rmtree(run_dir, ignore_errors=True)

    payload = {
        "run_id": run_id,
        "task": task.display_name(),
        "logs": log_lines,
    }
    return JSONResponse(content=payload)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("api_server.app:app", host="0.0.0.0", port=8000, reload=False)
