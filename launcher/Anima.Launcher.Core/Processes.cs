using System.Diagnostics;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;

namespace Anima.Launcher.Core;

public static class Commands
{
    public static readonly TimeSpan DefaultTimeout = TimeSpan.FromMinutes(30);
    public static ProcessStartInfo Info(string exe, IEnumerable<string> args, string cwd, IDictionary<string, string>? env = null)
    {
        var info = new ProcessStartInfo(exe) { WorkingDirectory = cwd, UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true, StandardOutputEncoding = Encoding.UTF8, StandardErrorEncoding = Encoding.UTF8 };
        foreach (var arg in args) info.ArgumentList.Add(arg);
        info.Environment["PYTHONUTF8"] = "1";
        info.Environment["PYTHONIOENCODING"] = "utf-8";
        info.Environment["PIP_DISABLE_PIP_VERSION_CHECK"] = "1";
        info.Environment["GIT_TERMINAL_PROMPT"] = "0";
        if (env is not null) foreach (var pair in env) info.Environment[pair.Key] = pair.Value;
        return info;
    }
    public static async Task<string> Run(string exe, IEnumerable<string> args, string cwd, Action<string>? log, CancellationToken ct, IDictionary<string, string>? env = null, TimeSpan? timeout = null)
    {
        using var process = new Process { StartInfo = Info(exe, args, cwd, env) };
        var output = new StringBuilder();
        var errors = new StringBuilder();
        process.OutputDataReceived += (_, e) => { if (e.Data is not null) { lock (output) output.AppendLine(e.Data); log?.Invoke(e.Data); } };
        process.ErrorDataReceived += (_, e) => { if (e.Data is not null) { lock (errors) errors.AppendLine(e.Data); log?.Invoke(e.Data); } };
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(ct);
        deadline.CancelAfter(timeout ?? DefaultTimeout);
        process.Start(); process.BeginOutputReadLine(); process.BeginErrorReadLine();
        try { await process.WaitForExitAsync(deadline.Token); process.WaitForExit(); }
        catch
        {
            try { process.Kill(true); await process.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)); } catch { }
            if (!ct.IsCancellationRequested && deadline.IsCancellationRequested) throw new TimeoutException($"{Path.GetFileName(exe)} 执行超时，已终止；请查看日志确认是否长时间无输出。");
            throw;
        }
        if (process.ExitCode != 0)
        {
            var tail = errors.ToString().TrimEnd();
            var detail = tail.Length > 1200 ? tail[^1200..] : tail;
            throw new InvalidOperationException($"{Path.GetFileName(exe)} 失败，退出码 {process.ExitCode}。{(detail.Length > 0 ? "末尾输出：" + detail : "请查看日志。")}");
        }
        return output.ToString();
    }
    public static string? Find(string name)
    {
        foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(Path.PathSeparator))
        {
            try { var path = Path.Combine(dir.Trim('"'), name); if (File.Exists(path)) return path; } catch { }
        }
        return null;
    }
}

public sealed class PortConflictException(int port) : Exception($"端口 {port} 被其他程序或其他目录的实例占用，请修改端口后重试。") { public int Port { get; } = port; }
public sealed record OwnedProcess(int Pid, long StartTimeUtcTicks, string Executable, string Role, string? ShutdownToken = null);

public sealed class ServiceManager(string stateDir, Action<string> log)
{
    private readonly List<Process> owned = [];
    private readonly List<OwnedProcess> records = [];
    private static readonly HttpClient Http = new(new HttpClientHandler { UseProxy = false }) { Timeout = TimeSpan.FromSeconds(30) };
    public static async Task<JsonElement?> Json(string url, CancellationToken ct = default)
    {
        try { using var d = JsonDocument.Parse(await Http.GetStringAsync(url, ct)); return d.RootElement.Clone(); }
        // Cancellation must reach the caller; only transport/parse failures map to null.
        catch (OperationCanceledException) { throw; }
        catch { return null; }
    }
    public static async Task<bool> PortOpen(int port)
    {
        try { using var client = new TcpClient(); await client.ConnectAsync("127.0.0.1", port).WaitAsync(TimeSpan.FromSeconds(2)); return true; } catch { return false; }
    }
    public static bool SameRoot(JsonElement stats, string root)
    {
        if (!stats.TryGetProperty("system", out var sys) || !sys.TryGetProperty("argv", out var argv)) return false;
        foreach (var arg in argv.EnumerateArray())
        {
            var text = arg.GetString();
            if (text is not null && Path.IsPathRooted(text) && Path.GetFileName(text).Equals("main.py", StringComparison.OrdinalIgnoreCase))
                return Path.GetFullPath(text).Equals(Path.Combine(Path.GetFullPath(root), "main.py"), StringComparison.OrdinalIgnoreCase);
        }
        return false;
    }
    public static bool IsOurWebUi(JsonElement? health, LauncherConfig config)
    {
        try
        {
            if (health is null) return false;
            var value = health.Value;
            if (value.ValueKind != JsonValueKind.Object) return false;
            // Any missing or malformed field means the port belongs to something else.
            if (!value.TryGetProperty("app_dir", out var app) || app.ValueKind != JsonValueKind.String || app.GetString() is not { Length: > 0 } appDir) return false;
            if (!value.TryGetProperty("data_dir", out var data) || data.ValueKind != JsonValueKind.String || data.GetString() is not { Length: > 0 } dataDir) return false;
            if (!value.TryGetProperty("comfy_url", out var comfy) || comfy.ValueKind != JsonValueKind.String || comfy.GetString() is not { Length: > 0 } comfyUrl) return false;
            return Path.GetFullPath(appDir).Equals(Path.GetFullPath(config.AppRoot), StringComparison.OrdinalIgnoreCase)
                && Path.GetFullPath(dataDir).Equals(Path.GetFullPath(config.DataDir), StringComparison.OrdinalIgnoreCase)
                && comfyUrl.TrimEnd('/') == config.ComfyUrl;
        }
        catch { return false; }
    }
    /// <summary>Ensure ComfyUI is reachable: reuse a same-root external instance or start a managed one, then verify nodes and write resource bindings.</summary>
    public async Task StartComfy(LauncherConfig config, RuntimeManifest manifest, CancellationToken ct)
    {
        config.Validate();
        var before = owned.Count;
        try
        {
            if (await PortOpen(config.ComfyPort))
            {
                var stats = await Json(config.ComfyUrl + "/system_stats", ct);
                if (stats is null || !SameRoot(stats.Value, config.ComfyRoot)) throw new PortConflictException(config.ComfyPort);
                log("复用已有 ComfyUI（外部进程不会被停止）。");
            }
            else
            {
                var args = new List<string> { Path.Combine(config.ComfyRoot, "main.py"), "--listen", "127.0.0.1", "--port", config.ComfyPort.ToString(), "--disable-auto-launch" };
                if (config.Python.Contains("python_embed", StringComparison.OrdinalIgnoreCase)) args.Add("--windows-standalone-build");
                args.AddRange(config.ComfyArguments);
                // Managed installations keep LoRA Manager settings/cache within that environment.
                // Its default user-wide profile otherwise changes roots of another ComfyUI install.
                var env = config.ManagedComfy || File.Exists(Path.Combine(config.ComfyRoot, ".anima-revision.json"))
                    ? new Dictionary<string, string> { ["LORA_MANAGER_PORTABLE"] = "1" } : null;
                var process = await StartProcess(config.Python, args, config.ComfyRoot, "ComfyUI", ct, env);
                await Ready(config.ComfyUrl + "/system_stats", process, ct);
            }
            var nodes = await Json(config.ComfyUrl + "/object_info", ct) ?? throw new IOException("ComfyUI 节点清单不可读取。");
            string[] core = ["UNETLoader", "CLIPLoader", "VAELoader", "KSampler", "SaveImage", "VAEDecode", "ImageScaleBy", "ImageUpscaleWithModel", "UpscaleModelLoader"];
            var missing = manifest.Nodes.SelectMany(n => n.RequiredClasses).Concat(core).Where(n => !nodes.TryGetProperty(n, out _)).ToArray();
            if (missing.Length > 0) throw new InvalidOperationException("缺少节点或导入失败：" + string.Join("、", missing));
            var bindings = ResourceBindings(nodes);
            var resourceFile = Path.Combine(stateDir, "resource-paths.json");
            JsonFile.Write(resourceFile, bindings);
        }
        catch
        {
            foreach (var p in owned.Skip(before).ToArray()) { Stop(p); owned.Remove(p); }
            Save(); throw;
        }
    }

    /// <summary>Ensure ComfyUI then start (or reuse) the Anima WebUI and verify it reports online.</summary>
    public async Task StartWebUI(LauncherConfig config, RuntimeManifest manifest, CancellationToken ct)
    {
        config.Validate();
        var before = owned.Count;
        try
        {
            await StartComfy(config, manifest, ct);
            var resourceFile = Path.Combine(stateDir, "resource-paths.json");
            if (await PortOpen(config.WebPort))
            {
                var current = await Json(config.WebUrl + "/api/launcher-health", ct);
                if (!IsOurWebUi(current, config)) throw new PortConflictException(config.WebPort);
                log("复用已有 Anima WebUI。");
            }
            else
            {
                // Per-run token lets the launcher ask this instance to stop during a version
                // switch. Only payloads that implement the endpoint get the argument —
                // rolling back to an older snapshot must not crash its argparse.
                var supportsShutdown = SupportsShutdownEndpoint(config.AppRoot);
                var shutdownToken = supportsShutdown ? Convert.ToHexString(RandomNumberGenerator.GetBytes(16)).ToLowerInvariant() : null;
                var argv = new List<string> { Path.Combine(config.AppRoot, "run.py"), "--host", "127.0.0.1", "--port", config.WebPort.ToString(), "--comfy-url", config.ComfyUrl, "--anima-tools-dir", Path.Combine(config.ComfyRoot, "custom_nodes", "Comfyui-Anima-Tools"), "--data-dir", config.DataDir, "--resource-paths", resourceFile, "--no-browser" };
                if (shutdownToken is not null) { argv.Add("--shutdown-token"); argv.Add(shutdownToken); }
                var process = await StartProcess(config.Python, argv, config.AppRoot, "WebUI", ct, shutdownToken: shutdownToken);
                await Ready(config.WebUrl + "/api/launcher-health", process, ct);
            }
            var status = await Json(config.WebUrl + "/api/status", ct);
            if (status is null || !status.Value.TryGetProperty("online", out var online) || !online.GetBoolean()) throw new IOException("工作台已启动，但尚未连接 ComfyUI，请查看日志。");
            log("ComfyUI 与工作台已就绪。");
        }
        catch
        {
            foreach (var p in owned.Skip(before).ToArray()) { Stop(p); owned.Remove(p); }
            Save(); throw;
        }
    }

    /// <summary>Start ComfyUI and the WebUI together; rolls back everything started during this call on failure.</summary>
    public async Task Start(LauncherConfig config, RuntimeManifest manifest, CancellationToken ct)
    {
        config.Validate();
        var before = owned.Count;
        try { await StartWebUI(config, manifest, ct); }
        catch
        {
            foreach (var p in owned.Skip(before).ToArray()) { Stop(p); owned.Remove(p); }
            Save(); throw;
        }
    }
    private static Dictionary<string, string> ResourceBindings(JsonElement nodes)
    {
        var bindings = new Dictionary<string, string>();
        (string Node, string Input, string Name)[] required = [("UNETLoader", "unet_name", "miaomiaoHarem_anima14.safetensors"), ("CLIPLoader", "clip_name", "qwen_3_06b_base.safetensors"), ("VAELoader", "vae_name", "qwen_image_vae.safetensors"), ("UpscaleModelLoader", "model_name", "4x_foolhardy_Remacri.pth"), ("SAMLoader", "model_name", "sam_vit_b_01ec64.pth"), ("UltralyticsDetectorProvider", "model_name", "bbox/hand_yolov9c.pt"), ("UltralyticsDetectorProvider", "model_name", "bbox/face_yolov9c.pt"), ("UltralyticsDetectorProvider", "model_name", "bbox/Eyeful_v2-Individual.pt"), ("UltralyticsDetectorProvider", "model_name", "segm/ntd11_anime_nsfw_segm_v5-variant1.pt")];
        foreach (var item in required)
        {
            string[] values = [];
            if (nodes.TryGetProperty(item.Node, out var node) && node.TryGetProperty("input", out var input) && input.TryGetProperty("required", out var fields) && fields.TryGetProperty(item.Input, out var choices) && choices.ValueKind == JsonValueKind.Array && choices.GetArrayLength() > 0)
            {
                if (choices[0].ValueKind == JsonValueKind.Array) values = choices[0].EnumerateArray().Select(x => x.GetString()!).ToArray();
                else if (choices.GetArrayLength() > 1 && choices[1].ValueKind == JsonValueKind.Object && choices[1].TryGetProperty("options", out var options) && options.ValueKind == JsonValueKind.Array) values = options.EnumerateArray().Select(x => x.GetString()!).ToArray();
            }
            var exact = values.FirstOrDefault(x => x.Replace('\\', '/') == item.Name);
            var matches = values.Where(x => Path.GetFileName(x.Replace('\\', '/')) == Path.GetFileName(item.Name)).ToArray();
            if (exact is not null) bindings[item.Name] = exact;
            else if (matches.Length == 1) bindings[item.Name] = matches[0].Replace('\\', '/');
            else if (item.Node is "UNETLoader" or "CLIPLoader" or "VAELoader") throw new InvalidOperationException($"ComfyUI 未识别基础模型或存在同名歧义：{item.Name}，请刷新/重启后再试。");
        }
        return bindings;
    }
    private async Task<Process> StartProcess(string exe, IEnumerable<string> args, string cwd, string role, CancellationToken ct, IDictionary<string, string>? env = null, string? shutdownToken = null)
    {
        var logs = Path.Combine(stateDir, "logs"); Directory.CreateDirectory(logs);
        var logPath = Path.Combine(logs, role + ".log");
        if (File.Exists(logPath) && new FileInfo(logPath).Length > 10 * 1024 * 1024) File.Move(logPath, logPath + ".previous", true);
        // Give the long-lived child real log file handles, not pipes owned by the launcher.
        // Otherwise closing the tray would break Python stdout/stderr on the next write.
        const string script = "import subprocess,sys,json,os; f=open(sys.argv[1],'ab',buffering=0); p=subprocess.Popen(json.loads(sys.argv[2]),cwd=sys.argv[3],stdin=subprocess.DEVNULL,stdout=f,stderr=f,creationflags=0x08000000); print(p.pid)";
        var pid = await Commands.Run(exe, ["-c", script, logPath, JsonSerializer.Serialize(new[] { exe }.Concat(args)), cwd], cwd, null, ct, env);
        Process process;
        try
        {
            if (!int.TryParse(pid.Trim(), out var id)) throw new InvalidDataException("服务引导未返回进程号。");
            process = Process.GetProcessById(id);
            var _ = process.StartTime; // Throws if the child already exited; keep inside the guard.
        }
        catch
        {
            TryKill(pid.Trim());
            throw;
        }
        string module;
        try { module = process.MainModule?.FileName ?? exe; } catch { module = exe; }
        log($"{role} 已启动；日志：{logPath}");
        owned.Add(process); records.Add(new(process.Id, process.StartTime.ToUniversalTime().Ticks, module, role, shutdownToken)); Save();
        return process;
    }
    private static void TryKill(string pidText)
    {
        try { if (int.TryParse(pidText, out var id) && Process.GetProcessById(id) is { HasExited: false } p) p.Kill(true); } catch { }
    }
    private static async Task Ready(string url, Process process, CancellationToken ct)
    {
        using var timeout = CancellationTokenSource.CreateLinkedTokenSource(ct);
        timeout.CancelAfter(TimeSpan.FromMinutes(6));
        while (true)
        {
            timeout.Token.ThrowIfCancellationRequested();
            if (process.HasExited) throw new IOException($"服务启动失败，退出码 {process.ExitCode}；请查看日志。");
            if (await Json(url, timeout.Token) is not null) return;
            await Task.Delay(750, timeout.Token);
        }
    }
    public void RecoverOwned()
    {
        var path = Path.Combine(stateDir, "processes.json");
        if (!File.Exists(path)) return;
        foreach (var record in JsonFile.Read<OwnedProcess[]>(path))
        {
            try
            {
                var process = Process.GetProcessById(record.Pid);
                if (process.StartTime.ToUniversalTime().Ticks == record.StartTimeUtcTicks && string.Equals(process.MainModule?.FileName, record.Executable, StringComparison.OrdinalIgnoreCase)) { owned.Add(process); records.Add(record); }
            }
            catch { }
        }
    }
    private void Save() { JsonFile.Write(Path.Combine(stateDir, "processes.json"), records.Where(r => owned.Any(p => p.Id == r.Pid && !p.HasExited)).ToArray()); }
    private static void Stop(Process p) { try { if (!p.HasExited) p.Kill(true); p.WaitForExit(5000); } catch { } }
    /// <summary>True when a service with this role ("ComfyUI" or "WebUI") is currently owned and running.</summary>
    public bool IsOwnedRunning(string role) => records.Where(r => r.Role == role).Any(r => owned.Any(p => p.Id == r.Pid && !p.HasExited));

    /// <summary>True when the payload's server.py implements /api/launcher-shutdown.</summary>
    public static bool SupportsShutdownEndpoint(string appRoot)
    {
        try { return File.ReadAllText(Path.Combine(appRoot, "anima_webui", "server.py")).Contains("launcher-shutdown"); }
        catch { return false; }
    }

    /// <summary>Reported app kind string of our workbench on /api/launcher-health.</summary>
    public const string WorkbenchAppName = "anima-random-studio";

    /// <summary>True when the health payload identifies our workbench app.</summary>
    public static bool IsWorkbenchHealth(JsonElement? health)
    {
        try { return health is { } value && value.ValueKind == JsonValueKind.Object && value.TryGetProperty("app", out var app) && app.GetString() == WorkbenchAppName; }
        catch { return false; }
    }

    /// <summary>
    /// Free the WebUI port when it is held by an instance of our own workbench.
    /// Order: owned process → recorded shutdown token → process kill, but only when
    /// the running app_dir sits under <paramref name="versionsRoot"/> (our extracted
    /// snapshots), so a user's own manually-started workbench is never touched.
    /// Returns false when the port is held by something we must not stop.
    /// </summary>
    public async Task<bool> TryStopWebUiAsync(LauncherConfig config, string versionsRoot, CancellationToken ct = default)
    {
        if (!await PortOpen(config.WebPort)) return true;
        var health = await Json(config.WebUrl + "/api/launcher-health", ct);
        if (!IsWorkbenchHealth(health)) return false; // Foreign service: caller reports a port conflict.
        if (IsOwnedRunning("WebUI"))
        {
            StopOwned("WebUI");
            return await WaitPortClosed(config.WebPort, ct);
        }
        var token = records.Where(r => r.Role == "WebUI").Select(r => r.ShutdownToken).FirstOrDefault(t => !string.IsNullOrEmpty(t));
        var fromOurSnapshots = health!.Value.TryGetProperty("app_dir", out var dir) && dir.ValueKind == JsonValueKind.String && PathUnder(versionsRoot, dir.GetString()!);
        if (token is not null && await ShutdownRemote(config.WebUrl, token))
        {
            log("已请求工作台停机端点。");
            return await WaitPortClosed(config.WebPort, ct);
        }
        // Old snapshots have no shutdown endpoint. Only an instance extracted under
        // our versions directory may be terminated here — never a user-run copy.
        if (!fromOurSnapshots) { log("端口上的工作台不在快照目录内，不接管。"); return false; }
        var pid = FindPortOwner(config.WebPort);
        if (pid <= 0) return await WaitPortClosed(config.WebPort, ct);
        // Any interpreter (venv, system, embedded) may host run.py; what makes the
        // process ours is that its argv references the reported app_dir snapshot.
        if (!ProcessCommandLineMatches(pid, dir.GetString()!)) { log($"PID {pid} 的命令行不属于旧快照，不接管。"); return false; }
        try
        {
            Process.GetProcessById(pid).Kill(true);
            log($"已结束旧版本工作台进程 {pid}。");
            return await WaitPortClosed(config.WebPort, ct);
        }
        catch { return await WaitPortClosed(config.WebPort, ct); }
    }

    private static bool PathUnder(string root, string path)
    {
        try
        {
            var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            return Path.GetFullPath(path).StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase);
        }
        catch { return false; }
    }

    private static async Task<bool> ShutdownRemote(string webUrl, string token)
    {
        try
        {
            using var body = new StringContent(JsonSerializer.Serialize(new { token }), Encoding.UTF8, "application/json");
            using var response = await Http.PostAsync(webUrl + "/api/launcher-shutdown", body);
            return response.IsSuccessStatusCode;
        }
        catch { return false; }
    }

    private static async Task<bool> WaitPortClosed(int port, CancellationToken ct)
    {
        for (var i = 0; i < 40; i++)
        {
            if (!await PortOpen(port)) return true;
            await Task.Delay(250, ct);
        }
        return false;
    }

    /// <summary>True when PID's command line launches run.py from the given app dir (any interpreter).</summary>
    public static bool ProcessCommandLineMatches(int pid, string appDir)
    {
        var commandLine = ProcessCommandLine(pid);
        if (commandLine is null) return false;
        var normalized = commandLine.Replace('/', '\\');
        var dir = Path.GetFullPath(appDir).TrimEnd('\\') + "\\";
        return normalized.IndexOf(dir, StringComparison.OrdinalIgnoreCase) >= 0;
    }

    /// <summary>Process command line via WMIC (absent on some newer Windows) with a PowerShell fallback.</summary>
    private static string? ProcessCommandLine(int pid)
    {
        foreach (var (exe, args, prefix) in new (string, string, string)[]
        {
            ("wmic.exe", $"process where processid={pid} get commandline /format:list", "CommandLine="),
            ("powershell.exe", $"-NoProfile -Command \"(Get-CimInstance Win32_Process -Filter 'ProcessId={pid}').CommandLine\"", ""),
        })
        {
            var path = Commands.Find(exe);
            if (path is null) continue;
            try
            {
                var info = new ProcessStartInfo(path, args) { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true };
                using var p = Process.Start(info)!;
                var output = p.StandardOutput.ReadToEnd();
                p.WaitForExit(10000);
                var text = output.Trim();
                if (text.StartsWith(prefix, StringComparison.OrdinalIgnoreCase)) text = text[prefix.Length..].Trim();
                if (text.Length > 0) return text;
            }
            catch { }
        }
        return null;
    }

    /// <summary>Owning PID of a 127.0.0.1 LISTEN socket on <paramref name="port"/>; 0 when unknown.</summary>
    private static int FindPortOwner(int port)
    {
        try
        {
            // netstat is the simplest reliable owner lookup available everywhere;
            // lines look like: "  TCP    127.0.0.1:8190    0.0.0.0:0    LISTENING    16728".
            var info = new ProcessStartInfo("netstat", "-ano") { CreateNoWindow = true, UseShellExecute = false, RedirectStandardOutput = true };
            using var process = Process.Start(info)!;
            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit(5000);
            foreach (var line in output.Split('\n'))
            {
                var parts = line.Trim().Split(' ', StringSplitOptions.RemoveEmptyEntries);
                if (parts.Length >= 5 && parts[0] == "TCP" && parts[^2] == "LISTENING" && parts[1].EndsWith(":" + port, StringComparison.Ordinal) && int.TryParse(parts[^1], out var pid)) return pid;
            }
        }
        catch { }
        return 0;
    }

    /// <summary>Stop only the managed processes of one role; external services are never touched.</summary>
    public void StopOwned(string role)
    {
        var pids = records.Where(r => r.Role == role).Select(r => r.Pid).ToHashSet();
        foreach (var p in owned.Where(p => pids.Contains(p.Id)).ToArray()) { Stop(p); owned.Remove(p); }
        records.RemoveAll(r => r.Role == role);
        Save();
        log($"已停止启动器拥有的 {role}；外部服务保持运行。");
    }
    public void StopOwned() { foreach (var p in owned) Stop(p); owned.Clear(); records.Clear(); Save(); log("已停止启动器拥有的服务；外部服务保持运行。"); }
}
