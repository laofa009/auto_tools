# rzapply FastAPI Service

该目录提供一个最小的 FastAPI 项目，通过 HTTP 上传 ZIP 后复用现有的 `TaskLoader` 与 `TaskUploader` 立即发起一次自动化上传。

## 快速开始

1. 安装依赖（可继续使用仓库根目录的虚拟环境）：

   ```bash
   pip install -r api_server/pyproject.toml
   # 或者使用 uv:
   uv pip install -r api_server/pyproject.toml
   再或者
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r .\requirements.txt -i https://mirrors.aliyun.com/pypi/simple
   python -m playwright install chromium
   ```
2. 启动服务：

   ```bash
   uvicorn api_server.app:app --host 0.0.0.0 --port 8000
   ```
3. 调用接口（以 curl 为例）：

   ```bash
   curl -X POST "http://localhost:8000/tasks/upload" \
        -F "file=@/path/to/task.zip" \
        -F "login_username=账号" \
        -F "login_password=密码" \
        -F "submit_role=申请人" \
        -F "cleanup=true"
   ```

可选字段：

- `task_id`：业务系统中的任务 ID，便于对齐前端状态或排查问题。
- `config_json`：附加 JSON 配置（字符串形式），可写入 `owners`、`meta` 等自定义字段。
- `cleanup`：布尔值，默认 `true`，上传完成后删除临时目录。若希望保留运行产物，可设为 `false`。

响应示例（成功）：

```json
{
  "code": 0,
  "messages": "上传成功，已打印签章页",
  "data": {
    "task_id": "foobar-001",
    "run_id": "2d07f0c7f0cd4fb68f95438ea118d9aa",
    "task": "示例任务",
    "status": "success",
    "reason": "上传成功，已打印签章页",
    "logs": ["[2024-05-01 10:00:00] login ..."],
    "files": {
      "sign_page_pdf": "tasks_output/foobar-001/software_copyright_output/示例任务_签章页.pdf"
    }
  }
}
```

当 Playwright 上传流程中出现异常时，接口会返回 `code=1`、`messages=失败原因`，`data` 为空对象，HTTP 状态码保持 4xx/5xx，便于前端统一处理。

如需下载签章页，可在任务完成后调用：

```bash
curl -L "http://localhost:8000/files/sign-page/foobar-001"
```

若同一任务生成多个签章页，可通过 `filename` 查询参数指定文件名，例如 `?filename=知识库管理系统_签章页.pdf`。

## Docker 构建

也可以直接使用仓库内的 Dockerfile 打包服务：

```bash
docker build -f rzapply/api_server/Dockerfile -t rzapply-api .
docker run --rm -p 8000:8000 \
  -v $(pwd)/rzapply/tasks_output:/app/tasks_output \
  -e RZAPPLY_API_OUTPUT=/app/tasks_output \
  rzapply-api
```

挂载 `tasks_output` 目录可以让签章页保存在宿主机，必要时可按需再挂载 `api_runtime`（用于查看解压后的中间文件）。

## 运行机制

- 请求会被保存到 `api_runtime/<run_id>/`，然后借助 `TaskLoader` 自动解压 ZIP 并读取 `meta.json`。
- 每个请求都会新建独立的 `TaskUploader` / 浏览器实例，互不共享登录态，避免多用户并发时串线。
- 会将 `submit_role` 默认设为“申请人”，这样无需填著作权人也能测试流程；如切换为“代理人”，请在 `config_json` 中补齐 `owners` 信息。
- 当任务进入“申请人”流程并成功打印签章页时，会把生成的 PDF 复制到 `tasks_output/<task_id or run_id>/software_copyright_output/`（可通过 `RZAPPLY_API_OUTPUT` 环境变量调整），并在响应的 `files` 字段返回保存路径。
- 所有 Playwright 操作仍由原始 `TaskUploader` 完成，并通过 API 响应返回日志。
