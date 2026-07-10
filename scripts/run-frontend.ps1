$root = Split-Path -Parent $PSScriptRoot
Push-Location "$root\frontend"
npm run dev -- --host 127.0.0.1 --port 5173
Pop-Location
