# One-time setup for a NOVA development checkout.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
#
# Needs Python 3.12+, Node.js with pnpm, the Rust toolchain and Ollama already installed.

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$model = if ($env:NOVA_MODEL) { $env:NOVA_MODEL } else { 'qwen3:8b' }
$embedModel = if ($null -ne $env:NOVA_EMBED_MODEL) { $env:NOVA_EMBED_MODEL } else { 'embeddinggemma' }

foreach ($tool in 'python', 'pnpm', 'cargo', 'ollama') {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool is not on PATH. Install it, open a new terminal and run this script again."
    }
}

Write-Host '== Backend: virtual environment and dependencies'
$venv = Join-Path $root 'backend\.venv'
if (-not (Test-Path $venv)) { python -m venv $venv }
& "$venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -e "$root\backend[dev]"

Write-Host '== Desktop: frontend dependencies'
pnpm --dir "$root\apps\desktop" install

Write-Host "== Model: $model"
if (-not (ollama list | Select-String -SimpleMatch $model)) { ollama pull $model }

if ($embedModel) {
    Write-Host "== Memory embeddings: $embedModel"
    if (-not (ollama list | Select-String -SimpleMatch $embedModel)) { ollama pull $embedModel }
}

# Voice models live in backend\models, outside AppData, so NOVA finds them however it is started.
Write-Host '== Voice: Whisper tiny.en and base.en, Piper en_US-ljspeech-high (about 330 MB)'
$models = Join-Path $root 'backend\models'
$env:HF_HUB_DISABLE_SYMLINKS_WARNING = '1'
& "$venv\Scripts\python.exe" -c @"
from pathlib import Path
from faster_whisper import WhisperModel
from piper.download_voices import download_voice
models = Path(r'$models')
(models / 'voices').mkdir(parents=True, exist_ok=True)
download_voice('en_US-ljspeech-high', models / 'voices')
for name in ('tiny.en', 'base.en'):
    WhisperModel(name, device='cpu', compute_type='int8', download_root=str(models / 'whisper'))
print('voice models ready')
"@

Write-Host ''
Write-Host 'Ready. Start NOVA with:  pnpm --dir apps\desktop tauri dev'
