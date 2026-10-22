param([int]$Port = 8000)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$pythonPath = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $pythonPath)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create virtual environment. Install Python 3.11+.' }
}
& $pythonPath -m pip install --disable-pip-version-check -r requirements.lock.txt
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }
Write-Host "Reading Agent: http://127.0.0.1:$Port"
& $pythonPath -m uvicorn agent.app:app --host 127.0.0.1 --port $Port
