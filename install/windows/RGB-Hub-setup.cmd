@echo off
rem SPDX-License-Identifier: GPL-3.0-or-later
rem
rem RGB-Hub: finish setting up, once, at the first sign-in after the install
rem from Ubuntu (install-windows.sh puts this file in the Startup folder and
rem writes the owner's name in place of @USER@). It asks Windows for admin
rem rights once (UAC) and runs finish-setup.ps1, which deletes this file when
rem it is done. Inside a virtual machine (WinBridge boots this same Windows)
rem it does nothing, so setup is never used up there.

powershell.exe -NoProfile -NonInteractive -Command "if ((Get-CimInstance Win32_ComputerSystem).Manufacturer -match 'QEMU') { exit 1 } else { exit 0 }"
if errorlevel 1 exit /b 0

powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command "Start-Process powershell.exe -Verb RunAs -ArgumentList '-NoProfile -ExecutionPolicy Bypass -File \"C:\Program Files\RGB-Hub\install\windows\finish-setup.ps1\" -User @USER@'"
exit /b 0
