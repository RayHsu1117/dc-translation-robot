# 使用官方 Python 基礎映像
FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 複製 requirements.txt 並安裝
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製專案檔案
COPY . .

# R7：以非 root 使用者執行；/data 供 named volume 存放 runtime 資料（見 docker-compose.yml）
RUN useradd -m appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

# 設定環境變數 (透過 .env)
ENV PYTHONUNBUFFERED=1 \
    HEARTBEAT_FILE=/tmp/heartbeat

# R7：心跳健康檢查——bot 每 60 秒 touch 一次 HEARTBEAT_FILE，超過 180 秒沒更新視為 unhealthy
HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD ["python", "-c", "import os, sys, time; p = os.environ.get('HEARTBEAT_FILE', '/tmp/heartbeat'); sys.exit(0 if os.path.exists(p) and time.time() - os.path.getmtime(p) < 180 else 1)"]

# 預設啟動 bot.py
CMD ["python", "bot.py"]
