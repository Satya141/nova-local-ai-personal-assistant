# Builds NOVA's Windows installer (NSIS, per-user, no admin rights needed).
#
#   powershell -ExecutionPolicy Bypass -File scripts\package.ps1 -PythonZip <python-3.12.x-embed-amd64.zip>
#
# Needs a checkout set up with scripts\setup.ps1 (its .venv and backend\models are copied in),
# and Python's official "Windows embeddable package (64-bit)" for the same 3.12 version as the
# .venv, from https://www.python.org/downloads/windows/. The installer carries:
#
#   backend\python         the embeddable Python, its ._pth file pointing at app and site-packages
#   backend\app            the nova package
#   backend\site-packages  the .venv's packages, without the development-only ones
#   backend\models         the Whisper and Piper voice models
#   backend\phone-ui       the built phone app
#
# Ollama and the language models are not included: NOVA's first-run screen offers them.
# The first build lets the Tauri CLI fetch its NSIS tools.

param(
    [Parameter(Mandatory = $true)][string]$PythonZip
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$desktop = Join-Path $root "apps\desktop"
$stage = Join-Path $root "dist\backend"
$venvPython = Join-Path $backend ".venv\Scripts\python.exe"

if (-not (Test-Path $venvPython)) { throw "No backend\.venv: run scripts\setup.ps1 first." }
if (-not (Test-Path (Join-Path $backend "models"))) { throw "No backend\models: run scripts\setup.ps1 first." }
if (-not (Test-Path $PythonZip)) { throw "Not found: $PythonZip" }

# The embeddable Python must match the .venv's minor version, or compiled packages will not load.
$version = & $venvPython -c "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')"
$tag = $version -replace "\.", ""
if ((Split-Path -Leaf $PythonZip) -notmatch "python-$([regex]::Escape($version))\.\d+-embed-amd64\.zip") {
    throw "The .venv is Python $version; use python-$version.x-embed-amd64.zip."
}

Write-Host "Staging the backend in $stage"
if (Test-Path $stage) { Remove-Item -Recurse -Force $stage }
New-Item -ItemType Directory -Force $stage | Out-Null

# Python itself, told where NOVA's code and packages are (paths relative to python.exe).
$python = Join-Path $stage "python"
Expand-Archive -Path $PythonZip -DestinationPath $python
Set-Content -Encoding ascii -Path (Join-Path $python "python$tag._pth") -Value @(
    "python$tag.zip", ".", "..\app", "..\site-packages", "import site"
)

# NOVA's code, without caches.
robocopy (Join-Path $backend "nova") (Join-Path $stage "app\nova") /E /XD __pycache__ /NFL /NDL /NJH /NJS /NP | Out-Null

# Its packages, without what only development needs: tests, pip, and the editable link
# that points a checkout's .venv at the checkout.
$excluded = @(
    "pip", "_pytest", "pytest", "pytest_asyncio", "pluggy", "iniconfig", "httpx2",
    "pip-*.dist-info", "pytest-*.dist-info", "pytest_asyncio-*.dist-info", "pluggy-*.dist-info",
    "iniconfig-*.dist-info", "httpx2-*.dist-info", "nova_backend-*.dist-info"
)
robocopy (Join-Path $backend ".venv\Lib\site-packages") (Join-Path $stage "site-packages") /E `
    /XD __pycache__ @excluded /XF "__editable__*" "*.pyc" /NFL /NDL /NJH /NJS /NP | Out-Null

# The voice models. Not the phone's pocket model: about 1 GB that only phones use.
robocopy (Join-Path $backend "models") (Join-Path $stage "models") /E /XD pocket /NFL /NDL /NJH /NJS /NP | Out-Null

Write-Host "Building the interface"
Push-Location $desktop
try {
    pnpm build
    if ($LASTEXITCODE -ne 0) { throw "pnpm build failed" }
    robocopy (Join-Path $desktop "out") (Join-Path $stage "phone-ui") /E /NFL /NDL /NJH /NJS /NP | Out-Null

    # Smoke test: the staged Python finds NOVA and every package it imports.
    $check = & (Join-Path $python "python.exe") -c "import nova.api.app, faster_whisper, piper, playwright, cryptography, segno; print('ok')"
    if ($check -ne "ok") { throw "The staged backend does not import: $check" }

    Write-Host "Building the installer"
    pnpm exec tauri build --config src-tauri\tauri.bundle.conf.json
    if ($LASTEXITCODE -ne 0) { throw "tauri build failed" }
}
finally {
    Pop-Location
}

$installer = Get-ChildItem (Join-Path $desktop "src-tauri\target\release\bundle\nsis") -Filter "*.exe" |
    Sort-Object LastWriteTime -Descending | Select-Object -First 1
Write-Host ("Installer: {0} ({1:N0} MB)" -f $installer.FullName, ($installer.Length / 1MB))
