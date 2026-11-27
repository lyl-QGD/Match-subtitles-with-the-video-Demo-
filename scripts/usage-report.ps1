param(
    [ValidateRange(1, 31)][int]$Days = 7,
    [switch]$Record
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install services/backend/requirements.lock first; see docs/M8_OPERATIONS.md.' }
Push-Location (Join-Path $root 'services/backend')
try {
    if ($Record) { & $python -m app.backup_cli usage --days $Days --record }
    else { & $python -m app.backup_cli usage --days $Days }
    if ($LASTEXITCODE -ne 0) { throw "Usage report failed (exit $LASTEXITCODE)." }
}
finally { Pop-Location }
