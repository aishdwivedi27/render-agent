<#
.SYNOPSIS
  Installs or upgrades the render agent on a Windows host and registers it as a Scheduled Task.
.DESCRIPTION
  Run from the repository root in an elevated PowerShell:
    Set-ExecutionPolicy -Scope Process Bypass
    .\scripts\install.ps1
  Safe to re-run for upgrades: jobs, config.yaml and .env are never overwritten.
#>
[CmdletBinding()]
param(
  [string]$PythonVersion = "3.12",
  [string]$TaskName = "RenderAgent",
  [switch]$NoScheduledTask
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Install root: $Root"

# 1. Python virtual environment
if (-not (Test-Path ".venv")) {
  Write-Host "Creating virtual environment (Python $PythonVersion)..."
  py -$PythonVersion -m venv .venv
}
$Py = Join-Path $Root ".venv\Scripts\python.exe"
& $Py -m pip install --upgrade pip
if (Test-Path "requirements.lock") {
  & $Py -m pip install -r requirements.lock
} else {
  Write-Warning "requirements.lock not found; installing from pyproject.toml (unpinned). See docs/LOCKFILE.md."
}
& $Py -m pip install -e .

# 2. muapi-cli (optional provider)
try {
  & $Py -m pip install muapi-cli
} catch {
  Write-Warning "muapi-cli install failed; the MuAPI provider will be unavailable until it is installed."
}

# 3. Config and runtime folders (never overwritten)
if (-not (Test-Path "config.yaml")) { Copy-Item "config.example.yaml" "config.yaml"; Write-Host "Created config.yaml" }
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env - add your API keys" }
foreach ($d in "inbox", "jobs", "failed", "logs") { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# 4. Scheduled Task: start at logon, restart on failure
if (-not $NoScheduledTask) {
  $action = New-ScheduledTaskAction -Execute $Py -Argument "-m render_agent watch" -WorkingDirectory $Root
  $trigger = New-ScheduledTaskTrigger -AtLogOn
  $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
    -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
  Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Write-Host "Scheduled Task '$TaskName' registered (starts at logon, restarts on failure)."
}

# 5. Health check
& $Py -m render_agent doctor
Write-Host "Done. Drop a PDF into $Root\inbox to start a job."
