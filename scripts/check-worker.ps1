$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
Push-Location (Join-Path $root 'services/backend')
try { & (Join-Path $root '.venv/Scripts/python.exe') -m app.worker --check }
finally { Pop-Location }
