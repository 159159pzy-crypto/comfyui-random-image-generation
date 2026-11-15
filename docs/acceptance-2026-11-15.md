# 0.2.0 本地迁移验收

验收日期：2026-11-15。目标目录：`F:\animarandom`。本页记录最初的本地迁移验收，当时仅准备源码与源码包；后续 GitHub 发布信息见 [0.2.0 Release](https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/tag/anima-webui-v0.2.0)。下列测试结果与 GPU 验收范围对应迁移验收阶段。

## 来源与范围

- 精简版提取基线：`02299605f4e499c5135ca7350e2c4540179adb2c`；本次修改前目标提交：`8dfe243`。
- 上游来源：`F:\comfyuishengtu`，提交 `602e74f1d1747bdd8a050d3c2358dc86f2a7bb60`。
- 完成作品筛选/批量删除、风格预设库、角色分类、v5、资源解析与冻结、结果恢复、缩略图及交互改进。
- 继续排除 Krea 2、单图工作台、图生图、AI 助手、图片反推和 EXE 启动器；生成接口只接受 Anima 随机文生图。
- 保留 8191、源码启动、`--resource-paths`、独立数据目录及 `anima-random-webui` 浏览器存储命名空间。
- 结束时复查来源 HEAD 未变；来源工作区仍只有开始前已有的未跟踪目录 `artifacts/comparisons/`。

## 自动化检查

执行：

```powershell
.\.venv\Scripts\python.exe scripts\check.py
$env:ANIMA_BROWSER_TESTS = '1'
.\.venv\Scripts\python.exe -m pytest tests\browser\test_lightweight_updates.py -q
```

| 检查 | 结果 |
| --- | --- |
| Ruff、Windows PowerShell 脚本解析、前端 JavaScript 语法 | 通过 |
| Python 主测试集 | 1504 passed，505 subtests passed |
| 默认跳过的浏览器测试 | 5 项；随后单独开启 Chrome 全部通过 |
| Node 前端测试 | 579 passed |
| Chrome 浏览器回归 | 5 passed；无页面 JavaScript 异常 |

日志位于 `output/acceptance-v020/check-final.log` 和 `browser-final.log`。测试包含：

- 旧自定义词库、预设迁移前备份；写入/恢复故障与只读保护；新旧预设尺寸和修复携带开关默认关闭。
- 旧草稿、历史和预设来源 v4 语义；v5 像素高清与整图后采样尺寸；明确参数保留；负向条件零化。
- 兼容资源替代、严格历史路径/指纹检查、LoRA 禁止替代、提交至执行的资源变化、自动绑定后再次校验。
- 提示词/五池/人物属性筛选、列表与详情一致、跨页删除逐项失败和重试、共享原图保护。
- 结果恢复、停止竞态、忙碌/缺失扩展时清理反馈、缩略图重试与缓存上限、过期异步响应保护。
- 本地 LoRA 收藏、标签、备注、覆盖、角色绑定和使用统计隔离。
- 扩展安装的文件所有权检查、Windows junction、防并发变更、备份与回滚；源码包白名单和包资源。

浏览器使用临时数据和 FakeComfy，阻止外部请求，不使用 GPU。实际操作覆盖：预设新默认及管理器、角色原文与人工分类、旧 v4 草稿恢复、作品搜索、跨页删除；390 px 窄屏筛选面板关闭后焦点恢复且无横向溢出。截图位于 `output/playwright/lightweight-updates/`。

## 真实 ComfyUI 验收

使用现有 `127.0.0.1:8188` ComfyUI、已安装的资源扩展和 KJNodes。未安装、更新、重启 ComfyUI 或其扩展。

验收服务使用独立端口及 `output/acceptance-v020/data`、`latest-data`，没有使用本项目正式数据目录。基础图为 512×512、4 步的小规模功能冒烟测试；产品新建默认值仍为 30 步、CFG 4、`er_sde + simple`。

| 用例 | 实际结果 | 证据文件（位于 output/acceptance-v020） |
| --- | --- | --- |
| v5 普通生成 | 成功保存图片、参数及资源记录 | normal-v5.json |
| 历史 replay | 正负提示词及两类种子一致，解码 RGB SHA-256 完全一致 | replay.json |
| prompt_variant | 提示词和提示词种子不变，图像种子改变，成功出图 | prompt_variant.json |
| 明确 v4 历史语义 | v4、CFG 8、Euler 明确参数保留并成功出图 | normal-v4.json |
| 双采样 + 负向条件零化 | 成功出图，保存 zero_negative=true | dual-v5-zero-negative.json |
| 像素高清 + 整图后采样 | 512×512 经 4 倍放大、50% 缩图后保持 1024×1024 | pixel-post-v5.json |
| 角色自动绑定 LoRA | 自动 LoRA 实际参与生成，其资源指纹进入历史 | binding-v5.json |
| 只重抽姿势 | 保留原角色 LoRA 的规范化路径、强度和资源指纹 | binding-preserved-redraw.json |
| 生成中停止 | 状态 stopped；等待清理完成后 memory_release=completed | stop.json |
| 共享 LoRA 资料隔离 | 81 条共享个人资料前后相同；本地修改只写隔离数据目录 | isolation.json、plugin-personal-before.json、plugin-personal-after.json |

运行日志：`generation.log`、`binding.log`、`stop.log`、`isolation.log`。真实界面截图：`output/playwright/v020-live-desktop.png`（1440×1000）和 `v020-live-mobile.png`（390×844）。移动界面实测文档宽度 381 px，没有横向溢出。

资源扩展只读检查返回 `ready`；使用现有 `F:\comfyui\custom_nodes\ComfyUI-Atelier-Resources`。`VRAM_Debug` 和资源检查接口已确认可用。停止后的清理没有使用全局模型卸载回退。

## 数据和验收边界

- 数据迁移通过临时旧格式文件和故障注入验证；未把用户的正式数据用于验收。首次启动新版并加载旧文件时，程序会先备份再迁移；旧预设被移除的携带参数可从迁移备份查回。
- 真实 GPU 验收覆盖上表所列路径。SeedVR2、SD 放大、脸部细化等其他可选阶段的参数、节点构建和校验由自动化测试覆盖，本次未逐一进行 GPU 出图。
- 兼容替代及资源在提交后发生变化的异常路径通过可控模拟验证，没有为了测试移动、替换或删除用户真实模型。
- 真实验收生成的图片仍位于 ComfyUI 输出目录；记录及截图保存在本项目忽略的 `output/` 下，不进入源码包。

## 源码包

本地交付文件为 `output/release-0.2.0/AnimaRandomWebUI-0.2.0-source.zip`，对应提交及 SHA-256 见同目录 `build-info.json` 和 `SHA256SUMS.txt`。使用 `scripts/build_release.py` 从固定 Git 提交导出并审计；个人数据、模型、测试产物和环境不打包。

源码包包含 `Install-ComfyExtension.ps1`、扩展安装模块、5 个资源扩展 Python 文件，以及新增的静态模块和样式。解压后的 HTTP 冒烟结果见 `output/acceptance-v020/package-smoke.json`。
