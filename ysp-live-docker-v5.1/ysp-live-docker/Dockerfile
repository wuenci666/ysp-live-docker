FROM python:3.11-slim

LABEL description="央视频全频道直播代理 v5.1（纯 Python）"

WORKDIR /app

COPY ysp-live.py ./

# 所有文件都在 /app，不另建目录；状态文件也写在这里
EXPOSE 8766 8767

CMD ["python3", "ysp-live.py"]
