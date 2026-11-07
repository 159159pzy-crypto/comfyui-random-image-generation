param([switch]$Dev)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    python -c "import sys; assert sys.version_info >= (3, 11), 'Python 3.11+ is required'"
    if ($LASTEXITCODE -ne 0) { throw '请安装 Python 3.11 或更高版本。' }
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw '无法创建项目虚拟环境。' }
}
$target = if ($Dev) { '.[dev]' } else { '.' }
& $venvPython -X utf8 -m pip install -e $target
if ($LASTEXITCODE -ne 0) { throw '依赖安装失败，请检查上方错误后重试。' }
Write-Host '安装完成。运行 .\Start.ps1 打开 Anima Random WebUI。'
