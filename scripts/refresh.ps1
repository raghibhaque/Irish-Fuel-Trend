$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$log = Join-Path $root "data\refresh.log"
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
$stamp = (Get-Date).ToUniversalTime().ToString("s") + "Z"
Set-Location $backend
"[$stamp] ingest start" | Out-File -FilePath $log -Append -Encoding utf8
python ingest.py *>> $log
"[$stamp] export start" | Out-File -FilePath $log -Append -Encoding utf8
python export_static.py *>> $log
"[$stamp] done" | Out-File -FilePath $log -Append -Encoding utf8
