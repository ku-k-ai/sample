[CmdletBinding()]
param(
    [string]$PythonExe = 'py',
    [string]$ChromeVersion = ''
)
$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
Set-Location -LiteralPath $PSScriptRoot

function Assert-Exit([string]$Step) {
    if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)." }
}

if ($env:OS -ne 'Windows_NT') { throw 'This setup script is for Windows.' }
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if ($PythonExe -eq 'py') {
        & $PythonExe -3 -m venv .venv
    } else {
        & $PythonExe -m venv .venv
    }
    Assert-Exit 'Create Python virtual environment'
}
$Python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $Python -c "import sys,struct; assert sys.version_info >= (3,10), 'Python 3.10+ required'; assert struct.calcsize('P') == 8, '64-bit Python required'"
Assert-Exit 'Check Python'
& $Python -m pip install --upgrade pip
Assert-Exit 'Update pip'
& $Python -m pip install -r requirements.txt
Assert-Exit 'Install dependencies'
$SBase = Join-Path $PSScriptRoot '.venv\Scripts\sbase.exe'
if ($ChromeVersion) { & $SBase get cft $ChromeVersion } else { & $SBase get cft }
Assert-Exit 'Download Chrome for Testing'
# Find the downloaded browser and install its exact matching ChromeDriver.
$DriverRoot = & $Python -c "import seleniumbase; from pathlib import Path; print(Path(seleniumbase.__file__).parent / 'drivers')"
Assert-Exit 'Locate SeleniumBase drivers'
$Chrome = @(Get-ChildItem -LiteralPath $DriverRoot -Filter 'chrome.exe' -Recurse)
if ($Chrome.Count -ne 1) { throw 'Expected one Chrome for Testing chrome.exe under seleniumbase\drivers.' }
$Version = $Chrome[0].VersionInfo.ProductVersion
if ($Version -notmatch '^\d+\.\d+\.\d+\.\d+$') { throw 'Could not read the downloaded Chrome version.' }
& $SBase get chromedriver $Version
Assert-Exit 'Download matching ChromeDriver'
& $Python -m pip freeze | Set-Content -LiteralPath 'installed-requirements.txt' -Encoding UTF8
Assert-Exit 'Record installed package versions'
@{ browser = $Chrome[0].FullName; version = $Version; seleniumbase = '4.51.12' } |
    ConvertTo-Json | Set-Content -LiteralPath 'installed-browser.json' -Encoding UTF8
if (-not (Test-Path -LiteralPath '.env')) {
    Copy-Item -LiteralPath '.env.example' -Destination '.env'
}
Write-Host "Setup complete. Chrome for Testing: $Version"
Write-Host 'Edit .env locally, then execute: .\run.ps1'
