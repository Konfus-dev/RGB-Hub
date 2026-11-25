# SPDX-License-Identifier: GPL-3.0-or-later
<#
RGB-Hub: remove it from Windows. Run with admin rights:

    powershell -ExecutionPolicy Bypass -File "C:\Program Files\RGB-Hub\install\windows\uninstall.ps1" [-RemoveSettings] [-User <folder name under C:\Users>]

- removes the scheduled tasks (RGB-Hub, RGB-Hub update, RGB-Hub RAM);
- puts HP's services back the way they were before setup (disabled-services.json);
- removes C:\Program Files\RGB-Hub, %LOCALAPPDATA%\Programs\RGB-Hub and
  %LOCALAPPDATA%\rgbhub (logs, the RAM helper's key);
- keeps the settings (%APPDATA%\rgbhub) unless -RemoveSettings is given.

OMEN Gaming Hub itself can be installed again from the Microsoft Store.

Written for Windows PowerShell 5.1 (part of Windows 11); ASCII only.
#>
param(
    [switch]$RemoveSettings,
    [string]$User = (Split-Path -Leaf $env:USERPROFILE)
)

$ErrorActionPreference = 'Stop'

$me = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
if (-not $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host 'Run this from a PowerShell window opened with "Run as administrator".'
    exit 1
}

$Program = Join-Path $env:ProgramFiles 'RGB-Hub'
$UserHome = Join-Path $env:SystemDrive "Users\$User"
$AppRoot = Join-Path $UserHome 'AppData\Local\Programs\RGB-Hub'
$Data = Join-Path $UserHome 'AppData\Local\rgbhub'
$Roaming = Join-Path $UserHome 'AppData\Roaming\rgbhub'
$Startup = Join-Path $UserHome 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\RGB-Hub-setup.cmd'
$Record = Join-Path $Program 'disabled-services.json'
$StartTypes = @{ 0 = 'boot'; 1 = 'system'; 2 = 'auto'; 3 = 'demand'; 4 = 'disabled' }

foreach ($task in @('RGB-Hub update', 'RGB-Hub', 'RGB-Hub RAM')) {
    if (Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue) {
        Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
        Unregister-ScheduledTask -TaskName $task -Confirm:$false
        Write-Host "Removed the task '$task'"
    }
}
# The hub shows the Default look when it is stopped; give it a moment to let go.
Start-Sleep -Seconds 3

if (Test-Path $Record) {
    $saved = Get-Content -Raw $Record | ConvertFrom-Json
    foreach ($entry in $saved.PSObject.Properties) {
        $name = $entry.Name
        $start = [int]$entry.Value.Start
        $mode = $StartTypes[$start]
        if (($start -eq 2) -and ([int]$entry.Value.DelayedAutostart -eq 1)) { $mode = 'delayed-auto' }
        if (-not $mode) { continue }
        & sc.exe config $name start= $mode 2>&1 | Out-Null
        if ($LASTEXITCODE -eq 0) {
            Write-Host "${name}: start type is '$mode' again"
            if ($mode -in @('auto', 'delayed-auto')) { & sc.exe start $name 2>&1 | Out-Null }
        } else {
            Write-Host "${name}: could not set its start type back to '$mode'"
        }
    }
}

foreach ($folder in @($AppRoot, $Data, $Program)) {
    if (Test-Path $folder) {
        Remove-Item -Recurse -Force $folder
        Write-Host "Removed $folder"
    }
}
Remove-Item -Force -ErrorAction SilentlyContinue $Startup
if ($RemoveSettings -and (Test-Path $Roaming)) {
    Remove-Item -Recurse -Force $Roaming
    Write-Host "Removed the settings ($Roaming)"
} elseif (Test-Path $Roaming) {
    Write-Host "Kept the settings in $Roaming (add -RemoveSettings to remove them too)"
}

Write-Host ''
Write-Host 'RGB-Hub is removed. OMEN Gaming Hub can be installed again from the Microsoft Store.'
exit 0
