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


4.windows环境运行步骤
  cd .\rzapply\
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  pip install "playwright>=1.55.0" "pyside6>=6.7.0"
  python -m playwright install chromium
  pip install pyinstaller
  python main.py

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


下面是一套从零开始到重新打包可运行 rzapply.exe 的完整流程，确保 Playwright 浏览器一并打包进去，最终 exe 开箱即用。

1. 准备环境

安装 Python ≥3.12，并在安装时勾选 “Add python.exe to PATH”。
用 PowerShell 在项目根目录（E:\auto_tools\rzapply）创建虚拟环境并激活：
python -m venv .venv
.\.venv\Scripts\Activate.ps1
安装依赖：
pip install --upgrade pip
pip install -r pyproject.toml
安装 Playwright 浏览器和 PyInstaller：
python -m playwright install chromium
pip install pyinstaller
2. 准备 Playwright 浏览器目录

下载后的浏览器位于 C:\Users\<当前用户名>\AppData\Local\ms-playwright。复制整个目录到项目中，例如：
Copy-Item "$env:LOCALAPPDATA\ms-playwright" "E:\auto_tools\rzapply\ms-playwright" -Recurse
在 uploader.py（或 main.py）初始化 Playwright 前设置浏览器路径。例如在 uploader.py 顶部加入：
import os
from pathlib import Path

MS_PLAYWRIGHT = Path(getattr(sys, "_MEIPASS", Path(__file__).parent)) / "playwright" / "driver" / "package" / "ms-playwright"
if not MS_PLAYWRIGHT.exists():
    MS_PLAYWRIGHT = Path.home() / "AppData/Local/ms-playwright"
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(MS_PLAYWRIGHT))
这样 exe 会优先找随包目录，找不到再退回用户的全局目录。
3. 生成 PyInstaller spec（可重复使用）

先运行一次 PyInstaller 生成基础 rzapply.spec：
pyinstaller --name rzapply --windowed main.py
打开生成的 rzapply.spec，在 datas 中加入刚复制的浏览器目录和 playwright/.auth 等资源，例如：
datas=[
    ('ms-playwright', 'playwright/driver/package/ms-playwright'),
    ('playwright/.auth', 'playwright/.auth'),
]
如果有其他静态文件或配置，一并放进 datas。
4. 重新打包

清理旧构建：
Remove-Item build -Recurse -Force
Remove-Item dist -Recurse -Force
使用 spec 重新构建：
pyinstaller rzapply.spec
完成后，dist/rzapply/rzapply.exe 或 dist/rzapply.exe 即为新可执行文件。
5. 验证

在未安装 Playwright 的干净环境（或另一台机器）运行 rzapply.exe，正常情况下不会再弹出 “BrowserType.launch Executable doesn’t exist”。
如果还有问题，检查以下几点：
ms-playwright 目录是否完整、包含 chromium-xxxx\chrome-win\chrome.exe。
rzapply.spec 的 --add-data 路径书写是否正确（Windows 路径用 ; 分隔目标目录）。
PLAYWRIGHT_BROWSERS_PATH 环境变量是否被正确设置到随包目录。
按上述步骤重新打包即可。如果需要我帮忙修改 uploader.py 或 rzapply.spec，告诉我当前代码内容即可。


