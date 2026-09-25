using System.Windows.Input;

namespace Anima.Launcher;

/// <summary>Async command that disables itself while running.</summary>
internal sealed class AsyncCommand(Func<Task> execute, Func<bool>? canExecute = null) : ICommand
{
    private bool busy;
    public event EventHandler? CanExecuteChanged;
    public bool CanExecute(object? parameter) => !busy && (canExecute?.Invoke() ?? true);
    public async void Execute(object? parameter)
    {
        busy = true;
        CanExecuteChanged?.Invoke(this, EventArgs.Empty);
        try { await execute(); }
        finally { busy = false; CanExecuteChanged?.Invoke(this, EventArgs.Empty); }
    }
    public void RaiseCanExecuteChanged() => CanExecuteChanged?.Invoke(this, EventArgs.Empty);
}
