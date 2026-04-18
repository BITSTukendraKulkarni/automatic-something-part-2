<#
.SYNOPSIS
    PSADT Toolkit Stub — AppDeployToolkitMain.ps1
    *** PLACEHOLDER — Replace with the real PSADT toolkit from psappdeploytoolkit.com ***

.DESCRIPTION
    This is a minimal stub so the PSADT Agentic AI can detect version info and
    build the folder structure before you install the real toolkit.

    REQUIRED ACTION: Download the official PSADT toolkit (v3.10.x) from:
    https://psappdeploytoolkit.com/docs/reference/
    and replace the contents of this directory with the real toolkit files.

.NOTES
    PowerShell App Deployment Toolkit
    Version: 3.10.2
    URL: https://psappdeploytoolkit.com
#>

[string]$appDeployToolkitVersion = '3.10.2'

# Stub implementations — real toolkit has full implementations
Function Show-InstallationProgress { param([string]$StatusMessage = '') Write-Host "[Progress] $StatusMessage" }
Function Show-InstallationWelcome  { param([string]$CloseApps = '', [switch]$Silent, [switch]$AllowDefer, [int]$DeferTimes = 0) }
Function Execute-MSI               { param([string]$Action, [string]$Path, [string]$Parameters = '', [switch]$PassThru) Write-Host "[Execute-MSI] $Action $Path $Parameters" }
Function Execute-Process           { param([string]$Path, [string]$Parameters = '', [string]$WindowStyle = 'Normal', [switch]$PassThru) Write-Host "[Execute-Process] $Path $Parameters" }
Function Get-InstalledApplication  { param([string]$Name = '') @() }
Function Set-RegistryKey           { param([string]$Key, [string]$Name, $Value, [string]$Type = 'String') }
Function Exit-Script               { param([int]$ExitCode = 0) exit $ExitCode }
Function Write-Log                 { param([string]$Message, [string]$Source = 'Deploy') Write-Host "[$Source] $Message" }

[int32]$mainExitCode = 0
[string]$dirFiles = Join-Path (Split-Path -Parent $MyInvocation.MyCommand.Definition) '..\Files'
