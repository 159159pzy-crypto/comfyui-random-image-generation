# Anima Random WebUI

**纯粹、轻量的 Anima 随机生图工作台。**

本项目是 [Comfy Image Atelier](https://github.com/159159pzy-crypto/comfy-Image-Atelier) 的精简分支项目：剔除了 **Krea 2、单图工作台、图生图、AI 助手、图片反推和 EXE 启动器**等功能，专注于连接本机 ComfyUI，进行 Anima 五池随机生图。

保留人物 / 服装 / 姿势 / 背景 / 表情词池、LoRA 与人物绑定、批量队列、历史复现、作品收藏和可选放大。源码运行，无需 Node.js 或前端构建；个人数据独立保存，不自动导入原项目数据。

[下载 0.1.0](https://github.com/159159pzy-crypto/comfyui-random-image-generation/releases/tag/anima-webui-v0.1.0) · [使用指南](docs/guide.md) · [完整依赖](docs/dependencies.md) · [发行说明](RELEASE_NOTES.md)

## 1. 准备 ComfyUI 与节点

先安装并启动支持 Anima 的 [ComfyUI](https://github.com/Comfy-Org/ComfyUI)。本项目只连接本机 ComfyUI，默认地址为 `http://127.0.0.1:8188`。

| 节点 / 扩展 | 是否需要 | 用途 |
| --- | --- | --- |
| [ComfyUI-Lora-Manager](https://github.com/willmiao/ComfyUI-Lora-Manager) | 必需 | 提供 LoRA 加载节点及主模型 / LoRA 分类；不使用 LoRA 时也需要 |
| [Comfyui-Anima-Tools](https://github.com/nregret/Comfyui-Anima-Tools) | 必需 | 提供人物、服装、姿势、背景基础词库；表情词库已内置 |
| [SesquiLSR](https://github.com/LoganBooker/SesquiLSR) | 默认流程需要 | 双采样中间调整与可选潜空间放大；Anima 选择 `Wan 2.1` 格式 |

**手动安装：**在 ComfyUI 的 `custom_nodes` 目录打开终端，运行：

```powershell
git clone https://github.com/willmiao/ComfyUI-Lora-Manager.git
git clone https://github.com/nregret/Comfyui-Anima-Tools.git
git clone https://github.com/LoganBooker/SesquiLSR.git
```

用 **ComfyUI 自己的 Python** 安装 LoRA Manager 依赖。下面二选一，在同一 `custom_nodes` 目录运行，并按实际安装位置调整路径：

```powershell
# 官方 Windows 便携版（ComfyUI_windows_portable/ComfyUI/custom_nodes）
..\..\python_embeded\python.exe -m pip install -r .\ComfyUI-Lora-Manager\requirements.txt

# 使用 ComfyUI 根目录 .venv 的源码版
..\.venv\Scripts\python.exe -m pip install -r .\ComfyUI-Lora-Manager\requirements.txt
```

Anima Tools 无需额外 pip 依赖；SesquiLSR 的放大权重随仓库提供，无需另装 requirements。安装完重启 ComfyUI。只想用最少依赖时，可不装 SesquiLSR，在 WebUI 选择「普通底图」并关闭全部后处理；默认双采样即使中间倍率为 1，也需要 Sesqui 节点。

## 2. 下载模型

从 [Anima 官方模型仓库](https://huggingface.co/circlestone-labs/Anima/tree/main/split_files) 下载以下 **3 个必需文件**。下表路径均相对于 ComfyUI 根目录；不要将三者合并放入 `checkpoints`。

| 用途 | 下载文件 | 放置目录 |
| --- | --- | --- |
| Anima 主模型 | [anima-base-v1.0.safetensors](https://huggingface.co/circlestone-labs/Anima/blob/main/split_files/diffusion_models/anima-base-v1.0.safetensors) | `models/diffusion_models/` |
| 文本编码器 | [qwen_3_06b_base.safetensors](https://huggingface.co/circlestone-labs/Anima/blob/main/split_files/text_encoders/qwen_3_06b_base.safetensors) | `models/text_encoders/` |
| VAE | [qwen_image_vae.safetensors](https://huggingface.co/circlestone-labs/Anima/blob/main/split_files/vae/qwen_image_vae.safetensors) | `models/vae/` |
| Anima LoRA | 自选，与 Anima 架构兼容即可 | `models/loras/`，可选 |

下载后刷新模型列表，让 LoRA Manager 完成扫描，确认主模型及启用的 LoRA 的基础模型分类为 **Anima**。资源不在清单中、未分类或分类错误时，WebUI 会阻止生成。模型放在子目录或使用其他兼容文件名时，见[路径映射说明](docs/guide.md#3-模型选择与路径映射)。

## 3. 安装并启动本项目

需要 **Python 3.11+**。下载上方 Release 的源码 ZIP 并完整解压，或使用 Git：

```powershell
git clone --branch codex/anima-random-webui https://github.com/159159pzy-crypto/comfyui-random-image-generation.git anima-random-webui
cd anima-random-webui
```

在项目目录打开 PowerShell，安装一次，然后启动。把下面的 Anima Tools 路径改为你的实际位置：

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Install.ps1
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Start.ps1 --anima-tools-dir "D:\ComfyUI\custom_nodes\Comfyui-Anima-Tools"
```

浏览器会打开 `http://127.0.0.1:8191`。确认连接状态、模型和词池正常，选择随机内容后开始生成。端口不同可追加 `--comfy-url http://127.0.0.1:8189`。

如果词库位于项目相邻的 `comfyui/custom_nodes` 等可自动发现的位置，可直接双击 `Start.cmd`。其他位置可使用上述启动命令，或设置 `ANIMA_TOOLS_DIR` 环境变量后双击。按 Ctrl+C 或关闭终端停止 WebUI。

安装脚本只安装本项目的 Python 依赖；**ComfyUI、节点、模型需按前两步自行准备，ComfyUI 也需单独启动**。更多启动参数见[使用指南](docs/guide.md#1-源码安装与启动)。

## 4. 推荐放大链路

**日常建议从「底图 → Sesqui 潜空间放大 1.5×（含重采样）→ 保存」开始。** 先检查底图构图，再逐步提高倍率；不必同时开启所有后处理。

| 步骤 | 建议起点 |
| --- | --- |
| 底图 | 普通或双采样；默认 30 步、CFG 8、`euler / simple`；双采样中间倍率保持 1，切换步 15 |
| 潜空间放大 | 开启，Sesqui / `Wan 2.1`，倍率 1.5 |
| 潜空间重采样 | 16 步、CFG 4、降噪 0.2、`er_sde / simple` |
| 其他阶段 | 先关闭像素高清、整图后采样、脸部细化、SeedVR2、VSR 和 DLSS |

这是基于本项目参数的建议起点，需要按显存和画面效果调整。**潜空间放大已经包含一次重采样**，再开启「整图后采样」会额外采样一次。

- **尽量保留底图：**底图 → 像素高清 → 保存。使用 Remacri 4× 模型、缩放 50%，关闭其他阶段时最终为底图的 2×；开启整图后采样时不应用这一步缩图。
- **进阶尝试：**底图 → SeedVR2 2× → 保存。先关闭其他放大阶段单独验证，再考虑组合。

### 可选扩展与模型

只安装自己启用的阶段。第三方节点克隆到 `ComfyUI/custom_nodes/`，有 `requirements.txt` 时用 ComfyUI 自己的 Python 安装，然后重启；硬件与运行库按对应仓库说明准备。

| 功能 | 安装来源与模型 |
| --- | --- |
| 像素高清 | ComfyUI 内置节点；下载 [4x_foolhardy_Remacri.pth](https://openmodeldb.info/models/4x-Remacri) 放入 `models/upscale_models/` |
| SeedVR2 | 更新 ComfyUI，使用其原生 SeedVR2 节点；从 [Comfy-Org/SeedVR2](https://huggingface.co/Comfy-Org/SeedVR2/tree/main) 下载 `diffusion_models/seedvr2_3b_int8_convrot.safetensors` 放入 `models/diffusion_models/`，`vae/ema_vae_fp16.safetensors` 放入 `models/vae/` |
| 脸部细化 | [ComfyUI-Forbidden-Vision](https://github.com/luxdelux7/ComfyUI-Forbidden-Vision)；安装 requirements，首次运行自动下载检测 / 分割模型；若缺少 `res_2m_ode / beta57`，改选已安装的采样选项 |
| N 卡 VSR | [ComfyUI-NVIDIA-RTX-VSR-Pro](https://github.com/whmc76/ComfyUI-NVIDIA-RTX-VSR-Pro)；安装 requirements（含 `nvidia-vfx`），按插件要求准备 NVIDIA 环境；不要与注册同名节点的 `Nvidia_RTX_Nodes_ComfyUI` 同时启用 |
| DLSS | [DLSS5-Comfyui](https://github.com/storyAura/DLSS5-Comfyui)；Windows + 支持的 RTX 显卡，安装 VC++ 运行库并按仓库要求准备 DLL / 7-Zip；这是同分辨率处理，不会增加像素尺寸 |

SeedVR2 需要 `SeedVR2Preprocess`、`SeedVR2Conditioning`、`SeedVR2PostProcessing` 这组原生节点，安装其他同名 VideoUpscaler 插件不能替代。全部阶段的固定顺序如下，关闭的阶段直接跳过：

```text
底图 → 像素高清 → 潜空间放大（含重采样）→ 整图后采样
     → 脸部细化 → SeedVR2 → N 卡 VSR → DLSS → 保存
```

本次已实测普通 / 双采样底图、LoRA、历史复现与队列；可选后处理通过流程构建和资源检查，尚未完成真实 GPU 生图验收。详细节点契约见[完整依赖清单](docs/dependencies.md)。

## 数据与开发

- 个人词条、收藏、LoRA 整理、人物绑定、预设和历史：项目 `data/`，可用 `--data-dir` 更改。
- 原图：ComfyUI 实际输出目录下的 `AnimaRandomWebUI/`。备份时同时保留原图与整个 `data/`。
- 表单草稿、主题：浏览器本地存储。基础词库、模型文件和插件分类共享读取，个人整理独立保存。

开发检查（另需 Node.js 22+）：

```powershell
.\Install.ps1 -Dev
.\.venv\Scripts\python.exe scripts/check.py
```

MIT 许可证，保留原项目作者版权声明。第三方节点、词库与模型遵循各自许可。
