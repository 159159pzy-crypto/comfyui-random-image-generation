# Windows 安装启动器

Windows 10/11 x64，**WinUI 3（Windows App SDK，C#）**，非打包（unpackaged）+ 自包含文件夹分发。用户无需安装 .NET 或 Windows App Runtime，也不需要从源码启动 WebUI。模型、ComfyUI 和第三方节点按需从各自来源下载，不随启动器再分发。

> 说明：WinUI 3 不支持单文件发布。启动器输出为 `dist\launcher` 文件夹（`AnimaRandomStudio.exe` 与其依赖 DLL），请将**整个目录**一并分发。

## 使用

1. 双击 `AnimaRandomStudio.exe`，首次打开选择导入已有 ComfyUI 或新建环境。
   - 默认进入「服务」主页，可独立查看并控制 ComfyUI 与工作台的启动/停止（外部实例只检测不停止），也可一键全部启动/停止。
2. 导入可选择包含 `main.py` 的目录，或便携版上级目录。自动识别 `.venv`、`venv`、`python_embeded`；其他环境手动选择 `python.exe`。点击“检查 / 导入目录”。
3. 新建请选择空目录、来源，点击“安装环境 / 补齐缺失节点”。新安装仅支持 NVIDIA，需已安装支持 CUDA 12.8 的 NVIDIA 驱动；AMD/Intel 用户导入已经可用的 ComfyUI。安装过程记录阶段，失败后可在同一目录重试。
4. 模型页面按基础生成、高清修复、Detailer 分组。检查来源、大小、目标、SHA-256 和授权后勾选下载。**只有基础生成的三个模型必需**，其他模块按需安装。未安装高清模型时，首次默认关闭高清修复。
5. 点击“启动并打开工作台”。检查 ComfyUI `/system_stats`、`/object_info`、必需节点和 WebUI `/api/status` 后打开浏览器。以后双击自动启动并驻留托盘。
6. 如需验证生成，主动点击“生成一张测试图”：生成一张 512×512、4 步、无 LoRA/高清/Detailer 的测试图片。存在其他生成任务时拒绝提交。

安装建议在环境盘预留至少 20 GiB；模型另需约 6 GiB。安装工具、Python 和下载缓存还会占用启动器状态目录所在盘。模型下载前按目标磁盘核算剩余空间；下载完成验证大小和 SHA-256 后才原子发布。暂停保留 `.part`，重试或更换镜像继续；取消只删除所选下载的临时文件。已存在但校验不符的文件不会覆盖，请先手动备份。安装记录最后一个阶段，中断后重试会自动续跑幂等步骤；克隆残留的 `.anima-installing` 临时目录也会被识别，完成的克隆直接接管、未完成的继续拉取，无需手动清理。

## 模型路径与来源

`manifests/model-manifest.json` 是唯一自动下载清单。条目只有 HTTPS URL、确定大小和完整 SHA-256 同时存在才可自动安装。不按文件名猜地址。眼部和 NSFW 检测模型目前为仅检测 / 手动导入；界面列出规定目录。LoRA 只扫描本地文件，具体选择留在工作台。

通过 ComfyUI 自己的 `folder_paths` 与 `load_extra_path_config` 解析 `extra_model_paths.yaml`，支持 `models/clip`、`models/unet`、安全子目录及 Impact 的 `ultralytics_bbox` / `ultralytics_segm`。新下载遵循配置的默认搜索目录。启动时从 `/object_info` 取得实际模型相对名称，映射到工作流；同名歧义会报错。启动后新增模型可能需要重启 ComfyUI。

提供官方、HF/Python 国内镜像、GitHub 镜像组合、自定义来源。Git 镜像可填 HTTPS 前缀或 `{url}` 模板；自定义 ComfyUI 仓库也必须包含清单锁定提交。镜像只作用于本程序子进程，不修改系统 Git/pip 配置。镜像可用性取决于提供方。PyTorch 使用官方固定 Windows CUDA wheel，UV 管理固定 Python 版本。

信任边界：ComfyUI/节点锁定到 40 位提交并在克隆后复核，模型与工具下载锁定 SHA-256；Python 包只锁定版本号、不逐包锁定哈希。自定义 Python 包索引等价于信任该索引提供的全部 wheel——请只填写可信源。

若官方模型站需要令牌，在设置里保存至 Windows Credential Manager；`launcher.json` 不存密码。令牌只发送给对应官方 Civitai/Hugging Face 主机，不传给镜像或重定向 CDN。不要将凭据写入自定义 URL。源码 Git/pip 暂不支持需要交互认证的私有仓库或私有包源。

## 服务、状态与升级

- 默认 ComfyUI `127.0.0.1:8188`，工作台 `127.0.0.1:8190`。冲突时提示选择自动分配空闲端口或手动修改，并同步保存 WebUI 的 ComfyUI 地址。
- 只停止本程序创建、PID/启动时间/可执行路径一致的进程。退出并保留服务后，再次运行能恢复进程所有权。外部 ComfyUI 始终不归启动器停止。
- 外部实例仅在 `/system_stats` 明确报告同一绝对 `main.py` 时复用；无法确认目录时按端口冲突处理。现有源码 WebUI 不具备启动器身份信息时也会提示换端口。
- 关闭窗口最小化到托盘；“退出（保留服务）”保持服务；“停止服务并退出”停止拥有的服务。设置页可创建桌面快捷方式。
- 配置：`%LOCALAPPDATA%\AnimaRandomStudio\launcher.json`；日志：`logs\`；独立用户数据：`data\`；应用版本：`versions\`。高级验收可用 `ANIMA_LAUNCHER_HOME` 指向隔离状态目录。
- 不自动拉取最新 ComfyUI 或节点。导入已有节点版本会保留，并在日志标注版本差异；启动时再验证必需类是否实际加载。正在运行的 ComfyUI 端口会阻止依赖安装。
- 替换 EXE 后，从设置主动“应用内置新版”。启动健康检查通过才保存版本指针，旧资源保留；停止工作台后可“回退上一工作台版本”。用户数据与应用代码分离。应用回退不回滚用户生成的数据。
- 要更换锁定的 ComfyUI/节点版本，使用新版清单在**另一空目录新建环境**，验证后切换目录；旧环境保留用于回退。不在用户现有节点目录强行 checkout/reset。模型可通过 `extra_model_paths.yaml` 共用，避免重复下载。

## 开发和构建

需要 .NET 8 SDK 和 PowerShell 7；最终用户不需要 SDK。UI 工程为 `launcher/Anima.Launcher.WinUI`（WinUI 3，非打包、`WindowsAppSDKSelfContained`）。

```powershell
dotnet run -c Release --project launcher/Anima.Launcher.Tests
python -m pytest -q
pwsh ./Build-Launcher.ps1
```

构建脚本优先使用本机 `%LOCALAPPDATA%\Codex\dotnet-sdk-8`，否则使用 PATH 中的 SDK。输出 `dist/launcher/AnimaRandomStudio.exe` 和 `SHA256SUMS.txt`。资源打包仅包含 WebUI 源码、静态文件、模板和许可证，不包含本机数据、密钥、模型或 `.venv`。构建时会打包当前工作树中的前端文件。

```powershell
# 只读导入诊断；完整哈希扫描增加 --hashes
./dist/launcher/AnimaRandomStudio.exe --diagnose F:\comfyui --report F:\report.json
# 外部服务生命周期验收；--generate 会实际生成一张图片
dotnet run -c Release --project launcher/Anima.Launcher.Tests -- --live F:\comfyui --generate
# 新建 NVIDIA 环境（会下载 Python / CUDA wheel / 节点及依赖）
dotnet run --project launcher/Anima.Launcher.Tests -- --install-new F:\AnimaTest\ComfyUI
# 验证新建环境的启动/停止；先备齐模型，可用 extra_model_paths.yaml 共享
dotnet run -c Release --project launcher/Anima.Launcher.Tests -- --live F:\AnimaTest\ComfyUI --owned --generate
```

不在普通 CI 中运行 GPU 或大模型下载验收。Windows CI 运行核心测试、Python 测试与单文件发布。清单维护者必须在改动提交或依赖版本后重新执行真实安装和生成。

## 发布

正式宣发前使用可信的代码签名证书。示例：

```powershell
pwsh ./Build-Launcher.ps1 -CertificateThumbprint '<CurrentUser/My 代码签名证书指纹>'
```

签名完成后才生成 SHA-256。没有证书时生成的是**未签名候选包**；不应声称已完成 Authenticode。发布方必须逐项复核模型作者许可与再分发条款；哈希证明文件身份，不代表授予模型使用或分发许可。第三方链接和版本见三份清单。
