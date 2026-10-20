# Run from the CodeIntelligence backend directory (where main.py is located).
$ErrorActionPreference = 'Stop'
Write-Host 'Current directory:' (Get-Location)
Write-Host 'Main file:' (Resolve-Path .\main.py)
Write-Host 'Requirement router present:' (Test-Path .\routers\requirement_impact_router.py)
Write-Host 'Running processes listening on 8081:'
Get-NetTCPConnection -LocalPort 8081 -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
  $pidOfServer = $_.OwningProcess
  Get-CimInstance Win32_Process -Filter "ProcessId=$pidOfServer" | Select-Object ProcessId,ExecutablePath,CommandLine
} | Format-List
Write-Host 'Verifying imported app and registered route:'
@'
import main
print('Loaded main.py:', main.__file__)
paths = [(r.path, sorted(getattr(r, 'methods', []) or [])) for r in main.app.routes]
for path, methods in paths:
    if 'requirement-impact' in path:
        print('Registered:', path, methods)
assert any(path == '/api/requirement-impact/analyze' and 'POST' in methods for path, methods in paths), 'Requirement impact POST route not registered in this Python environment'
'@ | python -
Write-Host 'If route is registered above but absent from port 8081 Swagger, stop the old server and restart from this directory.'
