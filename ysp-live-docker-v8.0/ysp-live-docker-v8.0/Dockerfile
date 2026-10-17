# ==============================================================================
# ysp-live v8.0 官方轻量 Dockerfile (单端口 / 纯标准库 / 镜像仅约 50MB)
# ==============================================================================
FROM python:3.12-alpine

# 设置时区与环境变量
ENV TZ=Asia/Shanghai \
    PYTHONUNBUFFERED=1 \
    YSP_DATA_DIR=/app/data

RUN apk add --no-cache tzdata ca-certificates && \
    cp /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    echo "Asia/Shanghai" > /etc/timezone && \
    mkdir -p /app /app/data

WORKDIR /app

# 拷贝单文件服务代码 (内置 64 路全部央视与卫视频道，无需任何外部 yaml)
COPY ysp-live.py /app/ysp-live.py

# 开放单端口 8767
EXPOSE 8767

# 持久化状态数据目录 (设备信息与缓存状态)
VOLUME ["/app/data"]

ENTRYPOINT ["python3", "/app/ysp-live.py"]
CMD ["8767"]
