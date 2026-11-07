$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $venvPython)) {
    throw '尚未安装依赖，请先运行 .\Install.ps1。'
}
& $venvPython -X utf8 (Join-Path $PSScriptRoot 'run.py') @args
exit $LASTEXITCODE
