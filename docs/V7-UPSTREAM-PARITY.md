# Anima Studio V7 上游能力矩阵

## 固定边界与判定规则

- 历史授权快照：`yenn001/astrbot_plugin_comfy_anima@9220b1cbcb3026c14554331fdbccd7d08314cb35`。仅用于来源审计、授权说明、工作流 JSON 和迁移/测试夹具。
- V7 能力验收基准：`yenn001/astrbot_plugin_comfy_anima@8202024084c6115b41c2a012bf226c0c245f2c66`。后续上游提交不自动进入本轮范围。
- 上游基准测试记录：`1101 passed, 683 subtests passed`。首次全量运行有 1 个 TTL 时序断言抖动，隔离重跑通过；保留该记录，不把单次抖动描述为稳定失败。
- V7 复现领域能力，不复现 QQ、NapCat、OneBot、群权限、AstrBot 命令分发、消息回复和聊天事件运输。
- 外部网络、下载、索引构建和模型隔离必须由用户明确确认；自动化测试使用临时目录、假服务或受控 HTTP 服务。
- `完成` 表示当前代码、界面或测试已有直接证据；`部分` 表示只有子集、契约或模拟证据；`未验收` 表示本轮没有真实运行证据。四列相互独立，不能用单元测试代替管理 UI 或真实运行验收。

## 能力矩阵

| 上游领域能力 | 原生实现 | 管理 UI | 自动化测试 | 真实运行验收 |
| --- | --- | --- | --- | --- |
| Generation Intent、参数优先级、草稿与风格预设 | **完成**：`anima_studio.domain`、`V7Store` 和 `/api/v7/intents/preview`、`/drafts/*`、`/presets` 统一模型、revision 与 digest | **完成**：随机/自然双工作台共用草稿、预设、模型和 LoRA 控件，并显示解析来源 | **完成**：`test_v7_domain.py`、`test_v7_store.py`、`test_v7_routes.py`、`test_v7_frontend_contract.py` 覆盖合并、冲突、迁移和恢复契约 | **完成**：自然草稿已跨刷新和服务重启恢复；双工作台均完成真实生成，历史库现有 948 条作品 |
| Provider：文本、视觉、Embedding、Rerank 与有界工具循环 | **完成**：原生 Provider 注册、角色绑定、结构化/流式响应和有界工具循环，不接收 AstrBot Event/Context | **完成**：Provider CRUD、四类模型字段、连接测试和绑定入口已接入 V7 管理面 | **完成**：`test_natural.py` 覆盖文本、视觉、Embedding、Rerank、工具调用组装、空响应扩容和脱敏；路由使用受控 Provider | **部分**：真实文本 Provider 已完成一次自然语言规划并生成；视觉、Embedding、Rerank 尚无本轮真实 Provider 验收 |
| Prompt Director/Composer、画师、角色别名、风格预设与 LoRA Resolver | **完成**：原生 Planner、Composer、工具注册和 fail-closed Resolver；歧义返回确认项 | **完成**：自然侧计划预览、来源说明和确认回执可见，模型/多 LoRA 可逐项覆盖 | **完成**：`test_natural_m1.py`、`test_v7_domain.py`、`test_v7_routes.py` 覆盖 exact、别名、多语言、歧义、显式 LoRA 覆盖和冻结计划 | **部分**：真实请求已解析 `@rucarachi`、风格预设和两个指定 LoRA 并成功生成；歧义确认流程尚未做浏览器实机验收 |
| Prompt Asset Library、Prompt Lab 与 Prompt Plan | **完成**：共享 SQLite 素材库、原生分类、候选生成/确认和带 revision/digest 的 Plan CRUD | **完成**：Studio 提供搜索、导入、远程更新确认、Lab 候选和 Plan 创建/更新/删除入口 | **完成**：`test_studio_services.py`、`test_v7_studio_routes.py`、`test_v7_studio_frontend_contract.py` 覆盖原生分类、确认、冲突和手动门禁 | **未验收**：本轮没有在真实浏览器完成素材导入、候选确认、Plan CRUD 与自然规划联动的完整流程 |
| LoRA 统一选择、清单、顺序、强度、角色与工作流注入 | **完成**：统一 `{filename, enabled, strength, role, order}`，两侧共用存在性校验、排序和注入 | **完成**：双工作台均有多 LoRA 编辑器；Studio 有清单筛选和刷新入口 | **完成**：`test_v7_domain.py`、`test_v7_frontend_contract.py`、`test_workflow.py` 覆盖结构、顺序、强度、空 LoRA、失效文件和工作流替换 | **部分**：本机清单可读取，真实自然任务已按明确文件名注入两个 LoRA；禁用、失效和重排尚未逐项实机验收 |
| LoRA 详情、预览图与本地目录扫描 | **完成**：原生目录扫描、SHA-256、Schema 3 详情对象和视觉清单 API | **完成**：Studio 有详情、预览清单和分页结果入口 | **完成**：`test_studio_services.py` 使用临时 LoRA 文件验证扫描、哈希、详情和视觉清单；路由契约亦覆盖 | **部分**：真实生成证明 LoRA 文件可加载，但尚未留下详情/预览页逐项浏览证据 |
| LoRA Schema v3 身份绑定、语义索引、检索与预设 | **完成**：`lora_profiles_v3.json` 与 `identity_bindings_v3.json` 支持共享 `activation_terms[]`、逐项 `character_canonical/copyright_canonical`、Danbooru exact 一致性、SHA-256/语义指纹失效和多角色专属激活词；运行时只读取仍有效的绑定 | **完成**：Studio 可读取 LoRA 详情、编辑共享激活词和逐角色/作品 exact 绑定，并显示校验与失效状态 | **完成**：`test_lora_identity_schema_v3.py`、`test_v7_studio_routes.py`、`test_v7_studio_frontend_contract.py` 覆盖旧档案幂等迁移、exact 绑定、文件内容跟随、失效、多角色独占激活词和前端字段 | **部分**：真实 LoRA 目录详情和单角色绑定链路已可用；尚未在浏览器对一个真实多角色 LoRA 完成逐项写入并回读的验收 |
| LoRA 语义分析、归档与下载 | **完成**：原生分析管线、归档器、受允许主机约束的流式下载器和 Studio 长任务接口；均要求明确确认 | **完成**：分析、归档、URL 下载均有确认对话框和任务提交入口 | **部分**：已验证手动门禁、路由、可注入后端和假服务结果；缺少真实下载器受控 HTTP、超限/中断/残留文件的完整测试 | **未验收**：本轮未执行真实语义分析、归档或联网下载 |
| Danbooru exact 查询、本地索引、构建、断点与定期更新 | **完成**：Schema v2 exact/alias 索引、持久增量 checkpoint、高水位续建、有界重试、内容哈希、失败保留旧库和原子替换均为原生实现；定期更新持久化且默认禁用，每次联网执行仍要求安全触发 | **完成**：Studio 提供状态、exact 搜索、identity/full 构建、更新计划开关/间隔和到期执行入口；对应 V7 API 为 `/danbooru/build`、`/danbooru/schedule`、`/danbooru/schedule/run` | **完成**：`test_danbooru_builder.py`、`test_v7_studio_routes.py`、`test_v7_studio_frontend_contract.py` 覆盖首次构建、增量续建、失败恢复、摘要、原子发布、默认离线调度和手动门禁 | **部分**：真实诊断读取到 1,236,139 条索引及 40,034 条 alias；本轮未联网重建索引或真实执行定期更新，不能把受控服务测试写成联网验收 |
| 图片输入、反推、整图语义改图、多人换角与 Subject Slots | **完成**：原生上传资产、视觉适配、语义改图、确定性换角和结构化角色槽位边界 | **完成**：自然工作台保留反推、改图、换角、上传和计划预览入口 | **完成**：`test_natural.py`、`test_natural_m1.py` 覆盖上传生命周期、反推适配、语义改图、换角选择和歧义失败 | **未验收**：旧 V6 截图不作为 V7 真实运行证据；本轮尚未在 `/api/v7` 完成这些图片模式的实际提交 |
| Base/RTX/Iterative、Control、img2img、RTX Upscale、Quick/LanPaint Inpaint | **完成**：10 个工作流描述符与原生 Builder 覆盖上游工作流文件、输入图、Control、蒙版和依赖边界 | **完成**：自然工作台提供对应模式、管线、Control 与蒙版控件；Studio 可列出工作流 | **完成**：`test_natural.py` 与工作流夹具测试覆盖模式路由、节点依赖、输入和渲染；`test_workflow.py` 覆盖随机链路 | **部分**：本轮仅真实验收自然 text-to-image 和随机生成；其余模式尚未逐个提交到本机 ComfyUI |
| 工作流发现、依赖诊断与环境档案 | **完成**：原生 Workflow Registry 与无密钥配置档案支持发现、保存、导入导出、启用和删除 | **完成**：Studio 有工作流清单、环境档案编辑与操作入口 | **完成**：`test_studio_services.py`、`test_v7_studio_routes.py` 覆盖发现、无密钥导出和 CRUD 契约 | **完成**：Studio runtime 已实测 ComfyUI 在线，9/9 可执行工作流均为 ready，13 项 Studio capabilities 均可用；第 10 个 JSON 描述符为兼容/发现项，不计入 9 个运行工作流 |
| 模型/UNET 清单、刷新、选择与切换 | **完成**：`/assets/models`、`/studio/models/refresh`、Intent 模型字段和工作流模型注入使用原生边界 | **完成**：双工作台暴露固定模型选择；Studio 提供模型/UNET 刷新 | **完成**：受控 ComfyUI 清单、刷新路由和工作流模型替换有测试 | **完成**：已从默认 `miaomiaoHarem_anima14.safetensors` 切换到 `miaomiaoHarem_anima8Step10.safetensors`，完成真实生成并在任务 Intent/历史中回读模型 |
| 模型与 LoRA 可恢复隔离 | **完成**：精确相对路径、允许根目录、引用阻断、SHA-256、审计和恢复均由原生服务处理 | **完成**：Studio 要求精确名称二次输入、人工确认，并展示隔离区和恢复动作 | **完成**：`test_studio_services.py` 在临时目录验证引用阻断、路径逃逸、重名、移动、校验和与恢复 | **未验收**：按安全边界，本轮未对真实模型文件执行隔离或恢复 |
| 全局 FIFO、等待项删除、取消、重试与重启中断 | **完成**：随机、自然和 Studio 操作共用 V7 FIFO；任务状态持久化，重启将未完成项标记 interrupted，不自动重放；运行取消检查 `prompt_id` 所有权；终态不可逆且 runtime 使用单实例租约避免活跃实例被误恢复 | **完成**：全局任务中心提供筛选、取消、重试、来源跳转和事件查看 | **完成**：`test_v7_queue.py`、`test_v7_routes.py`、`test_natural_m1.py`、`test_v6_runtime.py` 覆盖 FIFO、等待删除、Studio 共队列、重试、所有权、终态竞争、单实例租约和重启中断 | **完成**：真实运行已逐项验证 FIFO 顺序、等待项删除、运行中仅中断自身 `prompt_id`、失败项重试、服务重启后 queued/running 变为 interrupted 且不自动重放 |
| 日志、诊断、任务事件与可恢复 SSE | **完成**：脱敏日志、持久事件游标和 `Last-Event-ID` SSE 发布 `job.*`、`history.*`、`draft.updated`、`asset.changed` | **完成**：任务中心、日志和诊断面板可打开；Studio 正确显示 ComfyUI 在线、工作流 readiness 和 capability 状态 | **完成**：`test_v7_store.py`、`test_v7_routes.py`、`test_v7_frontend_contract.py`、`test_v7_studio_routes.py` 覆盖游标、续传、事件族、脱敏和日志控制 | **完成**：双工作台已验证新任务/作品无需刷新同步，并用历史事件游标实测 `Last-Event-ID` 补齐；任务终态与角标同步一致 |
| 作品库、复现、变体与共享大图浏览器 | **完成**：`history.sqlite3` 关联规范化 Intent，并保留旧记录安全转换、精确字符串 Seed 和来源工作台 | **完成**：双工作台共用大图、前后切换、缩放、平移、适应、下载、复制、删除、复现、变体和来源跳转；移动端支持未放大时左右滑动、放大后拖拽，手势不会抢占页面纵向滚动 | **完成**：历史/Intent 关联、跨工作台事件、前端恢复、LoRA 结构、大整数 Seed、touch/pointer 手势和 `touch-action` 均有契约测试 | **完成**：948 条作品可读；桌面和 390px 已验收共享大图切换、缩放/平移、移动滑动和零横向溢出，跨工作台实时刷新通过 |
| AstrBot/QQ 运输排除与 V7 原生发布门禁 | **完成**：运行时代码不依赖 `anima_natural.upstream`，不构造 Event/Context/PluginSettings；上游运输层明确排除 | **不适用**：V7 WebUI 不提供 QQ、群权限或 AstrBot 命令管理 | **完成**：`test_v7_native_gate.py` 同时验证当前源码与禁用模式反例；前端契约禁止旧 API | **完成**：最新完整门禁为 `246 passed, 102 subtests passed`；Ruff、compileall、全部 JS 语法、native gate 与 `git diff --check` 均通过 |

## 当前实机证据（2026-08-05）

- V7 服务：`http://127.0.0.1:8194`；ComfyUI：`http://127.0.0.1:8188`。Studio runtime 实测 ComfyUI 在线，9/9 运行工作流 ready，13 项 capabilities 可用。
- 自然任务 `job_a8180255bdf147e4` 成功，ComfyUI `prompt_id=a46e2140-efbd-477a-a3d4-77981d6199d6`；其 Intent 同时解析画师、预设和两个明确 LoRA。
- 随机工作台已分别使用 `miaomiaoHarem_anima14.safetensors` 与 `miaomiaoHarem_anima8Step10.safetensors` 完成实际生成；当前历史总数为 948。
- 自然侧在不刷新页面的情况下接收到随机侧新作品，来源为 `random`，字符串 Seed 保持精确。
- 真实队列验收覆盖 FIFO、等待项删除、精确 `prompt_id` 取消、重试、重启 interrupted 和不自动重放；持久账本同时验证终态不可逆与单实例租约。
- `/api/v7/studio/workflows` 返回 10 个描述符，其中 9 个运行工作流 ready；`/api/v7/studio/diagnostics` 返回 `native=true`、ComfyUI online、13 项 capability ready，现有 Danbooru 索引为 ready。
- 浏览器证据：`output/playwright/v7-studio-runtime-online.png`、`output/playwright/v7-mobile-gallery-viewer.png`、`output/playwright/v7-final-studio-desktop.png`、`output/playwright/v7-final-mobile-studio.png` 和 `output/playwright/v7-final-natural-workspace.png`；390px 测得零横向溢出，控制台零错误/警告。
- 最新发布门禁：`245 passed, 102 subtests passed`；Ruff、compileall、全部前端 JS 语法、V7 native boundary 和 `git diff --check` 均通过。
- 浏览器已验证共享大图滑动/拖拽手势、跨工作台实时刷新和 `Last-Event-ID` 补齐。LoRA Schema v3 管理 UI 已可用，但尚未用真实多角色 LoRA 完成浏览器写入回读。

## 发布前剩余缺口

1. 使用一个真实多角色 LoRA，在浏览器完成 Schema v3 多绑定写入、回读和实际规划验收；现有自动化覆盖不能替代这一步。
2. 补做 Provider 视觉/Embedding/Rerank 和 text-to-image 以外各图片模式的真实运行验收。
3. 在明确人工确认后，完成 LoRA 下载/分析/归档与模型/LoRA 可恢复隔离的受控真实验收；本轮没有执行联网下载、联网 Danbooru 构建或真实文件隔离，不能据自动化结果宣称已做。

## 发布门禁

`python tools/check_v7_native.py` 扫描活动 Python 运行时和 V7 前端：

- 禁止 `anima_natural.upstream` 或相对 `.upstream` 运行时导入；
- 禁止 `ProviderContext`、`BrowserEvent`、`PluginSettings` 和 AstrBot 运行时标识；
- 禁止前端调用 `/api/natural/`、`/api/batches` 或 `/api/v6/`；
- 仅排除 `anima_natural/upstream/` 历史授权快照和 `anima_webui/migrations.py` 迁移夹具；文档和测试不属于运行时代码。

CI 同时运行门禁反例测试，证明每一种禁用模式都会造成失败，而不是只验证当前源码恰好没有命中。
