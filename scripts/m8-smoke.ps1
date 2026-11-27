param([switch]$ShareWrite)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install services/backend/requirements.lock first.' }
Push-Location (Join-Path $root 'services/backend')
try {
    if ($ShareWrite) { & $python -m app.m8_cli --share-write }
    else { & $python -m app.m8_cli }
    if ($LASTEXITCODE -ne 0) { throw "M8 smoke check failed (exit $LASTEXITCODE)." }
}
finally { Pop-Location }
