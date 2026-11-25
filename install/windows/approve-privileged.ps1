# SPDX-License-Identifier: GPL-3.0-or-later
<#
RGB-Hub: install the admin parts of an update the owner approved.

The updater runs with the owner's normal rights. When a new version changes
the RAM helper or the setup scripts, it installs everything else and the hub's
tray menu offers "Approve update...", which runs this script (the installed
copy in C:\Program Files\RGB-Hub, never one from a version) with admin rights:

    approve-privileged.ps1 -From C:\Users\<user>\AppData\Local\Programs\RGB-Hub\versions\<commit>

It hands the version to finish-setup.ps1 -HelperOnly, and once that worked it
removes %LOCALAPPDATA%\rgbhub\pending-privileged.json, so the menu item goes away.

Written for Windows PowerShell 5.1 (part of Windows 11); ASCII only.
#>
param(
    [Parameter(Mandatory = $true)][string]$From
)

$ErrorActionPreference = 'Stop'

if ((Get-CimInstance Win32_ComputerSystem).Manufacturer -match 'QEMU') {
    exit 0
}

$full = [IO.Path]::GetFullPath($From)
if ($full -notmatch '^([A-Za-z]:\\Users\\[^\\]+)\\AppData\\Local\\Programs\\RGB-Hub\\versions\\[0-9a-f]{40}$') {
    Write-Host "Not an RGB-Hub version folder: $From"
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
$userHome = $Matches[1]
$user = Split-Path -Leaf $userHome

Write-Host "Installing the approved part of RGB-Hub update $(Split-Path -Leaf $full) ..."
& powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$PSScriptRoot\finish-setup.ps1" -User $user -HelperOnly -From $full
if ($LASTEXITCODE -ne 0) {
    Write-Host "That did not work; see $userHome\AppData\Local\rgbhub\setup.log"
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
Remove-Item -Force -ErrorAction SilentlyContinue "$userHome\AppData\Local\rgbhub\pending-privileged.json"
Write-Host 'Done.'
exit 0
