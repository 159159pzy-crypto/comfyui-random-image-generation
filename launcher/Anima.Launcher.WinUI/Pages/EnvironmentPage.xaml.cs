using Anima.Launcher.Core;
using Microsoft.UI;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Navigation;
using System.Runtime.InteropServices;
using Windows.Storage.Pickers;
using WinRT.Interop;

namespace Anima.Launcher.Pages;

public sealed partial class EnvironmentPage : LauncherPage
{
    public EnvironmentPage()
    {
        InitializeComponent();
        SourceBox.ItemsSource = Program.Sources.Profiles;
        SourceBox.DisplayMemberPath = nameof(SourceProfile.Name);
    }

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
        RootBox.Text = c.ComfyRoot;
        PythonBox.Text = c.Python;
        SourceBox.SelectedItem = Program.Sources.Profiles.FirstOrDefault(s => s.Id == c.SourceId) ?? Program.Sources.Profiles[0];
        GitMirrorBox.Text = c.GitMirror;
        HfMirrorBox.Text = c.ModelMirror;
        PipBox.Text = c.PipIndex;
        CustomRepoBox.Text = c.CustomRepository;
        SystemInfo.Text = State.SystemInfoText;
        ImportMode.IsChecked = !State.FreshInstallRequested;
        FreshMode.IsChecked = State.FreshInstallRequested;
    }

    public void SyncConfig()
    {
        State.PendingEnvironmentFields = new(
            RootBox.Text,
            PythonBox.Text,
            (SourceBox.SelectedItem as SourceProfile)?.Id ?? "official",
            GitMirrorBox.Text,
            HfMirrorBox.Text,
            PipBox.Text,
            CustomRepoBox.Text);
        State.FreshInstallRequested = FreshMode.IsChecked == true;
        State.ApplyPendingFields();
    }

    private void Source_Changed(object sender, SelectionChangedEventArgs e)
    {
        if (SourceBox.SelectedItem is SourceProfile p && p.Id != "custom")
        {
            GitMirrorBox.Text = p.GitTemplate;
            HfMirrorBox.Text = p.HuggingFaceBase;
            PipBox.Text = p.PipIndex;
        }
    }

    private async void PickRoot_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FolderPicker { SuggestedStartLocation = PickerLocationId.ComputerFolder };
        picker.FileTypeFilter.Add("*");
        InitializeWithWindow.Initialize(picker, Win32Interop.GetWindowFromWindowId(Shell.AppWindow.Id));
        var folder = await picker.PickSingleFolderAsync();
        if (folder is not null) RootBox.Text = folder.Path;
    }

    private async void PickPython_Click(object sender, RoutedEventArgs e)
    {
        var picker = new FileOpenPicker { SuggestedStartLocation = PickerLocationId.ComputerFolder };
        picker.FileTypeFilter.Add(".exe");
        InitializeWithWindow.Initialize(picker, Win32Interop.GetWindowFromWindowId(Shell.AppWindow.Id));
        var file = await picker.PickSingleFileAsync();
        if (file is not null) PythonBox.Text = file.Path;
    }

    private async void Check_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            SyncConfig();
            await State.Run(async ct => await State.CheckImportAsync(ct));
        }
        catch (Exception ex) { await ShowError(ex); }
    }

    private async void Install_Click(object sender, RoutedEventArgs e)
    {
        try
        {
            SyncConfig();
            await State.Run(async ct => await State.InstallAsync(ct));
        }
        catch (Exception ex) { await ShowError(ex); }
    }

    private async Task ShowError(Exception ex)
    {
        State.AppendLog(ex.Message);
        State.SetStatus(ex.Message);
        Shell.ShowWindow();
        if (State.Root is { } root) await Dialogs.Error(root, ex.Message);
    }
}
