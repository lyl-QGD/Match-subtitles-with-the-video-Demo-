# 双语台词素材库demo

M1—M7 的本地 MVP 代码已实现，包括 Supabase 查询、R2 私有素材播放、双语字幕、片段分享与本机分享管理。M8 的备份、恢复和用量工具见 [M8 操作手册](docs/M8_OPERATIONS.md)；真实云端备份/恢复仍需你创建独立备份桶并安装 PostgreSQL 客户端工具后演练。项目没有本地 PostgreSQL，也没有公网部署。

## 环境

- Python 3.13（本机已检测到 3.13.15）。
- Node.js 24 LTS：本项目验证使用 `.tools` 下官方便携版，不修改系统安装；可自行安装 [官方 Node.js](https://nodejs.org/) 后使用系统 npm。
- FFmpeg 当前未检测到，M1 不需要；M4 前从 [官方入口](https://ffmpeg.org/download.html) 选择 Windows 构建，并将其 bin 加入 PATH，验证 `ffmpeg -version` 和 `ffprobe -version`。
- 不需要 Docker、本地数据库或云服务器。M2 注册 Supabase，M3 注册 R2，详见 [实现手册](docs/实现手册.md)。

## 安装与配置（PowerShell，在项目根目录）

```powershell
python -m venv .venv
.venv/Scripts/python.exe -m pip install -r services/backend/requirements.lock
Copy-Item .env.example .env
# 使用系统 Node 时：
npm.cmd ci --prefix apps/web
```

如果使用项目便携 Node，先在当前终端执行：

```powershell
$nodeDir = (Get-ChildItem .tools -Directory -Filter 'node-v*-win-x64' | Select-Object -First 1).FullName
$env:Path = "$nodeDir;$env:Path"
```

首次配置时才复制 .env，已有配置不要覆盖。M1 可不填任何云凭据；M2/M3 按手册填写后端 `.env`。不要把密码放入前端环境变量。

## 启动

打开两个终端，分别执行（仅本次进程允许执行项目脚本，不修改系统策略）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-api.ps1
```

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-web.ps1
```

浏览器访问 http://127.0.0.1:5173，API 文档 http://127.0.0.1:8000/docs。各终端按 Ctrl+C 停止服务。前端通过 Vite /api 代理访问后端，不需要开放跨域数据库访问。

## 验证

```powershell
Push-Location services/backend
../../.venv/Scripts/python.exe -m unittest discover -s tests -v
Pop-Location
npm.cmd run build --prefix apps/web
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/check-worker.ps1
```

- `/health/live` 返回 200，表示 API 进程可处理请求。
- `/health/ready` 实际检查 Supabase PostgreSQL 和 R2；未配置或不可达时返回 503，只返回缺少变量名和状态，不返回密钥。
- Worker `--check` 只检查前置条件，不下载或处理素材。

代码目录遵循手册第 6 节；云端业务数据保存在 Supabase Database / Cloudflare R2。M8 运维脚本与剩余云端准备步骤见对应手册；本地只保留代码、依赖和受限临时文件。


## M3 storage
See [M3 storage operations](docs/M3_STORAGE.md) for R2 check/upload/resume commands and required browser CORS setup. Upload tooling is CLI-only; no anonymous upload endpoint is exposed.


[👉 在线体验 Demo](https://match-subtitles-with-the-video-demo.netlify.app/)




