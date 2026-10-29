[CmdletBinding()]
param(
    [string]$Config = '',
    [string]$Session = '',
    [string]$TraceDir = '',
    [string]$PythonPath = 'python',
    [string]$Assets = '',
    [switch]$Validate,
    [switch]$Setup
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$petArguments = @('-B', '-X', 'utf8', '-m', 'petlab_app')
if ($Config) { $petArguments += @('--config', $Config) }
if ($Session) { $petArguments += @('--session', $Session) }
if ($TraceDir) { $petArguments += @('--traces', $TraceDir) }
if ($Assets) { $petArguments += @('--assets', $Assets) }
if ($Validate) { $petArguments += '--validate' }
if ($Setup) { $petArguments += '--setup' }
& $PythonPath @petArguments
exit $LASTEXITCODE
