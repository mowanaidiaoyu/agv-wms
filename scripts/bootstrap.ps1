$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path -LiteralPath "$root\.venv")) {
  python -m venv "$root\.venv"
}

& "$root\.venv\Scripts\python.exe" -m pip install --upgrade pip
& "$root\.venv\Scripts\pip.exe" install -r "$root\backend\requirements.txt"

Push-Location "$root\frontend"
npm ci
Pop-Location

Write-Host "Bootstrap complete."
