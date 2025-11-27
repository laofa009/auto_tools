FROM mcr.microsoft.com/playwright/python:v1.55.0-jammy

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    RZAPPLY_HEADLESS=1

WORKDIR /app

# 预先安装依赖，最大化利用构建缓存
COPY rzapply/api_server/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

# 复制全部源代码（包含核心模块、任务样本、Playwright 配置等）
COPY . /app

# 确保 Chromium 浏览器可用于后续的 Playwright 自动化
RUN playwright install chromium

EXPOSE 8000

CMD ["uvicorn", "api_server.app:app", "--host", "0.0.0.0", "--port", "8000"]
