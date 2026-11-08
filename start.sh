#!/bin/bash
# 纯内网启动：不再依赖系统 cron，定时整理由 app.py 内置线程负责。
cd "$(dirname "$0")" || exit 1
mkdir -p config output/playlists log

# 公开镜像不含任何真实机顶盒凭据。首次启动时从占位示例生成一份空配置，
# 保证服务可直接起来；用户在 http://<内网IP>:5000/config 填入自己的参数即可。
if [ ! -f config/config.json ]; then
  cp config/config.example.json config/config.json
  echo "[start.sh] 已生成 config/config.json（占位示例），请在 /config 页面填写真实参数"
fi

exec python app.py
