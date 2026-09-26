using H.NotifyIcon;
using Microsoft.UI;
using Microsoft.UI.Windowing;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using System.Drawing;
using System.Runtime.InteropServices;
using Windows.Graphics;

namespace Anima.Launcher;

public sealed partial class MainWindow : Window
{
    [DllImport("user32.dll")]
    private static extern uint GetDpiForWindow(IntPtr hWnd);

    internal LauncherState State { get; }

    private readonly bool forceShow;
    private TaskbarIcon? tray;
    private volatile bool reallyClose;
    private bool minimizeNotified;

    public MainWindow(App app, bool forceShow)
    {
        this.forceShow = forceShow;
        InitializeComponent();
        Title = "Anima Random Studio · 安装与启动";
        SystemBackdrop = new MicaBackdrop();
        ExtendsContentIntoTitleBar = true;
        SetTitleBar(AppTitleBar);

        var hwnd = Win32Interop.GetWindowFromWindowId(AppWindow.Id);
        var scale = GetDpiForWindow(hwnd) / 96.0;
        AppWindow.Resize(new SizeInt32((int)(1180 * scale), (int)(780 * scale)));
        if (AppWindow.Presenter is OverlappedPresenter presenter)
        {
            presenter.PreferredMinimumWidth = (int)(900 * scale);
            presenter.PreferredMinimumHeight = (int)(640 * scale);
        }
        var area = DisplayArea.GetFromWindowId(AppWindow.Id, DisplayAreaFallback.Primary).WorkArea;
        var size = AppWindow.Size;
        AppWindow.Move(new PointInt32(area.X + (area.Width - size.Width) / 2, area.Y + (area.Height - size.Height) / 2));
        try { AppWindow.SetIcon("app.ico"); } catch { }

        State = new LauncherState(() => Content?.XamlRoot, ShowWindow)
        {
            ForceShow = forceShow,
            HideWindow = () => { try { if (tray is not null) AppWindow.Hide(); } catch { } },
        };
        app.State = State; // Pages resolve state via App.Shared during navigation.
        WireState();

        Nav.SelectedItem = Nav.MenuItems[0];
        ContentFrame.Navigate(typeof(Pages.ServicesPage));

        CreateTray();
        StartShowSignalListener();
        AppWindow.Closing += OnClosing;
        Activated += OnActivated;
        ApplyTheme();
        Program.Log("原生窗口就绪");
    }

    private void WireState()
    {
        State.StatusChanged += text => DispatcherQueue.TryEnqueue(() => StatusBar.Message = text);
        State.ProgressChanged += value => DispatcherQueue.TryEnqueue(() => TaskProgress.Value = value);
        State.BusyChanged += busy => DispatcherQueue.TryEnqueue(() =>
        {
            StartButton.IsEnabled = !busy;
            StopButton.IsEnabled = !busy;
            PauseButton.IsEnabled = busy;
            if (!busy) { TaskProgress.Value = 0; CancelButton.IsEnabled = false; }
        });
        State.DownloadCancelable += on => DispatcherQueue.TryEnqueue(() => CancelButton.IsEnabled = on);
        State.Scanned += scan => DispatcherQueue.TryEnqueue(() => State.RebuildRows(scan));
        State.NavigateRequested += index => DispatcherQueue.TryEnqueue(() => GoToPage(index));
        State.TaskFailed += ex => DispatcherQueue.TryEnqueue(async () =>
        {
            State.AppendLog(ex.Message);
            StatusBar.Severity = InfoBarSeverity.Error;
            StatusBar.Message = ex.Message;
            ShowWindow();
            if (Content?.XamlRoot is { } root) await Dialogs.Error(root, ex.Message);
        });
        State.FieldsReloaded += () => DispatcherQueue.TryEnqueue(() => ApplyTheme());
    }

    // --- Navigation ---

    private void Nav_SelectionChanged(NavigationView sender, NavigationViewSelectionChangedEventArgs args)
    {
        if (args.IsSettingsSelected) { Navigate(typeof(Pages.SettingsPage), "设置"); return; }
        var tag = (args.SelectedItemContainer?.Tag as string) ?? "home";
        switch (tag)
        {
            case "env": Navigate(typeof(Pages.EnvironmentPage), "ComfyUI 环境"); break;
            case "models": Navigate(typeof(Pages.ModelsPage), "模型与节点"); break;
            case "logs": Navigate(typeof(Pages.LogsPage), "日志"); break;
            default: Navigate(typeof(Pages.ServicesPage), "服务"); break;
        }
    }

    private void Navigate(Type page, string header)
    {
        if (ContentFrame.CurrentSourcePageType != page) ContentFrame.Navigate(page);
        Nav.Header = header;
    }

    private void GoToPage(int index)
    {
        switch (index)
        {
            case 1: Nav.SelectedItem = Nav.MenuItems[1]; break;   // 环境
            case 2: Nav.SelectedItem = Nav.MenuItems[2]; break;   // 模型与节点
            case 3: Nav.SelectedItem = Nav.MenuItems[3]; break;   // 日志
            case 4: Nav.SelectedItem = Nav.SettingsItem; break;   // 设置
            default: Nav.SelectedItem = Nav.MenuItems[0]; break;  // 服务
        }
    }

    // --- Lifecycle ---

    private bool firstActivate = true;
    private async void OnActivated(object sender, WindowActivatedEventArgs args)
    {
        if (!firstActivate) return;
        firstActivate = false;
        try
        {
            var info = await State.DetectSystem();
            State.SystemInfoText = info;
            State.Reloaded();
            State.OfferUpgradeIfNeeded();
            // Even with --show (no auto-start), migrate to the bundled build so the
            // version takes effect; a stale running instance is replaced, not orphaned.
            if (State.Config.SetupComplete)
                await State.Run(async ct =>
                {
                    var stoppedStale = await State.EnsureCurrentVersionAsync(ct);
                    if (!forceShow || stoppedStale) await State.StartServicesAsync(ct);
                });
        }
        catch (Exception ex)
        {
            State.AppendLog(ex.ToString());
            StatusBar.Severity = InfoBarSeverity.Error;
            StatusBar.Message = ex.Message;
        }
    }

    private void OnClosing(AppWindow sender, AppWindowClosingEventArgs args)
    {
        if (reallyClose) return;
        if (tray is null)
        {
            // No tray icon means a hidden window would be unreachable. While a task
            // runs, refuse to close so the user can pause it; otherwise allow the
            // real exit — owned services keep running like "退出（保留服务）".
            if (State.Busy)
            {
                args.Cancel = true;
                StatusBar.Message = "托盘不可用且任务进行中；请先暂停任务再退出。";
                return;
            }
            Program.Log("托盘不可用，关闭窗口将直接退出（服务保留）。");
            return;
        }
        args.Cancel = true;
        try { AppWindow.Hide(); } catch { }
        if (minimizeNotified) return;
        minimizeNotified = true;
        try { tray?.ShowNotification("Anima Random Studio", "已最小化到托盘，任务继续运行。"); } catch { }
    }

    public void ShowWindow()
    {
        if (reallyClose) return;
        try { AppWindow.Show(); } catch { }
        try { AppWindow.MoveInZOrderAtTop(); } catch { }
        try { Activate(); } catch { }
    }

    private void Exit(bool stop)
    {
        if (State.Busy) { ShowWindow(); StatusBar.Message = "请先暂停当前任务再退出。"; return; }
        reallyClose = true;
        try
        {
            if (stop) State.Services.StopOwned();
            try { tray?.Dispose(); } catch { }
            tray = null;
            State.Dispose();
            Close();
        }
        catch (Exception ex)
        {
            Program.Log("退出清理失败：" + ex);
            // The user asked to quit; never leave a silent process behind.
            try { Environment.Exit(0); } catch { }
        }
    }

    // --- Theme ---

    internal void ApplyTheme()
    {
        if (Content is not FrameworkElement element) return;
        element.RequestedTheme = State.Config.Theme switch
        {
            "dark" => ElementTheme.Dark,
            "light" => ElementTheme.Light,
            _ => ElementTheme.Default,
        };
    }

    // --- Tray ---

    private void CreateTray()
    {
        try
        {
            var menu = new MenuFlyout();
            void Item(string text, Action action)
            {
                // ContextMenuMode defaults to PopupMenu: the items are cloned into a
                // native Win32 menu that only executes Command — the Click event is
                // never forwarded, so items must use Command rather than Click.
                var item = new MenuFlyoutItem { Text = text };
                item.Command = new RelayCommand(action);
                menu.Items.Add(item);
            }
            Item("打开工作台", () => Program.Open(State.Config.WebUrl));
            Item("打开 ComfyUI", () => Program.Open(State.Config.ComfyUrl));
            Item("启动服务", () => _ = State.Run(State.StartServicesAsync));
            Item("停止本程序服务", () => _ = State.StopServicesAsync());
            Item("重新检查模型和节点", () => { ShowWindow(); GoToPage(2); _ = State.Run(ct => State.Scan(ct)); });
            Item("打开环境 / 安装页面", () => { ShowWindow(); GoToPage(1); });
            Item("设置", () => { ShowWindow(); GoToPage(4); });
            Item("打开日志目录", () => Program.Open(Path.Combine(Program.StateDir, "logs")));
            menu.Items.Add(new MenuFlyoutSeparator());
            Item("退出（保留服务）", () => Exit(false));
            Item("停止服务并退出", () => Exit(true));

            tray = new TaskbarIcon
            {
                ToolTipText = "Anima Random Studio",
                ContextFlyout = menu,
                LeftClickCommand = new RelayCommand(ShowWindow),
            };
            // Environment.ProcessPath points to dotnet.exe under `dotnet run`; prefer the app file next to the exe.
            var iconPath = Environment.ProcessPath!;
            var sibling = Path.Combine(AppContext.BaseDirectory, Path.GetFileNameWithoutExtension(iconPath) + ".exe");
            if (Path.GetFileName(iconPath).Equals("dotnet.exe", StringComparison.OrdinalIgnoreCase))
                iconPath = Directory.GetFiles(AppContext.BaseDirectory, "*.exe").FirstOrDefault() ?? iconPath;
            else if (File.Exists(sibling)) iconPath = sibling;
            try { tray.Icon = Icon.ExtractAssociatedIcon(iconPath) ?? SystemIcons.Application; } catch { }
            // The default ForceCreate(true) puts the whole process into Windows
            // Efficiency Mode, which throttles the installs/downloads this app runs.
            tray.ForceCreate(enablesEfficiencyMode: false);
        }
        catch (Exception ex)
        {
            Program.Log("托盘图标创建失败：" + ex.Message);
            try { tray?.Dispose(); } catch { }
            tray = null;
        }
    }

    /// <summary>Surface the window when a second instance signals the named event.</summary>
    private void StartShowSignalListener()
    {
        var signal = Program.ShowSignal;
        if (signal is null) return;
        new Thread(() =>
        {
            try
            {
                while (signal.WaitOne())
                {
                    if (reallyClose) return;
                    DispatcherQueue.TryEnqueue(() => { if (!reallyClose) ShowWindow(); });
                }
            }
            catch (ObjectDisposedException) { }
            catch (AbandonedMutexException) { }
        })
        { IsBackground = true, Name = "ShowSignal" }.Start();
    }

    // --- Footer commands ---

    private async void Start_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { ReadPagesIntoConfig(); await State.StartServicesAsync(ct); });

    private async void Stop_Click(object sender, RoutedEventArgs e) => await State.StopServicesAsync();

    private void Pause_Click(object sender, RoutedEventArgs e) => State.PauseTask();

    private void Cancel_Click(object sender, RoutedEventArgs e) => State.CancelTaskAndDownloads();

    private void OpenWebUI_Click(object sender, RoutedEventArgs e) => Program.Open(State.Config.WebUrl);
    private void OpenComfy_Click(object sender, RoutedEventArgs e) => Program.Open(State.Config.ComfyUrl);

    private async void Rescan_Click(object sender, RoutedEventArgs e)
    {
        ShowWindow();
        GoToPage(2);
        await State.Run(async ct => { ReadPagesIntoConfig(); await State.Scan(ct); });
    }

    private void GoEnvironment_Click(object sender, RoutedEventArgs e) => GoToPage(1);
    private void GoSettings_Click(object sender, RoutedEventArgs e) => GoToPage(4);
    private void OpenLogs_Click(object sender, RoutedEventArgs e) => Program.Open(Path.Combine(Program.StateDir, "logs"));
    private void ExitKeep_Click(object sender, RoutedEventArgs e) => Exit(false);
    private void ExitStop_Click(object sender, RoutedEventArgs e) => Exit(true);

    /// <summary>Push current page field values into config before running tasks.</summary>
    internal void SyncPagesIntoConfig() => ReadPagesIntoConfig();

    private void ReadPagesIntoConfig()
    {
        if (ContentFrame.Content is Pages.EnvironmentPage env) env.SyncConfig();
        if (ContentFrame.Content is Pages.SettingsPage settings) settings.SyncConfig();
    }
}
