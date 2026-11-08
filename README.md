# HBTV All-in-One（河北电信 IPTV · 抓取 + 整理 + 内网订阅）

把原来两个项目合并成一个可独立运行的内网服务：

| 来源 | 提供什么 |
|---|---|
| `IPTV-channels-hebei` | 河北电信认证、频道与 EPG 抓取、机顶盒配置页 |
| `hbtv-playlist`（整理层，移植自 `suzukua/iptv-cd-telecom`） | 名称规范化、分组去重、台标匹配、`tvg-id`、7 类多播放器格式、动态参数 |

**原项目没有被修改**：本目录是完整复制后合并的独立项目，`IPTV-channels-hebei/` 与 `hbtv-playlist/` 保持原样。

仓库：<https://github.com/czmayue2008/IPTV-channels-hebei-allinone>

---

## 一、启动（全部内网）

### 直接拉取公开镜像（推荐）

```bash
docker run -d --name hbtv -p 5000:5000 \
  -v /your/path/config:/app/config \
  -v /your/path/output:/app/output \
  --restart unless-stopped \
  czmayue2008/iptv-channels-hebei-allinone:latest

# 也可用 GHCR（同一份镜像，国内网络可任选其一）
# docker pull ghcr.io/czmayue2008/iptv-channels-hebei-allinone:latest
```

镜像内**不含任何真实机顶盒参数**：首次启动会从 `config.example.json` 生成占位配置，打开 `http://<内网IP>:5000/config` 填自己的 UserID / MAC / STBID / Authenticator 后即可抓取。挂载 `config` 与 `output` 两个卷可让配置和整理产物持久化；即使挂载的是空 `config` 目录，启动脚本也会从镜像内置模板自动补齐 `config.example.json` / `organize.json` / `config.json`。

### 源码运行 / 本地构建

```bash
cd IPTV-channels-hebei-allinone
pip install -r requirements.txt          # 只有 Flask + requests
python app.py                            # 监听 0.0.0.0:5000
```

```bash
bash start.sh
# 或
docker build -t hbtv-allinone .
docker run -d --name hbtv -p 5000:5000 \
  -v /your/path/config:/app/config \
  -v /your/path/output:/app/output \
  --restart unless-stopped hbtv-allinone
```

打开 `http://<本机或内网IP>:5000/` 即控制台。绑定 `0.0.0.0`，同一局域网的手机、电视、盒子都能访问。

### 内网地址一览

| 用途 | 地址 | 说明 |
|---|---|---|
| 控制台 | `http://<内网IP>:5000/` | 手动整理、定时整理、输出文件列表、日志 |
| 机顶盒配置 | `http://<内网IP>:5000/config` | 填 UserID / MAC / STBID / Authenticator 等 |
| 订阅中心 | `http://<内网IP>:5000/playlists/` | 静态订阅页，列出全部文件 |
| 播放列表 | `http://<内网IP>:5000/playlists/hbtv_aptv.m3u` | 所有 `.m3u` / `.m3u8` / `.txt` 均可直接订阅 |
| EPG | `http://<内网IP>:5000/playlists/epg.xml` | 另有 `.gz` 压缩版 |
| 台标 | `http://<内网IP>:5000/logo/CCTV1.png` | 128 个台标全部本地存放 |
| 抓取原始产物 | `http://<内网IP>:5000/output/hbtv.m3u` | 抓取脚本直接产出的文件 |

**台标和 EPG 用当前访问主机自动补全**：生成物里写的是根相对地址 `/logo/xxx.png`、`/playlists/epg.xml`，服务返回播放列表时会按请求的 Host 补成绝对地址。所以用 `192.168.1.10`、`hbtv.local` 或端口映射访问，播放器都能正确拿到台标与节目单，无需重新生成。

---

## 二、手动整理 / 定时整理

控制台「手动整理」区两个按钮：

- **立即整理**：只读现有的 `output/hbtv.m3u` + `output/epg.xml`，重新生成全部格式，不联网、不抓 EPG，约 1 秒。
- **抓取并整理**：先连内网 EPG 重新抓频道与节目单（`--no-sync` 固定带上），再整理。串行抓 EPG，约 1~3 分钟。

「定时整理」区可配置：

| 项 | 说明 |
|---|---|
| 启用开关 | 关掉后不再自动运行 |
| 模式 | `interval` 按分钟间隔（默认 360 分钟）/ `daily` 每天固定时间（如 04:30） |
| EPG 天数 | 定时抓取时用几天节目单（1~7，默认 2） |
| 同时抓取 | 勾选 = 定时跑「抓取并整理」；不勾 = 定时只整理现有数据 |
| 启动后整理一次 | 服务启动 8 秒后自动跑一次 |

调度由 `app.py` 内置守护线程完成，**不依赖系统 crontab**，Windows / Linux / Docker 行为一致；页面实时显示下次运行时间、上次运行结果和日志。配置保存在 `config/organize.json`。

「整理设置」区可改 udpxy 地址、预设（`lean` 精简 / `core` 核心）、排除关键字、列表名、是否生成 `.m3u` 别名，保存后点「立即整理」生效。

### 整理完成后显示的文件

整理成功后控制台会弹出提示，并在「输出文件」区列出**所有生成的 m3u 文件名**（可一键复制文件名清单、逐个打开或复制完整内网订阅地址）：

```
hbtv_kodi.m3u        hbtv_kodi.m3u8       KODI / IPTV Simple / ffmpegdirect
hbtv_aptv.m3u        hbtv_aptv.m3u8       APTV / mytv-android / 完美解码
hbtv_tvbox.m3u       hbtv_tvbox.m3u8      TVBox / Fileball / 通用
hbtv_udpxy.m3u       hbtv_udpxy.m3u8      udpxy / msd_lite 组播转单播
hbtv_rtp2httpd.m3u   hbtv_rtp2httpd.m3u8  rtp2httpd 增强版
hbtv_diyp.txt                            DIYP / 影视仓（#genre# 分组）
hbtv_simple.txt                          极简 名称,地址
```

每个 `.m3u8` 都额外输出一份同名 `.m3u`，兼容只认 `.m3u` 的播放器。另有 `epg.xml`、`epg.xml.gz`、`channels.json`、`manifest.json`、`index.html`。全部位于 `output/playlists/`。

---

## 三、动态订阅参数

对所有 `.m3u` / `.m3u8` 生效，无需重新整理：

```
?aptv=1                          KODI 时移模板 → APTV 模板
?proxy=http://192.168.1.10:8888   替换主播放地址的代理主机
?fcc=192.168.40.10:8027          覆盖 FCC 快速换台服务器
?r2hToken=xxxx                   rtp2httpd 认证令牌
?rtspProxy=http://192.168.1.10:4022
?exclude=4K,慢直播,购物            排除频道
?include=CCTV,卫视                只保留频道
?logo=http://192.168.1.10:5000/logo/   覆盖台标前缀
?epg=http://192.168.1.10:5000/playlists/epg.xml
```

---

## 四、纯内网 / 零外部地址

- 页面只用本地 `static/css/bootstrap.min.css`，**移除了原模板里的 socket.io CDN**；没有 jQuery、字体、图标等任何外链。
- 台标 `logo/` 随项目本地存放，由 `/logo/` 路由提供；播放列表中的台标与 EPG 地址由访问主机动态补全。
- 抓取脚本固定使用 `--no-sync`，不会向外部 Git 仓库同步；配置的 `epg_git_repo` 在页面标注为禁用。
- 抓取只访问 `config/config.json` 里的 `AuthenticationIP`（内网 EPG）；整理层完全离线，只用 Python 标准库。
- 定时任务为进程内线程，不写 crontab、不启 cron 服务。
- 提交前可用 `python verify.py` 做自检（含外部地址扫描）。

> 生成的播放地址指向你自己的 rtp2httpd / udpxy 组播代理，需在能访问 IPTV 专网的环境里才能播放；EPG 与频道数据仅在内网有效。

---

## 五、目录结构

```
IPTV-channels-hebei-allinone/
├── app.py                     单一 Flask 应用：控制台 / 手动 / 定时 / 订阅 / 台标
├── organize.py                整理层入口（可独立 CLI 运行）
├── get_iptv_channels_V1.1.py  河北电信抓取器（原样复制）
├── lib/                       整理层：source / normalize / logo / exporters / epg / dynamic
├── logo/                      128 个本地台标
├── presets/                   lean / core 预设
├── config/
│   ├── config.json            机顶盒真实配置（本地，不入库）
│   ├── config.example.json    占位符示例
│   └── organize.json          整理 + 定时调度配置
├── output/
│   ├── hbtv.m3u / hbtv.txt / epg.xml   抓取产物（整理层的只读输入）
│   └── playlists/                        整理产物（12 个播放列表 + EPG + 清单）
├── templates/ static/         页面与本地样式
├── verify.py                  自检脚本
└── Dockerfile / start.sh      纯内网容器启动
```

---

## 六、常用命令

```bash
python organize.py                       # 只整理，不启服务
python organize.py --preset lean         # 精简列表
python organize.py --exclude "4K,慢直播" # 临时过滤
python verify.py                         # 20+ 项自检
PORT=8080 python app.py                  # 改端口
```

定时更新建议：在控制台开启「同时抓取」，间隔 360 分钟；或保持默认关闭，需要时手动点按钮。

---

## 七、安全提醒

- `config/config.json`、`output/`、`log/`、`*.pcapng` 含真实 UserID、MAC、STBID、`Authenticator`、回看 `accountinfo` 与内网地址，已被 `.gitignore` / `.dockerignore` 排除，**不要公开上传**。
- 服务默认监听 `0.0.0.0`，请只在可信内网使用；不要直接映射到公网。
- 台标图片来自参考项目，仅供个人学习使用。
