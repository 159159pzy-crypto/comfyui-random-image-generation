# Anima Studio V7 数据迁移与回滚

## 数据所有权

- `data/history.sqlite3` 继续保存作品和原文件引用；V7 增加 `intent_id`、`source_workspace` 和 `intent_json` 规范元数据。
- `data/studio.sqlite3` 保存 `drafts`、`presets`、`intents`、`studio_events`、弃用调用审计和持久化任务账本。
- `data/style_presets.json`、`data/natural/` 与其他旧 JSON 只作为迁移来源，迁移成功后仍保留，不删除、不覆盖。

## 启动迁移

1. 在任何 V7 写连接创建前调用 `prepare_v7_backup`。
2. SQLite 文件使用 SQLite Backup API 创建事务一致快照，包含活动 WAL；普通文件使用复制并校验 SHA-256。
3. 固定备份目录为 `data/backups/v6-pre-v7/`，完成标记为 `data/migrations/v7.json`。
4. 初始化 V7 幂等 Schema，再把旧风格预设规范化为 Generation Intent 并写入正式 `presets` 表。
5. 标记同时保存 `schema_version=7` 和内部 `migration_revision`。旧修订标记会执行补偿迁移，不会覆盖已验证备份。
6. 相同迁移连续执行返回相同报告；主键和唯一约束防止重复导入。

## 实机验收基线

2026-08-05 的受控迁移验收结果：

| 项目 | 迁移结果 |
| --- | ---: |
| 作品 | 940 |
| 历史批次 | 243（随机 205、自然语言 38） |
| 风格预设 | 7 |
| Provider 档案 | 1 |
| Provider 绑定引用 | 2 |
| 密钥档案引用 | 1 |

`history.sqlite3`、`studio.sqlite3` 及两份备份的 `PRAGMA quick_check` 均为 `ok`。连续执行两次迁移报告完全一致，迁移前存在的 6 个源文件迁移后全部存在，风格预设、Provider 和密钥 JSON 的 SHA-256 未变化。计数是验收证据，不是迁移器中的硬编码条件。

## 回滚

1. 停止 WebUI，保留失败后的数据库、WAL 和日志用于诊断。
2. 先对待恢复文件再次执行 `PRAGMA quick_check` 或 SHA-256 校验。
3. 从 `data/backups/v6-pre-v7/` 恢复对应数据库和 JSON。
4. 启动上一版本；旧版继续读取旧 JSON 和历史库，不依赖 V7 新表。
5. 不自动删除 V7 期间生成的图片；新增作品如何回填由操作员确认。
