# HBTV All-in-One · 河北电信 IPTV 抓取 + 整理 + 内网订阅

[![访问量](https://hits.sh/github.com/czmayue2008/IPTV-channels-hebei-allinone.svg?label=%E8%AE%BF%E9%97%AE&color=4f46e5)](https://github.com/czmayue2008/IPTV-channels-hebei-allinone)
![stars](https://img.shields.io/github/stars/czmayue2008/IPTV-channels-hebei-allinone?color=4f46e5&label=stars)
![forks](https://img.shields.io/github/forks/czmayue2008/IPTV-channels-hebei-allinone?color=4f46e5&label=forks)
![last commit](https://img.shields.io/github/last-commit/czmayue2008/IPTV-channels-hebei-allinone?label=last%20commit)
![docker pulls](https://img.shields.io/docker/pulls/czmayue2008/iptv-channels-hebei-allinone?label=docker%20pulls)
![python](https://img.shields.io/badge/python-3.11-blue)

一个跑在自己内网里的容器：**登录机顶盒、抓频道与节目单、按常规 IPTV 分组整理成十几种播放器格式、用手机电视直接订阅**。全程只访问你自己的内网地址，不依赖任何外部服务。

> 本项目由 HY4 构建，DeepSeek改写，把两段来自不同仓库的能力合成一个容器，完整开源，欢迎反馈问题：
>
> | 模块 | 来源 | 负责什么 |
> |---|---|---|
> | 抓取层 | `IPTV-channels-hebei` | 河北电信认证、频道列表与 EPG 抓取、机顶盒参数配置 |
> | 整理层 | `hbtv-playlist` | 名称规范化、分组去重、台标匹配、`tvg-id`、多播放器格式、动态参数 |

---

## 目录

- [为什么需要它](#为什么需要它)
- [功能一览](#功能一览)
- [快速开始](#快速开始)
- [四条上手路径](#四条上手路径)
- [控制台](#控制台)
- [频道分组规则](#频道分组规则)
- [台标：内置 128 个 + 自己上传](#台标内置-128-个--自己上传)
- [健康检查](#健康检查)
- [订阅地址与动态参数](#订阅地址与动态参数)
- [定时整理与 EPG 滑动窗口](#定时整理与-epg-滑动窗口)
- [登录认证与订阅令牌](#登录认证与订阅令牌)
- [纯内网 / 零外部地址](#纯内网--零外部地址)
- [目录结构](#目录结构)
- [常用命令](#常用命令)
- [常见问题](#常见问题)
- [安全提醒](#安全提醒)

---

## 为什么需要它

运营商下发的 `hbtv.m3u` 和 `epg.xml` 能播，但直接用很别扭：

- **分组很乱**——原文件只按「央视 / 卫视 / 数字 / 其他」粗分，几百个频道全堆在「其他」里；
- **名字不统一**——`CCTV-1 综合高清`、`CCTV1综合`、`cctv1` 是同一个台，播放器却认成三个；
- **台标和节目单对不上**——`tvg-logo`、`tvg-id` 大量为空，EPG 挂不上；
- **回看地址带一次性票据**——`accountinfo`、`it` 会过期，`r2h-seek-mode=range(259200)` 把回看锁死 3 天；
- **每种播放器要一种格式**——KODI、APTV、TVBox、DIYP、udpxy 各有各的写法。

这个项目做的就是这些事：抓一次，整理成**分组干净、名字统一、台标全、EPG 对得上、回看 7 天**的成品，然后由内置 Web 服务在内网提供订阅——播放器只要填一个地址，换 IP、换端口、换代理都不用重新生成。

## 功能一览

| 能力 | 说明 |
|---|---|
| **抓取** | 按机顶盒参数（`UserID` / `MAC` / `STBID` / `Authenticator`）登录内网 EPG，抓频道表与节目单，固定带 `--no-sync`，不向任何外部仓库同步 |
| **整理** | 8 个常规分组、名称规范化、重复频道去重、4K 归并到对应 SD 台的 `tvg-id`、128 个台标自动匹配 |
| **多格式** | 一次产出 12 个播放列表：KODI / APTV / TVBox / udpxy / rtp2httpd / DIYP / 极简，每个 `.m3u8` 都带一份同名 `.m3u` |
| **回看** | 默认精简回看地址（丢掉过期票据），`catchup-days=7` 与 EPG 历史窗口对齐，KODI 自动换时移模板 |
| **订阅** | 内置 Web 服务直接对外提供 m3u / EPG / 台标，台标与 EPG 按**当前访问主机**自动补全绝对地址 |
| **台标** | 内置 128 个，控制台可批量上传自己的图；文件名即频道名，上传后自动整理即刻生效 |
| **调度** | 进程内守护线程，按间隔或每天定时；支持「只整理」与「先抓再整理」，不写 crontab |
| **健康检查** | `/health` 一页体检：服务、数据与产物、节目单覆盖、任务与计划、配置与连通性，附 `?format=json` 给监控用 |
| **安全** | 首次访问自己设密码（PBKDF2 哈希）、7 天会话、5 次错误锁定 60 秒、可选订阅令牌保护 |
| **零外部依赖** | 运行时只有 Flask + requests；CSS、图标、台标、字体全部本地，页面不引用任何 CDN |

## 快速开始

### 直接拉公开镜像（推荐）

```bash
docker run -d --name hbtv -p 5000:5000 \
  -v /your/path/config:/app/config \
  -v /your/path/output:/app/output \
  --restart unless-stopped \
  czmayue2008/iptv-channels-hebei-allinone:latest

# 也可用 GHCR（同一份镜像，国内网络可任选其一）
# docker pull ghcr.io/czmayue2008/iptv-channels-hebei-allinone:latest
```

镜像里**不含任何真实机顶盒参数**。首次启动会从 `config.example.json` 生成占位配置，打开 `http://<内网IP>:5000/config` 填自己的参数即可。

> **务必挂载 `output` 卷**：`output/epg_archive/` 是 EPG 按天归档，不挂载的话每次重启都要重新回补 11 天历史。
>
> 挂载卷里的 `organize.json` 会盖住镜像内的新版规则。分组规则带 `groups_version`，镜像内置版本更高时，升级后首次整理会自动只替换 `groups` 一项（代理、定时、过滤词保留）；想完全自己维护，把它改成 `"manual"` 就不再自动升级。

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

绑定 `0.0.0.0`，同一局域网的手机、电视、盒子都能访问。

## 四条上手路径

1. **设密码**：首次访问 `http://<内网IP>:5000/` 会跳到 `/login`，自己设置管理员密码（至少 8 位）。容器部署也可以用 `-e HBTV_PASSWORD='你的强密码'` 跳过这一步。
2. **填机顶盒参数**：进 `/config`，填 `UserID` / `MAC` / `STBID` / `Authenticator`（这四项是抓取脚本真正校验的必填项），保存。
3. **抓取并整理**：回控制台点「抓取并整理」。首次要补齐 11 天节目单，约 3~4 分钟；之后每次 1~2 分钟。
4. **订阅**：打开「订阅中心」`/playlists/`，按播放器复制对应地址，贴进 APTV / KODI / TVBox / DIYP 即可。

## 控制台

打开 `/` 就是控制台，卡片按「能干什么」从上到下排：

| 卡片 | 内容 |
|---|---|
| 顶部统计 | 频道数、EPG 节目数、播放列表文件数、当前任务状态 |
| 手动整理 | 「立即整理」（只用现有数据，约 1 秒）/「抓取并整理」（先连内网抓再整理）、上次与下次运行时间 |
| 定时整理 | 启用开关、间隔或每日固定时间、EPG 天数与滑动窗口、是否同时抓取 |
| 整理设置 | udpxy / rtp2httpd 代理、预设（`lean` / `core`）、排除关键字、列表名、是否生成 `.m3u` 别名 |
| 运行日志 | 滚动输出，任务结束显示「整理完成」与生成清单；换任务自动清屏重来 |
| 安全设置 | 改密码、开关订阅令牌保护、重置令牌 |
| 内网访问地址 | 控制台 / 订阅中心 / APTV / KODI / EPG / 台标的完整地址，随当前访问的 IP 或域名自动变化；是否带 `?token=` 由「安全设置」里的开关决定 |
| 台标管理 | 批量上传、逐个改名、还没台标的频道点一下就能选图 |
| 输出文件 | 默认收起，展开后可逐个打开或复制地址，也能一键复制全部 m3u 文件名 |
| 频道预览 | 整理后的频道与分组，确认分对了再去订阅 |

右上角还有**主题切换**（深浅色，记在浏览器里）和**退出登录**；「健康检查」在页头。

## 频道分组规则

默认 8 个常规分组 + 1 个兜底，`config/organize.json` 里可改：

| 分组 | 命中方式 | 实测数量 |
|---|---|---|
| 央视频道 | 名称含 `CCTV` / `CGTN` | 34 |
| 卫视频道 | 省级卫视、`卫视` 关键字 | 50 |
| 河北本地 | 河北 / 石家庄 / 各市台 | 34 |
| 河北县级 | 正则匹配县级台名 | 99 |
| 少儿频道 | 卡通 / 少儿 / 动漫 | 7 |
| 数字频道 | 体育、影视、纪实、生活时尚等专业数字频道 | 39 |
| 购物频道 | 购物 / 优购物 / 家家购物等 | 7 |
| 测试与直播室 | 测试卡、直播室、慢直播 | 72 |
| 其他频道 | 以上都没命中的兜底（空规则，正常应为 0） | 0 |

规则按顺序匹配：`keywords`（包含即命中）→ `regex`（正则）→ 都不中落到「其他频道」。**不做过细拆分**——体育休闲、影视、纪实文化、生活时尚这些统一归到「数字频道」，跟机顶盒里的分组习惯一致，减少滚动。

## 台标：内置 128 个 + 自己上传

- **内置库**在 `logo/`，随项目走，不联网。
- **自己上传**：控制台「台标管理」支持一次选多张（png / jpg / webp / gif，单张 ≤ 2 MB），上传前可以逐个改频道名。**文件名就是频道名**，比如 `井陉电视.png`；不合法的字符（`\ / : * ? " < > |`）会被自动去掉。
- 上传的图放在 `config/logos/`（已被 `.gitignore` / `.dockerignore` 排除），**优先级高于内置库**：同名时用你上传的那张，即便扩展名不同。
- 勾选「上传后自动整理」就会立刻重新生成播放列表和 EPG，生效不用等定时任务。
- 「还没有台标的频道」折叠区列出现存缺口，点频道名即可为它单独选一张图。

## 健康检查

`/health` 从原来的裸 JSON 改成了一页体检报告，五个分区、每项都写明「值 + 状态 + 建议怎么办」：

| 分区 | 查什么 |
|---|---|
| 服务 | 服务运行状态、当前时间 |
| 数据与整理结果 | 源频道表、源节目单的频道/节目数与更新时间；整理产物概况；整理结果是否落后于源数据 |
| 节目单覆盖 | 输出节目单的覆盖范围（起止日期）、按天的节目条数（哪天缺一眼看出）、今天的节目数 |
| 任务与计划 | 当前任务、上次任务是成功还是失败（失败附原因）、下次定时时间 |
| 配置与连通性 | 组播代理、认证服务器、FCC 等内网地址的 TCP 连通性（并行探测，有超时上限），机顶盒必填项是否齐全，分组规则版本状态 |

- 状态分 `ok` / `warn` / `fail` / `info` / `off`，顶部汇总成三项计数。
- **不自动刷新**——右上角「刷新」按钮点一次查一次，避免后台一直轮询。
- 给监控探活用 `/health?format=json`，返回整体状态与三项计数：`{"ok": true, "status": "ok", "counts": {...}}`，脚本里判断 `ok` 即可。
- 本页**免登录**：未登录时只核对「服务是否在运行」，频道数、代理地址这些细节要登录后才展示。
- 扫描 8 MB 的 `epg.xml` 用分块解析，不做整树加载；`<programme>` 统计用分块计数，不会把内存打满。

## 订阅地址与动态参数

| 用途 | 地址 |
|---|---|
| 登录 / 初始化 | `http://<内网IP>:5000/login` |
| 控制台 | `http://<内网IP>:5000/` |
| 机顶盒配置 | `http://<内网IP>:5000/config` |
| 订阅中心 | `http://<内网IP>:5000/playlists/` |
| 播放列表 | `http://<内网IP>:5000/playlists/hbtv_aptv.m3u` |
| EPG | `http://<内网IP>:5000/playlists/epg.xml`（另有 `.gz`） |
| 台标 | `http://<内网IP>:5000/logo/CCTV1.png` |
| 抓取原始产物 | `http://<内网IP>:5000/output/hbtv.m3u` |

**台标和 EPG 用当前访问主机自动补全**：产物里写的是根相对地址 `/logo/xxx.png`、`/playlists/epg.xml`，服务返回播放列表时按请求的 Host 补成绝对地址。用 `192.168.1.10`、`hbtv.local` 或端口映射访问都能正确拿到台标与节目单，**无需重新生成**。

对所有 `.m3u` / `.m3u8` 生效、不用重新整理的动态参数：

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

### 整理完成后生成的 12 个文件

```
hbtv_kodi.m3u        hbtv_kodi.m3u8       KODI / IPTV Simple / ffmpegdirect
hbtv_aptv.m3u        hbtv_aptv.m3u8       APTV / mytv-android / 完美解码
hbtv_tvbox.m3u       hbtv_tvbox.m3u8      TVBox / Fileball / 通用
hbtv_udpxy.m3u       hbtv_udpxy.m3u8      udpxy / msd_lite 组播转单播
hbtv_rtp2httpd.m3u   hbtv_rtp2httpd.m3u8  rtp2httpd 增强版
hbtv_diyp.txt                            DIYP / 影视仓（#genre# 分组）
hbtv_simple.txt                          极简 名称,地址
```

另有 `epg.xml`、`epg.xml.gz`、`channels.json`、`manifest.json`、`index.html`，全部位于 `output/playlists/`。

### 回看地址与播放器

源 `hbtv.m3u` 的 `catchup-source` 带着 `rrsip`、`zoneoffset`、`icpid`、`accountinfo`、`it`、`r2h-seek-mode` 等十来个参数。其中 `accountinfo` / `it` 是带时间戳的一次性票据（会过期），`r2h-seek-mode=range(259200)` 把回看锁死 3 天。整理层默认全部丢掉，只输出：

```text
catchup="default" catchup-days="7" catchup-source="http://<内网代理>/rtsp/.../xxx.smil?playseek=${(b)yyyyMMddHHmmss}-${(e)yyyyMMddHHmmss}"
```

实测代理会按 `playseek` 自行回源，去掉冗余参数后过去 7 天的回放都能正常播放。KODI 格式自动换成 `{utc:YmdHMS}-{utcend:YmdHMS}` 模板；DIYP、`channels.json` 以及全部 m3u/m3u8 产物同样精简。

| 配置项 | 说明 |
|---|---|
| `catchup_clean` | 默认 `true`；改 `false` 恢复源里的完整参数 |
| `catchup_keep_params` | 精简时保留的参数名，默认 `["r2h-token"]` |
| `catchup_days_override` | 默认 `7`，与 EPG 历史窗口对齐；改 `0` 则按每个频道 `r2h-seek-mode` 的实际能力换算 |
| `proxy.rtp2httpd` / `proxy.rtsp_proxy` | 回看地址的代理主机，与直播地址一致 |

## 定时整理与 EPG 滑动窗口

「定时整理」可配置：启用开关、模式（`interval` 按分钟 / `daily` 每天固定时间）、未来天数（1~7，默认 3）、回看天数（0~14，默认 7）、是否开启滑动窗口、是否同时抓取、启动后是否整理一次。

调度由 `app.py` 内置守护线程完成，**不依赖系统 crontab**，Windows / Linux / Docker 行为一致；页面实时显示下次运行时间、上次结果与日志。配置存在 `config/organize.json`。

默认窗口 **11 天**（过去 7 + 今天 + 未来 3），专为回看设计。抓取器把每天存成 `output/epg_archive/YYYY-MM-DD.json`，再合并成 `output/epg.xml`：

| 日期 | 数据来源 |
|---|---|
| 过去 7 天 | 本地按天归档，首次运行或缺档才回补接口，之后不再重复请求 |
| 今天 + 未来 3 天 | 每次更新都重抓（节目可能变动），并覆盖当天归档 |

每次滚动：昨天的归档变历史（顺延保留），窗口外最旧那天被删掉，新增的未来日期补抓一天。历史一旦落盘就稳定保留，不会因为运营商后来查不到而丢失；关机几天再启动会自动回补缺失日期（接口实测只保留最近约 7 天历史）。

- **首次启用**约 3700 次请求 / 3~4 分钟，之后每次约 1400 次请求 / 1~2 分钟。EPG 会话每 300 次请求自动重新认证，失败自动换会话重试；失败率 > 20% 或零节目时任务报错，并**保留上一次的 `epg.xml`**（先写 `.tmp` 再原子替换）。
- 关闭滑动窗口就回到普通模式：只抓今天 + 未来 N 天。接口对第 5 天起返回空数据（`retCode` 仍为成功），这是运营商限制，不是抓取失败。

## 登录认证与订阅令牌

首次访问自动跳 `/login` 让**你自己设置管理员密码**（至少 8 位）。之后控制台、机顶盒配置页、所有管理接口都需要登录；会话默认 7 天，密码连续错 5 次锁定 60 秒。

- 密码用 PBKDF2 哈希存到 `config/auth.json`，同文件还保存会话密钥与订阅令牌。**该文件已被 `.gitignore` / `.dockerignore` 排除**，页面和代码里也没有默认密码。
- 忘记密码：删除 `config/auth.json` 重启服务，重新设置即可（**机顶盒配置不受影响**）。

**关于播放器**：m3u、台标、EPG 默认**免登录**——播放器和盒子不会带浏览器 Cookie，强制登录会让所有订阅失效。想连订阅资源一起保护，就在「安全设置」里打开**订阅令牌保护**：

| 状态 | 播放列表 / 台标 / EPG | 控制台 |
|---|---|---|
| 默认（关闭） | 局域网内直接可访问，地址不带 `?token=` | 必须登录 |
| 开启令牌保护 | 需带 `?token=…`，控制台与订阅中心复制的地址会自动附带 | 必须登录 |

开启后播放列表内部的台标与 EPG 绝对地址也会自动带上令牌，**复制一个地址就够用**；令牌可一键重置，旧地址立即失效。令牌等同于访问凭据，别把带令牌的地址发到公网。

## 纯内网 / 零外部地址

- 页面只用本地 `static/css/bootstrap.min.css`，图标是本地生成的 `static/favicon.png`。
- 台标 `logo/` 随项目本地存放，由 `/logo/` 路由提供；播放列表里的台标与 EPG 地址按访问主机动态补全。
- 抓取脚本固定 `--no-sync`，不会向外部 Git 仓库同步；配置页也不提供 Git 相关字段。
- 抓取只访问 `config/config.json` 里的 `AuthenticationIP`（内网 EPG）；整理层完全离线，只用 Python 标准库。
- 定时任务是进程内线程，不写 crontab、不启 cron 服务。
- `python verify.py` 会扫描产物与模板里的外部地址，提交前跑一遍即可。

> 生成的播放地址指向你自己的 rtp2httpd / udpxy 组播代理，需在能访问 IPTV 专网的环境里才能播放；EPG 与频道数据仅在内网有效。

## 目录结构

```
IPTV-channels-hebei-allinone/
├── app.py                     单一 Flask 应用：登录认证 / 控制台 / 手动 / 定时 / 订阅 / 台标 / 健康检查
├── organize.py                整理层入口（可独立 CLI 运行）
├── get_iptv_channels_V1.1.py  河北电信抓取器（原样复制）
├── lib/
│   ├── source.py              读取源 m3u / EPG
│   ├── normalize.py           名称规范化、tvg-id、去重
│   ├── logo.py                台标索引（内置库 + 上传目录，上传优先）
│   ├── exporters.py           7 类播放器格式 + channels.json
│   ├── epg.py                 EPG 重建与别名
│   ├── dynamic.py             动态订阅参数改写
│   └── health.py              健康检查报告生成
├── logo/                      128 个内置台标
├── presets/                   lean / core 预设
├── config/
│   ├── config.json            机顶盒真实配置（本地，不入库）
│   ├── config.example.json    占位符示例
│   ├── auth.json              密码哈希 / 会话密钥 / 订阅令牌（运行时生成）
│   ├── logos/                 控制台上传的台标（运行时生成）
│   └── organize.json          整理 + 定时调度配置
├── output/
│   ├── hbtv.m3u / hbtv.txt / epg.xml   抓取产物（整理层只读输入）
│   ├── epg_archive/YYYY-MM-DD.json     按天归档，滑动窗口靠它删旧顺延
│   └── playlists/                      整理产物（12 个播放列表 + EPG + 清单）
├── templates/                 index / config / login / health 四个页面（订阅中心是整理时生成的 index.html）
├── static/css/app.css         统一视觉（本地，支持深浅色）
├── static/js/theme.js         主题切换 + 会话过期跳转
├── static/favicon.png         站点图标（本地生成）
├── verify.py                  自检脚本（127 项）
└── Dockerfile / start.sh      纯内网容器启动
```

## 常用命令

```bash
python organize.py                       # 只整理，不启服务
python organize.py --preset lean         # 精简列表
python organize.py --no-epg              # 不重建节目单（源 EPG 缺失时用）
python organize.py --exclude "4K,慢直播" # 临时过滤
python verify.py                         # 全量自检（认证 / 令牌 / 产物 / 调度 / 图标）
PORT=8080 python app.py                  # 改端口
```

定时更新建议：在控制台开启「同时抓取」，间隔 360 分钟；或保持默认关闭，需要时手动点按钮。

## 常见问题

**改完分组规则，页面上的分类没变？**
先确认点的是「立即整理」。分组来自 `config/organize.json` 的 `groups`，只有重新整理才会写进产物；如果 `groups_version` 比镜像内置的低，升级后首次整理会自动覆盖 `groups`（其余设置不动），想自己维护就改成 `"manual"`。

**订阅地址能打开，播放器却没台标 / 没节目单？**
看播放列表里 `tvg-logo`、`tvg-url` 是不是绝对地址。用非标准端口或反向代理时，确认 `Host` 头被正确透传；实在不行用 `?logo=`、`?epg=` 参数手动指定。

**回看只有 3 天，或者提示失效？**
默认已经是 `catchup-days="7"` 并去掉了 `r2h-seek-mode` 限制。若仍只有 3 天，检查是否把 `catchup_clean` 改成了 `false`（那样会把 `range(259200)` 带回产物）。

**「立即整理」后节目单没更新？**
这是设计如此。「立即整理」只读现有 `output/epg.xml`，改 EPG 天数必须点「抓取并整理」。

**容器重启后历史节目单没了？**
`output` 卷没挂。`output/epg_archive/` 丢失后需要重新回补 11 天。

**忘记管理员密码？**
删除 `config/auth.json` 并重启，重新设置即可，机顶盒配置与产物都不受影响。

**README 顶部的「访问」数字是怎么来的？**
来自第三方计数服务 `hits.sh`：这张徽标每次被拉取就 +1，GitHub 会把图片缓存一段时间，所以它偏保守、也有延迟，只能看个大概趋势。真正准确的数字在仓库自己的 **Insights → Traffic**（只有仓库所有者能看，保留最近 14 天，另有 clone 数、来源站点和热门页面）。

## 安全提醒

- `config/config.json`、`config/auth.json`、`config/logos/`、`output/`、`log/` 含真实 UserID、MAC、STBID、`Authenticator`、回看票据与内网地址，已被 `.gitignore` / `.dockerignore` 排除，**不要公开上传**。
- 服务默认监听 `0.0.0.0`，请只在可信内网使用，不要直接映射到公网。
- 台标图片来自参考项目，仅供个人学习使用。
