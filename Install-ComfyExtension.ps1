param(
    [Parameter(Mandatory = $true)][string]$ComfyUIPath,
    [string]$ComfyUrl = 'http://127.0.0.1:8188',
    [switch]$Check
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw '请先运行 Install.ps1 安装本项目的 Python 环境。'
}
$extensionArgs = @('-X', 'utf8', '-m', 'anima_webui.extension_install', '--comfy-root', $ComfyUIPath, '--comfy-url', $ComfyUrl)
if ($Check) { $extensionArgs += '--check' }
& $venvPython @extensionArgs
if ($LASTEXITCODE -ne 0) { throw '配套扩展检查或安装未完成，请查看上方信息。' }
