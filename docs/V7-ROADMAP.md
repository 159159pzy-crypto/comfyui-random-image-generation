# Anima Studio V7 里程碑与验收

V7 保留随机和自然语言两个独立工作区，以原生领域模型和 `/api/v7` 作为唯一的新前端运行边界。以下里程碑按数据层到体验层依次验收。

| 里程碑 | 交付结果 | 发布验收 |
| --- | --- | --- |
| M0 基线与回滚 | 写前一致性备份、幂等 Schema、预设和历史元数据迁移 | 计数、SHA-256、`quick_check`、连续迁移两次 |
| M1 原生领域核心 | Generation Intent、Provider、Planner、工具循环、Composer、Resolver、Workflow 与调度协议 | 单元/契约测试；发布门禁无 AstrBot Context/Event 和 upstream import |
| M2 共享设置与素材 | 服务端草稿、统一风格预设、模型和多 LoRA、共享 Prompt Asset | 刷新/重启恢复、revision 冲突、双侧参数一致 |
| M3 任务与事件 | 全局 FIFO、精确取消、重试、持久化任务中心、可恢复 SSE | 顺序、所有权、重启中断、`Last-Event-ID` 和跨工作区刷新 |
| M4 作品库与双工作区 | Intent 历史、大图浏览、分页、下载、复现、变体和来源跳转 | 桌面/390px 浏览器流程及跨工作区复现 |
| M5 上游领域能力 | Prompt/LoRA/Danbooru/模型/工作流/日志/诊断的 V7 API 与管理面 | 固定基准矩阵；外部操作人工确认；不复现 QQ/AstrBot 运输 |

发布完成不等同于每台机器都具备全部模型或第三方节点。真实生成、下载、索引构建和模型隔离仍以本机能力检查及操作员确认为准。
