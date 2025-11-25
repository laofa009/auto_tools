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

- `config_json`：附加 JSON 配置（字符串形式），可写入 `owners`、`meta` 等自定义字段。
- `cleanup`：布尔值，默认 `true`，上传完成后删除临时目录。若希望保留运行产物，可设为 `false`。

## 运行机制

- 请求会被保存到 `api_runtime/<run_id>/`，然后借助 `TaskLoader` 自动解压 ZIP 并读取 `meta.json`。
- 会将 `submit_role` 默认设为“申请人”，这样无需填著作权人也能测试流程；如切换为“代理人”，请在 `config_json` 中补齐 `owners` 信息。
- 所有 Playwright 操作仍由原始 `TaskUploader` 完成，并通过 API 响应返回日志。
