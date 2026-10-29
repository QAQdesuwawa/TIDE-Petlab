[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$petPython = Join-Path $PSScriptRoot '.venv/Scripts/pythonw.exe'
if (-not (Test-Path -LiteralPath $petPython)) { throw 'Run START.cmd once to install PetLab first.' }
$env:PYTHONDONTWRITEBYTECODE = '1'
$petLogs = Join-Path $PSScriptRoot 'output/launcher'
New-Item -ItemType Directory -Force -Path $petLogs | Out-Null
Start-Process -FilePath $petPython -ArgumentList @('-B','-X','utf8','-m','petlab_app','--resume') -WorkingDirectory $PSScriptRoot -WindowStyle Hidden -RedirectStandardOutput (Join-Path $petLogs 'stdout.log') -RedirectStandardError (Join-Path $petLogs 'stderr.log')
