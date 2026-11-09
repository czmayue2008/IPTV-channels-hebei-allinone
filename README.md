# HBTV All-in-One（河北电信 IPTV · 抓取 + 整理 + 内网订阅）

本项目由HY4构建

| 来源 | 提供什么 |
|---|---|
| `IPTV-channels-hebei` | 河北电信认证、频道与 EPG 抓取、机顶盒配置页 |
| `hbtv-playlist`（整理层，移植自 `suzukua/iptv-cd-telecom`） | 名称规范化、分组去重、台标匹配、`tvg-id`、7 类多播放器格式、动态参数 |


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

### 登录认证（默认开启）

首次访问会自动跳到 `/login` 让**你自己设置管理员密码**（至少 8 位），设置完即进入控制台。之后控制台、机顶盒配置页、所有管理接口都需要登录；会话默认保持 7 天，密码连续错 5 次会锁定 60 秒。

- 密码用 PBKDF2 哈希存到本地 `config/auth.json`，同文件还保存会话密钥与订阅令牌。**该文件已被 `.gitignore` / `.dockerignore` 排除**，不会进公开仓库或镜像；页面与代码里也没有默认密码。
- 容器部署可跳过设置页：`docker run ... -e HBTV_PASSWORD='你的强密码' ...`。忘记密码时删除 `config/auth.json` 并重启服务，重新设置即可（**机顶盒配置不受影响**）。
- 控制台「安全设置」区可修改登录密码、开启/关闭订阅令牌保护、重置令牌；右上角可切换深浅色主题、退出登录。

**关于播放器**：m3u、台标、EPG 默认**免登录**——播放器和电视盒子不会带浏览器 Cookie，强制登录会让所有订阅失效。如果你希望连订阅资源也保护起来，在「安全设置」里开启**订阅令牌保护**：

| 状态 | 播放列表 / 台标 / EPG | 控制台 |
|---|---|---|
| 默认（关闭） | 局域网内直接可访问 | 必须登录 |
| 开启令牌保护 | 需带 `?token=…`，控制台与订阅中心复制的地址会自动附带 | 必须登录 |

开启后，播放列表内部的台标与 EPG 绝对地址也会自动带上令牌，所以**复制一个地址就够用**；令牌可在控制台一键重置，旧订阅地址立即失效。注意 URL 里的令牌等同于访问凭据，别把带令牌的地址发到公网。

### 内网地址一览

| 用途 | 地址 | 说明 |
|---|---|---|
| 登录 / 初始化 | `http://<内网IP>:5000/login` | 首次启动设置管理员密码；已登录自动跳回控制台 |
| 控制台 | `http://<内网IP>:5000/` | 手动整理、定时整理、输出文件列表、日志、安全设置 |
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
- **抓取并整理**：先连内网 EPG 重新抓频道与节目单（`--no-sync` 固定带上），再整理。天数直接取页面上的「未来天数 / 回看天数 / 滑动窗口」设置（无需先保存定时设置），串行抓 EPG，首次补齐约 3~4 分钟，之后每次约 1~2 分钟。注意「立即整理」**不会**重新抓 EPG，改天数后必须点「抓取并整理」才会生效。

「定时整理」区可配置：

| 项 | 说明 |
|---|---|
| 启用开关 | 关掉后不再自动运行 |
| 模式 | `interval` 按分钟间隔（默认 360 分钟）/ `daily` 每天固定时间（如 04:30） |
| 未来天数 | 今天之后保留几天节目单（1~7，默认 3）。运营商实际只给 4~5 天，超出按实际可用天数抓取 |
| 回看天数（过去） | 保留过去几天的历史节目单（0~14，默认 7），需开启滑动窗口 |
| EPG 滑动窗口 | 开启后按天归档，每次更新删除最旧一天、顺延保留新一天；关闭则退回「只抓未来 N 天」 |
| 同时抓取 | 勾选 = 定时跑「抓取并整理」；不勾 = 定时只整理现有数据 |
| 启动后整理一次 | 服务启动 8 秒后自动跑一次 |

调度由 `app.py` 内置守护线程完成，**不依赖系统 crontab**，Windows / Linux / Docker 行为一致；页面实时显示下次运行时间、上次运行结果和日志。配置保存在 `config/organize.json`。

### EPG 滑动窗口：过去 7 天 + 今天 + 未来 3 天

默认窗口共 **11 天**，专为回看设计。抓取器把每一天的节目单单独存成 `output/epg_archive/YYYY-MM-DD.json`，再合并成完整的 `output/epg.xml`：

| 日期 | 数据来源 |
|---|---|
| 过去 7 天 | 本地按天归档（首次运行或缺档时才回补接口），**不再重复请求** |
| 今天 + 未来 3 天 | 每次更新都重新抓（节目单可能变动），并覆盖当天归档 |

每次定时更新的滚动过程：昨天的归档自动变成历史（顺延保留），窗口外最旧那天的归档文件被删除（删一天），新增的未来日期补抓一天。因此历史一旦落盘就稳定保留，不会因运营商后来查不到而丢失；关机几天后再启动会自动回补缺失日期（接口实测只保留最近约 7 天历史）。

- **首次启用**需补齐 11 天（约 3700 次请求、3~4 分钟），之后每次只抓 4 天（约 1400 次请求、1~2 分钟）。EPG 会话每 300 次请求自动重新认证，失败自动换会话重试；失败率 >20% 或零节目时任务报错并保留上一次的 `epg.xml`（先写 `.tmp` 再原子替换）。
- 归档目录在 `output/epg_archive/`，已被 `.gitignore` / `.dockerignore` 排除。**容器务必挂载 `output` 卷**，否则重启后归档丢失会重新补齐历史。
- 关闭「EPG 滑动窗口」即回到普通模式：只抓今天 + 未来 N 天，接口对第 5 天起返回空数据（`retCode` 仍为成功），这是运营商限制，不是抓取失败。

### 回看参数与播放器

**回看地址默认精简**。源 `hbtv.m3u` 里机顶盒下发的 `catchup-source` 带着 `rrsip`、`zoneoffset`、`icpid`、`accountinfo`、`it`、`r2h-seek-mode` 等十来个参数。其中 `accountinfo` / `it` 是带时间戳的一次性票据（会过期），`r2h-seek-mode=range(259200)` 会把回看锁死在 3 天。整理层默认把这些参数全部丢掉，只输出：

```text
catchup="default" catchup-days="7" catchup-source="http://<内网代理>/rtsp/.../xxx.smil?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"
```

实测代理会按 `playseek` 自行回源，去掉冗余参数后过去 7 天的回放都能正常播放。KODI 格式自动换成 `{utc:YmdHMS}-{utcend:YmdHMS}` 模板；DIYP、`channels.json` 以及全部 m3u/m3u8 产物同样精简。

`config/organize.json` 相关项：

| 配置 | 说明 |
|---|---|
| `catchup_clean` | 默认 `true` 精简；改成 `false` 恢复源里的完整参数 |
| `catchup_keep_params` | 精简时保留的参数名，默认 `["r2h-token"]`（仅当 rtp2httpd 需要令牌认证时才会出现） |
| `catchup_days_override` | 默认 `7`，`catchup-days` 与 EPG 的 7 天历史窗口对齐；改成 `0` 则按每个频道 `r2h-seek-mode` 的实际能力换算 |
| `proxy.rtp2httpd` / `proxy.rtsp_proxy` | 换回看地址的代理主机，与直播地址保持一致；当前默认 `http://192.168.123.2:5140` |

> **换环境必改**：当前项目默认代理为 `http://192.168.123.2:5140`。若你的 rtp2httpd / udpxy 在别的地址，请同时修改 `config/config.json` 的 `RTP2HTTPD`（抓取时生成源地址）和 `config/organize.json` 的 `proxy.rtp2httpd`（整理时替换直播与回看地址），然后点「抓取并整理」或「立即整理」重新生成产物。Web 动态参数 `?proxy=http://…` 也可在不改文件的情况下临时替换。

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

- 页面只用本地 `static/css/bootstrap.min.css`。
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
├── app.py                     单一 Flask 应用：登录认证 / 控制台 / 手动 / 定时 / 订阅 / 台标
├── organize.py                整理层入口（可独立 CLI 运行）
├── get_iptv_channels_V1.1.py  河北电信抓取器（原样复制）
├── lib/                       整理层：source / normalize / logo / exporters / epg / dynamic
├── logo/                      128 个本地台标
├── presets/                   lean / core 预设
├── config/
│   ├── config.json            机顶盒真实配置（本地，不入库）
│   ├── config.example.json    占位符示例
│   ├── auth.json              登录密码哈希 / 会话密钥 / 订阅令牌（运行时生成，不入库不进镜像）
│   └── organize.json          整理 + 定时调度配置
├── output/
│   ├── hbtv.m3u / hbtv.txt / epg.xml   抓取产物（11 天窗口合并结果，整理层只读输入）
│   ├── epg_archive/YYYY-MM-DD.json     按天归档，滑动窗口靠它删旧顺延
│   └── playlists/                        整理产物（12 个播放列表 + EPG + 清单）
├── templates/                 index / config / login 三个页面
├── static/css/app.css         统一视觉（本地，支持深浅色）
├── static/js/theme.js         主题切换 + 会话过期跳转
├── verify.py                  自检脚本
└── Dockerfile / start.sh      纯内网容器启动
```

---

## 六、常用命令

```bash
python organize.py                       # 只整理，不启服务
python organize.py --preset lean         # 精简列表
python organize.py --exclude "4K,慢直播" # 临时过滤
python verify.py                         # 全量自检（含认证 / 令牌 / 产物 / 调度）
PORT=8080 python app.py                  # 改端口
```

定时更新建议：在控制台开启「同时抓取」，间隔 360 分钟；或保持默认关闭，需要时手动点按钮。

---

## 七、安全提醒

- `config/config.json`、`output/`、`log/`、`*.pcapng` 含真实 UserID、MAC、STBID、`Authenticator`、回看 `accountinfo` 与内网地址，已被 `.gitignore` / `.dockerignore` 排除，**不要公开上传**。
- 服务默认监听 `0.0.0.0`，请只在可信内网使用；不要直接映射到公网。
- 台标图片来自参考项目，仅供个人学习使用。
