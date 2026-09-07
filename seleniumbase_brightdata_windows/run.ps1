[CmdletBinding()]
param(
    [string]$ConfigFile = '.env',
    [switch]$Headless
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location -LiteralPath $PSScriptRoot
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $Python)) { throw 'Run .\setup.ps1 first.' }
if (-not (Test-Path -LiteralPath $ConfigFile)) { throw 'Create a local .env from .env.example first.' }
$Arguments = @((Join-Path $PSScriptRoot 'run.py'), '--env-file', $ConfigFile)
if ($Headless) { $Arguments += '--headless' }
& $Python @Arguments
exit $LASTEXITCODE
