using System.Text.Json;

namespace Anima.Launcher.Core;

public sealed record ProbeResult(string Root, string Python, Dictionary<string, string[]> Paths);
public sealed record ModelStatus(ModelItem Model, string State, string? FoundPath, string Destination)
{
    public bool Missing => State is "缺失" or "大小异常" or "哈希异常";
}
public sealed record NodeStatus(string Name, string State, string Commit);

public static class Inventory
{
    public static string[] Loras(ProbeResult probe) => (probe.Paths.GetValueOrDefault("loras") ?? [])
        .Where(Directory.Exists)
        .SelectMany(dir => Directory.EnumerateFiles(dir, "*", new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true, AttributesToSkip = FileAttributes.ReparsePoint }))
        .Where(file => new[] { ".safetensors", ".ckpt", ".pt", ".bin" }.Contains(Path.GetExtension(file), StringComparer.OrdinalIgnoreCase))
        .Distinct(StringComparer.OrdinalIgnoreCase).Order().ToArray();
    public static string NormalizeRoot(string selected)
    {
        var path = Path.GetFullPath(selected.Trim().Trim('"'));
        if (File.Exists(Path.Combine(path, "ComfyUI", "main.py"))) path = Path.Combine(path, "ComfyUI");
        if (!File.Exists(Path.Combine(path, "main.py")) || !File.Exists(Path.Combine(path, "folder_paths.py"))) throw new InvalidDataException("请选择包含 main.py 的 ComfyUI 根目录，或其便携版上级目录。");
        return path;
    }
    public static string FindPython(string root, string manual)
    {
        var candidates = new[] { manual, Path.Combine(root, ".venv", "Scripts", "python.exe"), Path.Combine(root, "venv", "Scripts", "python.exe"), Path.Combine(Path.GetDirectoryName(root)!, "python_embeded", "python.exe"), Path.Combine(Path.GetDirectoryName(root)!, "python_embedded", "python.exe"), Path.Combine(root, "python_embeded", "python.exe") };
        return candidates.FirstOrDefault(p => !string.IsNullOrWhiteSpace(p) && File.Exists(p)) ?? throw new FileNotFoundException("未找到 ComfyUI Python，请在设置中选择该环境的 python.exe。");
    }
    public static async Task<ProbeResult> Probe(string root, string python, string script, CancellationToken ct)
    {
        var result = await Commands.Run(python, [script, root], root, null, ct);
        return JsonSerializer.Deserialize<ProbeResult>(result, JsonFile.Options) ?? throw new InvalidDataException("模型路径探测失败。");
    }
    public static async Task<List<ModelStatus>> Scan(ProbeResult probe, ModelManifest manifest, bool hashes, CancellationToken ct)
    {
        var results = new List<ModelStatus>();
        foreach (var item in manifest.Models)
        {
            ct.ThrowIfCancellationRequested();
            var dirs = probe.Paths.GetValueOrDefault(item.Category) ?? [Path.Combine(probe.Root, "models", item.Category)];
            var relative = item.RelativePath;
            if (item.Category == "ultralytics" && relative.Replace('\\', '/').Split('/') is [var detector, ..] && detector is "bbox" or "segm")
            {
                dirs = (probe.Paths.GetValueOrDefault("ultralytics_" + detector) ?? [])
                    .Concat(dirs.Select(dir => Path.Combine(dir, detector))).Distinct(StringComparer.OrdinalIgnoreCase).ToArray();
                relative = relative[(detector.Length + 1)..];
            }
            var destination = Safety.Under(Path.Combine(probe.Root, "models"), item.Category + "/" + item.RelativePath);
            // New downloads use ComfyUI's first configured path, including is_default paths.
            if (dirs.Length > 0) destination = Safety.Under(dirs[0], relative);
            string? found = null;
            foreach (var dir in dirs)
            {
                foreach (var name in item.Aliases.Select(alias => item.Category == "ultralytics" ? Path.GetFileName(alias.Replace('\\', '/')) : alias).Prepend(relative))
                {
                    var exact = Safety.Under(dir, name);
                    if (File.Exists(exact)) { found = exact; break; }
                    // Subfolders remain valid; identity is the full relative name returned to ComfyUI.
                    if (Directory.Exists(dir))
                    {
                        var matches = Directory.EnumerateFiles(dir, Path.GetFileName(name), new EnumerationOptions { RecurseSubdirectories = true, IgnoreInaccessible = true, AttributesToSkip = FileAttributes.ReparsePoint }).Take(2).ToArray();
                        if (matches.Length == 1) { found = matches[0]; break; }
                    }
                }
                if (found is not null) break;
            }
            var state = "缺失";
            if (found is not null)
            {
                state = item.Size > 0 && new FileInfo(found).Length != item.Size ? "大小异常" : "已存在";
                if (state == "已存在" && hashes && item.Sha256.Length == 64) state = await Downloads.Verify(found, item.Size, item.Sha256, ct) ? "已校验" : "哈希异常";
            }
            results.Add(new(item, state, found, destination));
        }
        return results;
    }
    public static async Task<List<NodeStatus>> Nodes(string root, RuntimeManifest manifest, string? git, CancellationToken ct)
    {
        var results = new List<NodeStatus>();
        foreach (var repo in manifest.Nodes)
        {
            var folder = Path.Combine(root, "custom_nodes", repo.Id);
            if (!Directory.Exists(folder)) { results.Add(new(repo.Id, "缺失", "")); continue; }
            if (!Directory.Exists(Path.Combine(folder, ".git")) || git is null) { results.Add(new(repo.Id, "已有安装，版本未验证", "")); continue; }
            var commit = (await Commands.Run(git, ["rev-parse", "HEAD"], folder, null, ct)).Trim();
            results.Add(new(repo.Id, commit == repo.Commit ? "锁定版本" : "已有其他版本（保留）", commit));
        }
        return results;
    }
}
