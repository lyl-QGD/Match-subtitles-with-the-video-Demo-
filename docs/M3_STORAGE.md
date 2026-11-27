# M3：R2 上传与验收

## 本次验收结果

- MP4 与两份 SRT 已上传，数据库登记 3 个 assets、2 条字幕轨和 1 个未发布视频版本。
- 视频分片主动暂停后成功续传；字幕全量 SHA-256 通过，视频大小与首/中/尾抽样通过，视频全量哈希尚未完成。
- 签名 Range 读取通过，未签名读取被拒绝。
- 用户保存 CORS 后复验通过：两个桶分别允许 localhost:5173 和 127.0.0.1:5173，共 4 项跨域预检均成功。
- 后续 M4 进行字幕解析、对齐、同步审核；M6 再验收实际网页播放器。当前不是完整播放与搜索产品。

已提供本地管理员命令，不向匿名网页开放上传和签名接口。数据库登记后仍保持未发布，字幕尚未解析、同步尚未核实，不自动授权公开播放。

## 使用方式

在项目根目录打开 PowerShell：

```powershell
cd services/backend
../../.venv/Scripts/python.exe -m app.storage_cli check
../../.venv/Scripts/python.exe -m app.storage_cli upload-episode
```

当前导入命令针对 `data/processed/S01E01.mp4` 及同目录一份 `*.en.srt`、一份 `*.zh.srt`。不是通用导入界面，其他季集的配置入口将在导入阶段实现。

上传以 16 MiB 分块，进度记录在被 Git 忽略的 `data/tmp/r2-resume`；记录不含密钥或签名链接。中断后再次执行同一命令，向 R2 查询已完成块并继续；不要在任务完成前删除恢复记录。远端已经存在且哈希/大小匹配时跳过上传，避免覆盖。文件哈希改变后使用不同对象 key。

上传后检查 HEAD 大小/元信息；字幕流式读取远端全文核对 SHA-256，视频默认核对首、中、尾字节抽样。元信息中的哈希由上传端提供，不等于存储服务重新计算的哈希。需要视频端到端完整校验时执行 `python -m app.storage_cli upload-episode --full-verify`，它会流式回读整集，耗时取决于下载速度，不保存另一份本地视频。验证短期签名 Range GET 返回 206，未签名 HEAD 不能成功；这些检查不代表控制台其他公开域名也已关闭，仍须检查 Public Development URL 和 Custom Domains。

全部文件验证通过后，在一个数据库事务内创建/复用 series、episodes、assets、video_versions、rights 和 subtitle_tracks。失败会回滚数据库事务；已完成的云端对象保留，便于重试，不自动删素材。上传完成但数据库失败时重新执行命令即可。

## 你需要在 Cloudflare 配置 CORS

对两个桶分别执行：R2 → 选择桶 → Settings → CORS Policy → 编辑，粘贴 [r2-cors.json](../infra/r2-cors.json) 并保存。当前凭据只有对象读写权限，不能据此自动修改桶配置。

配置只允许本地两个前端来源；上线后更新实际域名。不要启用公开访问来修复 CORS。浏览器跨域验收需要先保存该配置；命令行上传不经过浏览器，因此 CLI 成功不能证明浏览器 CORS 已完成。

保存后运行 `python -m app.storage_cli cors-check`，两个桶、两个来源共 4 项均为 cors_ok=true 才通过跨域预检。此命令不替代之后实际播放器的浏览器验收。

## 安全与费用

上传和完整性下载会产生 R2 请求；存储占用增加，按账户额度计费。不会自动升级套餐或删除原片。建议在桶的 Lifecycle Rules 中添加自动中止超过 7 天的未完成 multipart uploads，避免中断上传长期占用空间；规则只针对未完成分片，不能设置为删除 originals。

`/health/ready` 现在分别探测数据库和两个桶，均通过才返回 200。健康检查只证明访问可用，不证明字幕同步、版权许可和素材质量。
