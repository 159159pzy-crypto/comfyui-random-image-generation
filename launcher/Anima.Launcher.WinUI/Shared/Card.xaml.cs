using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Anima.Launcher;

/// <summary>A settings-style card: optional icon + header/description + action area that
/// reflows below the text when the card is narrow (instead of being clipped).</summary>
public sealed partial class Card : UserControl
{
    private const double NarrowThreshold = 560;

    public Card()
    {
        InitializeComponent();
        SizeChanged += OnSizeChanged;
    }

    public static readonly DependencyProperty HeaderProperty =
        DependencyProperty.Register(nameof(Header), typeof(string), typeof(Card), new PropertyMetadata("", (d, e) => ((Card)d).HeaderText.Text = (string)e.NewValue));
    public string Header { get => (string)GetValue(HeaderProperty); set => SetValue(HeaderProperty, value); }

    public static readonly DependencyProperty DescriptionProperty =
        DependencyProperty.Register(nameof(Description), typeof(string), typeof(Card), new PropertyMetadata("", (d, e) =>
        {
            var s = (string)e.NewValue;
            var t = ((Card)d).DescriptionText;
            t.Text = s;
            t.Visibility = string.IsNullOrEmpty(s) ? Visibility.Collapsed : Visibility.Visible;
        }));
    public string Description { get => (string)GetValue(DescriptionProperty); set => SetValue(DescriptionProperty, value); }

    public static readonly DependencyProperty IconGlyphProperty =
        DependencyProperty.Register(nameof(IconGlyph), typeof(string), typeof(Card), new PropertyMetadata("", (d, e) =>
        {
            var g = (string)e.NewValue;
            var host = ((Card)d).IconHost;
            host.Visibility = string.IsNullOrEmpty(g) ? Visibility.Collapsed : Visibility.Visible;
            if (!string.IsNullOrEmpty(g)) ((Card)d).CardIcon.Glyph = g;
        }));
    public string IconGlyph { get => (string)GetValue(IconGlyphProperty); set => SetValue(IconGlyphProperty, value); }

    public static readonly DependencyProperty ActionContentProperty =
        DependencyProperty.Register(nameof(ActionContent), typeof(object), typeof(Card), new PropertyMetadata(null, (d, e) => ((Card)d).ActionHost.Content = e.NewValue));
    public object? ActionContent { get => GetValue(ActionContentProperty); set => SetValue(ActionContentProperty, value); }

    private void OnSizeChanged(object sender, SizeChangedEventArgs e)
    {
        var state = e.NewSize.Width < NarrowThreshold ? "Narrow" : "Wide";
        VisualStateManager.GoToState(this, state, true);
    }
}
