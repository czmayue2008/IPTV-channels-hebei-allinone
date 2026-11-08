#!/bin/bash
# 纯内网启动：不再依赖系统 cron，定时整理由 app.py 内置线程负责。
cd "$(dirname "$0")" || exit 1
mkdir -p config output/playlists log

# 公开镜像不含任何真实机顶盒凭据。若挂载了空的 config 卷，/app/config 里的
# 模板会被遮住，此时从镜像内置的 defaults/ 备份补齐，保证服务可直接启动。
for f in config.example.json organize.json; do
  if [ ! -f "config/$f" ] && [ -f "defaults/$f" ]; then
    cp "defaults/$f" "config/$f"
    echo "[start.sh] 已从内置模板补齐 config/$f"
  fi
done

if [ ! -f config/config.json ]; then
  if [ -f config/config.example.json ]; then
    cp config/config.example.json config/config.json
    echo "[start.sh] 已生成 config/config.json（占位示例），请在 /config 页面填写真实参数"
  else
    echo "[start.sh] 警告：缺少 config/config.json 与模板，请手动挂载配置文件"
  fi
fi

exec python app.py
