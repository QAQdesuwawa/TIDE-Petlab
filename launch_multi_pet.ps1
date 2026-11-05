[CmdletBinding()]
param(
    [Parameter(Mandatory=$true)][string]$Config,
    [string]$Workspace = '',
    [string]$PythonPath = '',
    [string]$Assets = '',
    [int]$PetCount = 0
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
if (-not $PythonPath) {
    $bundledPython = Join-Path $PSScriptRoot '_runtime/python.exe'
    $localPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
    if (Test-Path -LiteralPath $bundledPython) { $PythonPath = $bundledPython } elseif (Test-Path -LiteralPath $localPython) { $PythonPath = $localPython } else { $PythonPath = 'python' }
}
if (-not $Workspace) { $Workspace = Join-Path $PSScriptRoot 'output/multi-pet' }
if (-not $Assets) { $Assets = Join-Path $PSScriptRoot 'assets/pets/deepseek-bound-original-face' }
$countArgs = @()
if ($PetCount -gt 0) { $countArgs = @('--count', $PetCount) }
& $PythonPath -B -X utf8 -m petlab_app.multi_desktop --config $Config --workspace $Workspace --assets $Assets @countArgs
exit $LASTEXITCODE
