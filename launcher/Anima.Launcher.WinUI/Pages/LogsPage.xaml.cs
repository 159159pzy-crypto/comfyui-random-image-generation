using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Navigation;

namespace Anima.Launcher.Pages;

public sealed partial class LogsPage : LauncherPage
{
    public LogsPage() => InitializeComponent();

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        string[] snapshot;
        // LogLines is appended from worker threads; take a locked snapshot before joining.
        lock (State.LogLines) snapshot = State.LogLines.ToArray();
        LogBox.Text = string.Join("\r\n", snapshot);
        State.LogAppended += OnLog;
    }
    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        base.OnNavigatedFrom(e);
        State.LogAppended -= OnLog;
    }

    private void OnLog(string line)
    {
        if (LogBox.Text.Length > 200000) LogBox.Text = "";
        LogBox.Text += (LogBox.Text.Length == 0 ? "" : "\r\n") + line;
    }

    private void OpenDir_Click(object sender, RoutedEventArgs e) =>
        Program.Open(Path.Combine(Program.StateDir, "logs"));

    private void Clear_Click(object sender, RoutedEventArgs e) => LogBox.Text = "";
}
