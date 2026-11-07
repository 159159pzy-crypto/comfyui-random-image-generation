# Anima Random WebUI 0.1.1

[Comfy Image Atelier](https://github.com/159159pzy-crypto/comfy-Image-Atelier) 的精简分支，专注于轻量的 Anima 五池随机生图。本版修正项目介绍和发布包内容，生图功能、API 与个人数据格式保持不变。

## 本版更新

- 恢复完整图文介绍，展示随机工作台、角色池、LoRA 管理三张原始截图；安装步骤、节点与模型准备、推荐放大链路继续保留。
- 发布包改为从指定 Git 提交按白名单构建，只包含运行代码、安装入口、用户文档和明确授权的产品截图。
- 排除测试代码与夹具、CI、开发脚本、内部验收记录、个人数据和缓存。GitHub 自动生成的源码 ZIP / tar.gz 使用相同导出规则，并检查实际下载内容。
- 增加归档内容验证，检查完整文件清单、危险路径、软链接、图片元数据及文件摘要。

## 下载与安装

下载 **AnimaRandomWebUI-0.1.1-source.zip** 并完整解压。本项目按源码运行；该包不包含 Python、ComfyUI、节点、模型或 EXE。GitHub 自动生成的 Source code ZIP / tar.gz 同样可用于安装。开发测试请通过 Git 克隆完整仓库。

1. 按 [README](https://github.com/159159pzy-crypto/comfyui-random-image-generation/blob/anima-webui-v0.1.1/README.md) 准备 ComfyUI、Anima Tools、LoRA Manager、默认双采样使用的 SesquiLSR，以及 Anima 三个基础模型。
2. 安装 Python 3.11+，在解压目录运行 `Install.ps1`。PowerShell 执行策略处理方法见 README。
3. 单独启动 ComfyUI，再运行 `Start.ps1 --anima-tools-dir "你的词库目录"`；词库可自动发现时，也可双击 `Start.cmd`。
4. 浏览器访问 `http://127.0.0.1:8191`。

截图中的作品、模型及目录用于展示，不作为预装资源提供；其他模型目录名称不表示本项目支持对应架构。

`SHA256SUMS.txt` 用于校验 ZIP；`build-info.json` 仅记录版本、源码提交、附件名和 SHA-256，不包含本机路径或个人信息。

## 升级与数据

从 0.1.0 升级前，停止 WebUI，并备份原项目的整个 `data/` 和 ComfyUI 原图目录。解压到新目录、安装依赖后，可通过 `--data-dir "原数据目录"` 继续使用原有个人数据。新旧实例不要同时使用同一数据目录。

0.1.0 下载包包含测试和内部验收材料，因此从公开 Release 列表撤下；原 Git 标签保留，不重新指向其他提交。更早的 Anima Random Studio 历史版本不受此次修正影响。

## 验证范围

本版验证包括完整 Python / Node 回归、发布包内容检查、隔离安装、启动帮助、静态资源与工作流模板加载，以及首次启动时个人数据为空。GitHub Actions 覆盖 Windows / Ubuntu 与 Python 3.11 / 3.13。

本版不新增 GPU 生图验收。可选放大与修复阶段仍需对应节点、模型和硬件；README 推荐参数是起点，不代表所有组合已完成真实 GPU 验证。
