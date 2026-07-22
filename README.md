# Anima Random Studio

本地 WebUI 使用 `AnimaBasicV7 (3).json` 的模型、LoRA、采样、高清修复和保存链路，逐张调用本机 ComfyUI。随机画师固定关闭；角色、服装、姿势和背景可分别控制。

## 启动

1. 先启动 `F:\comfyui`，确认可以访问 `http://127.0.0.1:8188`。
2. 双击 `启动Anima随机生图WebUI.bat`。
3. 浏览器会打开 `http://127.0.0.1:8190`。

历史索引保存在 `data\history.sqlite3`，图片仍由 ComfyUI 保存到 `output\AnimaRandom\日期\批次编号`。删除 WebUI 历史不会删除图片文件。

派生的可视化工作流位于项目根目录的 `AnimaBasicV7-Random-WebUI.json`，原始工作流副本保存在 `sources` 目录且不会被修改。
