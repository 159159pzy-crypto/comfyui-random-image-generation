using System.Diagnostics;
using System.IO.Compression;
using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;

namespace Anima.Launcher.Core;

public sealed record InstallJournal(string Root, string Stage, string Status, DateTime UpdatedUtc, string? Error = null);

public sealed class Installer(string stateDir, RuntimeManifest manifest, Downloads downloads, Action<string> log, IProgress<DownloadProgress>? progress)
{
    private static readonly Regex Sam2Line = new(@"^(?:sam2(?:\[[^\]]*\])?\s*@\s*)?git\+https://github\.com/facebookresearch/sam2(?:\.git)?(?:@[\w./\-]+)?\s*$", RegexOptions.IgnoreCase | RegexOptions.Compiled);
    private string? git;
    private string? uv;
    private Dictionary<string, string> Env(LauncherConfig config) => new()
    {
        ["PATH"] = (git is null ? "" : Path.GetDirectoryName(git) + Path.PathSeparator) + Environment.GetEnvironmentVariable("PATH"),
        ["UV_PYTHON_INSTALL_DIR"] = Path.Combine(stateDir, "python"),
        ["UV_CACHE_DIR"] = Path.Combine(stateDir, "cache", "uv"),
        ["PIP_INDEX_URL"] = config.PipIndex,
        ["PIP_CONSTRAINT"] = Path.Combine(config.ComfyRoot, ".anima-constraints.txt"),
        ["COMFYUI_PATH"] = config.ComfyRoot,
        ["COMFYUI_MODEL_PATH"] = Path.Combine(config.ComfyRoot, "models"),
        ["GIT_CONFIG_COUNT"] = config.GitMirror.Length > 0 ? "1" : "0",
        ["GIT_CONFIG_KEY_0"] = "url." + Safety.MapSource("https://github.com/", config) + ".insteadOf",
        ["GIT_CONFIG_VALUE_0"] = "https://github.com/"
    };
    public static void ExtractZip(string zip, string destination)
    {
        Directory.CreateDirectory(destination);
        using var archive = ZipFile.OpenRead(zip);
        foreach (var entry in archive.Entries)
        {
            var path = Safety.Under(destination, entry.FullName);
            if (((entry.ExternalAttributes >> 16) & 0xF000) == 0xA000) throw new InvalidDataException("压缩包包含符号链接。");
            if (entry.FullName.EndsWith('/')) { Directory.CreateDirectory(path); continue; }
            Directory.CreateDirectory(Path.GetDirectoryName(path)!);
            entry.ExtractToFile(path, true);
        }
    }
    private async Task<string> Tool(string name, LauncherConfig config, CancellationToken ct)
    {
        var tool = manifest.Tools.Single(t => t.Id == name);
        var target = Path.Combine(stateDir, "tools", name, tool.Sha256[..12]);
        var exe = Safety.Under(target, tool.Executable);
        if (File.Exists(exe)) return exe;
        var zip = Path.Combine(stateDir, "cache", name + ".zip");
        await downloads.Fetch(name, Safety.MapSource(tool.Url, config), zip, tool.Size, tool.Sha256, progress, ct);
        ExtractZip(zip, target);
        if (!File.Exists(exe)) throw new IOException($"{name} 压缩包不包含预期程序。");
        return exe;
    }
    private async Task Stage(LauncherConfig config, string name, Func<Task> action)
    {
        var file = Path.Combine(stateDir, "install-state.json");
        JsonFile.Write(file, new InstallJournal(config.ComfyRoot, name, "running", DateTime.UtcNow)); log(name);
        try { await action(); JsonFile.Write(file, new InstallJournal(config.ComfyRoot, name, "complete", DateTime.UtcNow)); }
        catch (Exception ex) { JsonFile.Write(file, new InstallJournal(config.ComfyRoot, name, "interrupted", DateTime.UtcNow, ex.Message)); throw; }
    }
    private async Task Clone(Repository repo, string target, LauncherConfig config, CancellationToken ct)
    {
        Safety.Commit(repo.Commit);
        var marker = Path.Combine(target, ".anima-revision.json");
        if (File.Exists(marker) && JsonFile.Read<Repository>(marker).Commit == repo.Commit)
        {
            var head = (await Commands.Run(git!, ["rev-parse", "HEAD"], target, null, ct)).Trim();
            if (head != repo.Commit) throw new IOException($"受管理仓库的版本已被手动更改：{target}。请导入为现有环境或另选空目录。");
            var changes = await Commands.Run(git!, ["status", "--porcelain", "--untracked-files=no"], target, null, ct);
            if (!string.IsNullOrWhiteSpace(changes)) throw new IOException($"受管理仓库有源代码修改，已保留：{target}。请先备份处理后再继续安装。");
            return;
        }
        if (Directory.Exists(target) && Directory.EnumerateFileSystemEntries(target).Any()) throw new IOException($"目标已存在，不能覆盖：{target}");
        var staging = target + ".anima-installing";
        Directory.CreateDirectory(staging);
        var stageMarker = Path.Combine(staging, ".anima-install.json");
        // A crash between writing the revision marker and moving the directory leaves
        // a completed clone behind; adopt it instead of demanding manual cleanup.
        var finalized = File.Exists(Path.Combine(staging, ".anima-revision.json")) && !File.Exists(stageMarker);
        if (!finalized)
        {
            if (!File.Exists(stageMarker) && Directory.EnumerateFileSystemEntries(staging).Any()) throw new IOException("临时安装目录已被其他内容占用。");
            JsonFile.Write(stageMarker, repo);
            await Commands.Run(git!, ["init"], staging, log, ct);
            await Commands.Run(git!, ["fetch", "--depth", "1", repo.Url, repo.Commit], staging, log, ct, Env(config));
            await Commands.Run(git!, ["checkout", "--detach", "FETCH_HEAD"], staging, log, ct);
            var actual = (await Commands.Run(git!, ["rev-parse", "HEAD"], staging, null, ct)).Trim();
            if (actual != repo.Commit) throw new InvalidDataException("Git 来源提交与锁定版本不同。");
            await Commands.Run(git!, ["submodule", "update", "--init", "--recursive"], staging, log, ct, Env(config));
            JsonFile.Write(Path.Combine(staging, ".anima-revision.json"), repo);
            File.Delete(stageMarker);
        }
        if (Directory.Exists(target)) Directory.Delete(target); // Only an empty target reaches this point.
        Directory.Move(staging, target);
    }
    /// <summary>Pin any SAM2 source line in a requirements file to the locked commit. Returns the rewritten content, or null when no SAM2 line exists.</summary>
    public static string? PinSam2(string content, string commit)
    {
        var found = false;
        // Select is lazy: materialize before reading `found`, or this always returns null.
        var lines = content.Replace("\r\n", "\n").Split('\n').Select(line =>
        {
            if (!Sam2Line.IsMatch(line.Trim())) return line;
            found = true;
            return "git+https://github.com/facebookresearch/sam2@" + commit;
        }).ToArray();
        return found ? string.Join("\n", lines) : null;
    }
    private bool ManagedRoot(LauncherConfig config) => File.Exists(Path.Combine(config.ComfyRoot, ".anima-revision.json"));
    /// <summary>The pip constraint file mirrors the full lock for managed installs and freezes only the imported torch ABI otherwise.</summary>
    public static string[] ConstraintsFor(bool managedRoot, RuntimeManifest manifest, string importedPins) => managedRoot ? manifest.PythonConstraints : importedPins.Replace("\r", "").Split('\n', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries);
    private void EnsureConstraints(LauncherConfig config, string importedPins)
    {
        var file = Path.Combine(config.ComfyRoot, ".anima-constraints.txt");
        File.WriteAllLines(file, ConstraintsFor(ManagedRoot(config), manifest, importedPins));
    }
    private async Task Pip(LauncherConfig config, IEnumerable<string> args, CancellationToken ct)
    {
        var baseArgs = new List<string> { "-m", "pip", "install", "--disable-pip-version-check" };
        var arguments = args.ToArray();
        for (var i = 0; i + 1 < arguments.Length; i++)
        {
            if (arguments[i] != "-r") continue;
            var requirements = arguments[i + 1];
            var pinned = PinSam2(File.Exists(requirements) ? File.ReadAllText(requirements) : throw new FileNotFoundException($"依赖清单不存在：{requirements}"), manifest.Sam2Commit);
            if (pinned is not null)
            {
                // The rewritten file lives in launcher state, not inside the managed repo.
                var rewritten = Path.Combine(stateDir, "cache", "requirements", Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(requirements)))[..12] + ".txt");
                Directory.CreateDirectory(Path.GetDirectoryName(rewritten)!);
                File.WriteAllText(rewritten, pinned);
                arguments[i + 1] = rewritten;
                baseArgs.Add("--no-build-isolation"); // SAM2 builds against the installed, verified CUDA runtime.
                if (!ManagedRoot(config)) await Pip(config, ["setuptools", "wheel"], ct);
            }
        }
        await Commands.Run(config.Python, baseArgs.Concat(arguments), config.ComfyRoot, log, ct, Env(config));
    }
    /// <summary>True when a ComfyUI owned by this launcher is still running; its venv must not be modified underneath it.</summary>
    private bool OwnedComfyRunning()
    {
        try
        {
            var file = Path.Combine(stateDir, "processes.json");
            if (!File.Exists(file)) return false;
            return JsonFile.Read<OwnedProcess[]>(file).Where(r => r.Role == "ComfyUI").Any(r =>
            {
                try { var p = Process.GetProcessById(r.Pid); return !p.HasExited && p.StartTime.ToUniversalTime().Ticks == r.StartTimeUtcTicks; }
                catch { return false; }
            });
        }
        catch { return false; }
    }
    private async Task<string> ResolveGit(LauncherConfig config, CancellationToken ct)
    {
        var found = Commands.Find("git.exe");
        if (found is not null)
        {
            try { await Commands.Run(found, ["--version"], stateDir, null, ct, timeout: TimeSpan.FromSeconds(30)); return found; }
            catch (Exception ex) when (ex is not OperationCanceledException) { log($"PATH 中的 Git 不可用（{ex.Message}），改用隔离工具。"); }
        }
        var resolved = await Tool("git", config, ct);
        await Commands.Run(resolved, ["--version"], stateDir, null, ct, timeout: TimeSpan.FromSeconds(30));
        return resolved;
    }
    public async Task Install(LauncherConfig config, bool fresh, CancellationToken ct)
    {
        config.Validate();
        Safety.ValidateRuntime(manifest);
        if (string.IsNullOrWhiteSpace(config.ComfyRoot)) throw new InvalidDataException("请先选择 ComfyUI 目录：新建选空目录，导入选含 main.py 的目录。");
        config.ComfyRoot = Path.GetFullPath(config.ComfyRoot);
        if (await ServiceManager.PortOpen(config.ComfyPort) || OwnedComfyRunning()) throw new IOException("安装依赖前请先停止此环境的 ComfyUI，避免运行过程中改动 Python 包。");
        var file = Path.Combine(stateDir, "install-state.json");
        if (File.Exists(file))
        {
            try { var last = JsonFile.Read<InstallJournal>(file); if (last.Status == "interrupted" && Path.GetFullPath(last.Root).Equals(config.ComfyRoot, StringComparison.OrdinalIgnoreCase)) log($"上次安装中断于「{last.Stage}」，本次从幂等步骤续跑。"); } catch { }
        }
        if (fresh)
        {
            var smi = Commands.Find("nvidia-smi.exe");
            if (smi is null) throw new InvalidOperationException("新建环境需要 NVIDIA 驱动。其他显卡请选择导入已有 ComfyUI。");
            await Commands.Run(smi, ["--query-gpu=name,driver_version", "--format=csv,noheader"], stateDir, log, ct);
            Safety.FreeSpace(config.ComfyRoot, 15L * 1024 * 1024 * 1024);
        }
        else
        {
            if (string.IsNullOrWhiteSpace(config.Python)) throw new InvalidDataException("未找到 Python，请先选择该环境的 python.exe。");
            // Fail fast on a bad interpreter instead of at the first pip call.
            await Commands.Run(config.Python, ["--version"], config.ComfyRoot, log, ct, timeout: TimeSpan.FromSeconds(30));
        }
        await Stage(config, "准备隔离安装工具", async () => { git = await ResolveGit(config, ct); if (fresh) uv = await Tool("uv", config, ct); });
        var importedPins = "";
        if (fresh)
        {
            await Stage(config, "拉取锁定版本的 ComfyUI", () => Clone(manifest.Comfy with { Url = config.CustomRepository.Length > 0 ? config.CustomRepository : manifest.Comfy.Url }, config.ComfyRoot, config, ct));
            await Stage(config, "安装 Python 并创建独立虚拟环境", async () =>
            {
                var env = Env(config);
                await Commands.Run(uv!, ["python", "install", manifest.PythonVersion], stateDir, log, ct, env);
                config.Python = Path.Combine(config.ComfyRoot, ".venv", "Scripts", "python.exe");
                if (!File.Exists(config.Python)) await Commands.Run(uv!, ["venv", "--seed", "--python", manifest.PythonVersion, Path.Combine(config.ComfyRoot, ".venv")], stateDir, log, ct, env);
            });
            config.ManagedComfy = true;
            EnsureConstraints(config, importedPins); // PIP_CONSTRAINT is injected into every pip call; the file must exist first.
            await Stage(config, "安装锁定 CUDA / PyTorch", async () =>
            {
                await Pip(config, ["setuptools", "wheel"], ct);
                var wheels = new List<string>();
                foreach (var wheel in manifest.TorchWheels)
                {
                    var destination = Path.Combine(stateDir, "cache", "wheels", wheel.Executable);
                    await downloads.Fetch(wheel.Id, wheel.Url, destination, wheel.Size, wheel.Sha256, progress, ct);
                    wheels.Add(destination);
                }
                await Pip(config, wheels, ct);
            });
            await Stage(config, "安装 ComfyUI 依赖", () => Pip(config, ["-r", Path.Combine(config.ComfyRoot, "requirements.txt")], ct));
        }
        else
        {
            // Adding a missing node must not silently replace an imported GPU runtime.
            importedPins = await Commands.Run(config.Python, ["-c", "import importlib.metadata as m; print(chr(10).join(n+'=='+m.version(n) for n in ['torch','torchvision','torchaudio'] if m.packages_distributions().get(n)))"], config.ComfyRoot, null, ct);
            EnsureConstraints(config, importedPins);
        }
        var custom = Path.Combine(config.ComfyRoot, "custom_nodes");
        Directory.CreateDirectory(custom);
        // Supported by Impact's installers and prestartup; avoid unselected model downloads.
        var skip = Path.Combine(custom, "skip_download_model");
        var hadSkip = File.Exists(skip);
        if (!hadSkip) File.WriteAllText(skip, "Model downloads are managed by Anima Random Studio.\n");
        foreach (var repo in manifest.Nodes)
        {
            var target = Path.Combine(custom, repo.Id);
            var managed = File.Exists(Path.Combine(target, ".anima-revision.json"));
            if (Directory.Exists(target) && !managed) { log($"保留已有节点 {repo.Id}；不升级、不改依赖。"); continue; }
            await Stage(config, "安装节点 " + repo.Id, async () =>
            {
                await Clone(repo, target, config, ct);
                var complete = Path.Combine(target, ".anima-dependencies-complete");
                if (File.Exists(complete) && File.ReadAllText(complete).Trim() == manifest.Version) return;
                var requirements = Path.Combine(target, "requirements.txt");
                if (File.Exists(requirements)) await Pip(config, ["-r", requirements], ct);
                var install = Path.Combine(target, "install.py");
                if (File.Exists(install)) await Commands.Run(config.Python, [install], target, log, ct, Env(config));
                File.WriteAllText(complete, manifest.Version);
            });
        }
        await Stage(config, "验证运行依赖", async () =>
        {
            await Commands.Run(config.Python, ["-c", "import aiohttp,yaml,torch; print('PyTorch:',torch.__version__); print('GPU:',torch.cuda.is_available())" + (fresh ? "; assert torch.cuda.is_available()" : "")], config.ComfyRoot, log, ct);
            if (fresh) await Commands.Run(config.Python, ["-m", "pip", "check"], config.ComfyRoot, log, ct);
        });
    }
}
