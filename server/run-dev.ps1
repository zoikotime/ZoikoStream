# Start the ZoikoStream API for local development (Windows PowerShell):   .\run-dev.ps1
#
# Port 8001 by default. Port 8000 belongs to another local API on the development machine; two
# uvicorn --reload servers on one port can both bind it on Windows, and requests then reach
# whichever one Windows picks. Override for one session with:
#     $env:ZOIKOSTREAM_API_PORT = "8010"; .\run-dev.ps1
# and point the client at the same port (VITE_API_URL in the repo-root .env, then restart
# `npm run dev`, because Vite reads env files only at startup).
$port = if ($env:ZOIKOSTREAM_API_PORT) { $env:ZOIKOSTREAM_API_PORT } else { "8001" }
Set-Location $PSScriptRoot
Write-Host "ZoikoStream API on http://127.0.0.1:$port (docs: /docs)"
& "$PSScriptRoot\venv\Scripts\python.exe" -m uvicorn app.main:app --host 127.0.0.1 --port $port --reload
