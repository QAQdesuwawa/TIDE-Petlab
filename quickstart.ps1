[CmdletBinding()]
param([string]$PythonPath = '', [switch]$CheckOnly, [switch]$Setup)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:PIP_CACHE_DIR = Join-Path $PSScriptRoot 'output/pip-cache'
$venvPython = Join-Path $PSScriptRoot '.venv/Scripts/python.exe'
function Assert-Exit([string]$Step) { if ($LASTEXITCODE -ne 0) { throw "$Step failed (exit $LASTEXITCODE)." } }
if (-not (Test-Path -LiteralPath $venvPython)) {
    if (-not $PythonPath) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $PythonPath = & py -3.11 -c 'import sys; print(sys.executable)'
            Assert-Exit 'Find Python 3.11'
        } elseif (Get-Command python -ErrorAction SilentlyContinue) { $PythonPath = 'python' }
        else { throw 'Install 64-bit Python 3.11 with Tcl/Tk first: https://www.python.org/downloads/windows/' }
    }
    & $PythonPath -B -c 'import sys,struct,tkinter; assert sys.version_info[:2] == (3,11) and struct.calcsize(chr(80))==8'
    Assert-Exit 'Check Python and Tk'
    & $PythonPath -B -m venv (Join-Path $PSScriptRoot '.venv')
    Assert-Exit 'Create environment'
}
& $venvPython -B -c 'import tkinter,sys; assert sys.version_info[:2] == (3,11)'
Assert-Exit 'Check environment'
$requirements = Join-Path $PSScriptRoot 'requirements-pet.lock.txt'
if (-not (Test-Path -LiteralPath $requirements)) { $requirements = Join-Path $PSScriptRoot 'requirements-pet.txt' }
$stamp = Join-Path $PSScriptRoot '.venv/petlab-requirements.sha256'
$petDigestAlgorithm = [System.Security.Cryptography.SHA256]::Create()
$petRequirementsStream = [System.IO.File]::OpenRead($requirements)
try { $digest = [System.BitConverter]::ToString($petDigestAlgorithm.ComputeHash($petRequirementsStream)).Replace('-', '') }
finally { $petRequirementsStream.Dispose(); $petDigestAlgorithm.Dispose() }
if (-not (Test-Path -LiteralPath $stamp) -or ([System.IO.File]::ReadAllText($stamp)).Trim() -ne $digest) {
    & $venvPython -B -m pip install --disable-pip-version-check -r $requirements
    Assert-Exit 'Install runtime dependencies'
    [System.IO.File]::WriteAllText($stamp, $digest, [System.Text.Encoding]::ASCII)
}
if ($CheckOnly) {
    & $venvPython -B -X utf8 -m petlab_app --validate --config config.example.json
    Assert-Exit 'Validate configuration and assets'
}
if (-not $CheckOnly) {
    & $venvPython -B -X utf8 -m petlab_app.model_resources
    Assert-Exit 'Prepare local memory retrieval models'
    if ($Setup) { & $venvPython -B -X utf8 -m petlab_app --setup }
    else { & $venvPython -B -X utf8 -m petlab_app --resume }
    Assert-Exit 'Start PetLab'
}
