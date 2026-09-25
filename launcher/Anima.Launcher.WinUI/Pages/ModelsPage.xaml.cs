using Anima.Launcher.Core;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace Anima.Launcher.Pages;

public sealed partial class ModelsPage : LauncherPage
{
    public ModelsPage()
    {
        InitializeComponent();
        ModelView.Source = State.Groups;
        ModelList.SelectionChanged += ModelList_SelectionChanged;
    }

    protected override void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        State.Scanned += OnScanned;
        // Re-select a row to show its details after navigation.
        if (ModelList.SelectedItem is null && ModelList.Items.Count > 0) ModelList.SelectedIndex = 0;
        UpdateDetails();
    }
    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        base.OnNavigatedFrom(e);
        State.Scanned -= OnScanned;
    }

    private void OnScanned(List<ModelStatus> scan)
    {
        // Rebuild happens in MainWindow via RebuildRows; refresh selection details here.
        UpdateDetails();
    }

    private void ModelList_SelectionChanged(object sender, SelectionChangedEventArgs e) => UpdateDetails();

    private void UpdateDetails()
    {
        if (ModelList.SelectedItem is ModelRow row)
        {
            var s = row.Status;
            Details.Text = $"{s.Model.Name}\n目标：{s.Destination}\n来源：{string.Join(" ; ", s.Model.Urls)}\nSHA-256：{s.Model.Sha256}\n授权：{s.Model.License} {s.Model.LicenseUrl}\n{s.FoundPath}";
        }
    }

    private async void Rescan_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { State.ApplyPendingFields(); await State.Scan(ct); });

    private async void HashScan_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { State.ApplyPendingFields(); await State.Scan(ct, true); });

    private async void Download_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { State.ApplyPendingFields(); await State.DownloadSelectedAsync(ct); });

    private void SelectBase_Click(object sender, RoutedEventArgs e) => State.SelectBaseGroup();

    private void OpenModelDir_Click(object sender, RoutedEventArgs e)
    {
        if (ModelList.SelectedItem is ModelRow row)
        {
            var dir = Path.GetDirectoryName(row.Status.Destination)!;
            Directory.CreateDirectory(dir);
            Program.Open(dir);
        }
    }

    private void OpenLicense_Click(object sender, RoutedEventArgs e)
    {
        if (ModelList.SelectedItem is ModelRow row && row.Status.Model.LicenseUrl.Length > 0)
            Program.Open(row.Status.Model.LicenseUrl);
    }

    private async void TestImage_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { State.ApplyPendingFields(); await State.TestGenerationAsync(ct); });
}
