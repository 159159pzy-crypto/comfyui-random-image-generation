"""Run the standalone WebUI quality gates without launcher dependencies."""
from pathlib import Path
import shutil
import subprocess
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]


def run(*args: str) -> None:
    print('CHECK: ' + ' '.join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    metadata = tomllib.loads((ROOT / 'pyproject.toml').read_text(encoding='utf-8'))
    assert metadata['project']['name'] == 'anima-random-webui'
    assert metadata['project']['version'] == '0.1.0'
    run(sys.executable, '-m', 'ruff', 'check', 'anima_webui', 'tests', 'scripts', 'run.py')
    powershell = shutil.which('powershell.exe') or shutil.which('pwsh')
    if powershell:
        for name in ('Install.ps1', 'Start.ps1'):
            run(powershell, '-NoProfile', '-NonInteractive', '-Command',
                '$parseErrors = $null; '
                f"[System.Management.Automation.Language.Parser]::ParseFile('{name}', "
                '[ref]$null, [ref]$parseErrors) | Out-Null; '
                'if ($parseErrors) { $parseErrors | Out-String | Write-Error; exit 1 }')
    for path in sorted((ROOT / 'static').glob('*.js')):
        run('node', '--check', str(path))
    run(sys.executable, '-m', 'pytest', '-q')
    run('node', '--test', *map(str, sorted((ROOT / 'tests').glob('*.test.cjs'))))


if __name__ == '__main__':
    main()
