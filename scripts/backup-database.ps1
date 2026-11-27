$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install services/backend/requirements.lock first; see docs/M8_OPERATIONS.md.' }
Push-Location (Join-Path $root 'services/backend')
try {
    & $python -m app.backup_cli backup
    if ($LASTEXITCODE -ne 0) { throw "Backup failed (exit $LASTEXITCODE). Review the safe error code above." }
}
finally { Pop-Location }
