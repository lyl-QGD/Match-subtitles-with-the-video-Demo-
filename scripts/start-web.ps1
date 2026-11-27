$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$portable = Get-ChildItem (Join-Path $root '.tools') -Directory -Filter 'node-v*-win-x64' -ErrorAction SilentlyContinue | Select-Object -First 1
if ($portable) { $env:Path = "$($portable.FullName);$env:Path" }
if (!(Get-Command npm.cmd -ErrorAction SilentlyContinue)) { throw 'Install Node.js LTS first; see README.md.' }
Push-Location (Join-Path $root 'apps/web')
try { & npm.cmd run dev }
finally { Pop-Location }
