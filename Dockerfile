# 纯内网 All-in-One 镜像：抓取 + 整理 + 台标 + 多格式订阅。
# Debian 11 (bullseye) 已 EOL，apt 会 404，使用 Debian 12 (bookworm)。
FROM python:3.11-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONIOENCODING=utf-8 \
    TIME_ZONE=Asia/Shanghai \
    LC_ALL=C.UTF-8 \
    LANG=C.UTF-8 \
    PORT=5000

WORKDIR /app
EXPOSE 5000

# 只需要时区数据；定时整理由内置线程完成，无需 cron；
# --no-sync 禁用了外部 Git 同步，无需 git。
RUN apt-get update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# .dockerignore 排除 config/config.json、output/*、log/* 等真实凭据与产物；
# logo/ 与 presets/ 是本地资源，会随镜像提供，运行时不请求任何外部地址。
COPY . /app

RUN ln -snf /usr/share/zoneinfo/$TIME_ZONE /etc/localtime \
    && echo "$TIME_ZONE" > /etc/timezone \
    && chmod +x /app/start.sh \
    && mkdir -p /app/config /app/output/playlists /app/log

CMD ["/bin/bash", "/app/start.sh"]
