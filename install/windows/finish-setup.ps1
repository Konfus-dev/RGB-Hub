# SPDX-License-Identifier: GPL-3.0-or-later
<#
RGB-Hub: finish the install. Runs with admin rights, once:

    finish-setup.ps1 -User <folder name under C:\Users>
        after the install made from Ubuntu (install-windows.sh), at the owner's
        first sign-in afterwards (RGB-Hub-setup.cmd in the Startup folder asks
        for admin rights)
    finish-setup.ps1 -User <name> -FirstInstall -From <version folder>
        after the install made from Windows (install.ps1, which asks for admin
        rights): first puts C:\Program Files\RGB-Hub in place, made in
        Program Files (so with its permissions): a copy of the owner's Python
        (install.ps1 checked python.org's download), this script's
        verify_update.py, and then the RAM helper and setup scripts as for an
        approved update (GitHub's copies, checked by verify_update.py); then
        everything below

- C:\Program Files\RGB-Hub gets the permissions Windows gives Program Files
  (install-windows.sh already made it admin-only); the owner's own folders get
  their inherited permissions back (files written from Ubuntu carry their own);
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
        an approved update (approve-privileged.ps1): verify_update.py (the
        installed copy) checks that version's admin parts against GitHub and
        stages GitHub's copies under Program Files; only those are moved into
        place, then the tasks are registered again by the setup script just
        installed
    finish-setup.ps1 -User <name> -HelperOnly
        registers the tasks again and restarts the RAM helper

It never follows a link or junction in the owner's folders (they could point
anywhere, and this runs with full rights): it stops if it finds one.
Inside a virtual machine (the WinBridge VM boots this same Windows) it does
nothing at all.

Written for Windows PowerShell 5.1 (part of Windows 11); ASCII only.
#>
param(
    [Parameter(Mandatory = $true)][string]$User,
    [switch]$HelperOnly,
    [switch]$FirstInstall,
    [string]$From
)

$ErrorActionPreference = 'Stop'

if ((Get-CimInstance Win32_ComputerSystem).Manufacturer -match 'QEMU') {
    exit 0  # the lights and the RAM belong to the real PC
}

$Program = Join-Path $env:ProgramFiles 'RGB-Hub'
$Staging = Join-Path $Program 'staging'
$UserHome = Join-Path $env:SystemDrive "Users\$User"
$Local = Join-Path $UserHome 'AppData\Local'
$AppRoot = Join-Path $Local 'Programs\RGB-Hub'
$Data = Join-Path $Local 'rgbhub'
$Roaming = Join-Path $UserHome 'AppData\Roaming\rgbhub'
$Key = Join-Path $Data 'ram-key'
$Log = Join-Path $Data 'setup.log'
$Startup = Join-Path $UserHome 'AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup\RGB-Hub-setup.cmd'
$Record = Join-Path $Program 'disabled-services.json'
$Services = @('HPOmenCap', 'HPOmenCustomCapDriver', 'HpTouchpointAnalyticsService', 'hp-one-agent-service')
$SystemSid = 'S-1-5-18'
$AdminsSid = 'S-1-5-32-544'

function Test-Reparse([string]$Path) {
    # A link or junction (or anything else that redirects): never written through.
    if (-not (Test-Path -LiteralPath $Path)) { return $false }
    $item = Get-Item -LiteralPath $Path -Force
    return [bool]($item.Attributes -band [IO.FileAttributes]::ReparsePoint)
}

function Assert-NoReparse([string]$Path, [switch]$Tree) {
    if (Test-Reparse $Path) {
        throw "$Path is a link or junction; RGB-Hub setup will not work through it."
    }
    if ($Tree -and (Test-Path -LiteralPath $Path)) {
        $found = Get-ChildItem -LiteralPath $Path -Recurse -Force -Attributes ReparsePoint -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($found) {
            throw "$($found.FullName) is a link or junction; RGB-Hub setup will not work through it."
        }
    }
}

function Say([string]$Text) {
    $line = (Get-Date -Format 'yyyy-MM-dd HH:mm:ss') + ' ' + $Text
    Write-Host $line
    try {
        if ((Test-Path -LiteralPath $Data) -and -not (Test-Reparse $Data) -and -not (Test-Reparse $Log)) {
            Add-Content -LiteralPath $Log -Value $line -Encoding Ascii
        }
    } catch {
        Write-Host "(not written to ${Log}: $_)"
    }
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

function Reset-ProgramFolder {
    # Admin-only already (install-windows.sh); now exactly what Program Files gives.
    if (-not (Test-Path -LiteralPath $Program)) { return }
    Assert-NoReparse $Program -Tree
    Run-Native "Setting the owner of $Program" 'icacls.exe' @($Program, '/setowner', "*$AdminsSid", '/T', '/C', '/L', '/Q')
    Run-Native "Resetting the permissions of $Program" 'icacls.exe' @($Program, '/reset', '/T', '/C', '/L', '/Q')
}

function Reset-OwnerFolder([string]$Folder) {
    # The owner's folder inherits full control for the owner from the profile;
    # files written from Ubuntu carry their own permissions instead: drop those.
    if (-not (Test-Path -LiteralPath $Folder)) { return }
    Assert-NoReparse $Folder -Tree
    Run-Native "Resetting the permissions of $Folder" 'icacls.exe' @($Folder, '/reset', '/T', '/C', '/L', '/Q')
}

function New-RamKey([string]$OwnerSid) {
    # 32 random bytes; only the owner (the hub), SYSTEM and administrators may read them.
    Assert-NoReparse $Data
    Assert-NoReparse $Key
    if ((Test-Path -LiteralPath $Key) -and ((Get-Item -LiteralPath $Key -Force).Length -ne 32)) {
        Remove-Item -LiteralPath $Key -Force
    }
    if (-not (Test-Path -LiteralPath $Key)) {
        $bytes = New-Object byte[] 32
        $random = New-Object Security.Cryptography.RNGCryptoServiceProvider
        $random.GetBytes($bytes)
        $random.Dispose()
        [IO.File]::WriteAllBytes($Key, $bytes)
        Say "Made the RAM helper's key $Key"
    }
    Assert-NoReparse $Key
    Run-Native 'Making the key private' 'icacls.exe' @(
        $Key, '/inheritance:r', '/grant:r', "*${OwnerSid}:(R)", "*${SystemSid}:(R)", "*${AdminsSid}:(R)", '/L', '/Q'
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

function Save-Record($Saved) {
    # Written beside it, then swapped in: never half a file.
    $temporary = "$Record.tmp"
    $Saved | ConvertTo-Json | Set-Content -LiteralPath $temporary -Encoding Ascii
    Move-Item -LiteralPath $temporary -Destination $Record -Force
}

function Disable-HpServices {
    # Each service's start type before setup, kept for uninstall.ps1. The note is
    # saved before the service is changed, and a service already noted keeps its
    # first note, so an interrupted or repeated run never notes "disabled".
    $saved = @{}
    if (Test-Path -LiteralPath $Record) {
        (Get-Content -Raw -LiteralPath $Record | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $saved[$_.Name] = $_.Value }
    }
    foreach ($name in $Services) {
        $key = "HKLM:\SYSTEM\CurrentControlSet\Services\$name"
        if (-not (Test-Path $key)) {
            Say "${name}: not installed"
            continue
        }
        if (-not $saved.ContainsKey($name)) {
            $item = Get-ItemProperty $key
            $delayed = 0
            if ($item.PSObject.Properties['DelayedAutostart']) { $delayed = [int]$item.DelayedAutostart }
            $saved[$name] = @{ Start = [int]$item.Start; DelayedAutostart = $delayed }
            Save-Record $saved
        }
        & sc.exe stop $name 2>&1 | Out-Null
        & sc.exe config $name start= disabled 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) {
            Set-ItemProperty -Path $key -Name Start -Value 4
        }
        Say "${name}: stopped and switched off"
    }
}

function Get-VersionFolder([string]$Version) {
    # Only an RGB-Hub version folder of this owner.
    $full = [IO.Path]::GetFullPath($Version)
    if (($full -notmatch '^(.+)\\versions\\([0-9a-f]{40})$') -or ($Matches[1] -ne $AppRoot)) {
        throw "Not an RGB-Hub version folder: $Version"
    }
    return $full
}

function Install-First([string]$Version) {
    # C:\Program Files\RGB-Hub made here, so it has Program Files' permissions
    # (administrators write, users read and run): Python and the update checker.
    $full = Get-VersionFolder $Version
    $python = Join-Path $AppRoot 'python'
    Assert-NoReparse $AppRoot
    Assert-NoReparse $python -Tree
    Assert-NoReparse $full -Tree
    if (-not (Test-Path -LiteralPath "$python\python.exe")) { throw "There is no Python in $python." }
    Assert-NoReparse $Program -Tree
    # A repair: the RAM helper runs from the Python being replaced.
    Stop-ScheduledTask -TaskName 'RGB-Hub RAM' -ErrorAction SilentlyContinue
    New-Item -ItemType Directory -Force -Path "$Program\install\windows" | Out-Null
    foreach ($leftover in @("$Program\python.new", "$Program\python.old")) {
        if (Test-Path -LiteralPath $leftover) { Remove-Item -LiteralPath $leftover -Recurse -Force }
    }
    Copy-Item -Recurse -LiteralPath $python -Destination "$Program\python.new"
    if (Test-Path -LiteralPath "$Program\python") { Rename-Item -LiteralPath "$Program\python" -NewName 'python.old' }
    Rename-Item -LiteralPath "$Program\python.new" -NewName 'python'
    if (Test-Path -LiteralPath "$Program\python.old") { Remove-Item -LiteralPath "$Program\python.old" -Recurse -Force }
    # The checker that runs next checks itself with GitHub too, with the rest.
    Copy-Item -Force -LiteralPath "$full\install\windows\verify_update.py" -Destination "$Program\install\windows\verify_update.py"
    Say "Put Python and the update checker into $Program"
}

function Install-Approved([string]$Version) {
    # Only an RGB-Hub version folder of this owner; only what GitHub confirms.
    $full = Get-VersionFolder $Version
    $commit = Split-Path -Leaf $full
    Assert-NoReparse $AppRoot
    Assert-NoReparse $full -Tree
    if (Test-Path -LiteralPath $Staging) { Remove-Item -LiteralPath $Staging -Recurse -Force }
    # The installed checker (admin-only), with the Python beside it.
    $output = & "$Program\python\python.exe" -I "$Program\install\windows\verify_update.py" --from $full --staging $Staging 2>&1
    $status = $LASTEXITCODE
    Say "verify_update.py: $output"
    if ($status -eq 2) {
        throw "GitHub could not be reached to check update $($commit.Substring(0, 7)); nothing was installed. Try again later."
    }
    if ($status -ne 0) {
        throw "Update $($commit.Substring(0, 7)) did not match GitHub; nothing was installed."
    }
    New-Item -ItemType Directory -Force -Path "$Program\helper", "$Program\install\windows" | Out-Null
    if (Test-Path -LiteralPath "$Staging\helper") {
        Copy-Item -Force -Path "$Staging\helper\*" -Destination "$Program\helper"
    }
    if (Test-Path -LiteralPath "$Staging\install\windows") {
        Copy-Item -Force -Path "$Staging\install\windows\*" -Destination "$Program\install\windows"
    }
    Remove-Item -LiteralPath $Staging -Recurse -Force
    Say "Installed the RAM helper and setup scripts of $($commit.Substring(0, 7)), checked with GitHub"
}

try {
    if (-not (Test-Admin)) {
        throw 'finish-setup.ps1 needs admin rights.'
    }
    # Nothing on the way to the owner's folders may redirect elsewhere.
    foreach ($folder in @($UserHome, (Join-Path $UserHome 'AppData'), $Local, (Join-Path $Local 'Programs'),
            (Join-Path $UserHome 'AppData\Roaming'), $Data)) {
        Assert-NoReparse $folder
    }
    New-Item -ItemType Directory -Force -Path $Data | Out-Null
    Assert-NoReparse $Data
    $Owner = Find-Owner
    Say "RGB-Hub setup for $($Owner.Account) (HelperOnly: $HelperOnly, FirstInstall: $FirstInstall)"

    if ($FirstInstall) {
        if (-not $From) { throw '-FirstInstall needs -From <version folder>.' }
        Install-First $From
        Install-Approved $From
    }

    if ($HelperOnly) {
        if ($From) {
            Install-Approved $From
            Reset-ProgramFolder
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

    Reset-ProgramFolder
    Reset-OwnerFolder $AppRoot
    Reset-OwnerFolder $Roaming
    New-RamKey $Owner.Sid
    Register-Tasks $Owner
    Remove-OmenHub
    Disable-HpServices

    Start-ScheduledTask -TaskName 'RGB-Hub RAM'
    Start-ScheduledTask -TaskName 'RGB-Hub'
    Start-ScheduledTask -TaskName 'RGB-Hub update'
    Say 'Tasks started'

    Remove-Item -Force -ErrorAction SilentlyContinue -LiteralPath $Startup
    Say 'Setup finished; RGB-Hub now starts at every sign-in.'
    exit 0
} catch {
    Say "Setup stopped: $_"
    if ($FirstInstall) {
        Write-Host 'RGB-Hub setup did not finish; run install.ps1 again to finish it.'
    } else {
        Write-Host 'RGB-Hub setup did not finish; it will try again at the next sign-in.'
    }
    Write-Host "Details are in $Log"
    Read-Host 'Press Enter to close' | Out-Null
    exit 1
}
