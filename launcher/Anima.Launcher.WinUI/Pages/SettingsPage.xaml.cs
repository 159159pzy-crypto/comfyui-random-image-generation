using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace Anima.Launcher.Pages;

public sealed partial class SettingsPage : LauncherPage
{
    public SettingsPage() => InitializeComponent();

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        LoadFields();
        State.FieldsReloaded += OnReloaded;
    }
    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        base.OnNavigatedFrom(e);
        State.FieldsReloaded -= OnReloaded;
    }
    private void OnReloaded() => LoadFields();

    private void LoadFields()
    {
        var c = State.Config;
        ComfyPortBox.Value = Math.Clamp(c.ComfyPort, 1024, 65535);
        WebPortBox.Value = Math.Clamp(c.WebPort, 1024, 65535);
        ThemeBox.SelectedIndex = c.Theme == "dark" ? 2 : c.Theme == "light" ? 1 : 0;
        CredHostBox.SelectedIndex = 0;
    }

    public void SyncConfig()
    {
        var c = State.Config;
        // An empty NumberBox reports NaN; keep the stored port in that case.
        var comfyPort = double.IsNaN(ComfyPortBox.Value) ? c.ComfyPort : (int)Math.Clamp(ComfyPortBox.Value, 1024, 65535);
        var webPort = double.IsNaN(WebPortBox.Value) ? c.WebPort : (int)Math.Clamp(WebPortBox.Value, 1024, 65535);
        State.PendingSettingsFields = new(
            comfyPort,
            webPort,
            ThemeBox.SelectedIndex == 2 ? "dark" : ThemeBox.SelectedIndex == 1 ? "light" : "system");
        State.ApplyPendingFields();
    }

    private void Theme_Changed(object sender, SelectionChangedEventArgs e)
    {
        // Live-apply theme immediately for feedback; persisted on 保存设置.
        SyncConfig();
        Shell.ApplyTheme();
    }

    private void Save_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            SyncConfig();
            State.Save();
            Shell.ApplyTheme();
            State.SetStatus("设置已保存。");
        }
        catch (Exception ex)
        {
            State.AppendLog(ex.Message);
            State.SetStatus(ex.Message);
        }
    }

    private void CredSave_Click(object sender, RoutedEventArgs e)
    {
        var host = ((CredHostBox.SelectedItem as ComboBoxItem)?.Content as string) ?? "civitai.com";
        try
        {
            Credentials.Save(host, CredTokenBox.Password.Trim());
            CredTokenBox.Password = "";
            State.SetStatus("凭据已保存到 Windows 凭据管理器。");
        }
        catch (Exception ex)
        {
            State.AppendLog(ex.Message);
            State.SetStatus(ex.Message);
        }
    }

    private void OpenLogs_Click(object sender, RoutedEventArgs e) =>
        Program.Open(Path.Combine(Program.StateDir, "logs"));

    private void Shortcut_Click(object sender, RoutedEventArgs e)
    {
        try { State.CreateDesktopShortcut(); }
        catch (Exception ex) { State.AppendLog(ex.Message); State.SetStatus(ex.Message); }
    }

    private async void ApplyVersion_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { State.ApplyPendingFields(); await State.ApplyBundledVersionAsync(ct); });

    private async void Rollback_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { State.ApplyPendingFields(); await State.RollbackVersionAsync(ct); });
}
