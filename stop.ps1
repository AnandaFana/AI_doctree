param([int]$Port = 8765)
$ErrorActionPreference = 'Stop'
Invoke-RestMethod -Uri "http://127.0.0.1:$Port/api/stop" -Method Post -ContentType 'application/json' -Body '{}'
