@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\setup-windows.ps1" %*
if errorlevel 1 echo Installation stopped. Check the message above.
pause
