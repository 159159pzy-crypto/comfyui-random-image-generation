# Anima 启动器(WinUI 3)代码审查报告

审查日期：2026-09-25
审查范围：`launcher/` 全部三个工程(Core / WinUI / Tests，共 26 个源文件)、3 份清单、`probe_comfy.py`、`Build-Launcher.ps1`、`.github/workflows/launcher.yml`、Python 侧集成点(`anima_webui/launcher.py`、`server.py` 的 `/api/launcher-health` 与启动参数、`tests/test_launcher.py`)
审查方式：全量逐行精读 + 本机实测(`dotnet` 测试、完整 `pytest`、`dotnet publish` 真实编译、`--diagnose` 对 F:\comfyui 实跑)+ 最小化复现工程验证疑点

---

## 一、总体评价

| 领域 | 评级 | 一句话 |
| --- | --- | --- |
| 下载与凭据(Downloads/Credentials) | A- | 边界校验、断点续传、凭据隔离都写得很硬，测试覆盖也密 |
| 进程与生命周期(Processes/Program) | B+ | 所有权恢复、PID 复用防护都考虑到了，但取消传播有缺口 |
| 安装幂等(Installer/Inventory) | B | 阶段日志、staging 目录接管做得好，但有一个已实证的死代码 bug |
| UI 与线程(WinUI 层) | C+ | 页面/状态分离干净，但日志事件跨线程是真实崩溃路径 |
| 测试与工程化 | D+ | Core 测试质量不错但**当前套件跑不完**(真 bug 触发崩溃式失败)；launcher/ 整个未纳入 git 版本控制 |
| **综合** | **B-** | 设计成熟度明显高于一般自写启动器，但"安全锁死 SAM2"这条承诺已经失效且没人发现——因为测试根本没跑通 |

**基线验证结果(本机实测):**

- `python -m pytest -q`:**140 通过 + 81 子测试通过**(含 `test_launcher.py` 的 launcher-health / 资源映射用例)
- `dotnet run -c Release --project launcher/Anima.Launcher.Tests`:**37 项 PASS 后崩溃**(P0-1 导致，见下文；不是"测试失败"而是进程级 NullReferenceException)
- `dotnet publish`(WinUI, Release, win-x64):**成功**，产物可生成
- `AnimaRandomStudio.exe --diagnose F:\comfyui`:**成功**，9 个模型全部识别"已存在"，节点锁定状态正确，报告 JSON 结构完整

**做得好的地方(实名表扬):**

- `JsonFile.Write` 全程 temp+rename 原子写(Configuration.cs:10-16)；损坏配置自动改名 `.corrupt-*` 备份后空配置启动(LauncherState.cs:58-66)
- 下载链是教科书级：HTTPS-only + `Safety.Under` 拒绝 `..`/UNC/绝对路径 + Range/Content-Range 一致性校验 + ETag/If-Range + 60s 空闲超时 + 校验后才 `File.Move` 落位(Downloads.cs 全文)
- 凭据只送 civitai.com / huggingface.co 官方主机，跨主机重定向时剥离 Authorization——测试用 FakeHandler 逐条断言过(Tests/Program.cs:77-81)
- `ExtractZip` 拒绝 zip 目录穿越与符号链接条目(Installer.cs:30-42)；本机 payload.zip 23 个条目已验证零恶意
- 进程所有权三元组(PID + StartTime + 可执行路径)防止误杀系统里恰好复用 PID 的无关进程(Processes.cs:247-260)
- `--no-build-isolation` + 本机 git/uv 隔离工具链 + 镜像只作用于子进程环境变量(`GIT_CONFIG_*`/`PIP_INDEX_URL`)，不污染系统 Git/pip 配置(Installer.cs:17-29)
- 测试自带 FakeHandler 桩，断点续传、镜像续传、HTTP 降级拒绝都有断言——这套测试的**设计**是好的，只是当前被 P0-1 挡住了

---

## 二、问题清单

### P0 — 立即修(各 30 分钟内)

**P0-1 | `PinSam2` 永远返回 null：SAM2 锁定提交承诺失效(死代码)**

`launcher/Anima.Launcher.Core/Installer.cs:101-107`

```csharp
var lines = content.Replace("\r\n", "\n").Split('\n').Select(line =>
{
    if (!Sam2Line.IsMatch(line.Trim())) return line;
    found = true;                                   // ← Select 是惰性枚举
    return "git+https://github.com/facebookresearch/sam2@" + commit;
});
return found ? string.Join("\n", lines) : null;     // ← found 在这里先被读取
```

`Select` 延迟执行：`found ? ... : null` 判断时 `lines` 尚未被枚举，`found` 永远是 `false` → **永远返回 null**。我编译了一个 20 行的最小工程直接调用真实 `Installer.PinSam2` 实证：含 3 行 sam2 的输入返回 `NULL`。

后果是双重的：
- 安全：`manifest.sam2Commit`(`2b90b9f…`)从未真正生效；节点 requirements 里的 sam2 git 依赖会按上游 HEAD 装(悬空引用，违反 README"锁定到 40 位提交"的供应链承诺)
- 正确性：`Pip()` 里 `pinned is not null` 分支永不进入 → `--no-build-isolation` 和"先装 setuptools/wheel"也从未执行过 → 若节点确实拉 sam2,build isolation 下会对着错误的构建环境编译
- 可观测性：`Anima.Launcher.Tests` 跑到第 38 项(`PinSam2`)时 `pinned.Split` 拿到 null → **整个测试进程 NullReferenceException 崩溃**,`Commands.Run` 超时等后续用例永远跑不到

**修复**:`var lines = …Select(…).ToList();` 一行即可(先物化再判断)。顺便建议把 Tests 里 `Check(pinned…)` 前加 null 兜底，让测试报告失败而不是崩溃退出。

**P0-2 | 日志事件跨线程：安装期间打开"日志"页会 RPC_E_WRONG_THREAD 崩溃**

调用链：

```
Commands.Run → process.OutputDataReceived(线程池线程)
  → log?.Invoke(e.Data)          // Processes.cs:27
  → Installer 的 log = LauncherState.AppendLog   // LauncherState.cs:198
  → LogAppended?.Invoke(line)    // LauncherState.cs:78
  → LogsPage.OnLog → LogBox.Text += …            // LogsPage.xaml.cs:25
```

对比 `MainWindow.WireState`(MainWindow.xaml.cs:66-89):**所有**其他状态事件都包了 `DispatcherQueue.TryEnqueue`，唯独 `LogAppended` 没有——它唯一的订阅者 `LogsPage` 直接改 `TextBox`。WinUI 对跨线程 UI 访问抛 `COMException/RPC_E_WRONG_THREAD`;`OnOutputDataReceived` 里未捕获的异常会终止进程。

复现条件：任务在跑(下载/安装/克隆，git/pip 会逐行回调 log)+ 用户停留在「日志」页。这正是用户最可能开日志页的场景。

**修复**:`LogsPage.OnLog` 里先 `DispatcherQueue.TryEnqueue`，或在 `LauncherState.AppendLog` 尾部统一 marshal。注意 `LogLines.Add` 同样在非 UI 线程被 `string.Join` 枚举竞争(LogsPage.xaml.cs:13),marshal 时一并解决。

### P1 — 发布前修

**P1-1 | `launcher/` 整个目录未纳入 git**

`git status` 显示 `launcher/`、`.github/workflows/launcher.yml`、`Build-Launcher.ps1`、`anima_webui/launcher.py`、`tests/test_launcher.py` 全部为 `??`(未跟踪)。后果：
- `launcher.yml` 这条 CI 永远不会在 GitHub 上跑(它要跑 `dotnet run` 测试——也就是当前必崩的那套)
- 服务端 `server.py`/`comfy.py`/`pyproject.toml` 的 launcher 配套改动(`--resource-paths`、`launcher_health`、`aiohttp` 依赖声明)散落为本地未提交修改，与 launcher/ 脱节
- 一旦发布，SHA256SUMS 证明的 EXE 与仓库代码对不上

**修复**:`git add` 全部 launcher 相关文件并提交；README 顶部声明的公开仓库与本地工作树保持一致。

**P1-2 | 端口冲突只重试一次**

`LauncherState.cs:351-362`:`StartServicesAsync` 捕获一次 `PortConflictException` → 弹"自动分配"对话框 → 调用 `StartWebUIAsync` 但**没有第二层 try**。若新分配的端口刚好被抢占，或 ComfyUI/WebUI 两个端口都被占用，`StartWebUIAsync` 二次抛 `PortConflictException` 直达 `TaskFailed`,用户看到一个"端口被占用"错误而不是二次分配机会。

**修复**:把第二次 `StartWebUIAsync` 也包进冲突处理，或循环最多 N 次。

**P1-3 | 取消令牌不传进 HTTP 调用**

`ServiceManager.Json`(Processes.cs:65-68)用 `Http.GetStringAsync(url)`,30s 超时与调用方 `ct` 完全脱节；`Ready()`(Processes.cs:235-246)每轮先 `GetStringAsync` 再检查 `timeout.Token`。ComfyUI 启动最坏 6 分钟轮询期间，"暂停任务"要等当前那次 HTTP 返回才生效——实际最多卡 30s,但用户感知是"点了暂停没反应"。`Commands.Run` 里同理:`WaitForExitAsync(deadline.Token)` 正常，但 `process.Kill(true)` 之后的 `WaitForExitAsync()`(Processes.cs:35)裸等，极端情况下挂死无超时。

**修复**:`Json` 接 `CancellationToken` 透传 `GetStringAsync(url, ct)`;`Kill` 后的等待给 5s 上限。

**P1-4 | `versions/` 目录无限堆积**

`Program.ExtractResources`(Program.cs:164-178)每次新 payload 哈希都 `Directory.Move` 出一个 `app-<hash>`，从不清理。本机 `versions\0.3.0.0\` 已实测堆了 **3 个** app-* 目录。单个体积 ~150KB 不算大，但外加每次 EXE 版本号变化再开一层 `versions\<ver>\` 目录,长期累积。

**修复**:提取成功后删除 `bundled` 之外的同级 `app-*` 目录；旧版本号目录保留上一个做回退即可。

### P2 — 排期修

1. **`SameRoot` 对相对路径 main.py 漏判**(Processes.cs:73-83):`Path.IsPathRooted(text)` 只在 argv 是绝对路径时比对；用户手动 `cd F:\comfyui && python main.py` 启动的 ComfyUI,argv 里是 `"main.py"` 相对路径 → `SameRoot` 返回 false → 被当成端口冲突报错，而不是正确识别为同根外部实例。
2. **ComfyUI 启动期间无状态文案**:`StartProcess` 调 `Commands.Run` 传 `log=null`(Processes.cs:212),bootstrap 的 6 分钟 `Ready` 轮询期间 UI 只有进度条停在某处，建议 `SetStatus("等待 ComfyUI 就绪…")`。
3. **`--diagnose` 写死 8188**(Program.cs:201):查 `http://127.0.0.1:8188/system_stats` 而不读 `config.ComfyPort`,自定义端口后诊断报告里的 comfy 字段永远是默认端口的(可能是别的程序)。
4. **`Arg()` 把下一个 flag 当值**(Program.cs:189):`--diagnose --hashes` 会把 `"--hashes"` 当目录路径，报"目录不存在"而非用法错误。
5. **`JsonFile.Write` 没有 fsync**:temp+rename 已原子，但断电场景下文件内容可能还在磁盘缓存——配置文件场景可接受，`processes.json`/`install-state.json` 崩溃恢复语义会弱一点，建议加 `FileStream.Flush(true)`。
6. **环境页编辑静默丢弃**:`SyncConfig()` 只在用户点"检查/安装"时把 `PendingEnvironmentFields` 写进 Config;`MainWindow.ReadPagesIntoConfig` 只同步**当前可见页**。用户在环境页改了目录但没点检查，直接到服务页点"全部启动" → 改的东西没生效，没有任何提示。
7. **ServiceCard 徽标死三元**(ServiceCard.xaml.cs:54-55):`state == 0 ? Secondary : Secondary` 两个分支相同；以及 ComfyUI/环境两个 Nav 项同用一个 `&#xE80F;` 图标，视觉区分度低。
8. **LogBox 拼接是 O(n²)**(LogsPage.xaml.cs:24-25):`LogBox.Text +=` 每次复制全文；装一次环境几千行日志时 UI 明显卡。改 `AppendText` 或 `ItemsRepeater`/`ListView`。
9. **`torchIndex` 字段死代码**(manifest + Configuration.cs:52):`RuntimeManifest` 声明了 `TorchIndex`,Installer 全程未读——torch wheel 是直连 `TorchWheels[].Url` 下载的，要么删字段要么真的用它做 pip `--index-url`。
10. **UA 版本漂移**(Downloads.cs:17):`AnimaRandomStudio/0.3` 与 csproj `0.3.0` 字面量分离，升版本号会忘改；建议从 `AssemblyInformationalVersion` 读。
11. **`AsyncCommand`/`RelayCommand` 从未被实例化**(Shared/):`AsyncCommand` 全项目无引用，托盘用的是 `new RelayCommand(ShowWindow)`——删掉或接上。
12. **`ComfyArguments` 无 UI 入口**:`LauncherConfig.ComfyArguments` 字段 + `Validate` 保留参数校验都有了，但设置页没有输入框，只能手改 launcher.json。
13. **测试套件无框架**(Tests/Program.cs):手写 `Check/Throws` + top-level statements,没有断言库、没有失败继续跑、没有标准退出码聚合；P0-1 一出后续用例直接消失。建议迁 MSTest/xUnit 或至少把 `Check` 包 try/catch 记数。
14. **CI 小问题**(launcher.yml):`windows-latest` 浮动 runner、上传 artifact 只含 EXE 和 SHA256SUMS.txt 而 `dist\launcher` 是文件夹分发——下载 artifact 的用户拿到单个 EXE 跑不起来，应打包整个目录为 zip。
15. **`argparse` 缺 `--version`**:`AnimaRandomStudio.exe` 没有 CLI 版本号输出；`--diagnose` 报告里也没有 launcher 自身版本，排障时无法确认用户跑的是哪一版。

### P3 — 记录备查(影响极小或属设计取舍)

- `Commands.Run` 环境变量注入 `PYTHONUTF8/PYTHONIOENCODING` 对 portable python 的 `_pth` 隔离行为良好，但 GIT_CONFIG_* 在 `GitMirror=""` 时仍写 `GIT_CONFIG_COUNT=0`,无副作用仅冗余。
- `Safety.Https` 拒绝任何 query 含 `key=` 的 URL,可能误伤个别合法带 `key` 参数的镜像地址(保守优先，可接受)。
- `ExtractResources` 用 `EndsWith("payload.zip")` 等文件名后缀匹配嵌入资源，若将来同名文件增多会有歧义，建议改用 `LogicalName` 显式命名。
- `Program.Log` 每行 `File.AppendAllText`(开-写-关)加进程内锁，高频日志下 IO 次数多；日志量目前不大，先观察。

---

## 三、信任边界复核(已逐条核实)

| 承诺 | 实现 | 状态 |
| --- | --- | --- |
| 只发 HTTPS | `Safety.Https` 拒绝 http/user-info/fragment/token/key= | ✅ |
| 凭据不出主机 | `Downloads.Request` 判断 `uri.Host == original.Host && host in {civitai,hf}`;跨重定向剥离 | ✅(测试覆盖) |
| 凭据不落盘 | CredWrite/CredRead 直通 Windows Credential Manager;`launcher.json` 无 token 字段 | ✅ |
| 仓库锁提交 | `Safety.Commit` 40 位 hex;`Clone` 克隆后 `rev-parse` 复核 | ✅(但 sam2 走 pip,见 P0-1) |
| 模型锁哈希 | `Downloads.Fetch` 强校验 size+SHA-256 后才落位 | ✅ |
| 镜像不改系统 | `GIT_CONFIG_*`/`PIP_*` 只注入子进程环境 | ✅ |
| zip 安全 | 拒绝 `..`/绝对路径/符号链接 | ✅(测试覆盖) |
| 外部进程不杀 | `SameRoot`/`IsOurWebUi`/`OwnedProcess` 三重身份判断 | ✅(除 P2-1 相对路径漏判) |
| pip 依赖锁定 | `PIP_CONSTRAINT` 全量锁文件(managed)/torch 导入锁(imported) | ✅ |
| **sam2 锁提交** | `PinSam2` 意在重写 requirements | ❌ **死代码，承诺未兑现(P0-1)** |

## 四、修复路线图

**第 0 步(今天，共 ~1 小时)**
1. `PinSam2`:`Select` 后加 `.ToList()` → 重跑 `dotnet run -c Release --project launcher/Anima.Launcher.Tests` 应全绿
2. `LogAppended` 跨线程:`LogsPage.OnLog` 改 `DispatcherQueue.TryEnqueue`
3. `git add` launcher 全套文件 + 服务端配套改动，让 CI 真正能跑

**第 1 步(发布前，共 ~半天)**
4. P1-2 端口冲突循环重试(最多 3 次)
5. P1-3 `Json()`/`Ready()`/Kill 后等待全部接 `ct`
6. P1-4 `versions/` 提取后清理旧 `app-*`

**第 2 步(下一迭代)**
7. 测试迁框架(MSTest)，把 `--live`/`--install-new` 保留为独立验收入口
8. P2-1~P2-15 按序消化；`--diagnose` 读 config 端口、launcher 版本号入报告、`versions` GC 一并带上
9. WinUI 侧可访问性走查:`Card`/`ServiceCard` 自定义控件补 `AutomationProperties`

## 五、复验清单

```powershell
dotnet run -c Release --project launcher/Anima.Launcher.Tests   # 期望:全绿(P0-1 修复后应 ~40 PASS)
python -m pytest -q                                              # 期望:140 全绿(回归)
dotnet publish launcher/Anima.Launcher.WinUI -c Release -r win-x64 -o $env:TEMP\pub
dist\launcher\AnimaRandomStudio.exe --diagnose F:\comfyui --report output\diag.json
git status --short -- launcher .github/workflows/launcher.yml Build-Launcher.ps1 anima_webui/launcher.py tests/test_launcher.py   # 期望:全部已跟踪
```

手工验证：安装任务进行中打开「日志」页不崩(P0-2)；把 ComfyUI/WebUI 端口同时占掉后点"全部启动"能被引导分配(P1-2)；连续两次换 EXE 后 `versions\` 里只剩当前+上一版(P1-4)。

---

## 六、已修复(2026-09-25,提交 `d6cdb1d`)

| 编号 | 修复 | 验证 |
| --- | --- | --- |
| P0-1 | `PinSam2` 的 `Select` 物化为 `.ToArray()`；测试空引用兜底 | `dotnet run` 从 37 PASS 崩溃 → **45 PASS 全绿** |
| P0-2 | `AppendLog` 捕获 UI `DispatcherQueue` 并 marshal `LogAppended`;`LogLines` 加锁、日志页加锁快照 | WinUI Release 构建 0 警告 0 错误 |
| P1-1 | launcher/、CI、构建脚本、Python 集成、审查报告全部入库 | 单次提交 54 文件;`static/*` 无关改动未混入 |
| P1-2 | 端口冲突改有界循环(最多 3 次分配),端口扫尽时明确报错 | — |
| P1-3 | `Json()` 接 `ct` 并在取消时重抛 OCE;`Ready()`/启动路径全部透传;`Kill` 后等待加 5s 上限 | — |

**未修(P1-4 及全部 P2/P3)**:留待下一轮；问题编号与文件:行号不变。

---

## 附：本次实测数据

- `dotnet run` 测试输出：`37 PASS` → `Program.cs:175 NullReferenceException`(即 P0-1)
- `PinSam2` 最小化复现工程：对含 `git+https://github.com/facebookresearch/sam2` 等 3 行变体的输入返回 `NULL`
- `--diagnose` 报告(`output/launcher-diagnose.json`):9/9 模型"已存在",节点 4 锁定 + 2"已有安装版本未验证",loras=57
- `versions/0.3.0.0/` 实际目录：`app-4a47d1a95a0f88d8`、`app-8739c76e681f9009`、`app-d3033039147a0f52` 三份共存
