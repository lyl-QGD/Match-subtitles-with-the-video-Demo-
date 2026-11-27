# M2 实施与验收

已实现后端数据库驱动、TLS（sslmode=require）、最多 3 条连接、连接/查询超时和真实数据库检查。接口不会返回密码或底层数据库错误文本。TLS require 保证传输加密，不等同于证书主机名校验；生产可进一步部署 CA 与 verify-full。

## 先保存连接配置

检查时磁盘上的 `.env` 中 DATABASE_URL 为空。如果已在编辑器填写，请按 Ctrl+S。不要发送连接字符串；此前在聊天中出现过的密码应先重设。恢复了 `.env.example` 空白模板，但未覆盖 `.env`。

从 services/backend 目录运行：

```powershell
../../.venv/Scripts/python.exe -m app.db_check
```

成功时应有 status=ok、table_count=19、version_001=true、rls_enabled=true、external_access_blocked=true、required_indexes_present=true。restricted_runtime_role=false 表示当前仍在使用管理账号，需完成下面的账号分离。

## Alembic 接管手动建表

先通过只读结构检查，再执行：

```powershell
../../.venv/Scripts/python.exe -m alembic upgrade head
../../.venv/Scripts/python.exe -m alembic current
```

该基线仅接受已有 `001` 结构，不重复创建业务表；创建 dialogue.alembic_version 记录迁移版本。因此原业务表仍为 19 张，表浏览器总数将是 20 张。没有已有 schema 时先执行 001_schema.sql。当前检查覆盖必需表、版本、RLS、API 角色 schema 权限和关键索引，不代表所有字段与业务约束已逐项比对。

## 账号分离（仍需完成）

按 README 执行 003_runtime_role.sql.example，并设置独立强密码。将管理连接保存在本机 `MIGRATION_DATABASE_URL`，把 `DATABASE_URL` 改为 dialogue_backend 账号的连接。Session pooler 的用户名通常还需带项目标识，按项目连接说明填写，不改变原 host/port。再次运行 db_check，确认 restricted_runtime_role=true。

这一步未自动执行：目前磁盘配置为空，尚无法连接，更没有创建云端账号或运行迁移。脚本中的管理员账号和密码不应放入前端。后续新增表的权限和策略必须在新增迁移中明确配置，不自动授权所有未来表。

## 健康检查与剩余验收

重启 API 后 `/health/ready` 会分别返回 database 与 storage 状态。数据库通过后，R2 尚未配置或未实现检查仍返回整体 503，这是正常的；不是数据库失败。

剩余：真实云端连接、迁移接管、独立账号验证，以及写入测试记录后关闭并重开应用的持久性检查。取得可用本机配置后继续这些操作，不因单元测试通过就宣称云端验收完成。
