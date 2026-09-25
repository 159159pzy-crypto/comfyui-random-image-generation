using Anima.Launcher.Core;
using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using System.Collections.ObjectModel;
using System.Text.Json;

namespace Anima.Launcher;

/// <summary>
/// Owns launcher state and orchestration: config, services, downloads, model scan,
/// install, start/stop and test generation. UI pages bind to this and raise commands;
/// dialogs use the live <see cref="Microsoft.UI.Xaml.XamlRoot"/>.
/// </summary>
internal sealed class LauncherState : IDisposable
{
    private readonly string configFile = Path.Combine(Program.StateDir, "launcher.json");
    private readonly Func<XamlRoot?> root;
    private readonly Action showWindow;

    public LauncherConfig Config { get; private set; }
    public ServiceManager Services { get; }
    public Downloads Downloads { get; } = new(Credentials.Read);
    public ObservableCollection<ModelRow> Rows { get; } = [];
    public ObservableCollection<ModelGroup> Groups { get; } = [];

    private CancellationTokenSource? task;
    private readonly DispatcherQueue? uiQueue;
    private List<ModelStatus> downloadQueue = [];
    private bool cancelDownloads;
    private bool upgradeOffered;

    /// <summary>Fired on the UI thread when a status message changes.</summary>
    public event Action<string>? StatusChanged;
    /// <summary>Fired on the UI thread with a log line.</summary>
    public event Action<string>? LogAppended;
    /// <summary>Fired on the UI thread when a task begins/ends (busy state).</summary>
    public event Action<bool>? BusyChanged;
    /// <summary>Fired on the UI thread after config is loaded/changed so pages can re-fill fields.</summary>
    public event Action? FieldsReloaded;
    /// <summary>Fired on the UI thread after a scan; pages rebuild the model list.</summary>
    public event Action<List<ModelStatus>>? Scanned;
    /// <summary>Fired on the UI thread to switch pages (0=环境,1=模型,2=日志,3=设置).</summary>
    public event Action<int>? NavigateRequested;
    /// <summary>Raised when a background task fails; UI shows a dialog.</summary>
    public event Action<Exception>? TaskFailed;

    public bool Busy => task is not null;
    public XamlRoot? Root => root();

    public LauncherState(Func<XamlRoot?> getRoot, Action showWindowAction)
    {
        root = getRoot;
        showWindow = showWindowAction;
        // Created on the UI thread; worker callbacks (process output, downloads) marshal UI events through this.
        uiQueue = DispatcherQueue.GetForCurrentThread();
        Config = File.Exists(configFile) ? LoadConfig() : new LauncherConfig { AppRoot = Program.AppRoot, DataDir = Path.Combine(Program.StateDir, "data") };
        Program.Log("配置已读取");
        Services = new ServiceManager(Program.StateDir, AppendLog);
        Services.RecoverOwned();
    }

    private LauncherConfig LoadConfig()
    {
        try { var loaded = JsonFile.Read<LauncherConfig>(configFile); loaded.Validate(); return loaded; }
        catch
        {
            File.Copy(configFile, configFile + ".corrupt-" + DateTime.Now.ToString("yyyyMMddHHmmss"), true);
            return new() { AppRoot = Program.AppRoot, DataDir = Path.Combine(Program.StateDir, "data") };
        }
    }

    /// <summary>In-memory copy of the session log for the log page.</summary>
    public List<string> LogLines { get; } = [];

    public void SetStatus(string text) => StatusChanged?.Invoke(text);
    public void AppendLog(string text)
    {
        Program.Log(text);
        var line = DateTime.Now.ToString("HH:mm:ss ") + text;
        // Called from worker threads (process output callbacks); keep the list and UI event safe.
        lock (LogLines)
        {
            LogLines.Add(line);
            if (LogLines.Count > 2000) LogLines.RemoveRange(0, LogLines.Count - 2000);
        }
        if (uiQueue is not null) uiQueue.TryEnqueue(() => LogAppended?.Invoke(line));
        else LogAppended?.Invoke(line);
    }
    public void Save() => JsonFile.Write(configFile, Config);
    public void Reloaded() => FieldsReloaded?.Invoke();
    public void GoTo(int page) => NavigateRequested?.Invoke(page);
    public void Show() => showWindow();

    // --- Field synchronization (pages call on save / before ops) ---

    /// <summary>Latest values captured from the environment page; null keeps current config.</summary>
    public EnvironmentFields? PendingEnvironmentFields { get; set; }
    /// <summary>Latest values captured from the settings page; null keeps current config.</summary>
    public SettingsFields? PendingSettingsFields { get; set; }

    public void SyncFromUi(EnvironmentFields e, SettingsFields s)
    {
        Config.ComfyRoot = e.ComfyRoot.Trim();
        Config.Python = e.Python.Trim();
        Config.SourceId = e.SourceId;
        Config.GitMirror = e.GitMirror.Trim();
        Config.ModelMirror = e.HfMirror.Trim();
        Config.PipIndex = e.PipIndex.Trim();
        Config.CustomRepository = e.CustomRepository.Trim();
        Config.ComfyPort = s.ComfyPort;
        Config.WebPort = s.WebPort;
        Config.Theme = s.Theme;
        if (Config.AppRoot.Length == 0) Config.AppRoot = Program.AppRoot;
        if (Config.DataDir.Length == 0) Config.DataDir = Path.Combine(Program.StateDir, "data");
        Config.Validate();
    }

    /// <summary>Apply any pending field edits into <see cref="Config"/>. Unset pages keep prior values.</summary>
    public void ApplyPendingFields()
    {
        var e = PendingEnvironmentFields ?? new EnvironmentFields(Config.ComfyRoot, Config.Python, Config.SourceId, Config.GitMirror, Config.ModelMirror, Config.PipIndex, Config.CustomRepository);
        var s = PendingSettingsFields ?? new SettingsFields(Config.ComfyPort, Config.WebPort, Config.Theme);
        SyncFromUi(e, s);
    }

    // --- Task gating ---

    public async Task Run(Func<CancellationToken, Task> action)
    {
        if (task is not null)
        {
            showWindow();
            SetStatus("已有任务进行中，请先等待完成或暂停后再操作。");
            return;
        }
        task = new CancellationTokenSource();
        BusyChanged?.Invoke(true);
        try { await action(task.Token); }
        catch (OperationCanceledException)
        {
            SetStatus("任务已暂停，可继续或重试。");
            AppendLog("任务已暂停，可继续或重试。");
        }
        catch (Exception ex) { TaskFailed?.Invoke(ex); }
        finally
        {
            task.Dispose();
            task = null;
            BusyChanged?.Invoke(false);
        }
    }

    public void PauseTask() => task?.Cancel();
    public void CancelTaskAndDownloads() { cancelDownloads = true; task?.Cancel(); }
    public bool CancelDownloadsRequested => cancelDownloads;

    public IProgress<DownloadProgress> DownloadProgress() => new Progress<DownloadProgress>(p =>
    {
        var percent = (int)Math.Clamp(p.Received * 100d / Math.Max(1, p.Total), 0, 100);
        ProgressChanged?.Invoke(percent);
        SetStatus($"{p.Name} · {p.Stage} · {p.Received / 1048576d:N1} / {p.Total / 1048576d:N1} MiB");
    });
    public event Action<int>? ProgressChanged;

    // --- Environment ---

    public async Task<string> DetectSystem()
    {
        var gpu = "未检测到 NVIDIA；可导入其他显卡环境";
        var smi = Commands.Find("nvidia-smi.exe");
        if (smi is not null)
        {
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(10));
            try { gpu = (await Commands.Run(smi, ["--query-gpu=name,driver_version", "--format=csv,noheader"], Program.StateDir, null, timeout.Token)).Trim(); }
            catch { gpu = "NVIDIA 检测失败，请检查驱动或使用导入方式"; }
        }
        var text = $"Windows {Environment.OSVersion.Version} · {(Environment.Is64BitOperatingSystem ? "x64" : "x86")}\n{gpu}\nGit：{(Commands.Find("git.exe") is null ? "新建时自动准备" : "已检测到")} · .NET：已内置\n";
        foreach (var drive in DriveInfo.GetDrives().Where(d => d.IsReady && d.DriveType == DriveType.Fixed))
            text += $"{drive.Name} 可用 {drive.AvailableFreeSpace / 1073741824d:N1} GiB  ";
        return text;
    }

    public async Task CheckImportAsync(CancellationToken ct)
    {
        // Called after ReadFields; normalize and rescan.
        Config.ComfyRoot = Inventory.NormalizeRoot(Config.ComfyRoot);
        Config.Python = Inventory.FindPython(Config.ComfyRoot, Config.Python);
        Save();
        Reloaded();
        await Scan(ct);
    }

    public async Task InstallAsync(CancellationToken ct)
    {
        RequireRoot();
        var fresh = FreshInstallRequested;
        if (fresh && Directory.Exists(Config.ComfyRoot) && File.Exists(Path.Combine(Config.ComfyRoot, "main.py")) && !File.Exists(Path.Combine(Config.ComfyRoot, ".anima-revision.json")))
            throw new IOException("此目录已有 ComfyUI，请选择导入方式。");
        if (!fresh)
        {
            Config.ComfyRoot = Inventory.NormalizeRoot(Config.ComfyRoot);
            Config.Python = Inventory.FindPython(Config.ComfyRoot, Config.Python);
        }
        Save();
        var x = root();
        if (x is not null && !await Dialogs.ConfirmCancel(x, "安装确认", "将安装缺失节点和所需 Python 包。已有节点版本保留；模型另行勾选。继续安装？", "继续安装")) return;
        var installer = new Installer(Program.StateDir, Program.Runtime, Downloads, AppendLog, DownloadProgress());
        await installer.Install(Config, fresh, ct);
        Save();
        Reloaded();
        await Scan(ct);
        GoTo(2); // 模型与节点
    }

    /// <summary>Pages set this before calling Install to choose fresh/import.</summary>
    public bool FreshInstallRequested { get; set; }

    /// <summary>Passed by MainWindow; when true the app stays visible instead of auto-hiding.</summary>
    public bool ForceShow { get; set; }
    /// <summary>Hide the main window to tray (injected by MainWindow).</summary>
    public Action? HideWindow { get; set; }
    /// <summary>Latest system-detection summary shown on the environment page.</summary>
    public string SystemInfoText { get; set; } = "正在检测系统…";

    // --- Models ---

    public async Task Scan(CancellationToken ct) => await Scan(ct, false);

    public async Task Scan(CancellationToken ct, bool hashes)
    {
        RequireRoot();
        Config.ComfyRoot = Inventory.NormalizeRoot(Config.ComfyRoot);
        Config.Python = Inventory.FindPython(Config.ComfyRoot, Config.Python);
        SetStatus("正在读取 ComfyUI 有效模型路径…");
        var probe = await Inventory.Probe(Config.ComfyRoot, Config.Python, Path.Combine(Program.Assets, "probe_comfy.py"), ct);
        var scan = await Task.Run(() => Inventory.Scan(probe, Program.Models, hashes, ct), ct);
        var nodes = await Inventory.Nodes(Config.ComfyRoot, Program.Runtime, Commands.Find("git.exe"), ct);
        var loras = await Task.Run(() => Inventory.Loras(probe), ct);
        AppendLog($"本地 LoRA：{loras.Length} 个，仅检测；请在工作台选择使用。");
        foreach (var node in nodes) AppendLog($"节点 {node.Name}: {node.State} {node.Commit}");
        Scanned?.Invoke(scan);
        Save();
        Reloaded();
        SetStatus($"已扫描：模型 {scan.Count(s => !s.Missing)}/{scan.Count}；缺失节点 {nodes.Count(n => n.State == "缺失")}；本地 LoRA {loras.Length}。勾选后才下载。");
    }

    public void RebuildRows(List<ModelStatus> scan)
    {
        Rows.Clear();
        Groups.Clear();
        foreach (var item in scan)
        {
            var row = new ModelRow(item);
            Rows.Add(row);
            var group = Groups.FirstOrDefault(g => g.Name == item.Model.Group);
            if (group is null) { group = new ModelGroup(item.Model.Group); Groups.Add(group); }
            group.Add(row);
        }
    }

    public void SelectBaseGroup()
    {
        foreach (var row in Rows) row.Selected = row.Status.Model.Group == "基础生成" && row.CanSelect;
    }

    public async Task DownloadSelectedAsync(CancellationToken ct)
    {
        var selected = Rows.Where(r => r.Selected).Select(r => r.Status).ToList();
        if (selected.Count == 0)
        {
            SetStatus("请勾选缺失模型。已有异常文件请先手动备份；未核实来源的模型请手动导入。");
            return;
        }
        foreach (var volume in selected.GroupBy(s => Path.GetPathRoot(s.Destination)))
            Safety.FreeSpace(volume.Key!, volume.Sum(s => s.Model.Size));
        downloadQueue = selected;
        cancelDownloads = false;
        DownloadCancelable?.Invoke(true);
        try
        {
            foreach (var item in selected)
            {
                ct.ThrowIfCancellationRequested();
                Exception? failure = null;
                var done = false;
                foreach (var source in item.Model.Urls)
                {
                    try
                    {
                        await Downloads.Fetch(item.Model.Name, Safety.MapSource(source, Config), item.Destination, item.Model.Size, item.Model.Sha256, DownloadProgress(), ct);
                        done = true;
                        break;
                    }
                    catch (Exception ex) when (ex is not OperationCanceledException && item.Model.Urls.Length > 1)
                    {
                        failure = ex;
                        AppendLog($"来源 {source} 失败：{ex.Message}；尝试下一来源。");
                    }
                }
                if (!done) throw failure ?? new InvalidOperationException($"{item.Model.Name} 没有可用下载来源。");
            }
            await Scan(ct);
            SetStatus("所选模型下载并校验完成。若 ComfyUI 已运行，请在任务空闲时重新启动以刷新清单。");
        }
        finally
        {
            if (cancelDownloads) foreach (var item in downloadQueue) Downloads.CancelPartial(item.Destination);
            DownloadCancelable?.Invoke(false);
        }
    }
    public event Action<bool>? DownloadCancelable;

    // --- Services ---

    /// <summary>Normalize ComfyRoot + Python for start/stop paths. Throws when unconfigured.</summary>
    private void ResolvePaths()
    {
        RequireRoot();
        Config.ComfyRoot = Inventory.NormalizeRoot(Config.ComfyRoot);
        Config.Python = Inventory.FindPython(Config.ComfyRoot, Config.Python);
    }

    /// <summary>Start (or reuse) ComfyUI only.</summary>
    public async Task StartComfyAsync(CancellationToken ct)
    {
        ResolvePaths();
        await Services.StartComfy(Config, Program.Runtime, ct);
        SetStatus("ComfyUI 已就绪：" + Config.ComfyUrl);
    }

    /// <summary>Start ComfyUI (if needed) and the Studio workbench.</summary>
    public async Task StartWebUIAsync(CancellationToken ct)
    {
        ResolvePaths();
        await EnsureBaseModels(ct);
        await Services.StartWebUI(Config, Program.Runtime, ct);
        Config.SetupComplete = true;
        Save();
        SetStatus("服务已就绪：" + Config.WebUrl);
    }

    /// <summary>Ensure base-generation models exist before starting the workbench.</summary>
    private async Task EnsureBaseModels(CancellationToken ct)
    {
        var probe = await Inventory.Probe(Config.ComfyRoot, Config.Python, Path.Combine(Program.Assets, "probe_comfy.py"), ct);
        var inventory = await Inventory.Scan(probe, Program.Models, false, ct);
        var required = inventory.Where(x => x.Model.Group == "基础生成" && x.Missing).ToArray();
        if (required.Length > 0)
        {
            GoTo(2); // 模型与节点
            await Scan(ct);
            throw new InvalidOperationException("基础模型未就绪：" + string.Join("、", required.Select(x => x.Model.Name)) + "。请先下载或手动导入。");
        }
    }

    /// <summary>Start everything, handle port conflicts (bounded retries), open the workbench, hide unless forced.</summary>
    public async Task StartServicesAsync(CancellationToken ct)
    {
        // A second conflict is common when both ports are taken, or a freed port is raced;
        // offer auto-assign up to three times instead of failing on the second conflict.
        for (var attempt = 0; ; attempt++)
        {
            try { await StartWebUIAsync(ct); break; }
            catch (PortConflictException ex) when (attempt < 2)
            {
                showWindow();
                GoTo(4); // 设置
                var x = root();
                if (x is null || !await Dialogs.ConfirmCancel(x, "端口冲突", $"{ex.Message}\n是否自动寻找并保存空闲端口，然后重新启动？", "自动分配")) throw;
                var port = ex.Port + 1;
                while (port < 65535 && (port == Config.ComfyPort || port == Config.WebPort || await ServiceManager.PortOpen(port))) port++;
                if (port >= 65535) throw new InvalidOperationException("没有可用空闲端口，请手动修改端口后重试。");
                if (ex.Port == Config.ComfyPort) Config.ComfyPort = port; else Config.WebPort = port;
                Save();
            }
        }
        Config.SetupComplete = true;
        Save();
        Program.Open(Config.WebUrl);
        SetStatus("服务已就绪：" + Config.WebUrl);
        if (!ForceShow) HideWindow?.Invoke();
    }

    /// <summary>Stop one managed service by role ("ComfyUI" or "WebUI"). Confirms when stopping ComfyUI while the workbench runs.</summary>
    public async Task StopServiceAsync(string role, bool webUiRunning)
    {
        showWindow();
        var x = root();
        if (x is null) return;
        var label = role == "ComfyUI" ? "ComfyUI" : "工作台";
        var message = role == "ComfyUI" && webUiRunning
            ? "停止本程序拥有的 ComfyUI？工作台将失去后端而不可用，正在执行的生成会中断。"
            : $"停止本程序拥有的{label}？正在执行的任务会中断。";
        if (!await Dialogs.ConfirmCancel(x, "停止" + label, message, "停止")) return;
        await Run(_ =>
        {
            Services.StopOwned(role);
            SetStatus($"已停止启动器拥有的{label}；外部服务保持运行。");
            return Task.CompletedTask;
        });
    }

    /// <summary>Probe ComfyUI reachability and ownership.</summary>
    public async Task<ServiceInfo> ProbeComfyAsync()
    {
        var info = new ServiceInfo { Url = Config.ComfyUrl };
        if (string.IsNullOrWhiteSpace(Config.ComfyRoot)) return info;
        info.Configured = true;
        info.Managed = Services.IsOwnedRunning("ComfyUI");
        if (await ServiceManager.PortOpen(Config.ComfyPort))
        {
            var stats = await ServiceManager.Json(Config.ComfyUrl + "/system_stats");
            info.Running = stats is not null;
            info.Ours = stats is not null && ServiceManager.SameRoot(stats.Value, Config.ComfyRoot);
        }
        return info;
    }

    /// <summary>Probe the Studio workbench reachability and ownership.</summary>
    public async Task<ServiceInfo> ProbeWebUIAsync()
    {
        var info = new ServiceInfo { Url = Config.WebUrl };
        if (string.IsNullOrWhiteSpace(Config.AppRoot)) return info;
        info.Configured = true;
        info.Managed = Services.IsOwnedRunning("WebUI");
        if (await ServiceManager.PortOpen(Config.WebPort))
        {
            var health = await ServiceManager.Json(Config.WebUrl + "/api/launcher-health");
            info.Ours = ServiceManager.IsOurWebUi(health, Config);
            if (info.Ours)
            {
                var status = await ServiceManager.Json(Config.WebUrl + "/api/status");
                info.Running = status is not null && status.Value.TryGetProperty("online", out var online) && online.GetBoolean();
            }
            else info.Running = health is not null;
        }
        return info;
    }

    public async Task StopServicesAsync()
    {
        showWindow();
        var x = root();
        if (x is null || !await Dialogs.ConfirmCancel(x, "停止服务", "停止启动器拥有的服务？正在执行的生成会中断。", "停止服务")) return;
        await Run(_ =>
        {
            Services.StopOwned();
            SetStatus("已停止启动器拥有的服务。");
            return Task.CompletedTask;
        });
    }

    public async Task TestGenerationAsync(CancellationToken ct)
    {
        var queue = await ServiceManager.Json(Config.ComfyUrl + "/queue", ct) ?? throw new IOException("请先启动 ComfyUI 和工作台。");
        if (queue.GetProperty("queue_running").GetArrayLength() > 0 || queue.GetProperty("queue_pending").GetArrayLength() > 0) throw new IOException("ComfyUI 正在处理其他任务，请等待空闲再测试。");
        var active = await ServiceManager.Json(Config.WebUrl + "/api/batches/current", ct) ?? throw new IOException("请先启动工作台。");
        if (active.TryGetProperty("batch", out var batch) && batch.ValueKind == JsonValueKind.Object && batch.TryGetProperty("status", out var s) && s.GetString() is "running" or "stopping") throw new IOException("工作台有正在执行的批次，请等待完成。");
        using var client = new HttpClient(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(30) };
        var body = JsonSerializer.Serialize(new { count = 1, width = 512, height = 512, steps = 4, extra_prompt = "1girl, portrait, gentle smile, simple background, safe", hires = new { enabled = false }, detailers = new { hand = false, face = false, eyes = false, nsfw = false } });
        using var response = await client.PostAsync(Config.WebUrl + "/api/batches", new StringContent(body, System.Text.Encoding.UTF8, "application/json"), ct);
        response.EnsureSuccessStatusCode();
        SetStatus("已提交一张 512 × 512 测试图（4 步、无高清/Detailer），可在工作台查看进度。");
        Program.Open(Config.WebUrl);
    }

    // --- Versioning ---

    public async Task ApplyBundledVersionAsync(CancellationToken ct)
    {
        if (await ServiceManager.PortOpen(Config.WebPort)) throw new IOException("请先停止工作台服务再更新；ComfyUI 无需停止。");
        if (Config.AppRoot == Program.AppRoot) { SetStatus("当前已经是此 EXE 内置版本。"); return; }
        var previous = JsonSerializer.Deserialize<LauncherConfig>(JsonSerializer.Serialize(Config, JsonFile.Options), JsonFile.Options)!;
        JsonFile.Write(Path.Combine(Program.StateDir, "launcher.previous.json"), previous);
        Config.AppRoot = Program.AppRoot;
        try
        {
            await Services.Start(Config, Program.Runtime, ct);
            Save();
            SetStatus("新版本健康检查通过；旧应用资源和用户数据已保留。");
        }
        catch { Config.AppRoot = previous.AppRoot; Save(); throw; }
    }

    public async Task RollbackVersionAsync(CancellationToken ct)
    {
        if (await ServiceManager.PortOpen(Config.WebPort)) throw new IOException("请先停止工作台服务再回退。");
        var previous = JsonFile.Read<LauncherConfig>(Path.Combine(Program.StateDir, "launcher.previous.json"));
        if (!File.Exists(Path.Combine(previous.AppRoot, "run.py"))) throw new IOException("上一版本资源已不存在。");
        var current = Config.AppRoot;
        Config.AppRoot = previous.AppRoot;
        try
        {
            await Services.Start(Config, Program.Runtime, ct);
            Save();
            SetStatus("已恢复上一工作台版本；用户数据保留。");
        }
        catch { Config.AppRoot = current; Save(); throw; }
    }

    // --- Misc ---

    public void CreateDesktopShortcut()
    {
        var exe = Environment.ProcessPath!;
        var shortcut = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.DesktopDirectory), "Anima Random Studio.lnk");
        // COM arguments are supplied as data, never interpolated into a shell command.
        var type = Type.GetTypeFromProgID("WScript.Shell")!;
        dynamic shell = Activator.CreateInstance(type)!;
        dynamic link = shell.CreateShortcut(shortcut);
        link.TargetPath = exe;
        link.WorkingDirectory = Path.GetDirectoryName(exe);
        link.IconLocation = exe + ",0";
        link.Save();
        SetStatus("桌面快捷方式已创建。");
    }

    public void OfferUpgradeIfNeeded()
    {
        if (Config.SetupComplete && Config.AppRoot != Program.AppRoot && !upgradeOffered)
        {
            upgradeOffered = true;
            SetStatus("检测到工作台新版本，可在设置中主动应用；现有版本继续保留。");
        }
    }

    private void RequireRoot()
    {
        if (string.IsNullOrWhiteSpace(Config.ComfyRoot)) throw new InvalidDataException("请先选择 ComfyUI 目录：新建选空目录，导入选含 main.py 的目录。");
    }

    public void Dispose() => Downloads.Dispose();
}

public sealed record EnvironmentFields(string ComfyRoot, string Python, string SourceId, string GitMirror, string HfMirror, string PipIndex, string CustomRepository);
public sealed record SettingsFields(int ComfyPort, int WebPort, string Theme);

/// <summary>Reachability/ownership snapshot for one managed service.</summary>
public sealed class ServiceInfo
{
    public bool Configured { get; set; }
    public bool Running { get; set; }
    public bool Managed { get; set; }
    public bool Ours { get; set; }
    public string Url { get; set; } = "";
}

/// <summary>Group header + items for the model ListView grouping.</summary>
internal sealed class ModelGroup(string name) : List<ModelRow>
{
    public string Name { get; } = name;
    public List<ModelRow> Items => this;
}
