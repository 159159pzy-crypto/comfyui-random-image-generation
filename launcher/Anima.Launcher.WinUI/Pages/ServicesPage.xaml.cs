using Microsoft.UI.Dispatching;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;

namespace Anima.Launcher.Pages;

public sealed partial class ServicesPage : LauncherPage
{
    private readonly DispatcherTimer timer;
    private ServiceInfo comfy = new();
    private ServiceInfo studio = new();
    private bool probing;

    public ServicesPage()
    {
        InitializeComponent();
        timer = new DispatcherTimer { Interval = TimeSpan.FromSeconds(2.5) };
        timer.Tick += async (_, _) => await Refresh();
    }

    protected override async void OnNavigatedTo(NavigationEventArgs e)
    {
        base.OnNavigatedTo(e);
        State.BusyChanged += OnBusy;
        await Refresh();
        timer.Start();
    }
    protected override void OnNavigatedFrom(NavigationEventArgs e)
    {
        base.OnNavigatedFrom(e);
        timer.Stop();
        State.BusyChanged -= OnBusy;
    }

    private void OnBusy(bool busy) => Apply();

    private async Task Refresh()
    {
        if (probing) return;
        probing = true;
        try
        {
            var c = await State.ProbeComfyAsync();
            var s = await State.ProbeWebUIAsync();
            comfy = c;
            studio = s;
            Apply();
        }
        catch { }
        finally { probing = false; }
    }

    private void Apply()
    {
        var busy = State.Busy;
        ComfyCard.Update(comfy, busy);
        StudioCard.Update(studio, busy);
        StartAllButton.IsEnabled = !busy && comfy.Configured && (!comfy.Running || !studio.Running);
        StopAllButton.IsEnabled = !busy && (comfy.Managed || studio.Managed);
        StatusNote.IsOpen = !(comfy.Configured);
        StatusNote.Message = "尚未配置 ComfyUI 目录。先到「环境」页选择目录并检查环境。";
    }

    private async void ComfyStart_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { ReadPages(); await State.StartComfyAsync(ct); await Refresh(); });

    private async void StudioStart_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { ReadPages(); await State.StartWebUIAsync(ct); await Refresh(); });

    private async void ComfyStop_Click(object sender, RoutedEventArgs e)
    {
        await State.StopServiceAsync("ComfyUI", studio.Running);
        await Refresh();
    }

    private async void StudioStop_Click(object sender, RoutedEventArgs e)
    {
        await State.StopServiceAsync("WebUI", studio.Running);
        await Refresh();
    }

    private void ComfyOpen_Click(object sender, RoutedEventArgs e) => Program.Open(comfy.Url);
    private void StudioOpen_Click(object sender, RoutedEventArgs e) => Program.Open(studio.Url);

    private async void StartAll_Click(object sender, RoutedEventArgs e) =>
        await State.Run(async ct => { ReadPages(); await State.StartServicesAsync(ct); await Refresh(); });

    private async void StopAll_Click(object sender, RoutedEventArgs e)
    {
        await State.StopServicesAsync();
        await Refresh();
    }

    private void ReadPages() => Shell.SyncPagesIntoConfig();
}
