param(
    [ValidateRange(1024,65535)][int]$Port = 8765,
    [ValidateSet('full','two-actions')][string]$CaptureProfile = 'full'
)
$ErrorActionPreference = 'Stop'
$python = 'C:\Users\14148\AppData\Local\Programs\Python\Python313\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw "Required Python 3.13 executable was not found: $python" }
$version = & $python -c 'import sys; print(sys.version.split()[0])'
if ($LASTEXITCODE -ne 0) { throw 'Could not determine the Python version.' }
if ($version -notmatch '^3\.13\.') { throw "Required Python 3.13, got $version." }
Write-Host "Antikythera UI: http://127.0.0.1:$Port"
Write-Host 'Keep this window open. Press Ctrl+C to stop.'
& $python (Join-Path $PSScriptRoot 'server.py') --port $Port --capture-profile $CaptureProfile
