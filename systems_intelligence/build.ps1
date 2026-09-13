#requires -Version 5.1
<#
.SYNOPSIS
Installs systems-intelligence and the shared P&R package, tests both, and builds distributions.
.EXAMPLE
.\build.ps1
.EXAMPLE
.\build.ps1 -EnvironmentPath '..\PandR_v3\.venv' -Offline
#>
[CmdletBinding()]
param(
    [string]$Python,
    [string]$EnvironmentPath,
    [string]$OutputPath,
    [switch]$SkipTests,
    [switch]$Offline
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
function Invoke-Checked {
    param([string]$Executable, [string[]]$Arguments)
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE`: $Executable" }
}
Push-Location -LiteralPath $PSScriptRoot
try {
    $pandrProject = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\PandR_v3'))
    if (-not (Test-Path -LiteralPath (Join-Path $pandrProject 'pyproject.toml'))) {
        throw "Shared P&R package not found: $pandrProject"
    }
    if (-not $EnvironmentPath) {
        $EnvironmentPath = Join-Path $PSScriptRoot '.venv'
        $sharedEnvironment = Join-Path $pandrProject '.venv'
        if (-not (Test-Path -LiteralPath $EnvironmentPath) -and (Test-Path -LiteralPath $sharedEnvironment)) { $EnvironmentPath = $sharedEnvironment }
    }
    $EnvironmentPath = [IO.Path]::GetFullPath($EnvironmentPath)
    $environmentPython = Join-Path $EnvironmentPath 'Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $environmentPython)) {
        $alternatePython = Join-Path $EnvironmentPath 'bin\python.exe'
        if (Test-Path -LiteralPath $alternatePython) { $environmentPython = $alternatePython }
    }
    $versionCheck = 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else "Python 3.11 or newer is required")'
    if (-not (Test-Path -LiteralPath $environmentPython)) {
        $bootstrapArgs = @()
        if ($Python) { $bootstrapPython = $Python }
        elseif (Get-Command py -ErrorAction SilentlyContinue) { $bootstrapPython = 'py'; $bootstrapArgs = @('-3') }
        else { $bootstrapPython = 'python' }
        Invoke-Checked $bootstrapPython ($bootstrapArgs + @('-c', $versionCheck))
        Invoke-Checked $bootstrapPython ($bootstrapArgs + @('-m', 'venv', $EnvironmentPath))
        if (-not (Test-Path -LiteralPath $environmentPython)) { $environmentPython = Join-Path $EnvironmentPath 'bin\python.exe' }
    }
    Invoke-Checked $environmentPython @('-c', $versionCheck)
    $offlineArgs = @()
    if ($Offline) { $offlineArgs = @('--no-index') }
    Invoke-Checked $environmentPython (@('-m', 'pip', 'install') + $offlineArgs + @('build', 'setuptools>=68', 'wheel'))
    Invoke-Checked $environmentPython (@('-m', 'pip', 'install', '--no-build-isolation') + $offlineArgs + @('-e', $pandrProject, '-e', $PSScriptRoot))
    if (-not $SkipTests) {
        foreach ($project in @($pandrProject, $PSScriptRoot)) {
            Push-Location -LiteralPath $project
            try { Invoke-Checked $environmentPython @('-B', '-m', 'unittest', 'discover', '-s', 'tests', '-v') }
            finally { Pop-Location }
        }
    }
    if (-not $OutputPath) { $OutputPath = Join-Path $PSScriptRoot ('dist\' + (Get-Date -Format 'yyyyMMdd-HHmmss')) }
    $OutputPath = [IO.Path]::GetFullPath($OutputPath)
    foreach ($project in @($pandrProject, $PSScriptRoot)) {
        Invoke-Checked $environmentPython @('-m', 'build', '--no-isolation', '--outdir', $OutputPath, $project)
    }
    [IO.File]::WriteAllText((Join-Path $PSScriptRoot '.controller-python'), $environmentPython)
    Write-Host "Both packages built: $OutputPath"
    Write-Host 'Start the application with start-context-studio.cmd.'
} finally { Pop-Location }
