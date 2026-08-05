# V5 到 V6 迁移与回滚

## 自动迁移

首次启动 V6 时，服务先执行只复制不删除的迁移:

1. 将已存在的 V5 数据复制到 `data/backups/v5-pre-v6/`。
2. 对源文件和备份逐个计算 SHA-256；不一致会终止启动。
3. 写入幂等标记 `data/migrations/v6.json`，后续启动不会重复覆盖备份。
4. 将 `data/natural/task_events.jsonl` 导入共享任务库 `data/studio.sqlite3`。无法确定终态的旧任务标记为 `interrupted`。

V5 的历史库、自定义提示词、风格预设、Provider 配置、加密密钥文件、LoRA 档案、身份绑定与语义索引均保留原路径。V6 新增数据位于 `data/studio/`、`data/studio.sqlite3` 和 `data/natural/plans.json`。

## 升级后检查

- 打开 `/api/v6/capabilities`，确认所需能力的 `ready` 状态。
- 打开任务中心，确认旧事件已出现且没有任务被自动重放。
- 分别进入随机与自然语言工作区，验证设置、历史和作品仍可读取。
- 先执行一条不生成图片的 Prompt Plan，再按需做最小生成测试。

## 回滚

1. 停止 WebUI，确认没有正在写入的任务。
2. 另存当前 `data/`，保留 V6 期间产生的作品与审计记录。
3. 从 `data/backups/v5-pre-v6/` 按原相对路径复制需要恢复的文件。
4. 切回 V5 代码后启动并检查历史、Provider 和预设。

不要在 WebUI 运行时覆盖 SQLite 或 JSON 数据。`data/studio.sqlite3`、`data/studio/` 和 `data/natural/plans.json` 是 V6 专用状态，V5 不读取它们；确认不再需要 V6 后再人工归档，而不是直接删除。
