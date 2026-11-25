# SPDX-License-Identifier: GPL-3.0-or-later
<#
RGB-Hub: install it on this Windows, from Windows (in place of HP's OMEN Gaming
Hub). Run it as yourself (not "Run as administrator") from a downloaded copy of
RGB-Hub's main branch:

    powershell -ExecutionPolicy Bypass -File install\windows\install.ps1 [-Repair]

What it does:
 1. Downloads Windows' embeddable Python and pip (pinned in pins.json beside
    this file, checksums checked) and unpacks Python into
    %LOCALAPPDATA%\Programs\RGB-Hub\python.
 2. Installs the newest main from GitHub with RGB-Hub's own updater code
    (rgbhub.updater.install --from-github): versions\<commit>\ with its
    libraries, current.txt, the updater's copy. launcher.py and pip.whl go
    beside them.
 3. Asks once for admin rights (UAC) and runs that version's finish-setup.ps1
    -FirstInstall: it copies Python, the RAM helper and the setup scripts into
    C:\Program Files\RGB-Hub (the helper and scripts checked against GitHub),
    registers the three scheduled tasks, removes OMEN Gaming Hub and switches
    off HP's OMEN services, and starts RGB-Hub.
Settings start as the defaults; an existing %APPDATA%\rgbhub\settings.json is
never touched.

Already installed: it says so and stops (RGB-Hub keeps itself up to date).
-Repair installs everything again. A setup that stopped at the admin prompt
is finished by running this again.
Inside a virtual machine (the WinBridge VM boots this same Windows) it does
nothing at all.

Written for Windows PowerShell 5.1 (part of Windows 11); ASCII only.
#>
param(
    [switch]$Repair
)

$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest is very slow with its progress bar

$Tasks = @('RGB-Hub', 'RGB-Hub update', 'RGB-Hub RAM')
# Puts the given `service` folder on the path and runs the updater's installer (as install-user.sh does).
$Boot = 'import sys; sys.path.insert(0, sys.argv.pop(1)); from rgbhub.updater.install import main; sys.exit(main(sys.argv[1:]))'

# ---- the decisions (plain functions; the tests run them) -----------------------------

function Test-VirtualMachine([string]$Described) {
    # Win32_ComputerSystem's manufacturer and model, as the hub checks them.
    return [bool]($Described -match 'qemu|virtual')
}

function Test-OtherAccount([bool]$Elevated, [string]$Me, [string]$SignedIn) {
    # Run as administrator with another account's rights: its folders are not
    # the owner's. (Elevated as the owner is fine; not knowing counts as other.)
    if (-not $Elevated) { return $false }
    if (-not $SignedIn) { return $true }
    return ($Me -ne $SignedIn)  # -ne ignores case
}

function Get-InstallState([string]$Root, [bool]$TasksThere) {
    # 'installed'   a version is active and setup finished (the tasks exist)
    # 'unfinished'  a version is active, setup did not finish (the admin prompt was refused?)
    # 'new'         nothing usable
    $result = @{ State = 'new'; Version = $null }
    $current = Join-Path $Root 'current.txt'
    if (-not (Test-Path -LiteralPath $current)) { return $result }
    $sha = ([string](Get-Content -LiteralPath $current -TotalCount 1)).Trim()
    if ($sha -cnotmatch '^[0-9a-f]{40}$') { return $result }
    $version = Join-Path $Root "versions\$sha"
    if (-not (Test-Path -LiteralPath (Join-Path $version 'install\windows\finish-setup.ps1'))) { return $result }
    $result.Version = $version
    if ($TasksThere) { $result.State = 'installed' } else { $result.State = 'unfinished' }
    return $result
}

function Get-Steps([string]$State, [bool]$Repair) {
    # What to do: 'python' (Python and pip), 'version' (the newest main), 'setup' (the admin part).
    if ($Repair -or ($State -eq 'new')) { return @('python', 'version', 'setup') }
    if ($State -eq 'unfinished') { return @('setup') }
    return @()
}

function Read-Pins([string]$Path) {
    $pins = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    foreach ($part in @($pins.python, $pins.pip)) {
        if (($part.sha256 -cnotmatch '^[0-9a-f]{64}$') -or ($part.url -notmatch '^https://[^\s]+/[^/\s]+$')) {
            throw "Odd pins in $Path"
        }
    }
    if ($pins.python.pth -notmatch '^python3\d+\._pth$') { throw "Odd pins in $Path" }
    return $pins
}

function Test-Checksum([string]$Path, [string]$Sha256) {
    return ((Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash -eq $Sha256)  # -eq ignores case
}

function Enable-ImportSite([string]$Text) {
    # The ._pth file's "#import site" switched on, so Python finds libraries put
    # next to it (as install-windows.sh does).
    $new = $Text -replace '(?m)^#import site', 'import site'
    if ($new -notmatch '(?m)^import site') { throw 'unexpected ._pth file in the Python download' }
    return $new
}

# Dot-sourced (by the tests): only the functions above.
if ($MyInvocation.InvocationName -eq '.') { return }

# ---- doing it -------------------------------------------------------------------------

function Say([string]$Text) { Write-Host $Text }

function Get-Checked($Part, [string]$Folder, [string]$What) {
    # Download one pinned file into $Folder; it must have the pinned SHA-256.
    $name = ($Part.url -split '/')[-1]
    $path = Join-Path $Folder $name
    Say "    $What ($name)"
    Invoke-WebRequest -UseBasicParsing -Uri $Part.url -OutFile "$path.part"
    Move-Item -Force -LiteralPath "$path.part" -Destination $path
    if (-not (Test-Checksum $path $Part.sha256)) {
        Remove-Item -Force -LiteralPath $path
        throw "$name is not the file that was published (checksum differs); run this again."
    }
    return $path
}

function Install-Python($Pins, [string]$Root, [string]$Temp) {
    Say '==> Python and pip'
    $zip = Get-Checked $Pins.python $Temp "Windows' Python $($Pins.python.version)"
    $whl = Get-Checked $Pins.pip $Temp "pip $($Pins.pip.version)"
    $new = Join-Path $Root 'python.new'
    $old = Join-Path $Root 'python.old'
    $python = Join-Path $Root 'python'
    foreach ($leftover in @($new, $old)) {
        if (Test-Path -LiteralPath $leftover) { Remove-Item -LiteralPath $leftover -Recurse -Force }
    }
    Expand-Archive -LiteralPath $zip -DestinationPath $new
    $pth = Join-Path $new $Pins.python.pth
    $text = Enable-ImportSite ([IO.File]::ReadAllText($pth))
    [IO.File]::WriteAllText($pth, $text, [Text.Encoding]::ASCII)
    try {
        if (Test-Path -LiteralPath $python) { Rename-Item -LiteralPath $python -NewName 'python.old' }
    } catch {
        Remove-Item -LiteralPath $new -Recurse -Force
        throw "RGB-Hub is running from $python. Quit it (its tray icon, Quit), then run this again."
    }
    Rename-Item -LiteralPath $new -NewName 'python'
    if (Test-Path -LiteralPath $old) { Remove-Item -LiteralPath $old -Recurse -Force -ErrorAction SilentlyContinue }
    Copy-Item -Force -LiteralPath $whl -Destination (Join-Path $Root 'pip.whl')
}

function Install-Version([string]$Repo, [string]$Root) {
    Say '==> RGB-Hub (the newest main from GitHub)'
    # The launcher starts the hub at sign-in; the updater's self-check needs it too.
    Copy-Item -Force -LiteralPath (Join-Path $Repo 'service\rgbhub\platforms\windows\launcher.py') `
        -Destination (Join-Path $Root 'launcher.py')
    $python = Join-Path $Root 'python\python.exe'
    # Python writes its progress to stderr; Windows PowerShell 5.1 must not take that as an error.
    $ErrorActionPreference = 'Continue'
    & $python -I -c $Boot (Join-Path $Repo 'service') --platform windows --from-github --root $Root
    $status = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($status -ne 0) { throw "RGB-Hub could not be installed from GitHub (exit code $status)." }
}

function Invoke-Setup([string]$Version, [string]$User) {
    Say '==> Setup with admin rights (Windows asks once)'
    $finish = Join-Path $Version 'install\windows\finish-setup.ps1'
    $arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$finish`" -User `"$User`" -FirstInstall -From `"$Version`""
    try {
        $process = Start-Process powershell.exe -Verb RunAs -Wait -PassThru -ArgumentList $arguments
    } catch {
        throw 'Setup needs admin rights once (the RAM helper and the scheduled tasks). Run this again and answer Yes.'
    }
    if ($process.ExitCode -ne 0) {
        throw "Setup with admin rights did not finish; see $env:LOCALAPPDATA\rgbhub\setup.log"
    }
}

$temp = $null
try {
    $system = Get-CimInstance Win32_ComputerSystem
    if (Test-VirtualMachine "$($system.Manufacturer) $($system.Model)") {
        Say 'This is a virtual machine: RGB-Hub belongs to the real PC. Nothing was done.'
        exit 0
    }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $elevated = (New-Object Security.Principal.WindowsPrincipal($identity)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    if (Test-OtherAccount $elevated $identity.Name ([string]$system.UserName)) {
        throw 'This window runs with another account''s admin rights. Run it as yourself, in a normal PowerShell window.'
    }

    $user = Split-Path -Leaf $env:USERPROFILE
    $root = Join-Path $env:LOCALAPPDATA 'Programs\RGB-Hub'
    if (($env:USERPROFILE -ine (Join-Path $env:SystemDrive "Users\$user")) -or
        ($env:LOCALAPPDATA -ine (Join-Path $env:USERPROFILE 'AppData\Local'))) {
        throw "RGB-Hub expects your folder at $env:SystemDrive\Users\<name>, not $env:USERPROFILE."
    }

    $tasksThere = @($Tasks | Where-Object { Get-ScheduledTask -TaskName $_ -ErrorAction SilentlyContinue }).Count -eq $Tasks.Count
    $found = Get-InstallState $root $tasksThere
    $steps = Get-Steps $found.State ([bool]$Repair)
    if ($steps.Count -eq 0) {
        Say 'RGB-Hub is already installed; it keeps itself up to date. (-Repair installs it again.)'
        exit 0
    }

    $repo = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
    $version = $found.Version
    if ($steps -contains 'python') {
        [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
        $pins = Read-Pins (Join-Path $PSScriptRoot 'pins.json')
        New-Item -ItemType Directory -Force -Path $root | Out-Null
        $temp = Join-Path ([IO.Path]::GetTempPath()) ('rgbhub-install-' + [guid]::NewGuid().ToString('N'))
        New-Item -ItemType Directory -Path $temp | Out-Null
        Install-Python $pins $root $temp
    }
    if ($steps -contains 'version') {
        Install-Version $repo $root
        $version = (Get-InstallState $root $false).Version
        if (-not $version) { throw "RGB-Hub was installed, but $root\current.txt names no version." }
    }
    Invoke-Setup $version $user
    Say ''
    Say "Done: RGB-Hub $((Split-Path -Leaf $version).Substring(0, 7)) is installed. It starts at every sign-in"
    Say 'and keeps itself up to date. OMEN Gaming Hub was removed.'
    exit 0
} catch {
    Say "RGB-Hub was not installed: $_"
    exit 1
} finally {
    if ($temp -and (Test-Path -LiteralPath $temp)) {
        Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
    }
}
