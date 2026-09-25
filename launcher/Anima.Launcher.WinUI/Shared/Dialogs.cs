using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Anima.Launcher;

internal static class Dialogs
{
    public static async Task<bool> Confirm(XamlRoot root, string title, string message, string yes = "确定", string no = "取消")
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = title,
            Content = new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap },
            PrimaryButtonText = yes,
            CloseButtonText = no,
            DefaultButton = ContentDialogButton.Primary,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    public static async Task<bool> ConfirmCancel(XamlRoot root, string title, string message, string primary = "确定")
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = title,
            Content = new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap },
            PrimaryButtonText = primary,
            CloseButtonText = "取消",
            DefaultButton = ContentDialogButton.Close,
        };
        return await dialog.ShowAsync() == ContentDialogResult.Primary;
    }

    public static async Task Error(XamlRoot root, string message, string title = "操作未完成")
    {
        var dialog = new ContentDialog
        {
            XamlRoot = root,
            Title = title,
            Content = new TextBlock { Text = message, TextWrapping = TextWrapping.Wrap },
            CloseButtonText = "关闭",
        };
        await dialog.ShowAsync();
    }
}
