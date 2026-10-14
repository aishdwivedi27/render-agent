<#
.SYNOPSIS
  Installs or upgrades the render agent on a Windows host.
.DESCRIPTION
  Run from the repository root in PowerShell (elevated only if -ScheduledTask is used):
    Set-ExecutionPolicy -Scope Process Bypass
    .\scripts\install.ps1
  Safe to re-run for upgrades: jobs, config.yaml and .env are never overwritten.

  Steps: private submodule (prompts, rules, fixtures) -> Python venv + packages -> muapi-cli
  (optional) -> config.yaml / .env / runtime folders -> optional Scheduled Task -> doctor.
.PARAMETER SkipInstall
  Skip the venv and package steps (re-check config, folders and health only).
.PARAMETER ScheduledTask
  Register a Scheduled Task that runs the inbox watcher at logon and restarts it on failure.
  Refused while the watcher is not built yet (deferred, see docs/MILESTONES.md).
#>
[CmdletBinding()]
param(
  [string]$PythonVersion = "3.12",
  [string]$TaskName = "RenderAgent",
  [switch]$SkipInstall,
  [switch]$ScheduledTask
)
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
Write-Host "Install root: $Root"

function Invoke-Checked([string]$What, [scriptblock]$Command) {
  # PowerShell does not stop on a failing external program; check its exit code.
  & $Command
  if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE)." }
}

# 1. Private files: prompts, rules and fixtures come from the private submodule
if ((Test-Path ".git") -and -not (Test-Path "private\prompts")) {
  Write-Host "Fetching the private submodule (needs access to the private repository)..."
  Invoke-Checked "git submodule update" { git submodule update --init --recursive }
}
foreach ($d in "private\prompts", "private\knowledge") {
  if (-not (Test-Path $d)) {
    throw "$d is missing. Clone with --recurse-submodules (access to the private repository is needed) or copy it from the private store."
  }
}

# 2. Python virtual environment and packages
$Py = Join-Path $Root ".venv\Scripts\python.exe"
if (-not $SkipInstall) {
  if (-not (Test-Path $Py)) {
    Write-Host "Creating virtual environment (Python $PythonVersion)..."
    Invoke-Checked "Creating the virtual environment" { py "-$PythonVersion" -m venv .venv }
  }
  Invoke-Checked "pip upgrade" { & $Py -m pip install --upgrade pip }
  if (Test-Path "requirements.lock") {
    Invoke-Checked "Installing pinned packages" { & $Py -m pip install -r requirements.lock }
  } else {
    Write-Warning "requirements.lock not found; installing from pyproject.toml (unpinned). See docs/LOCKFILE.md."
  }
  Invoke-Checked "Installing the agent" { & $Py -m pip install -e . }

  # 3. muapi-cli (optional provider): a failure is only a warning
  & $Py -m pip install muapi-cli
  if ($LASTEXITCODE -ne 0) { Write-Warning "muapi-cli install failed; the MuAPI provider is unavailable until it is installed." }
} elseif (-not (Test-Path $Py)) {
  throw "-SkipInstall was given but $Py does not exist; run without -SkipInstall first."
}

# 4. Config and runtime folders (never overwritten)
if (-not (Test-Path "config.yaml")) { Copy-Item "config.example.yaml" "config.yaml"; Write-Host "Created config.yaml" }
if (-not (Test-Path ".env")) { Copy-Item ".env.example" ".env"; Write-Host "Created .env - add your API keys" }
foreach ($d in "inbox", "jobs", "failed", "logs") { New-Item -ItemType Directory -Force -Path $d | Out-Null }

# 5. Scheduled Task (only once the inbox watcher exists)
if ($ScheduledTask) {
  & $Py -c "import sys, render_agent.watcher as w; sys.exit(0 if callable(getattr(w, 'watch', None)) else 3)"
  if ($LASTEXITCODE -ne 0) {
    Write-Warning "The inbox watcher is not built yet (deferred), so no Scheduled Task was registered. Start jobs by hand with 'run'."
  } else {
    $action = New-ScheduledTaskAction -Execute $Py -Argument "-m render_agent watch" -WorkingDirectory $Root
    $trigger = New-ScheduledTaskTrigger -AtLogOn
    $settings = New-ScheduledTaskSettingsSet -RestartCount 999 -RestartInterval (New-TimeSpan -Minutes 1) `
      -StartWhenAvailable -DontStopIfGoingOnBatteries -AllowStartIfOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
    Write-Host "Scheduled Task '$TaskName' registered (starts at logon, restarts on failure)."
  }
}

# 6. Health check
& $Py -m render_agent doctor
$doctor = $LASTEXITCODE
Write-Host ""
Write-Host "Start jobs by hand, for example:"
Write-Host "  .venv\Scripts\python -m render_agent run --batch <folder of PDFs>"
Write-Host "  .venv\Scripts\python -m render_agent run <drawing.pdf> --dry-run"
if ($doctor -ne 0) { Write-Warning "doctor reported problems (above). Fix them, then run: .venv\Scripts\python -m render_agent doctor" }
exit $doctor
