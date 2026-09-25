using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;

namespace Anima.Launcher;

/// <summary>A status card for one managed service: name + status badge + URL + start/stop/open.</summary>
public sealed partial class ServiceCard : UserControl
{
    private const double NarrowThreshold = 600;

    public ServiceCard()
    {
        InitializeComponent();
        SizeChanged += OnSizeChanged;
    }

    private void OnSizeChanged(object sender, SizeChangedEventArgs e)
    {
        var state = e.NewSize.Width < NarrowThreshold ? "Narrow" : "Wide";
        VisualStateManager.GoToState(this, state, true);
    }

    public event RoutedEventHandler? StartClicked;
    public event RoutedEventHandler? StopClicked;
    public event RoutedEventHandler? OpenClicked;

    public static readonly DependencyProperty TitleProperty =
        DependencyProperty.Register(nameof(Title), typeof(string), typeof(ServiceCard), new PropertyMetadata("", (d, e) => ((ServiceCard)d).NameText.Text = (string)e.NewValue));
    public string Title { get => (string)GetValue(TitleProperty); set => SetValue(TitleProperty, value); }

    public static readonly DependencyProperty GlyphTextProperty =
        DependencyProperty.Register(nameof(GlyphText), typeof(string), typeof(ServiceCard), new PropertyMetadata("", (d, e) => ((ServiceCard)d).Glyph.Glyph = (string)e.NewValue));
    public string GlyphText { get => (string)GetValue(GlyphTextProperty); set => SetValue(GlyphTextProperty, value); }

    /// <summary>Render status + button enablement for a service snapshot.</summary>
    public void Update(ServiceInfo info, bool busy)
    {
        UrlText.Text = info.Url;
        var (label, state) = !info.Configured ? ("未配置", 0)
            : info.Managed ? ("运行中 · 本程序", 2)
            : info.Running && info.Ours ? ("运行中 · 外部", 1)
            : info.Running ? ("运行中 · 未知", 1)
            : ("已停止", 0);

        StatusText.Text = label;
        var brush = state switch
        {
            2 => (Brush)Application.Current.Resources["SystemFillColorSuccessBrush"],
            1 => (Brush)Application.Current.Resources["SystemFillColorAttentionBrush"],
            _ => (Brush)Application.Current.Resources["TextFillColorTertiaryBrush"],
        };
        StatusText.Foreground = brush;
        StatusBadge.Background = (Brush)Application.Current.Resources[
            state == 0 ? "ControlFillColorSecondaryBrush" : "ControlFillColorSecondaryBrush"];

        // Only managed services can be stopped; only running services can be opened.
        StartButton.IsEnabled = !busy && info.Configured && !info.Running;
        StopButton.IsEnabled = !busy && info.Managed;
        OpenButton.IsEnabled = info.Running;

        if (!info.Configured)
        {
            HintText.Text = "先在「环境」页选择 ComfyUI 目录并完成首次配置。";
            HintText.Visibility = Visibility.Visible;
        }
        else if (info.Running && !info.Ours && !info.Managed)
        {
            HintText.Text = "该端口被其他程序占用；外部服务不归启动器停止。";
            HintText.Visibility = Visibility.Visible;
        }
        else if (info.Running && info.Ours && !info.Managed)
        {
            HintText.Text = "检测到同一 ComfyUI 的外部实例（可复用，不归启动器停止）。";
            HintText.Visibility = Visibility.Visible;
        }
        else HintText.Visibility = Visibility.Collapsed;
    }

    private void Start_Click(object sender, RoutedEventArgs e) => StartClicked?.Invoke(this, e);
    private void Stop_Click(object sender, RoutedEventArgs e) => StopClicked?.Invoke(this, e);
    private void Open_Click(object sender, RoutedEventArgs e) => OpenClicked?.Invoke(this, e);
}
