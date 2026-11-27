param(
    [Parameter(Mandatory = $true)][string]$ObjectKey,
    [Parameter(Mandatory = $true)][string]$TargetProjectRef,
    [switch]$ConfirmNewProject
)
$ErrorActionPreference = 'Stop'
if (!$ConfirmNewProject) {
    throw 'Restore is destructive to its target. Rerun only after setting RESTORE_DATABASE_URL to a new, empty Supabase project and add -ConfirmNewProject.'
}
$root = Split-Path $PSScriptRoot -Parent
$python = Join-Path $root '.venv/Scripts/python.exe'
if (!(Test-Path $python)) { throw 'Create .venv and install services/backend/requirements.lock first; see docs/M8_OPERATIONS.md.' }
Push-Location (Join-Path $root 'services/backend')
try {
    & $python -m app.backup_cli restore --object-key $ObjectKey --target-project-ref $TargetProjectRef --confirm-new-project
    if ($LASTEXITCODE -ne 0) { throw "Restore failed (exit $LASTEXITCODE). Review the safe error code above." }
}
finally { Pop-Location }
