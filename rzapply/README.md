## rzapply GUI 上传助手

图形界面工具会扫描 `files/` 目录下的每个 ZIP 包、读取其中的 `meta.json`，并为每个任务提供一套可编辑的字段。完成配置后点击“上传任务”即可触发基于 Playwright 的自动化流程。

### 准备工作

1. 准备一个目录放置所有待处理的 ZIP（每个压缩包需包含 `meta.json`）。
2. 安装依赖：

   ```bash
   uv pip install -r pyproject.toml
   playwright install chromium  # 首次运行 Playwright 需要安装浏览器
   ```

3. 运行 GUI：

   ```bash
   python main.py
   ```

启动后先点击“选择 ZIP 目录”并指向存放压缩包的文件夹，然后再点击“重新扫描 ZIP”即可生成任务列表。右侧表单包含必填字段（软件全称、版本号、软件分类、开发完成日期、首次发表日期），可以覆盖 `meta.json` 中的值。点击“保存配置”会把当前状态写入所选目录下的 `data.json`，方便下次启动继续。

### 上传与登录

- 上传按钮会在后台线程中调用 Playwright。系统默认复用 `playwright/.auth/storage_state.json`，如需重新登录，可删除该文件并在弹出的页面中完成账号认证。
- 上传过程中会实时更新任务状态（PENDING、CONFIGURED、UPLOADING、COMPLETED、FAILED），方便筛选和重试。

### 打包分发（可选）

推荐使用 PyInstaller 生成独立可执行文件：

```bash
pyinstaller --name rzapply --onefile --windowed main.py
```

如需携带资源或 Playwright 数据，可通过 `.spec` 文件或 `--add-data` 参数一起打包。
