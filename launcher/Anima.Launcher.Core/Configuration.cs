using System.Text.Json;
using System.Text.RegularExpressions;

namespace Anima.Launcher.Core;

public static class JsonFile
{
    public static readonly JsonSerializerOptions Options = new() { PropertyNameCaseInsensitive = true, PropertyNamingPolicy = JsonNamingPolicy.CamelCase, WriteIndented = true };
    public static T Read<T>(string path) => JsonSerializer.Deserialize<T>(File.ReadAllText(path), Options) ?? throw new InvalidDataException($"无效文件: {path}");
    public static void Write<T>(string path, T value)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(Path.GetFullPath(path))!);
        var temp = path + ".tmp";
        File.WriteAllText(temp, JsonSerializer.Serialize(value, Options));
        File.Move(temp, path, true);
    }
}

public sealed class LauncherConfig
{
    public int SchemaVersion { get; set; } = 1;
    public string ComfyRoot { get; set; } = "";
    public string Python { get; set; } = "";
    public string AppRoot { get; set; } = "";
    public string DataDir { get; set; } = "";
    public bool ManagedComfy { get; set; }
    public bool SetupComplete { get; set; }
    public int ComfyPort { get; set; } = 8188;
    public int WebPort { get; set; } = 8190;
    public string SourceId { get; set; } = "official";
    public string GitMirror { get; set; } = "";
    public string ModelMirror { get; set; } = "";
    public string PipIndex { get; set; } = "https://pypi.org/simple";
    public string CustomRepository { get; set; } = "";
    public string Theme { get; set; } = "system";
    public List<string> ComfyArguments { get; set; } = [];
    public string ComfyUrl => $"http://127.0.0.1:{ComfyPort}";
    public string WebUrl => $"http://127.0.0.1:{WebPort}";
    public void Validate()
    {
        if (SchemaVersion != 1) throw new InvalidDataException("不支持此配置版本，请使用对应版本的启动器。");
        if (ComfyPort is < 1024 or > 65535 || WebPort is < 1024 or > 65535 || ComfyPort == WebPort) throw new InvalidDataException("端口需为 1024–65535 的两个不同值。");
        foreach (var url in new[] { GitMirror, ModelMirror, PipIndex, CustomRepository }.Where(x => x.Length > 0)) Safety.Https(url.Replace("{url}", "https://github.com/example/repo.git"));
        // Runtime and model-directory overrides must be explicit in the imported configuration.
        string[] reserved = ["--listen", "--port", "--base-directory", "--output-directory", "--user-directory", "--extra-model-paths-config"];
        if (ComfyArguments.Any(a => reserved.Any(r => a == r || a.StartsWith(r + "=")))) throw new InvalidDataException("额外参数不能覆盖端口、监听地址或目录；请在对应设置中配置。");
    }
}

public sealed record Repository(string Id, string Url, string Commit, string[] RequiredClasses);
public sealed record Artifact(string Id, string Url, long Size, string Sha256, string Executable);
public sealed record RuntimeManifest(int SchemaVersion, string Version, string Validation, Repository Comfy, string PythonVersion, string TorchVersion, string TorchVisionVersion, string TorchAudioVersion, string TorchIndex, Artifact[] Tools, Repository[] Nodes, Artifact[] TorchWheels, string[] PythonConstraints, string Sam2Commit);
public sealed record ModelItem(string Id, string Name, string Group, string Category, string RelativePath, string[] Aliases, long Size, string Sha256, string[] Urls, string License, string LicenseUrl)
{
    public bool CanDownload => Size > 0 && Regex.IsMatch(Sha256 ?? "", "^[a-fA-F0-9]{64}$") && Urls.Length > 0;
}
public sealed record ModelManifest(int SchemaVersion, ModelItem[] Models);
public sealed record SourceProfile(string Id, string Name, string GitTemplate, string HuggingFaceBase, string PipIndex);
public sealed record SourceManifest(int SchemaVersion, SourceProfile[] Profiles);

public sealed class InsufficientSpaceException(string volume, long required) : IOException($"磁盘 {volume} 可用空间不足（需 {required / 1048576d:N0} MiB，另保留 256 MiB）。") { }

public static class Safety
{
    public static Uri Https(string url)
    {
        if (!Uri.TryCreate(url, UriKind.Absolute, out var uri) || uri.Scheme != "https" || uri.UserInfo.Length > 0 || uri.Fragment.Length > 0) throw new InvalidDataException("来源必须是无内嵌凭据的 HTTPS 地址。");
        if (uri.Query.Contains("token", StringComparison.OrdinalIgnoreCase) || uri.Query.Contains("key=", StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("请使用凭据管理器保存令牌，不要写入 URL。");
        return uri;
    }
    public static string Under(string root, string relative)
    {
        if (Path.IsPathRooted(relative) || relative.Contains(':') || relative.Replace('\\', '/').Split('/').Any(x => x == "..")) throw new InvalidDataException("资源路径必须是安全的相对路径。");
        var fullRoot = Path.GetFullPath(root).TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
        var full = Path.GetFullPath(Path.Combine(fullRoot, relative));
        if (!full.StartsWith(fullRoot, StringComparison.OrdinalIgnoreCase)) throw new InvalidDataException("路径超出安装目录。");
        return full;
    }
    public static void Commit(string commit)
    {
        if (!Regex.IsMatch(commit, "^[a-f0-9]{40}$")) throw new InvalidDataException("仓库必须锁定为完整的 40 位提交。");
    }
    public static void Validate(RuntimeManifest runtime, ModelManifest models)
    {
        if (models.SchemaVersion != 1) throw new InvalidDataException("不支持的清单版本。");
        ValidateRuntime(runtime);
        foreach (var item in models.Models)
        {
            Under(Path.GetTempPath(), item.Category + "/" + item.RelativePath);
            foreach (var alias in item.Aliases) Under(Path.GetTempPath(), alias);
            foreach (var url in item.Urls) Https(url);
            if (item.Urls.Length > 0 && !item.CanDownload) throw new InvalidDataException($"{item.Id} 下载信息不完整。");
        }
    }
    public static void ValidateRuntime(RuntimeManifest runtime)
    {
        if (runtime.SchemaVersion != 1) throw new InvalidDataException("不支持的清单版本。");
        Commit(runtime.Sam2Commit);
        if (runtime.PythonConstraints.Length == 0 || runtime.PythonConstraints.Any(line => !Regex.IsMatch(line, "^[A-Za-z0-9_.-]+==[A-Za-z0-9.+!-]+$"))) throw new InvalidDataException("Python 依赖必须使用确定版本。");
        foreach (var repo in runtime.Nodes.Prepend(runtime.Comfy)) { Commit(repo.Commit); Https(repo.Url); Under(Path.GetTempPath(), repo.Id); }
        foreach (var tool in runtime.Tools.Concat(runtime.TorchWheels)) { Https(tool.Url); Under(Path.GetTempPath(), tool.Executable); if (tool.Size <= 0 || !Regex.IsMatch(tool.Sha256, "^[a-fA-F0-9]{64}$")) throw new InvalidDataException("工具缺少哈希。"); }
    }
    public static void FreeSpace(string path, long bytes)
    {
        var volume = Path.GetPathRoot(Path.GetFullPath(path))!;
        if (new DriveInfo(volume).AvailableFreeSpace < checked(bytes + 256L * 1024 * 1024)) throw new InsufficientSpaceException(volume, bytes);
    }
    public static string MapSource(string original, LauncherConfig config)
    {
        var uri = Https(original);
        if (uri.Host == "huggingface.co" && config.ModelMirror.Length > 0) return Https(config.ModelMirror.TrimEnd('/') + uri.PathAndQuery).AbsoluteUri;
        if (uri.Host == "github.com" && config.GitMirror.Length > 0) return Https(config.GitMirror.Contains("{url}") ? config.GitMirror.Replace("{url}", original) : config.GitMirror.TrimEnd('/') + "/" + original).AbsoluteUri;
        return original;
    }
}
