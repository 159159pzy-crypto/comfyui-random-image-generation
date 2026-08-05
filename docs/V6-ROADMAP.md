# Anima Random Studio V6 里程碑

V6 的设计目标不是合并两种创作方式，而是让随机生成与自然语言创作保持各自最短路径，同时共享任务、作品、配置与安全边界。

## M1:可靠性与一致性

- Prompt Plan 持久化并带 `id + revision + digest`，过期修订返回冲突，不会静默提交旧计划。
- 确认前可覆盖正向提示词、负向提示词、管线、重绘模式和锁定标签；最终工作流只使用确认后的版本。
- 自然任务、随机批次和 Studio 长操作进入同一个 `data/studio.sqlite3` 任务账本。
- 服务重启后，排队中任务标记为 `interrupted`，不会自动继续生成或访问外网。
- 仅当执行槽和 `prompt_id` 都属于目标任务时才调用 ComfyUI interrupt。
- 上传资产重启恢复，损坏、临时、过期和孤儿文件按有界策略清理。

## M2:双工作区与任务中心

- `/?workspace=random` 与 `/?workspace=natural` 分离入口并保留 URL 状态。
- 自然工作台按“描述 -> 结构化计划 -> 编辑确认 -> 提交”运行。
- 全局任务中心优先读取 `/api/v6/jobs`，并为旧服务保留兼容回退。
- 前端采用原生 ES Modules，无需构建步骤；API、DOM、计划与任务中心职责拆分。
- 动态数据使用 `textContent` 和 DOM API 渲染；桌面与 390px 移动视口无横向溢出。

## M3:Studio 管理能力

- Prompt Asset Library 保留 Anima 原生 `asset_type`、分类、特征与排序，不建立并行分类体系。
- Prompt Lab 候选为无副作用预览，必须通过批次 ID 再次确认后才产生 Composer Draft。
- LoRA 清单、可视化、语义分析、归档与下载使用可注入真实后端；不可用依赖在 capabilities 中明确为 disabled。
- Danbooru 构建支持检查点与取消；Civitai、Danbooru 及远程素材同步均要求 `confirm_manual=true`。
- 工作流发现与配置档案不导出 API Key 等敏感字段。
- 模型操作采用可恢复隔离：精确相对路径确认、引用检查、SHA-256 校验、审计与恢复。

## 共享 API

- 任务:`/api/v6/jobs`、`/api/v6/jobs/{id}`、`events`、`cancel`、`/api/v6/logs`
- 能力:`/api/v6/capabilities`、`/api/v6/studio`
- Prompt:`/api/v6/prompt-assets`、`/api/v6/prompt-lab/*`
- LoRA:`/api/v6/loras/*`
- Danbooru:`/api/v6/danbooru/*`
- 工作流与档案:`/api/v6/workflows`、`/api/v6/config-profiles/*`
- 模型隔离:`/api/v6/quarantine/*`

## 验收门槛

每次发布必须同时通过 Python 全量测试、全部前端模块语法检查、Python byte-compile、差异空白检查，以及桌面/移动浏览器验收。单独通过服务门面测试或只看到新界面均不视为里程碑完成。
