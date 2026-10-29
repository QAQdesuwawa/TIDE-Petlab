[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Config,
    [string]$Workspace = '',
    [string]$PythonPath = '',
    [string]$Assets = ''
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
if (-not $PythonPath) {
    $localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $localPython) { $PythonPath = $localPython } else { $PythonPath = 'python' }
}
if (-not $Workspace) { $Workspace = Join-Path $PSScriptRoot 'output/multi-pet' }
if (-not $Assets) { $Assets = Join-Path $PSScriptRoot 'assets/pets/deepseek-maid-v1' }
& $PythonPath -B -X utf8 -m petlab_app.multi_desktop --config $Config --workspace $Workspace --assets $Assets
exit $LASTEXITCODE
