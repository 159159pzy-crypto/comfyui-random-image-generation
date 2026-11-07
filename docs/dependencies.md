# 依赖与节点清单

本页根据当前源码中的工作流生成和资源检查逻辑编写。节点名称指 ComfyUI `/object_info` 的 `class_type`，不是节点在画布上的自定义标题。依赖存在表示流程可以构建，并不代表该阶段已完成真实 GPU 验收。

## Python、浏览器与开发工具

| 组件 | 要求与用途 |
| --- | --- |
| Python | 3.11+，使用本项目独立 `.venv` |
| `aiohttp` | `>=3.14.5,<4`，WebUI 服务与 ComfyUI 通信 |
| `multidict` | `>=6.9.1,<7`，HTTP 多值数据结构 |
| Pillow | `>=13.0,<14`，本地图像/缩略图处理 |
| 浏览器 | 支持原生 dialog、现代 JavaScript、localStorage 的桌面浏览器 |
| Node.js | 22+，仅运行 JavaScript 语法检查和前端测试；正常启动无需 Node |
| 开发依赖 | 仅 Git 克隆的完整仓库需要；`pytest>=8`、`ruff>=0.15,<0.16`、`setuptools>=68`，由 `Install.ps1 -Dev` 安装 |

权威版本范围见根目录 `pyproject.toml`。静态前端无需 npm 安装或打包。Release 下载包仅包含运行文件与用户文档，不包含测试、CI 或开发脚本。本项目不会代为安装 GPU 驱动、PyTorch、ComfyUI、自定义节点或模型。

## 所有生成模式都需要

本项目使用 Anima 的 UNet、文本编码器和 VAE，不使用整合 checkpoint 加载器代替这三个资源。主模型默认 `anima-base-v1.0.safetensors`，编码器默认 `qwen_3_06b_base.safetensors`，VAE 默认 `qwen_image_vae.safetensors`，编码类型为 `stable_diffusion`。资源映射规则见 [使用指南](guide.md#3-模型选择与路径映射)。

| 节点 | 作用 | 来源/要求 |
| --- | --- | --- |
| `UNETLoader` | 加载 Anima 主模型 | ComfyUI |
| `CLIPLoader`、`CLIPTextEncode` | 加载编码器并编码正负向提示词 | ComfyUI，需支持 Anima 所需编码器 |
| `VAELoader`、`VAEDecodeTiled` | 加载 VAE、分块解码 | ComfyUI |
| `EmptyLatentImage` | 创建底图 latent | ComfyUI |
| `Lora Loader (LoraManager)` | 主模型与 CLIP 的 LoRA 管线 | ComfyUI-Lora-Manager；当前生成图即使没有启用 LoRA 也保留此节点 |
| `PrimitiveInt`、`ComfyMathExpression` | 传递固定种子并让每次任务实际执行 | 支持这些节点的 ComfyUI；本机节点来源分别为 `comfy_extras.nodes_primitive`、`comfy_extras.nodes_math` |
| `SaveImage` | 保存最终原图和工作流元数据 | ComfyUI |

`LoraLoader` 还需在 `/object_info` 中提供有效 LoRA 文件清单，供资源分类检查使用。文件列表和 LoRA Manager 扫描结果必须一致。

### 普通底图

普通底图增加 `KSampler`。关闭所有可选后处理时，不需要 SesquiLSR、SeedVR2、脸部细化、VSR 或 DLSS 节点。其采样器和调度器选项仍必须存在于当前 ComfyUI 清单中。

### 双采样器底图（默认）

双采样器增加两段 `KSamplerAdvanced`，中间使用以下之一：

| 中间调整选项 | 节点 | 条件 |
| --- | --- | --- |
| Sesqui（默认） | `SesquiLatentUpscale` | 来自 SesquiLSR；默认格式 `Wan 2.1`，默认半精度开启，倍率范围 1–2 |
| bilinear、bislerp、nearest-exact、area、bicubic | `LatentUpscaleBy` | ComfyUI 对应插值选项必须可用 |

默认总步数 30、切换步 15、中间倍率 1、两段 CFG 均为 8，采样器 `euler`、调度器 `simple`。倍率为 1 仍构建所选中间调整节点，因此默认双采样器仍需 SesquiLSR。可以选择普通底图，或为双采样器选择受支持的其他插值方法。

## 可选后处理

当前仅支持 v4，执行顺序为：像素高清 → 潜空间放大 → 整图后采样 → 脸部细化 → SeedVR2 → N 卡 VSR → DLSS。只为启用的阶段添加对应节点与资源；未启用阶段的资源缺失不应阻止普通流程。

| 阶段 | 节点要求 | 模型与说明 |
| --- | --- | --- |
| 像素高清 | `UpscaleModelLoader`、`ImageUpscaleWithModel`；未启用整图后采样时还使用 `ImageScaleBy` | 默认 `4x_foolhardy_Remacri.pth`；实际输出还受缩放百分比和后续阶段影响 |
| 潜空间放大 | `SesquiLatentUpscale` 或 `LatentUpscaleBy`，以及 `KSampler` | 默认 Sesqui、1.5 倍；该阶段自带重采样，默认 16 步、CFG 4、`er_sde` / `simple`、降噪 0.2 |
| 整图后采样 | `KSampler` | 独立于潜空间重采样，默认 16 步、CFG 4、`er_sde` / `simple`、降噪 0.2 |
| 脸部细化 | `ForbiddenVisionFaceProcessorIntegrated` | 本机来源 `forbidden-vision`；使用当前主模型/LoRA。默认采样器 `res_2m_ode`、调度器 `beta57` 必须在相应节点中可用，或改选现有选项 |
| SeedVR2 | `ImageScaleBy`、`UNETLoader`、`VAELoader`、`SeedVR2Preprocess`、`VAEEncodeTiled`、`SeedVR2Conditioning`、`KSampler`、`VAEDecodeTiled`、`SeedVR2PostProcessing` | 默认模型 `seedvr2_3b_int8_convrot.safetensors`，VAE `ema_vae_fp16.safetensors`；本机三个 SeedVR2 专用节点来源为 `comfy_extras.nodes_seedvr` |
| N 卡 VSR | `RTXVideoSuperResolution` | 本机来源 `nvidia-rtx-vsr-pro`；硬件、驱动和插件运行库要求由该插件决定 |
| DLSS | `DLSS5NeuralRender` | 本机来源 `DLSS5-Comfyui`；与 VSR 是两个独立节点，不能相互替代 |

若像素高清已将数据变成图像，随后又启用潜空间放大或整图后采样，需要 `VAEEncodeTiled` 重新编码。最终图像使用分块 VAE 解码保存。潜空间和整图重采样继承底图最后一段的模型、LoRA 与正负向提示词。

SeedVR2 使用 ComfyUI 的上述节点契约；仅安装另一个同名插件、但未提供这些 `class_type`，不能满足依赖。Sesqui 的格式选项、脸部细化检测/分割模型、VSR/DLSS 的驱动或本机运行库等，还需按各节点自身安装说明准备。本项目只读取运行中节点声明的选项与范围，不下载这些资源，也不静默改成其他节点。

## ComfyUI-Lora-Manager 的边界

这是当前项目的必需依赖，承担两类职责：

1. 提供工作流使用的 `Lora Loader (LoraManager)` 节点。
2. 提供主模型/LoRA 的目录与分类 API、封面、文件信息和原始触发词。项目读取 `/api/lm/checkpoints/unet_roots`、`/api/lm/checkpoints/list`、`/api/lm/loras/roots`、`/api/lm/loras/list` 及预览接口。

分类先按 ComfyUI 加载器中的相对路径与 LoRA Manager 扫描的绝对路径、模型根目录精确匹配，再检查 `base_model`。只有 Anima 被识别为可生成资源；未索引、架构未知、不支持或路径歧义会显示具体原因。不要仅通过改文件名期待模型变为可用。

| 共享资源，只读获取 | 本项目独立保存 |
| --- | --- |
| 模型/LoRA 文件、目录、模型名称、`base_model`、封面、文件大小、原始触发词 | 星标、标签、备注、收藏分组、默认权重、人物绑定、触发词覆盖 |

修改 WebUI 中的整理数据不写回插件。首次个人整理数据为空，不继承插件的星标、标签或备注。“恢复插件原值”只取消本地触发词覆盖，重新读取原始触发词。若要改变共享模型文件或 `base_model`，需在外部 ComfyUI 环境中管理；这会影响其他使用该 ComfyUI 的应用。

预览通过本机代理读取，限制资产类型与大小；部分插件返回通用二进制 MIME 的 PNG/JPEG/GIF/WebP 会依据有界文件头识别。预览读取失败会显示占位内容，不改变模型分类或本地资料。

## Anima Tools 与本地数据

Anima Tools 用于提供人物、服装、姿势、背景基础词库，不需要在生成工作流中放置 Anima Tools 的随机节点；提示词抽取由本项目完成。表情基础词库内置。具体路径发现和必需文件见 [使用指南](guide.md#2-基础词库发现)。

WebUI 的数据库、JSON 文件、缩略图和日志默认位于项目 `data/`。原图位于 ComfyUI 输出目录的 `AnimaRandomWebUI/` 下，不在数据库中。基础词库只读共享；个人词条、收藏、规则、预设、LoRA 整理和绑定使用本项目的数据文件。

## 检查依赖是否齐全

先确认 ComfyUI 能访问 `/system_stats` 和 `/object_info`，再打开 WebUI 查看连接状态与模型分类。选择实际要使用的底图模式和后处理；界面及提交前检查会列出缺失节点、模型或无效采样选项。更新 ComfyUI/节点并重启后，刷新资源清单。

本机发现的节点模块名用于说明此环境中的提供者，不构成所有安装环境的版本兼容承诺。代码与模拟测试覆盖配置构建和校验；实际运行能力以模型、驱动、节点运行依赖及真实 GPU 验收结果为准。
