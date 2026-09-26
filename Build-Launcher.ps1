param(
    [string]$Configuration = 'Release',
    [string]$CertificateThumbprint = '',
    [switch]$InstallShortcut
)
$ErrorActionPreference = 'Stop'
$repo = $PSScriptRoot
$sdk = Join-Path $env:LOCALAPPDATA 'Codex\dotnet-sdk-8\dotnet.exe'
if (-not (Test-Path -LiteralPath $sdk)) { $sdk = (Get-Command dotnet -ErrorAction Stop).Source }
$payload = Join-Path $repo 'launcher\payload.zip'
Add-Type -AssemblyName System.IO.Compression
$stream = [System.IO.File]::Create($payload)
$archive = [System.IO.Compression.ZipArchive]::new($stream, [System.IO.Compression.ZipArchiveMode]::Create)
try {
    $files = @('run.py', 'LICENSE', 'pyproject.toml')
    foreach ($folder in @('anima_webui', 'static', 'templates')) {
        $files += Get-ChildItem -LiteralPath (Join-Path $repo $folder) -File -Recurse |
            Where-Object { $_.FullName -notmatch '__pycache__|\.pyc$' } |
            ForEach-Object { $_.FullName.Substring($repo.Length + 1) } # Path.GetRelativePath 需要 .NET Core，PS 5.1 没有
    }
    foreach ($file in $files) {
        $entry = $archive.CreateEntry($file.Replace('\', '/'), [System.IO.Compression.CompressionLevel]::Optimal)
        $target = $entry.Open()
        $source = [System.IO.File]::OpenRead((Join-Path $repo $file))
        try { $source.CopyTo($target) } finally { $source.Dispose(); $target.Dispose() }
    }
} finally { $archive.Dispose(); $stream.Dispose() }
$output = Join-Path $repo 'dist\launcher'
# WinUI 3 cannot publish as a single file; the launcher ships as an unpackaged,
# self-contained folder so end users need no .NET or Windows App Runtime install.
& $sdk publish (Join-Path $repo 'launcher\Anima.Launcher.WinUI\Anima.Launcher.WinUI.csproj') -c $Configuration -r win-x64 -o $output --nologo
if ($LASTEXITCODE -ne 0) { throw 'Launcher publish failed.' }
$exe = Join-Path $output 'AnimaRandomStudio.exe'
if ($CertificateThumbprint) {
    $certificate = Get-Item "Cert:\CurrentUser\My\$CertificateThumbprint"
    $signature = Set-AuthenticodeSignature -FilePath $exe -Certificate $certificate -TimestampServer 'http://timestamp.digicert.com'
    if ($signature.Status -ne 'Valid') { throw "Signing failed: $($signature.StatusMessage)" }
}
$hash = Get-FileHash -LiteralPath $exe -Algorithm SHA256
[System.IO.File]::WriteAllText((Join-Path $output 'SHA256SUMS.txt'), "$($hash.Hash.ToLowerInvariant())  AnimaRandomStudio.exe`n")
Write-Output "NOTE: WinUI 3 ships as a folder; distribute the whole dist\launcher directory (exe + DLLs)."
if ($InstallShortcut) {
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Anima Random Studio.lnk'))
    $link.TargetPath = $exe
    $link.WorkingDirectory = $output
    $link.IconLocation = "$exe,0"
    $link.Save()
}
Write-Output "Launcher: $exe"
Write-Output "SHA256: $($hash.Hash)"
