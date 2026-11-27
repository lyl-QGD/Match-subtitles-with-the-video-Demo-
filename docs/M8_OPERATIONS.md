# M8：备份、恢复、用量与交付操作手册

## M8 当前边界

- 后端备份范围是应用自己的 `dialogue` schema，包括台词、版本、许可、审核、分享和任务记录；不包含 Supabase 管理的 `auth` / `storage` 等 schema，也不包含 R2 视频和字幕文件本身。
- 备份使用 `pg_dump` custom archive，采用 AES-256-GCM 客户端加密后写入独立私有 R2 桶。数据库明文不会写入本地磁盘；加密临时文件在上传及远端校验后删除。
- 恢复脚本只接受另一个 Supabase 项目，并检查目标项目尚无 `dialogue` schema。不得把恢复目标指向当前项目。
- 不自动创建 Supabase/R2 资源、不自动升级套餐、不自动删除备份或素材。

## 1. 你需要在云端控制台做的准备

### 1.1 创建私有备份桶

在 Cloudflare Dashboard 打开 **R2 Object Storage**，创建 Standard 私有桶 `dialogue-mvp-backups`，保持 Public Access 关闭。这个桶与 originals、derived 分开；同账户的独立桶能降低误删应用桶的风险，但不能防护整个 Cloudflare 账户被删除或失陷。更高等级的备份隔离需要另一个账户或云厂商。

创建一组只授权该备份桶的 R2 API 凭据，权限选 **Object Read & Write**。备份程序需要列出、上传、下载并校验对象；不要给管理员级账户权限，也不要复用媒体上传凭据。此桶不需要浏览器 CORS。

### 1.2 生成并保管加密密钥

在项目根目录打开 PowerShell，运行：

```powershell
Push-Location services/backend
../../.venv/Scripts/python.exe -m app.backup_cli generate-key
Pop-Location
```

命令只生成一次随机密钥并显示在终端。先将它保存到密码管理器或其他独立的安全位置，再写入本机根目录 `.env` 的 `BACKUP_ENCRYPTION_KEY`。不要把它发到聊天、截图或 Git 仓库。丢失密钥后，云端密文无法恢复。

### 1.3 配置本机 `.env`

将 `.env.example` 中的 M8 变量复制到本机 `.env`，填入实际值：

| 变量 | 去哪里取得/用途 |
|---|---|
| `BACKUP_S3_ENDPOINT` | 可填 Cloudflare R2 的 S3 endpoint，通常与 `S3_ENDPOINT` 相同 |
| `BACKUP_S3_ACCESS_KEY_ID` / `BACKUP_S3_SECRET_ACCESS_KEY` | 刚创建的、限制在备份桶上的 R2 凭据 |
| `BACKUP_BUCKET` | `dialogue-mvp-backups` |
| `BACKUP_ENCRYPTION_KEY` | 上一步生成的 32 字节密钥；单独保存备份副本 |
| `CF_ACCOUNT_ID` / `CF_API_TOKEN` | 可选：Cloudflare 账户 ID 和仅有 Account Analytics Read 权限的 API token，用于自动记录 R2 请求、容量和流量 |

确认根目录 `.env` 已被 `.gitignore` 忽略。不得把真实值放进 `.env.example`、网页 `VITE_*` 变量或聊天消息。

## 2. 本机备份工具

### 前置条件

`pg_dump` 和 `pg_restore` 是 PostgreSQL 客户端命令，不会在本机启动数据库，也不会把云数据库迁到本地。当前开发环境没有检测到这两个命令；你需要自行安装 PostgreSQL 官方安装包中的 **Command Line Tools**，或将匹配的客户端工具目录加入当前终端 `PATH`。使用不低于 Supabase 服务端主版本的客户端。

新依赖 `cryptography` 已加入 `services/backend/requirements.lock`。在项目根目录首次安装或更新 Python 依赖：

```powershell
.venv/Scripts/python.exe -m pip install -r services/backend/requirements.lock
```

检查工具和变量是否齐全。输出只显示变量名和工具是否存在，不会显示密钥：

```powershell
Push-Location services/backend
../../.venv/Scripts/python.exe -m app.backup_cli preflight
Pop-Location
```

### 创建并检查备份

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/backup-database.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/list-backups.ps1
```

成功输出应为 `uploaded_and_verified`。脚本将 `pg_dump` 输出流式加密后上传，先检查 R2 对象大小，再流式读回密文核对 SHA-256，最后清理临时文件。每次备份会多一次完整对象读取，以换取远端校验；该请求与流量会出现在 R2 用量中。临时空间受 `TEMP_QUOTA_GB` 限制，默认最多 2 GB；超过限制时会停止，不会清理源素材。

每次备份生成唯一 object key，不覆盖旧备份。该版不自动执行保留策略或删除；按需在 Cloudflare 控制台检查和清理过期备份，并确保至少保留一份已验证可恢复的版本。

## 3. 在新 Supabase 项目做恢复演练

恢复会向目标项目写入表和数据。请只使用你确认可丢弃的**新建空项目**；新项目可能涉及套餐/计费条件，由你在 Supabase 控制台确认。脚本会拒绝源项目，也会拒绝已存在 `dialogue` schema 的目标。

1. 在 Supabase 创建一个用于演练的新项目，保存新项目数据库密码，从 Connect 面板取得连接 URI 和 Project Ref。
2. 检查新项目是否已启用 `pg_trgm` 扩展。若没有，在新项目 SQL Editor 运行：

   ```sql
   CREATE SCHEMA IF NOT EXISTS extensions;
   CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA extensions;
   ```

3. 临时将新项目的连接 URI 填入 `.env` 的 `RESTORE_DATABASE_URL`。不要替换当前 `DATABASE_URL`。
4. 从 `list-backups.ps1` 的结果复制一个 `object_key` 和新项目的 Project Ref，运行：

   ```powershell
   powershell -NoProfile -ExecutionPolicy Bypass -File scripts/restore-database.ps1 `
     -ObjectKey 'backups/postgres/dialogue/YYYY/MM/DD/时间-随机ID.dump.aesgcm' `
     -TargetProjectRef '新项目的ProjectRef' -ConfirmNewProject
   ```

5. 看到 `restored_and_verified` 后，在 Supabase Table Editor 选择 `dialogue` schema，确认台词、版本、许可和分享表存在；也可以把测试后端临时连到新项目，检查已知台词搜索。确认结束后从 `.env` 清除 `RESTORE_DATABASE_URL`。

恢复使用 SHA-256 和 GCM 认证校验密文/密钥，使用 PostgreSQL 单事务恢复。若进程中断，恢复事务回滚；若客户端连接在服务端提交结果后意外断开，重新检查目标 schema，再决定是否新建另一个空项目重试。

## 4. 记录数据库和 R2 用量

运行最近 7 天的用量报告：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/usage-report.ps1 -Days 7
```

报告包含 Supabase 当前数据库和 `dialogue` schema 大小。要自动取得每个 R2 桶的对象数、存储量、请求量及上传/下载字节，在 `.env` 配置 `CF_ACCOUNT_ID` 和 Cloudflare API token（权限为 Account Analytics Read）；最多查询 31 天。追加 `-Record` 会将 JSON 用量快照写到私有备份桶的 `ops/usage/` 前缀：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/usage-report.ps1 -Days 7 -Record
```

没有 Analytics token 时，报告会明确显示 `not_configured`，不会把缺失指标报成零。也可在 Cloudflare Dashboard 的 **R2 → 选桶 → Metrics** 查看请求/容量/流量；每月在 Supabase 项目用量与 Cloudflare Billing 页面核对账单。分析数据有聚合和时间范围限制，不代替账单。

参考官方说明：[Supabase 数据库备份](https://supabase.com/docs/guides/platform/backups)、[Cloudflare R2 Metrics 与 Analytics](https://developers.cloudflare.com/r2/platform/metrics-analytics/)。

## 5. 启动、停止与 M8 验收

- 启动 API：在一个终端运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-api.ps1`；停止时在该终端按 `Ctrl+C`。
- 启动前端：另一个终端运行 `powershell -NoProfile -ExecutionPolicy Bypass -File scripts/start-web.ps1`；停止时按 `Ctrl+C`。
- 做当前云资源烟雾检查：`powershell -NoProfile -ExecutionPolicy Bypass -File scripts/m8-smoke.ps1`。它核对 API ready、双语已知词搜索、9 个随机素材、授权播放与 R2 `Range 206`，不写业务记录。
- 默认检查还会将签名参数故意改错，确认 R2 拒绝请求，再向 API 申请新签名并确认恢复 `Range 206`。加 `-ShareWrite` 会创建一条真实测试分享、读取解析、验证 Range 播放、撤销并确认撤销生效。Supabase 会保留这条已撤销的测试分享/clip 行作记录；只有明确需要验证分享写链路时才运行。
- 数据库不可达的 503、搜索错误不伪装成空结果、签名恢复/分享过期等异常由后端单元测试验证。不会为测试实际暂停 Supabase 项目或断开当前服务；人工暂停可能中断你正在使用的页面。
- 重复导入演练：在 `services/backend` 运行 `../../.venv/Scripts/python.exe -m app.corpus_cli import --version d48b2a8c-b61b-4813-955d-bafc473efb5c`。已完成的同一版本应返回 `already_imported`，不会重复插入 cue/segment。
- 前端手工验收：用户在浏览器确认选择连续台词、重置选择、时间微调、播放结束暂停、截图下载及分享页双语显示。自动构建不代替真实交互检查。

## M8 目前验收状态

2026-11-27 已完成：40 项后端单元测试、前端 production build、云端只读烟雾检查、一次创建后立即撤销的分享写入闭环、重复导入幂等检查。实时报告读到数据库约 14.7 MiB、`dialogue` schema 约 3.5 MiB；R2 Analytics 尚未配置。分享管理列表现有一条活动分享和一条已撤销分享；活动项早于本轮测试创建，未对它做任何修改。

尚未完成真实云端备份/恢复演练：当前工作环境缺少 `pg_dump`、`pg_restore`，备份桶与专用凭据也需要由你在 Cloudflare 创建。创建资源、安装客户端软件和选择可能收费的 Supabase 演练项目均由你决定。浏览器中的片段选择和截图下载仍需你按验收项手工确认。
