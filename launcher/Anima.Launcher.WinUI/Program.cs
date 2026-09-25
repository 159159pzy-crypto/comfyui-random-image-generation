using Anima.Launcher.Core;
using Microsoft.UI.Xaml;
using System.Diagnostics;
using System.Reflection;
using System.Runtime.InteropServices;
using System.Security.Cryptography;
using System.Text.Json;

namespace Anima.Launcher;

internal static class Program
{
    internal static readonly string StateDir = Environment.GetEnvironmentVariable("ANIMA_LAUNCHER_HOME") ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AnimaRandomStudio");
    internal static string Assets = "";
    internal static string AppRoot = "";
    internal static RuntimeManifest Runtime = null!;
    internal static ModelManifest Models = null!;
    internal static SourceManifest Sources = null!;
    private static readonly object LogLock = new();

    /// <summary>Named auto-reset event: a second instance signals it to surface the hidden window.</summary>
    internal const string ShowEventName = "Local\\AnimaRandomStudioShow";
    /// <summary>Owned by the first instance; created before the window so early second instances can wait on it.</summary>
    internal static EventWaitHandle? ShowSignal;

    internal static void Log(string text)
    {
        // No request URLs with signed queries or credential values are logged.
        lock (LogLock)
        {
            var dir = Path.Combine(StateDir, "logs");
            Directory.CreateDirectory(dir);
            var path = Path.Combine(dir, "launcher-" + DateTime.Now.ToString("yyyyMMdd") + ".log");
            File.AppendAllText(path, DateTime.Now.ToString("HH:mm:ss ") + text + Environment.NewLine);
        }
    }

    internal static void Open(string target) => Process.Start(new ProcessStartInfo(target) { UseShellExecute = true });

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern int MessageBoxW(IntPtr hWnd, string text, string caption, uint type);

    [DllImport("kernel32.dll")]
    private static extern bool AttachConsole(uint processId);

    private static void NativeMessage(string text, string caption, bool error)
    {
        // MB_ICONINFORMATION=0x40, MB_ICONERROR=0x10. A native box works before any window exists.
        MessageBoxW(IntPtr.Zero, text, caption, error ? 0x10u : 0x40u);
    }

    /// <summary>Acquire the single-instance mutex; an owner that died without release still yields first=true.</summary>
    private static Mutex AcquireInstanceMutex(out bool first)
    {
        try
        {
            return new Mutex(true, "Local\\AnimaRandomStudioLauncher", out first);
        }
        catch (AbandonedMutexException)
        {
            // A previous instance died holding the mutex; take ownership through WaitOne.
            var recovered = new Mutex(false, "Local\\AnimaRandomStudioLauncher");
            recovered.WaitOne();
            first = true;
            return recovered;
        }
    }

    /// <summary>Ask the running instance to surface its window (it may sit in the tray); fall back to a message.</summary>
    private static void WakeRunningInstance()
    {
        // Retry briefly: the first instance may hold the mutex but not have created the signal yet.
        for (var i = 0; i < 20; i++)
        {
            try
            {
                if (EventWaitHandle.TryOpenExisting(ShowEventName, out var signal))
                {
                    using (signal) signal.Set();
                    return;
                }
            }
            catch { }
            Thread.Sleep(250);
        }
        NativeMessage("启动器已运行，请从系统托盘打开。", "Anima Random Studio", false);
    }

    [STAThread]
    private static int Main(string[] args)
    {
        var diagnose = args.Contains("--diagnose");
        if (diagnose) AttachConsole(0xFFFFFFFF); // Attach to the parent console; EXE stays a GUI app.
        try
        {
            Directory.CreateDirectory(StateDir);
            Log("启动器初始化");
            // Resource extraction is serialized across GUI launches and concurrent --diagnose
            // runs before the single-instance mutex decides who owns the window.
            using var extract = new Mutex(false, "Local\\AnimaRandomStudioExtract");
            var acquired = false;
            try { acquired = extract.WaitOne(TimeSpan.FromSeconds(60)); }
            catch (AbandonedMutexException) { acquired = true; }
            try
            {
                if (!acquired) throw new IOException("等待资源提取锁超时，请关闭其他实例后重试。");
                ExtractResources();
            }
            finally { if (acquired) extract.ReleaseMutex(); }
            Log("应用资源就绪");
            if (diagnose) return Diagnose(args).GetAwaiter().GetResult();
            using var mutex = AcquireInstanceMutex(out var first);
            if (!first)
            {
                WakeRunningInstance();
                return 0;
            }
            ShowSignal = new EventWaitHandle(false, EventResetMode.AutoReset, ShowEventName);
            var forceShow = args.Contains("--show");
            WinRT.ComWrappersSupport.InitializeComWrappers();
            Application.Start(_ =>
            {
                // The generated Main normally installs this; we must too so awaits return to the UI thread.
                var context = new Microsoft.UI.Dispatching.DispatcherQueueSynchronizationContext(Microsoft.UI.Dispatching.DispatcherQueue.GetForCurrentThread());
                System.Threading.SynchronizationContext.SetSynchronizationContext(context);
                var app = new App();
                app.InitializeMainWindow(forceShow);
            });
            return 0;
        }
        catch (Exception ex)
        {
            Log(ex.ToString());
            if (diagnose) Console.Error.WriteLine(ex.Message);
            else NativeMessage(ex.Message, "启动器错误", true);
            return 1;
        }
    }

    private static void ExtractResources()
    {
        var assembly = Assembly.GetExecutingAssembly();
        Assets = Path.Combine(StateDir, "versions", assembly.GetName().Version!.ToString());
        Directory.CreateDirectory(Assets);
        foreach (var resource in assembly.GetManifestResourceNames())
        {
            // The SDK embeds resources as "<rootNamespace>.<file>" (folder prefixes are dropped),
            // so match on the file-name suffix rather than a folder prefix.
            var name = resource.EndsWith("payload.zip") ? "payload.zip"
                : resource.EndsWith("probe_comfy.py") ? "probe_comfy.py"
                : resource.EndsWith("launcher-manifest.json") ? "launcher-manifest.json"
                : resource.EndsWith("model-manifest.json") ? "model-manifest.json"
                : resource.EndsWith("source-profiles.json") ? "source-profiles.json"
                : null;
            if (name is null) continue;
            using var stream = assembly.GetManifestResourceStream(resource)!;
            using var file = File.Create(Path.Combine(Assets, name));
            stream.CopyTo(file);
        }
        Runtime = JsonFile.Read<RuntimeManifest>(Path.Combine(Assets, "launcher-manifest.json"));
        Models = JsonFile.Read<ModelManifest>(Path.Combine(Assets, "model-manifest.json"));
        Sources = JsonFile.Read<SourceManifest>(Path.Combine(Assets, "source-profiles.json"));
        Safety.Validate(Runtime, Models);
        var zip = Path.Combine(Assets, "payload.zip");
        if (File.Exists(zip))
        {
            using var input = File.OpenRead(zip);
            var identity = Convert.ToHexString(SHA256.HashData(input))[..16].ToLowerInvariant();
            var bundled = Path.Combine(Assets, "app-" + identity);
            if (!Directory.Exists(bundled))
            {
                var staging = bundled + ".extracting";
                if (Directory.Exists(staging)) Directory.Delete(staging, true);
                Installer.ExtractZip(zip, staging);
                Directory.Move(staging, bundled);
            }
            AppRoot = bundled;
        }
        else
        {
            var directory = new DirectoryInfo(AppContext.BaseDirectory);
            while (directory is not null && !File.Exists(Path.Combine(directory.FullName, "run.py"))) directory = directory.Parent;
            AppRoot = directory?.FullName ?? throw new FileNotFoundException("发布文件缺少工作台资源，请使用 Build-Launcher.ps1 构建。");
        }
    }

    private static async Task<int> Diagnose(string[] args)
    {
        string Arg(string flag, string fallback = "") { var i = Array.IndexOf(args, flag); return i >= 0 && i + 1 < args.Length ? args[i + 1] : fallback; }
        var target = Arg("--diagnose");
        if (target.Length == 0)
        {
            Console.Error.WriteLine("用法：AnimaRandomStudio.exe --diagnose <ComfyUI 目录> [--python <python.exe>] [--hashes] [--report <报告路径>]");
            return 2;
        }
        var root = Inventory.NormalizeRoot(target);
        var python = Inventory.FindPython(root, Arg("--python"));
        var probe = await Inventory.Probe(root, python, Path.Combine(Assets, "probe_comfy.py"), CancellationToken.None);
        var models = await Inventory.Scan(probe, Models, args.Contains("--hashes"), CancellationToken.None);
        var nodes = await Inventory.Nodes(root, Runtime, Commands.Find("git.exe"), CancellationToken.None);
        var report = new { root, python, models, nodes, loras = Inventory.Loras(probe), comfy = await ServiceManager.Json("http://127.0.0.1:8188/system_stats") };
        JsonFile.Write(Arg("--report", Path.Combine(StateDir, "diagnosis.json")), report);
        return 0;
    }
}
