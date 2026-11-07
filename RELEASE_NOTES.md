# Anima Random WebUI 0.1.0

这是 [Comfy Image Atelier](https://github.com/159159pzy-crypto/comfy-Image-Atelier) 的精简分支首版，专注于轻量的 Anima 随机生图。发布标签为 `anima-webui-v0.1.0`，与旧 Anima Random Studio 系列区分。

## 本版内容

- 保留人物、服装、姿势、背景、表情五池，词条导入、收藏、本地提示词规范与预设。
- 保留 LoRA 管理、人物绑定、批量队列、历史复现与变体、作品收藏及可选放大链路。
- 剔除 Krea 2、单图工作台、图生图、AI 助手、图片反推和 EXE 启动器，使用独立个人数据目录。
- 顶部连接状态与主题按钮靠右对齐；移除人物词池中重复的 LoRA 绑定入口，绑定仍可在 LoRA 管理中维护。
- README 提供节点安装命令、必需模型下载与目录、可选扩展，以及 Sesqui / SeedVR2 放大建议。

## 下载与安装

下载附件 **AnimaRandomWebUI-0.1.0-source.zip** 并完整解压。此包为源码，不包含 Python、ComfyUI、节点、模型或 EXE；也可使用 GitHub 自动生成的 Source code 压缩包。

1. 按 [README](https://github.com/159159pzy-crypto/comfyui-random-image-generation/blob/anima-webui-v0.1.0/README.md) 准备 ComfyUI、Anima Tools、LoRA Manager、默认双采样使用的 SesquiLSR，以及三个 Anima 模型。
2. 安装 Python 3.11+，在项目目录运行 `Install.ps1`。
3. 单独启动 ComfyUI，再运行 `Start.ps1 --anima-tools-dir "你的词库目录"`；词库能自动发现时也可双击 `Start.cmd`。
4. 浏览器访问 `http://127.0.0.1:8191`。PowerShell 执行策略处理方法见 README。

本版不自动迁移旧项目数据。需要保留旧数据时，请保留旧安装，并备份其数据目录与 ComfyUI 原图目录。

`SHA256SUMS.txt` 提供附件 ZIP 校验值；`build-info.json` 记录版本、源码提交与 ZIP 校验值。模型和第三方插件遵循各自许可。

## 验证范围

- 发布前完整本地检查：Ruff、Windows PowerShell 语法解析、10 个 JavaScript 文件语法检查通过。
- Python：1152 项与 419 个子测试通过；Node：310 项通过。
- 顶栏在 1440、800、390 像素宽度下靠右对齐，无重叠或横向溢出。
- 提取阶段已实测普通 / 双采样、LoRA、队列、原种子复现及历史变体，详见 [验收记录](https://github.com/159159pzy-crypto/comfyui-random-image-generation/blob/anima-webui-v0.1.0/docs/acceptance-2026-11-07.md)。
- 可选后处理经过构建与资源检查，尚未完成真实 GPU 生图验收；推荐参数是起点，需按显存与效果调整。
