param(
    [switch]$Launch
)

$ErrorActionPreference = "Stop"

function Require-Command {
    param([string]$Name)
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        throw "Required command '$Name' was not found in PATH."
    }
}

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

Require-Command python

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    Write-Host "Creating virtual environment..."
    python -m venv .venv
}

$venvPython = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $venvPython)) {
    throw "Failed to create virtual environment at .venv"
}

Write-Host "Installing dependencies..."
& $venvPython -m pip install --upgrade pip
& $venvPython -m pip install -r requirements.txt

Write-Host ""
Write-Host "Install complete."
Write-Host "Start GUI with:"
Write-Host "  .\run_gui.bat"

if ($Launch) {
    Write-Host "Launching GUI..."
    Start-Process -FilePath (Join-Path $root "run_gui.bat")
}
