#requires -Version 5.1
<#
.SYNOPSIS
Sets up P&R, runs its tests, and builds a wheel and source distribution.
.DESCRIPTION
Requires Python 3.11 or newer with pip and venv. Dependencies are downloaded
on the first run. Uses a project-local .venv and writes packages to dist.
Run launch-pandr.cmd after building to open the application.
.EXAMPLE
.\build.ps1
.EXAMPLE
.\build.ps1 -Python 'C:\Python314\python.exe' -SkipTests
#>
[CmdletBinding()]
param(
    [string]$Python,
    [switch]$SkipTests
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Invoke-Checked {
    param(
        [string]$Executable,
        [string[]]$Arguments
    )
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "Command failed (exit $LASTEXITCODE): $Executable $($Arguments -join ' ')"
    }
}

Push-Location -LiteralPath $PSScriptRoot
try {
    if (-not (Test-Path -LiteralPath 'pyproject.toml')) {
        throw 'Place build.ps1 beside the project pyproject.toml.'
    }

    $venvPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        # Also support virtual environments created by MSYS Python.
        $alternativePython = Join-Path $PSScriptRoot '.venv\bin\python.exe'
        if (Test-Path -LiteralPath $alternativePython) {
            $venvPython = $alternativePython
        }
    }

    $versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required.")'
    if (-not (Test-Path -LiteralPath $venvPython)) {
        $pythonArguments = @()
        if ($Python) {
            $bootstrapPython = $Python
        } elseif (Get-Command py -ErrorAction SilentlyContinue) {
            $bootstrapPython = 'py'
            $pythonArguments = @('-3')
        } elseif (Get-Command python -ErrorAction SilentlyContinue) {
            $bootstrapPython = 'python'
        } else {
            throw 'Install Python 3.11+ with pip and venv, or pass -Python with its executable path.'
        }
        Invoke-Checked $bootstrapPython ($pythonArguments + @('-c', $versionCheck))
        Write-Host 'Creating .venv...'
        Invoke-Checked $bootstrapPython ($pythonArguments + @('-m', 'venv', '.venv'))
        if (-not (Test-Path -LiteralPath $venvPython)) {
            $venvPython = Join-Path $PSScriptRoot '.venv\bin\python.exe'
        }
        if (-not (Test-Path -LiteralPath $venvPython)) {
            throw 'Virtual environment creation did not produce a Python executable.'
        }
    }

    Invoke-Checked $venvPython @('-c', $versionCheck)
    Write-Host 'Installing build tools and project dependencies...'
    Invoke-Checked $venvPython @('-m', 'pip', 'install', 'build', 'setuptools>=68', 'wheel')
    Invoke-Checked $venvPython @('-m', 'pip', 'install', '-e', '.')

    if (-not $SkipTests) {
        Write-Host 'Running tests...'
        Invoke-Checked $venvPython @('-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v')
    }

    Write-Host 'Building wheel and source distribution...'
    Invoke-Checked $venvPython @('-m', 'build', '--outdir', (Join-Path $PSScriptRoot 'dist'))
    Write-Host "Build complete. Packages: $(Join-Path $PSScriptRoot 'dist')"
} finally {
    Pop-Location
}
