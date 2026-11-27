# 新数据集重新导入（2026-11-27）

本轮输入为用户指定的 `data/raw/SHDBZ S01E01.mkv`、`SHDBZ S01E01.srt` 和 `SHDBZ S01E01-EN.srt`。

- MKV 约 4.06 GB、1080p H.264 视频、DTS 音轨；本地原片保留。
- 输出 `data/processed/new-source/S01E01.mp4`：720p H.264、立体声 AAC，时长约 22:58.38，约 524 MB。此次为了兼容性和体积重新编码，画质会有变化，不是无损换封装。
- 按用户明确指定使用外部两份 SRT：双语文件提取含中文行，得到中文 421 条；英文文件得到英文 419 条。原文件不修改，处理结果统一 UTF-8。
- 曾提取内嵌字幕用于检查；内嵌双语文件仅作对照，不作为本轮最终输入。
- 为避免与旧素材混用，上传命令新增 `--folder` 参数，明确指定 prepared 文件夹；新视频哈希生成独立视频版本。
- 当前上传的是可播放的标准化 MP4，并非 4.06 GB 原 MKV 的云端备份。本地原片不能据此自动删除。
- 不沿用旧片源的 -1800ms 校准。当前时间轴已在 /review 抽查；用户反馈未发现需要纠错的问题，并已确认审核通过。

命令在 `services/backend` 运行：

```powershell
../../.venv/Scripts/python.exe -m app.storage_cli upload-episode --folder ../../data/processed/new-source
```

随后使用返回的新 version_id 执行 `app.corpus_cli import --version 新ID`。不复用旧视频版本 ID，不自动发布。

## 本轮结果

新视频版本为 `d48b2a8c-b61b-4813-955d-bafc473efb5c`，新字幕版本为 `9f920c7f-8dc9-4343-a889-e26024fbd5e8`。中文 421 条、英文 419 条，生成 417 个片段、20 个抽查点和 2 个机器标记的待关注片段。用户完成抽查后反馈未发现需要纠错的问题，Codex 已执行显式审核确认命令。

当前数据库状态：视频版本为 `published`，活动字幕修订为 `published`，417 个 segments 均已审核。用户明确确认该视频版本有预览/搜索授权；`allow_preview=true`。M4 验收时 `allow_share=false`、`allow_download=false`。2026-11-27，用户明确要求启用当前视频版本的分享权限；随后将该版本 `allow_share` 更新为 `true` 并核对审计记录。当前 `allow_download=false`，其他版本未修改，也没有因此自动创建分享链接。用户未提供截止时间，因此 `valid_to` 留空。应用尚未公网部署。字幕审核的审计记录 `public_publish=false` 表示字幕审核命令本身没有授予公开权利；之后的预览和分享授权均由用户单独确认并记录。自动报告的 `sync_verified` 仍为 `false`，因为导入程序没有自动音频漂移检测。

旧云端对象缺失已核实，3 条旧 assets 标记 deleted，旧视频标记 withdrawn；历史数据未物理删除，抽查列表排除撤下版本。

为减少云数据库往返，cue、segment 和关联改为批量写入。未完成的旧导入事务回滚后重试，新版本按独立事务提交。

前后端可通过 http://127.0.0.1:5173/review 查看本轮数据。原始 MKV 与两份 SRT 均保留。
