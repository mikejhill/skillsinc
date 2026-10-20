<#
.SYNOPSIS
Registers a Windows Task Scheduler task that runs `skillsinc sync`.

.DESCRIPTION
Creates or replaces a per-user task that runs at logon and then every -Interval.
Output and errors are appended to sync.log in the SkillSinc state directory.
The task runs as the current user, so it sees the same user environment
variables (XDG_*, CLAUDE_CONFIG_DIR, COPILOT_HOME, CODEX_HOME) as a new shell.

.EXAMPLE
.\Register-SkillSincTask.ps1 -WhatIf

.EXAMPLE
.\Register-SkillSincTask.ps1 -Interval (New-TimeSpan -Minutes 30)

.EXAMPLE
.\Register-SkillSincTask.ps1 -Background   # from an elevated shell; no console window

.EXAMPLE
.\Register-SkillSincTask.ps1 -Unregister
#>
[CmdletBinding(SupportsShouldProcess)]
param(
    # Name of the task in Task Scheduler.
    [string] $TaskName = 'SkillSinc sync',

    # Time between runs after logon.
    [TimeSpan] $Interval = (New-TimeSpan -Hours 1),

    # skillsinc executable; defaults to the one on PATH (see `uv tool dir --bin`).
    [string] $SkillSinc,

    # Extra arguments after `sync`, for example '--config C:\path\config.toml'.
    [string] $Arguments = '',

    # Run without a console window, also while logged off (S4U logon). Registering
    # needs an elevated shell once; no password is stored. The task cannot reach
    # network shares, so keep the library and harness folders local.
    [switch] $Background,

    # Remove the task instead of registering it.
    [switch] $Unregister
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

if ($Unregister) {
    if (Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue) {
        if ($PSCmdlet.ShouldProcess($TaskName, 'Unregister scheduled task')) {
            Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
        }
    }
    else {
        Write-Warning "No task named '$TaskName'."
    }
    return
}

if (-not $SkillSinc) {
    $command = Get-Command skillsinc -CommandType Application -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if (-not $command) {
        throw 'skillsinc is not on PATH. Install it with `uv tool install skillsinc` or pass -SkillSinc.'
    }
    $SkillSinc = $command.Source
}
$SkillSinc = (Resolve-Path -LiteralPath $SkillSinc).Path

# Same rule as SkillSinc itself: $XDG_STATE_HOME, else ~/.local/state.
$stateHome = if ($env:XDG_STATE_HOME) { $env:XDG_STATE_HOME } else { Join-Path $HOME '.local\state' }
$logDirectory = Join-Path $stateHome 'skillsinc'
$log = Join-Path $logDirectory 'sync.log'

# cmd.exe does the redirection; /s strips only the outer pair of quotes.
$commandLine = "`"$SkillSinc`" sync $Arguments".Trim()
$action = New-ScheduledTaskAction -Execute 'cmd.exe' `
    -Argument "/d /s /c `"$commandLine >> `"$log`" 2>&1`""

$user = "$env:USERDOMAIN\$env:USERNAME"
$triggers = @(
    New-ScheduledTaskTrigger -AtLogOn -User $user
    New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval $Interval
)
$logonType = if ($Background) { 'S4U' } else { 'Interactive' }
if ($Background) {
    $identity = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $identity.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw '-Background registers an S4U task, which needs an elevated shell.'
    }
}
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType $logonType -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 30)

if ($PSCmdlet.ShouldProcess($TaskName, "Register task running '$commandLine' every $Interval")) {
    New-Item -ItemType Directory -Force -Path $logDirectory | Out-Null
    Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $triggers `
        -Principal $principal -Settings $settings -Force `
        -Description 'Runs skillsinc sync. Created by Register-SkillSincTask.ps1.' | Out-Null
    Write-Host "Registered '$TaskName'. Log: $log"
}
