[CmdletBinding(DefaultParameterSetName='Enable')]
param(
    [Parameter(ParameterSetName='Enable',Mandatory=$true)][switch]$Enable,
    [Parameter(ParameterSetName='Disable',Mandatory=$true)][switch]$Disable,
    [string]$StartupDirectory = [Environment]::GetFolderPath('Startup')
)
$ErrorActionPreference = 'Stop'
$petStartup = [IO.Path]::GetFullPath($StartupDirectory)
$petLink = [IO.Path]::GetFullPath((Join-Path $petStartup 'TIDE-PetLab.lnk'))
if ([IO.Path]::GetDirectoryName($petLink) -ne $petStartup.TrimEnd('\')) { throw 'Invalid startup shortcut path' }
if ($Disable) {
    if (Test-Path -LiteralPath $petLink) { Remove-Item -LiteralPath $petLink }
    Write-Output 'PetLab login startup disabled.'
    return
}
$petResume = Join-Path $PSScriptRoot 'resume_pet.ps1'
if (-not (Test-Path -LiteralPath $petResume)) { throw 'Missing resume_pet.ps1' }
New-Item -ItemType Directory -Force -Path $petStartup | Out-Null
$petShell = New-Object -ComObject WScript.Shell
$petShortcut = $petShell.CreateShortcut($petLink)
$petShortcut.TargetPath = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$petShortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $petResume + '"'
$petShortcut.WorkingDirectory = $PSScriptRoot
$petShortcut.WindowStyle = 7
$petShortcut.Description = 'Restore your last TIDE desktop pet at Windows login'
$petShortcut.Save()
Write-Output 'PetLab login startup enabled for the current user.'
