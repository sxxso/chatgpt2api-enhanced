@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\clean-cache.ps1" %*
if errorlevel 1 echo Cleanup stopped. Check the message above.
pause
