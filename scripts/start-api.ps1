$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install requirements.lock first; see README.md.' }
Push-Location (Join-Path $root 'services/backend')
try { & $python -m uvicorn app.main:app --host 127.0.0.1 --port 8000 }
finally { Pop-Location }
