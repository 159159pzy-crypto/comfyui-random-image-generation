using Microsoft.UI.Xaml;

namespace Anima.Launcher;

public partial class App : Application
{
    internal MainWindow? Window;
    internal LauncherState? State;

    internal static LauncherState Shared => ((App)Current).State!;
    internal static MainWindow Shell => ((App)Current).Window!;

    public App()
    {
        InitializeComponent();
        UnhandledException += (_, e) =>
        {
            Program.Log("UI 未处理异常: " + e.Exception.ToString());
            e.Handled = true;
        };
    }

    /// <summary>Called from <see cref="Program.Main"/> inside <c>Application.Start</c>.</summary>
    internal void InitializeMainWindow(bool forceShow)
    {
        Window = new MainWindow(this, forceShow);
        State = Window.State;
        Window.Activate();
    }

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        // Entry is handled by Program.Main → InitializeMainWindow; nothing to do here.
    }
}
