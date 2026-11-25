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
        try {
            Stop-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $task -Confirm:$false
            Write-Host "Removed the task '$task'"
        } catch {
            Write-Host "Could not remove the task '$task': $_"
        }
    }
}
# The hub shows the Default look when it is stopped; give it a moment to let go.
Start-Sleep -Seconds 3

function Test-Reparse([string]$Path) {
    # A link or junction (or anything else that redirects).
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Find-Reparse([string]$Folder) {
    # The first link or junction at $Folder, on the way to it, or anywhere under
    # it; $null if there is none. Windows PowerShell 5.1 would delete through one.
    $path = $Folder
    while ($path -and ($path.Length -gt 3)) {
        if (Test-Reparse $path) { return $path }
        $path = Split-Path -Parent $path
    }
    $found = Get-ChildItem -LiteralPath $Folder -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($found) { return $found.FullName }
    return $null
}

function Remove-Folder([string]$Folder) {
    # True if $Folder is gone (or was never there).
    if (-not (Test-Path -LiteralPath $Folder)) { return $true }
    try {
        $link = Find-Reparse $Folder
        if ($link) {
            Write-Host "Not removing ${Folder}: $link is a link or junction (remove it by hand)"
            return $false
        }
        Remove-Item -LiteralPath $Folder -Recurse -Force
        Write-Host "Removed $Folder"
        return $true
    } catch {
        Write-Host "Could not remove all of ${Folder}: $_"
        return $false
    }
}

try {
    if (Test-Path -LiteralPath $Record) {
        $saved = Get-Content -Raw -LiteralPath $Record | ConvertFrom-Json
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
} catch {
    Write-Host "Could not put HP's services back from ${Record}: $_"
    Write-Host 'Their earlier start types can be set again in the Services app.'
}

# Anything still running from the two install folders (the hub, the RAM helper,
# the updater) would keep its files in use.
try {
    foreach ($process in @(Get-CimInstance Win32_Process)) {
        $exe = $process.ExecutablePath
        if ($exe -and ($exe.StartsWith("$Program\", 'OrdinalIgnoreCase') -or $exe.StartsWith("$AppRoot\", 'OrdinalIgnoreCase'))) {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
            Write-Host "Stopped $($process.Name) ($($process.ProcessId))"
        }
    }
} catch {
    Write-Host "Could not look for RGB-Hub programs still running: $_"
}
Start-Sleep -Seconds 1

$left = @()
foreach ($folder in @($AppRoot, $Data, $Program)) {
    if (-not (Remove-Folder $folder)) { $left += $folder }
}
try {
    if (-not (Test-Reparse $Startup)) {
        Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $Startup
    }
} catch {
    Write-Host "Could not remove ${Startup}: $_"
}
if ($RemoveSettings) {
    if (-not (Remove-Folder $Roaming)) { $left += $Roaming }
} elseif (Test-Path -LiteralPath $Roaming) {
    Write-Host "Kept the settings in $Roaming (add -RemoveSettings to remove them too)"
}

Write-Host ''
if ($left.Count) {
    Write-Host ('Still there (delete by hand): ' + ($left -join ', '))
    exit 1
}
Write-Host 'RGB-Hub is removed. OMEN Gaming Hub can be installed again from the Microsoft Store.'
exit 0
