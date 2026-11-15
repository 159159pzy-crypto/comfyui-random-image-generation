<p align="center">
  <img src="docs/readme/app-icon.png" width="96" alt="Anima Random WebUI 图标">
</p>

<h1 align="center">Anima Random WebUI</h1>

<p align="center">
  <strong>纯粹、轻量的 Anima 随机生图工作台</strong><br>
  固定喜欢的角色与风格，让服装、动作、场景和表情带来新的可能。
</p>

<p align="center">
  <a href="https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/latest"><strong>查看已发布版本</strong></a> ·
  <a href="#开始使用">开始使用</a> ·
  <a href="#安装前先看">依赖与 Agent 安装</a> ·
  <a href="docs/guide.md">使用指南</a> ·
  <a href="RELEASE_NOTES.md">发行说明</a> ·
  <a href="https://github.com/159159pzy-crypto/comfyui-random-image-generation/issues">反馈问题</a>
</p>

> [!IMPORTANT]
> **本项目需要 ComfyUI。** 请先准备能运行 Anima 的本机 ComfyUI，并在使用 WebUI 时保持它运行。
>
> **能让 Agent 帮装就别自己装。** 推荐把本项目目录和下方[安装说明](#安装前先看)交给能操作本机文件、终端的 AI Agent，让它检查已有环境、补齐插件与模型并验证启动。

![Anima 随机工作台：左侧配置生成设置与提示词池，右侧浏览作品库，底部控制批量生成](docs/readme/random-workbench.png)

<p align="center"><sub>页面中的作品、模型、LoRA 和目录均为使用示例，不随下载包提供。截图展示的是本项目实际界面。</sub></p>

**把想固定的内容留下，把想探索的部分交给随机；抽到满意的图，再沿着它继续尝试。**

Anima Random WebUI 连接已有的本机 [ComfyUI](https://github.com/Comfy-Org/ComfyUI)，把五池选词、LoRA、批量生成、放大和作品历史放进同一个浏览器工作台。选好候选范围与风格后，就可以连续探索人物、动作和场景的不同组合。

> [!NOTE]
> 本项目是 [Comfy Image Atelier](https://github.com/159159pzy-crypto/comfy-Image-Atelier) 的精简分支，专注于 **Anima 随机生图**。剔除了 Krea 2、单图工作台、图生图、AI 助手、图片反推和 EXE 启动器等功能。
>
> 当前版本 **0.2.0**，采用源码安装与启动，可从 [GitHub Release](https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/tag/anima-webui-v0.2.0) 下载。正常使用无需 Node.js 或前端构建；ComfyUI、节点与模型按下方说明单独准备。个人数据独立保存，不自动继承原项目数据。

## 0.2.0 更新

迁入 Atelier 截至 `602e74f` 的随机台与公共功能更新：作品内容筛选和跨页批量删除、风格预设资料库、本地角色标签分类、v5 放大流程、负向条件零化，以及资源检查、缩略图和 LoRA 加载改进。

旧 v4 草稿和历史仍按 v4 执行。新旧风格预设统一采用字段级应用，默认不携带尺寸和放大修复；需要时重新捕获并勾选。升级前备份个人数据，迁移也会保留旧词库和预设文件的备份。

新任务可以提示并使用兼容替代模型；历史原样复现和同词变体仍要求原资源存在。配套 ComfyUI 扩展通过源码脚本单独安装，已有兼容安装直接复用。

## 你可以用它做什么

| 创作场景 | 工作台里的做法 |
| --- | --- |
| 给同一角色探索不同动作与场景 | 固定角色、画师或服装，让其他词池在选定范围内随机组合 |
| 挑一批符合自己喜好的题材 | 用中文名称、作品系列和角色特征筛选候选，收藏常用词条或导入自己的主题库 |
| 比较不同画风与 LoRA 搭配 | 独立调整多个 LoRA 的权重、触发词与启停，把常用配置存成风格预设 |
| 连续生成，再从结果中挑选 | 每批最多 1000 张，下一批可继续排队；作品逐张进入历史 |
| 喜欢这张图，想继续找变化 | 按原种子复现、保持提示词换种子，或只重抽指定内容 |
| 把满意的结果整理起来 | 查看完整原图与生成参数，将作品收入自己的收藏夹 |

[五池随机](#五池随机) · [批量与队列](#批量与队列) · [LoRA 管理](#lora-管理) · [采样与放大](#采样与放大) · [历史与作品收藏](#历史与作品收藏)

## 五池随机

### 角色、服装、姿势、背景、表情，分别决定怎么抽

五个维度都可以选择 **关闭 / 随机池 / 固定**。先挑出喜欢的候选，再决定每张图要抽什么；画师、质量词、额外提示词和负面词可以另行配置。

| 组合方式 | 可以探索什么 |
| --- | --- |
| 固定角色和服装，随机姿势、背景、表情 | 同一主题下的动作、环境与情绪变化 |
| 固定画师和背景，随机角色与服装 | 在相近画风与场景下比较人物搭配 |
| 只开启一个随机维度 | 缩小变化范围，逐项挑出更喜欢的结果 |
| 五池全部开放 | 从意料之外的组合里寻找灵感 |

人物描述支持仅触发词、触发词与特征，以及剔除角色自带服装标签的描述模式。可以保留角色外貌，再由独立服装池搭配穿着；实际成图仍由提示词、模型与参数共同决定。

### 用中文找到角色，把内容整理成自己的候选库

![角色池：中文名称与缩略图、作品系列和外观筛选、收藏分组、跨页选择与批量导入](docs/readme/prompt-pool.png)

- **按名字和特征找角色。** 支持中文优先、英文和中英双语显示，按名称、系列、性别、发色与瞳色等筛选；尚未覆盖的中文名称保留英文。
- **候选范围自己控制。** 选中本页、追加全部筛选结果、仅看已选、清空选择，也可使用全库并排除不想参与的条目。
- **常用内容随手收藏。** 词条收藏与自定义分组分别整理，常用画师也能快速加入。
- **把自己的内容导入词池。** 支持单条自定义和 JSON / CSV 批量导入，每次最多 2000 条；先预览和检查冲突，再确认写入。
- **提示词在本地整理。** 高级提示词中的规范与替换可作用于正向、负向和 LoRA 触发词，不调用外部 AI 服务。

基础词库来自 Anima Tools；表情词库内置。截图中的词条数量会随基础词库版本和自己的内容变化。[查看词池与整理说明](docs/guide.md#4-五池随机与本地整理)

## 批量与队列

**每批 1–1000 张，最多 20 个等待任务。** 当前批次运行时可以提交下一批，任务按顺序接续，并保留各自提交时的设置。可移除等待任务；停止操作会停止当前批次并清空等待队列，已完成作品仍保留在历史中。

图像种子支持固定或随机。固定图像种子时，每张图仍会重新抽取词池内容；需要冻结某张图的提示词时，使用历史里的原样复现或同词变体。底图模式、参数草稿和主题会在当前浏览器中保存，减少重复配置。

## LoRA 管理

### 看封面挑风格，触发词与人物绑定一起管理

![LoRA 管理：目录树、模型封面、搜索与筛选、星标收藏，以及加入当前配置的快捷按钮](docs/readme/lora-manager.png)

<p align="center"><sub>LoRA 封面与名称来自已有 ComfyUI 库存。目录树可能显示其他架构目录；这不代表本项目支持对应架构，实际生成仅使用已识别为 Anima 的资源。</sub></p>

从 LoRA 快速添加弹窗右上角展开管理区，按目录与封面浏览库存，结合搜索、筛选和图库 / 列表切换找到需要的资源。

- **组合多个 LoRA。** 独立启停、调整权重，单项或批量加入、移出当前配置。
- **触发词集中管理。** 读取插件原始触发词，按需本地覆盖、恢复原值，或留空禁用自动触发词。
- **把常用模型整理好。** 星标、标签、备注、收藏分组和默认权重保存在本项目，便于下次继续使用。
- **让角色带上自己的 LoRA。** 为人物配置绑定，随机抽到时自动应用；手动配置优先，历史保留实际使用的 LoRA 与权重。
- **把画风存成预设。** 保存模型、LoRA、画师、附加提示词和采样参数；尺寸与放大修复默认不携带，勾选后才保存和应用。

模型文件、目录与架构分类共享读取；本项目的个人整理不会写回 LoRA Manager。主模型及启用的 LoRA 必须被识别为 Anima，缺失或分类不明的资源会在生成前提示。[查看 LoRA 与本地整理说明](docs/guide.md#4-五池随机与本地整理)

## 采样与放大

先用普通底图或双采样确认构图，再按需要开启后处理。双采样支持两段采样设置、切换步数与中间潜空间调整；后处理各阶段独立开关，按下列顺序执行：

```text
底图 → 像素高清 → 潜空间放大（含重采样）→ 整图后采样
     → 脸部细化 → SeedVR2 → N 卡 VSR → DLSS → 保存
```

关闭的阶段直接跳过，缺少对应节点、模型或采样选项时会给出检查提示。潜空间重采样与整图后采样分别保存参数，SeedVR2 使用自己的模型和配套 VAE。

### 推荐放大链路

**日常建议从「底图 → Sesqui 潜空间放大 1.5×（含重采样）→ 保存」开始。** 先检查底图构图，再逐步提高倍率；不必同时开启所有后处理。

| 步骤 | 建议起点 |
| --- | --- |
| 底图 | 普通或双采样；新建默认 30 步、CFG 4、`er_sde / simple`；双采样中间倍率保持 1，切换步 15 |
| 潜空间放大 | 开启，Sesqui / `Wan 2.1`，倍率 1.5 |
| 潜空间重采样 | 16 步、CFG 4、降噪 0.2、`er_sde / simple` |
| 其他阶段 | 先关闭像素高清、整图后采样、脸部细化、SeedVR2、VSR 和 DLSS |

这是基于本项目参数的建议起点，需要按显存和画面效果调整。**潜空间放大已经包含一次重采样**，再开启「整图后采样」会额外采样一次。

- **尽量保留底图：**底图 → 像素高清 → 保存。使用已有兼容 4× 模型、缩放 50%，最终为底图的 2×；新 v5 开启整图后采样时也保留这一步缩图。旧 v4 设置继续采用原执行规则。
- **进阶尝试：**底图 → SeedVR2 2× → 保存。先关闭其他放大阶段单独验证，再考虑组合。

可选阶段的安装来源见下方[可选扩展与模型](#可选扩展与模型)。基础生图、LoRA、队列和历史重生成已有实机验证；可选后处理经过流程构建与资源检查，尚未完成真实 GPU 生图验收，推荐参数需要按自己的显存和画面效果调整。

## 历史与作品收藏

### 留下画面，也留下再次生成它的条件

每张作品保存最终提示词、抽取内容、模型、LoRA、种子、尺寸、采样和后处理信息。打开详情查看原图和参数，再选择下一步：

| 操作 | 保留什么 | 适合什么时候用 |
| --- | --- | --- |
| **原样复现** | 历史最终提示词、生成设置与原双种子 | 按保存条件重新生成一次 |
| **同词变体** | 最终提示词与生成设置，更换图像种子 | 继续探索相同描述下的构图与细节 |
| **局部重抽** | 保留其他可恢复内容，只重抽指定随机维度 | 换姿势、背景或表情，沿用其他内容 |
| **按原设置重抽** | 使用历史词池与参数设置 | 继续生成同一配方下的新组合 |

“载入参数”可以把模型、LoRA、种子和技术参数带回随机台，同时保留当前五池选择与生成数量。原样复现会重新计算，不承诺跨模型文件、节点版本或硬件环境逐像素一致。[查看历史操作规则](docs/guide.md#6-历史的四种重生成方式)

### 看清细节，把满意的作品收进自己的分类

![作品筛选：按提示词、人物、服装、姿势、背景和表情筛选历史作品，支持包含与排除条件](docs/readme/artwork-filters.png)

- **查看完整原图。** 支持缩放、拖动和连续浏览，在本机 Windows 中可定位原图文件。
- **整理作品收藏。** 新建、重命名与排序收藏夹，按收藏夹浏览，批量加入或移出作品；作品收藏与提示词收藏相互独立。
- **删除方式自己选。** 可以只删除记录并保留原图，也可以永久删除原图与记录；删除收藏夹不会删除图片。
- **按内容找作品。** 提示词搜索可与五池词条、分组、人物属性的包含／排除条件组合使用；筛选不会改变生成词池。
- **跨页整理。** 批量删除保留逐项结果，部分失败时只重试失败项，共享原图继续受到保护。

界面提供浅色 / 深色主题，并适配窄屏和系统减少动态效果设置。服务面向本机单用户使用，界面适配不等于开放局域网访问。

## 开始使用

从 [0.2.0 Release](https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/tag/anima-webui-v0.2.0) 下载 [AnimaRandomWebUI-0.2.0-source.zip](https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/download/anima-webui-v0.2.0/AnimaRandomWebUI-0.2.0-source.zip)，完整解压到自己的项目目录。使用同页的 [SHA256SUMS.txt](https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/download/anima-webui-v0.2.0/SHA256SUMS.txt) 核对文件，`build-info.json` 记录版本、源码提交和包哈希。包内是运行源码、配套扩展、用户文档和本页展示图，不附带个人数据、模型或 EXE。

已有 Git 工作目录也可以更新到标签 `anima-webui-v0.2.0`。从 0.1.1 升级前，请先阅读[升级注意](RELEASE_NOTES.md#升级注意)并备份个人数据。

首次使用依次准备 **ComfyUI 与节点 → 三个模型 → 本项目 Python 环境**。已经配好 ComfyUI 的用户可以复用现有环境和模型文件。

### 安装前先看

**推荐让 Agent 完成安装与排查。** 本项目安装脚本只负责 WebUI 的 Python 环境，不会自动安装或启动 ComfyUI，也不会下载第三方节点与模型。下面按实际用途区分依赖，避免把可选后处理当成基础生图的必装项。

| 组件 | 是否必需 | 准备方式 |
| --- | --- | --- |
| 本机 ComfyUI | **必需** | 能加载 Anima，提供下方列出的内置节点与 HTTP API；先启动 ComfyUI，再启动 WebUI |
| ComfyUI 的运行环境 | **必需** | 按 [ComfyUI 官方说明](https://github.com/Comfy-Org/ComfyUI#installing)准备其 Python、PyTorch、适合硬件的驱动与运行依赖；复用已有可用安装 |
| LoRA Manager、Anima Tools、KJNodes | **必需** | 分别提供 LoRA 加载与分类、基础词库、阶段清理节点，具体见下一节 |
| SesquiLSR | **默认双采样需要** | 也用于 Sesqui 潜空间放大；普通底图关闭后处理时可不装 |
| Anima 主模型、文本编码器、VAE | **必需** | 三个文件分别放到对应模型目录，见[下载模型](#2-下载模型)；LoRA 自选 |
| WebUI 的 Python 与运行包 | **必需** | Python 3.11+；`Install.ps1` 创建本项目 `.venv` 并安装运行包 |
| 现代浏览器 | **必需** | 用于访问 WebUI，需要支持 dialog、JavaScript 与 localStorage |
| Git | 按安装方式需要 | 使用下方 `git clone` 命令时需要；下载完整源码 ZIP 解压可替代克隆 |
| 配套资源检查扩展 | 推荐 | 使用随包 `Install-ComfyExtension.ps1` 单独安装，提供模型及可选阶段检查 |
| 脸部细化、SeedVR2、VSR、DLSS 等 | 按需 | 仅启用对应阶段时准备[可选扩展与模型](#可选扩展与模型) |

正常使用无需 Node.js、npm 或前端构建。**WebUI 的 `.venv` 与 ComfyUI 的 Python 是两套环境：第三方节点的依赖必须装进 ComfyUI 实际使用的 Python。**

可以把下面这段连同项目路径发给 Agent：

```text
请按此项目的 README.md 和 docs/dependencies.md 帮我安装 Anima Random WebUI。
先查找并复用本机已有的 ComfyUI、它实际使用的 Python、节点和模型；缺少 ComfyUI 时先配置它。
检查 LoRA Manager、Anima Tools、KJNodes，以及默认双采样需要的 SesquiLSR。
将缺失节点的依赖装进 ComfyUI 的 Python，准备 Anima 主模型、文本编码器、VAE，确认模型分类。
在 WebUI 项目目录运行 Install.ps1，检查并按需安装配套资源扩展，配置 Anima Tools 路径和 ComfyUI 地址。
先启动 ComfyUI，再启动 WebUI，检查 /system_stats、/object_info、词池与模型列表，并用一张图验证基础生成。
可选后处理仅安装我明确需要的阶段。完成后告诉我启动方式、安装位置和仍缺少的项目。
```

### 1. 准备 ComfyUI 与节点

先安装并启动支持 Anima 的 [ComfyUI](https://github.com/Comfy-Org/ComfyUI)。本项目只连接本机 ComfyUI，默认地址为 `http://127.0.0.1:8188`。

| 插件 / 扩展 | 是否需要 | 实际节点或接口 | 用途与额外依赖 |
| --- | --- | --- | --- |
| [ComfyUI-Lora-Manager](https://github.com/willmiao/ComfyUI-Lora-Manager) | **必需**，不使用 LoRA 也需要 | `Lora Loader (LoraManager)`、`/api/lm/…` | 加载 LoRA，提供主模型 / LoRA 分类与库存；安装插件的 `requirements.txt` |
| [Comfyui-Anima-Tools](https://github.com/nregret/Comfyui-Anima-Tools) | **必需** | 插件 `js/` 下的四个基础词库文件 | 提供人物、服装、姿势、背景词库；不需要将其随机节点放入工作流，表情词库由 WebUI 内置 |
| [ComfyUI-KJNodes](https://github.com/kijai/ComfyUI-KJNodes) | **必需**，普通底图也需要 | `VRAM_Debug` | 当前 v4/v5 流程在阶段之间清缓存和执行 GC，保留已加载模型；安装插件的 `requirements.txt` |
| [SesquiLSR](https://github.com/LoganBooker/SesquiLSR) | **默认双采样需要**；Sesqui 潜空间放大也需要 | `SesquiLatentUpscale` | Anima 选择 `Wan 2.1` 格式，所需 `models/upscaler_Wan21.safetensors` 随插件仓库提供 |
| 随包 [ComfyUI-Atelier-Resources](anima_webui/comfy_extension/atelier_resources) | 推荐 | 资源检查与空闲清理 API，无需在工作流中添加节点 | 检查模型文件用途及所选阶段依赖；停止后在队列空闲时安全清理；复用 ComfyUI 运行依赖 |

<details>
<summary>ComfyUI 内置节点清单（供 Agent 检查缺失节点）</summary>

以下名称是 `/object_info` 中的 `class_type`。它们来自 ComfyUI 本身；缺失时先检查 ComfyUI 版本与启动日志。

| 使用条件 | 内置节点 |
| --- | --- |
| 所有生成模式 | `UNETLoader`、`CLIPLoader`、`CLIPTextEncode`、`VAELoader`、`VAEDecodeTiled`、`EmptyLatentImage`、`PrimitiveInt`、`ComfyMathExpression`、`SaveImage` |
| 模型 / LoRA 资源列表 | `LoraLoader` 提供 LoRA 文件清单；实际生成使用上表的 LoRA Manager 节点 |
| 普通底图 | `KSampler` |
| 双采样底图 | `KSamplerAdvanced`；中间默认使用 Sesqui 节点，选择内置插值时使用 `LatentUpscaleBy` |
| 负向条件零化 | `ConditioningZeroOut` |

新建默认采样器 / 调度器为 `er_sde / simple`，也需出现在当前 ComfyUI 的选项清单中。节点与输入选项齐全才表示满足工作流要求，不能仅以网页能打开或截图中的版本号判断兼容。

</details>

已安装且可用的节点直接复用，无需重复克隆或重装。让 Agent 检查缺失项后，在 ComfyUI 的 `custom_nodes` 目录打开终端，仅运行对应的安装命令：

```powershell
git clone https://github.com/willmiao/ComfyUI-Lora-Manager.git
git clone https://github.com/nregret/Comfyui-Anima-Tools.git
git clone https://github.com/LoganBooker/SesquiLSR.git
git clone https://github.com/kijai/ComfyUI-KJNodes.git
```

新装 LoRA Manager 或 KJNodes 后，用 **ComfyUI 自己的 Python** 安装对应依赖。下面选择符合自己环境的一组，在同一 `custom_nodes` 目录运行，并按实际安装位置调整路径；已有可用安装无需重复执行：

```powershell
# 官方 Windows 便携版（ComfyUI_windows_portable/ComfyUI/custom_nodes）
..\..\python_embeded\python.exe -m pip install -r .\ComfyUI-Lora-Manager\requirements.txt
..\..\python_embeded\python.exe -m pip install -r .\ComfyUI-KJNodes\requirements.txt

# 使用 ComfyUI 根目录 .venv 的源码版
..\.venv\Scripts\python.exe -m pip install -r .\ComfyUI-Lora-Manager\requirements.txt
..\.venv\Scripts\python.exe -m pip install -r .\ComfyUI-KJNodes\requirements.txt
```

Anima Tools 无需额外 pip 依赖；SesquiLSR 复用 ComfyUI 的 PyTorch / safetensors，放大权重随仓库提供，无需另装 requirements。安装完重启 ComfyUI。只想用最少依赖时，可不装 SesquiLSR，在 WebUI 选择「普通底图」并关闭全部后处理，**仍需 LoRA Manager、Anima Tools 和 KJNodes**；默认双采样即使中间倍率为 1，也需要 Sesqui 节点。

### 2. 下载模型

从 [Anima 官方模型仓库](https://huggingface.co/circlestone-labs/Anima/tree/main/split_files) 下载以下 **3 个必需文件**。下表路径均相对于 ComfyUI 根目录；不要将三者合并放入 `checkpoints`。

| 用途 | 下载文件 | 放置目录 |
| --- | --- | --- |
| Anima 主模型 | [anima-base-v1.0.safetensors](https://huggingface.co/circlestone-labs/Anima/blob/main/split_files/diffusion_models/anima-base-v1.0.safetensors) | `models/diffusion_models/` |
| 文本编码器 | [qwen_3_06b_base.safetensors](https://huggingface.co/circlestone-labs/Anima/blob/main/split_files/text_encoders/qwen_3_06b_base.safetensors) | `models/text_encoders/` |
| VAE | [qwen_image_vae.safetensors](https://huggingface.co/circlestone-labs/Anima/blob/main/split_files/vae/qwen_image_vae.safetensors) | `models/vae/` |
| Anima LoRA | 自选，与 Anima 架构兼容即可 | `models/loras/`，可选 |

下载后刷新模型列表，让 LoRA Manager 完成扫描，确认主模型及启用的 LoRA 的基础模型分类为 **Anima**。新任务和重新抽取类任务在所选模型缺失或不兼容时，可以使用已识别的兼容替代模型，界面会展示替换记录；没有兼容资源时阻止生成。原样复现和同词变体要求历史资源仍然可用，LoRA 始终不会自动替换。模型放在子目录或使用其他兼容文件名时，见[路径映射说明](docs/guide.md#3-模型选择与路径映射)。

### 3. 安装并启动本项目

需要 **Python 3.11+**。使用上方下载的 0.2.0 源码 ZIP，或已更新到标签 `anima-webui-v0.2.0` 的 Git 工作目录。

`Install.ps1` 按 [pyproject.toml](pyproject.toml) 自动安装以下运行依赖，无需逐包手装：

| Python 包 | 版本范围 | 用途 |
| --- | --- | --- |
| `aiohttp` | `>=3.14.5,<4` | WebUI 服务、ComfyUI HTTP / WebSocket 通信 |
| `multidict` | `>=6.9.1,<7` | HTTP 多值数据结构 |
| `Pillow` | `>=13.0,<14` | 图片和缩略图处理 |

构建依赖为 `setuptools>=68`，由 pip 按项目配置处理。PyTorch 等推理依赖属于 ComfyUI 环境。

在项目目录打开 PowerShell，安装一次，然后启动。把下面的 Anima Tools 路径改为你的实际位置，指定包含 `js/` 的插件根目录：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start.ps1 --anima-tools-dir "D:\ComfyUI\custom_nodes\Comfyui-Anima-Tools"
```

浏览器会打开 `http://127.0.0.1:8191`。确认连接状态、模型和词池正常，选择随机内容后开始生成。端口不同可追加 `--comfy-url http://127.0.0.1:8189`。

使用官方 Windows 便携版时，路径通常多一层 `ComfyUI`，例如：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start.ps1 --anima-tools-dir "D:\ComfyUI_windows_portable\ComfyUI\custom_nodes\Comfyui-Anima-Tools"
```

自动发现会检查项目目录及其父目录下的 `ComfyUI/custom_nodes`、`comfyui/custom_nodes`、`custom_nodes`，也支持直接放在这两处的 `ComfyUI_windows_portable*` 便携版，包括 AMD / Intel 版本。只有一个有效词库时可直接双击 `Start.cmd`；其他位置或多个独立词库请使用上述参数明确选择，也可设置 `ANIMA_TOOLS_DIR` 环境变量。自动发现只检查这些常见位置，不搜索整个磁盘。指定路径无效时会提示修正，不会改用其他词库。完整规则见[基础词库发现](docs/guide.md#2-基础词库发现)。按 Ctrl+C 或关闭终端停止 WebUI。

安装脚本只安装本项目的 Python 依赖；**ComfyUI、节点、模型请让 Agent 按前两步检查并补齐，ComfyUI 需单独启动**。更多启动参数见[使用指南](docs/guide.md#1-源码安装与启动)。

已有配套资源扩展可先检查；检查通过即可复用。缺失或需要更新时，正常关闭 ComfyUI，再执行第二行安装命令：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-ComfyExtension.ps1 -ComfyUIPath "D:\ComfyUI" -Check
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install-ComfyExtension.ps1 -ComfyUIPath "D:\ComfyUI"
```

ComfyUI 使用其他端口时追加 `-ComfyUrl http://127.0.0.1:8189`。脚本复用已有同名扩展，拒绝覆盖用户修改，更新前备份到 ComfyUI 根目录的 `.anima-extension-backups`；不会升级、停止或重启 ComfyUI。首次安装或更新后需重新启动 ComfyUI。

### 可选扩展与模型

只安装自己启用的阶段。第三方节点克隆到 `ComfyUI/custom_nodes/`，有 `requirements.txt` 时用 ComfyUI 自己的 Python 安装，然后重启；硬件与运行库按对应仓库说明准备。

| 功能 | 实际节点 | 安装来源、模型与额外依赖 |
| --- | --- | --- |
| 像素高清 | `UpscaleModelLoader`、`ImageUpscaleWithModel`、`ImageScaleBy` | ComfyUI 内置；下载 [4x_foolhardy_Remacri.pth](https://openmodeldb.info/models/4x-Remacri) 放入 `models/upscale_models/` |
| 潜空间放大 | `SesquiLatentUpscale` 或 `LatentUpscaleBy`，以及 `KSampler` | 默认使用上文的 SesquiLSR / `Wan 2.1` 权重，也可选择 ComfyUI 内置插值；此阶段自带重采样 |
| 整图后采样 | `KSampler` | ComfyUI 内置；若前一阶段输出图像，还需内置 `VAEEncodeTiled` 重新编码，潜空间放大同理 |
| 脸部细化 | `ForbiddenVisionFaceProcessorIntegrated` | [ComfyUI-Forbidden-Vision](https://github.com/luxdelux7/ComfyUI-Forbidden-Vision)；安装其 requirements，包括 `ultralytics`、`segmentation-models-pytorch` 等。检测与分割权重放入 `models/forbidden_vision/`，详情见下文 |
| SeedVR2 | `SeedVR2Preprocess`、`SeedVR2Conditioning`、`SeedVR2PostProcessing`，配合内置加载、采样及分块 VAE 节点 | 使用提供这些节点的 ComfyUI；从 [Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2/tree/main) 下载 `diffusion_models/seedvr2_3b_int8_convrot.safetensors` 放入 `models/diffusion_models/`，`vae/ema_vae_fp16.safetensors` 放入 `models/vae/` |
| N 卡 VSR | `RTXVideoSuperResolution` | [ComfyUI-NVIDIA-RTX-VSR-Pro](https://github.com/whmc76/ComfyUI-NVIDIA-RTX-VSR-Pro)；安装 requirements（含 `nvidia-vfx`），准备兼容 NVIDIA 驱动与 VC++ 运行库；不要与注册同名节点的 `Nvidia_RTX_Nodes_ComfyUI` 同时启用 |
| DLSS | `DLSS5NeuralRender` | [DLSS5-Comfyui](https://github.com/storyAura/DLSS5-Comfyui)；按仓库安装依赖，准备 VC++ 2015–2022 运行库、`vendor/dlssnr_host.dll` 与当前显卡代数对应的 `nvngx_dlssnr.dll`；解压 RTX 30/50 的 DLL 包还需要 7-Zip。此阶段不增加像素尺寸 |

脸部细化需要检测权重 `ForbiddenVision_face_detect_v1.pt`（或 `.onnx`）；开启分割（默认开启）时，还需要 `ForbiddenVision_face_segment_v1.safetensors`。使用 ONNX 检测权重时，`onnxruntime` 还需提供匹配当前设备的执行提供器。插件支持首次运行下载模型；启用本项目的深度资源检查时，应先按插件说明备齐文件，再回到 WebUI 刷新检查。新建默认 `er_sde / simple`；旧草稿若使用缺失的 `res_2m_ode / beta57`，需补齐对应选项或手动更换采样设置。

本项目配套扩展当前对 VSR / DLSS 的运行检查支持 **Windows + NVIDIA RTX 30 / 40 / 50**；其他硬件请关闭这两个阶段。插件自身支持的平台范围与 WebUI 的检查范围可能不同。DLL 应按插件说明提前准备，资源检查本身不会下载运行库。

SeedVR2 需要上述原生节点，其他同名 VideoUpscaler 插件不能替代。完整节点契约和模型要求见[依赖清单](docs/dependencies.md)。

### 安装完成后检查

1. 确认 ComfyUI 已启动，默认地址 `http://127.0.0.1:8188` 的 `/system_stats` 与 `/object_info` 可以访问，必需节点已加载。
2. 启动 WebUI，确认连接正常、基础词池可见，主模型、文本编码器、VAE 可选；LoRA Manager 已将主模型与启用的 LoRA 识别为 Anima。
3. 先关闭可选后处理，用一张图验证基础生成、进度、保存和历史；再逐个启用需要的后处理，按资源检查补齐对应依赖。

缺节点先核对上表的 `class_type` 与 ComfyUI 启动日志；缺 Python 包先确认是否装进了 **ComfyUI 实际使用的 Python**。把具体错误交给 Agent 排查，更新节点后重启 ComfyUI 并刷新 WebUI 资源列表。

## 数据保存与开发

| 内容 | 保存位置 |
| --- | --- |
| 个人词条、收藏、LoRA 整理、人物绑定、预设和历史 | 项目 `data/`，可用 `--data-dir` 更改 |
| 生成原图 | ComfyUI 实际输出目录下的 `AnimaRandomWebUI/` |
| 表单草稿、主题与浏览偏好 | 当前浏览器站点的本地存储 |
| 基础词库、模型与插件分类 | 读取已有 Anima Tools、ComfyUI 与 LoRA Manager |

停止 WebUI 后，同时备份整个数据目录和 ComfyUI 原图目录。数据库和缩略图不能代替原图；浏览器草稿也不包含在数据目录中。更多说明见[作品与备份指南](docs/guide.md#7-作品收藏原图与备份)。

<details>
<summary>参与开发与运行检查</summary>

开发检查应在完整 Git 工作目录运行。Release 源码 ZIP 和 GitHub 自动源码归档不包含测试、CI 和开发脚本，正常使用也无需 Node.js。可克隆发布标签获取完整工作目录：

```powershell
git clone --branch anima-webui-v0.2.0 https://github.com/159159pzy-crypto/comfyui-random-image-generation.git AnimaRandomWebUI
Set-Location AnimaRandomWebUI
```

```powershell
# 在完整 Git 工作目录运行
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1 -Dev
.\.venv\Scripts\python.exe scripts/check.py
```

开发检查另需 Node.js 22+，覆盖 Python 与前端测试、静态检查和脚本语法。

</details>

## 交流与致谢

欢迎在 [Issues](https://github.com/159159pzy-crypto/comfyui-random-image-generation/issues) 分享使用反馈，附上版本、复现步骤和已去除私人信息的错误日志。

[使用指南](docs/guide.md) · [依赖说明](docs/dependencies.md) · [发行说明](RELEASE_NOTES.md) · [原项目 Comfy Image Atelier](https://github.com/159159pzy-crypto/comfy-Image-Atelier)

感谢 ComfyUI、Anima Tools 及各节点与模型作者。项目采用 [MIT License](LICENSE)，保留原项目作者版权声明；第三方节点、词库与模型遵循各自许可。
