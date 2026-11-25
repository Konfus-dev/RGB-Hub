# SPDX-License-Identifier: GPL-3.0-or-later
<#
RGB-Hub: finish the install made from Ubuntu (install-windows.sh). Runs with
admin rights, once, at the owner's first sign-in afterwards (RGB-Hub-setup.cmd
in the Startup folder asks for them):

    finish-setup.ps1 -User <folder name under C:\Users>

- gives the installed files the permissions Windows would have given them
  (files written from Ubuntu carry none of their own);
- makes the key the hub and the RAM helper share (%LOCALAPPDATA%\rgbhub\ram-key);
- registers three scheduled tasks, all at the owner's sign-in:
    RGB-Hub         the hub (launcher.py), with the owner's normal rights
    RGB-Hub update  the updater, then every hour, with the owner's normal rights
    RGB-Hub RAM     the RAM helper, with full rights (the only part that has them)
- uninstalls HP's OMEN Gaming Hub and switches off HP's OMEN and analytics
  services (their start types are kept in disabled-services.json, so
  uninstall.ps1 can put them back);
- starts the tasks and deletes RGB-Hub-setup.cmd from the Startup folder.
Everything goes to %LOCALAPPDATA%\rgbhub\setup.log.

    finish-setup.ps1 -User <name> -HelperOnly -From <version folder>
        an approved update (approve-privileged.ps1): copies that version's RAM
        helper and setup scripts into Program Files, then registers the tasks
        again with the setup script just copied
    finish-setup.ps1 -User <name> -HelperOnly
        registers the tasks again and restarts the RAM helper

Inside a virtual machine (the WinBridge VM boots this same Windows) it does
nothing at all.

Written for Windows PowerShell 5.1 (part of Windows 11); ASCII only.
#>
param(
    [Parameter(Mandatory = $true)][string]$User,
    [switch]$HelperOnly,
    [string]$From
)

$ErrorActionPreference = 'Stop'

if ((Get-CimInstance Win32_ComputerSystem).Manufacturer -match 'QEMU') {
    exit 0  # the lights and the RAM belong to the real PC
}

$Program = Join-Path $env:ProgramFiles 'RGB-Hub'
$UserHome = Join-Path $env:SystemDrive "Users\$User"
$AppRoot = Join-Path $UserHome 'AppData\Local\Programs\RGB-Hub'
$Data = Join-Path $UserHome 'AppData\Local\rgbhub'
$Roaming = Join-Path $UserHome 'AppData\Roaming\rgbhub'
$Key = Join-Path $Data 'ram-key'
$Startup = Join-Path $UserHome 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\RGB-Hub-setup.cmd'
$Record = Join-Path $Program 'disabled-services.json'
$Services = @('HPOmenCap', 'HPOmenCustomCapDriver', 'HpTouchpointAnalyticsService', 'hp-one-agent-service')
$SystemSid = 'S-1-5-18'
$AdminsSid = 'S-1-5-32-544'

New-Item -ItemType Directory -Force -Path $Data | Out-Null
$Log = Join-Path $Data 'setup.log'

function Say([string]$Text) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Text
    Add-Content -Path $Log -Value $line -Encoding Ascii
    Write-Host $line
}

function Run-Native([string]$What, [string]$Exe, [string[]]$Arguments) {
    $output = & $Exe @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw "$What failed (exit code $LASTEXITCODE): $output"
    }
}

function Test-Admin {
    $me = New-Object Security.Principal.WindowsPrincipal([Security.Principal.WindowsIdentity]::GetCurrent())
    return $me.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Find-Owner {
    # The account whose profile is C:\Users\<User> (with a Microsoft account the
    # folder name and the account name can differ).
    $found = Get-CimInstance Win32_UserProfile | Where-Object { $_.LocalPath -ieq $UserHome } | Select-Object -First 1
    if (-not $found) {
        throw "There is no Windows account with the folder $UserHome."
    }
    $sid = New-Object Security.Principal.SecurityIdentifier($found.SID)
    return @{ Sid = $found.SID; Account = $sid.Translate([Security.Principal.NTAccount]).Value }
}

function Reset-Permissions([string]$Folder, [string]$OwnerSid, [switch]$OwnerFullControl) {
    # As if Windows had made the files: inherited permissions, a proper owner.
    if (-not (Test-Path $Folder)) { return }
    Run-Native "Setting the owner of $Folder" 'icacls.exe' @($Folder, '/setowner', "*$OwnerSid", '/T', '/C', '/Q')
    Run-Native "Resetting the permissions of $Folder" 'icacls.exe' @($Folder, '/reset', '/T', '/C', '/Q')
    if ($OwnerFullControl) {
        Run-Native "Giving the owner $Folder" 'icacls.exe' @($Folder, '/grant', "*${OwnerSid}:(OI)(CI)F", '/Q')
    }
}

function New-RamKey([string]$OwnerSid) {
    # 32 random bytes; only the owner (the hub), SYSTEM and administrators may read them.
    if ((Test-Path $Key) -and ((Get-Item $Key).Length -ne 32)) {
        Remove-Item -Force $Key
    }
    if (-not (Test-Path $Key)) {
        $bytes = New-Object byte[] 32
        $random = New-Object Security.Cryptography.RNGCryptoServiceProvider
        $random.GetBytes($bytes)
        $random.Dispose()
        [IO.File]::WriteAllBytes($Key, $bytes)
        Say "Made the RAM helper's key $Key"
    }
    Run-Native 'Making the key private' 'icacls.exe' @(
        $Key, '/inheritance:r', '/grant:r', "*${OwnerSid}:(R)", "*${SystemSid}:(R)", "*${AdminsSid}:(R)", '/Q'
    )
}

function Register-Tasks($Owner) {
    $account = $Owner.Account
    $logon = New-ScheduledTaskTrigger -AtLogOn -User $account
    $normal = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Limited
    $full = New-ScheduledTaskPrincipal -UserId $account -LogonType Interactive -RunLevel Highest
    $forever = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew

    $hub = New-ScheduledTaskAction -Execute "$AppRoot\python\pythonw.exe" `
        -Argument "`"$AppRoot\launcher.py`"" -WorkingDirectory $AppRoot
    Register-ScheduledTask -TaskName 'RGB-Hub' -Action $hub -Trigger $logon -Principal $normal `
        -Settings $forever -Description 'RGB-Hub: the lights, the cooler screen and the keyboard.' -Force | Out-Null
    Say "Task 'RGB-Hub' registered for $account"

    $hourly = New-ScheduledTaskTrigger -AtLogOn -User $account
    $hourly.Repetition = (New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Hours 1)).Repetition
    $updater = New-ScheduledTaskAction -Execute "$AppRoot\python\pythonw.exe" `
        -Argument "-I `"$AppRoot\updater\run.py`" --platform windows --once --log `"$Data\updater.log`"" `
        -WorkingDirectory $AppRoot
    $twoHours = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit (New-TimeSpan -Hours 2) -MultipleInstances IgnoreNew -StartWhenAvailable
    Register-ScheduledTask -TaskName 'RGB-Hub update' -Action $updater -Trigger $hourly -Principal $normal `
        -Settings $twoHours -Description 'RGB-Hub: keeps itself up to date with GitHub.' -Force | Out-Null
    Say "Task 'RGB-Hub update' registered (at sign-in, then every hour)"

    $ram = New-ScheduledTaskAction -Execute "$Program\python\pythonw.exe" `
        -Argument "-I `"$Program\helper\rgbhub_ram_helper_windows.py`" --key-file `"$Key`"" `
        -WorkingDirectory "$Program\helper"
    $again = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
        -ExecutionTimeLimit ([TimeSpan]::Zero) -MultipleInstances IgnoreNew `
        -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
    Register-ScheduledTask -TaskName 'RGB-Hub RAM' -Action $ram -Trigger $logon -Principal $full `
        -Settings $again -Description 'RGB-Hub: RAM lighting through the BIOS (needs full rights).' -Force | Out-Null
    Say "Task 'RGB-Hub RAM' registered (full rights)"
}

function Remove-OmenHub {
    try {
        foreach ($app in @(Get-AppxPackage -AllUsers -Name 'AD2F1837.OMENCommandCenter')) {
            Remove-AppxPackage -Package $app.PackageFullName -AllUsers
            Say "Removed $($app.PackageFullName)"
        }
        $provisioned = @(Get-AppxProvisionedPackage -Online | Where-Object { $_.DisplayName -eq 'AD2F1837.OMENCommandCenter' })
        foreach ($package in $provisioned) {
            Remove-AppxProvisionedPackage -Online -PackageName $package.PackageName | Out-Null
            Say "Removed $($package.PackageName) for new accounts too"
        }
    } catch {
        Say "OMEN Gaming Hub could not be removed: $_"
    }
}

function Disable-HpServices {
    # Each service's start type before setup, kept for uninstall.ps1. A service
    # already noted keeps its first note (running setup again must not note "disabled").
    $saved = @{}
    if (Test-Path $Record) {
        (Get-Content -Raw $Record | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $saved[$_.Name] = $_.Value }
    }
    foreach ($name in $Services) {
        $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
        if (-not (Test-Path $key)) {
            Say "${name}: not installed"
            continue
        }
        $item = Get-ItemProperty $key
        if (-not $saved.ContainsKey($name)) {
            $delayed = 0
            if ($item.PSObject.Properties['DelayedAutostart']) { $delayed = [int]$item.DelayedAutostart }
            $saved[$name] = @{ Start = [int]$item.Start; DelayedAutostart = $delayed }
        }
        & sc.exe stop $name 2>&1 | Out-Null
        & sc.exe config $name start= disabled 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Set-ItemProperty -Path $key -Name Start -Value 4
        }
        Say "${name}: stopped and switched off"
    }
    $saved | ConvertTo-Json | Set-Content -Path $Record -Encoding Ascii
}

function Copy-Privileged([string]$Version) {
    # Only an RGB-Hub version folder of this owner, and only these files.
    $full = [IO.Path]::GetFullPath($Version)
    if (($full -notmatch '^(.+)\\versions\\[0-9a-f]{40}$') -or ($Matches[1] -ne $AppRoot)) {
        throw "Not an RGB-Hub version folder: $Version"
    }
    New-Item -ItemType Directory -Force -Path "$Program\helper", "$Program\install\windows" | Out-Null
    Copy-Item -Force -Path "$full\helper\*.py" -Destination "$Program\helper"
    Copy-Item -Force -Path "$full\install\windows\*.ps1" -Destination "$Program\install\windows"
    Say "Copied the RAM helper and setup scripts of $(Split-Path -Leaf $full)"
}

try {
    if (-not (Test-Admin)) {
        throw 'finish-setup.ps1 needs admin rights.'
    }
    $Owner = Find-Owner
    Say "RGB-Hub setup for $($Owner.Account) (HelperOnly: $HelperOnly)"

    if ($HelperOnly) {
        if ($From) {
            Copy-Privileged $From
            Reset-Permissions $Program $AdminsSid
            # The setup script itself may have changed: let the new one register the tasks.
            & powershell.exe -NoProfile -ExecutionPolicy Bypass -File "$Program\install\windows\finish-setup.ps1" -User $User -HelperOnly
            exit $LASTEXITCODE
        }
        Register-Tasks $Owner
        Stop-ScheduledTask -TaskName 'RGB-Hub RAM' -ErrorAction SilentlyContinue
        Start-ScheduledTask -TaskName 'RGB-Hub RAM'
        Say 'RAM helper restarted'
        exit 0
    }

    Reset-Permissions $Program $AdminsSid
    Reset-Permissions $AppRoot $Owner.Sid -OwnerFullControl
    Reset-Permissions $Roaming $Owner.Sid
    New-RamKey $Owner.Sid
    Register-Tasks $Owner
    Remove-OmenHub
    Disable-HpServices

    Start-ScheduledTask -TaskName 'RGB-Hub RAM'
    Start-ScheduledTask -TaskName 'RGB-Hub'
    Start-ScheduledTask -TaskName 'RGB-Hub update'
    Say 'Tasks started'

    Remove-Item -Force -ErrorAction SilentlyContinue $Startup
    Say 'Setup finished; RGB-Hub now starts at every sign-in.'
    exit 0
} catch {
    Say "Setup stopped: $_"
    Write-Host 'RGB-Hub setup did not finish; it will try again at the next sign-in.'
    Write-Host "Details are in $Log"
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
