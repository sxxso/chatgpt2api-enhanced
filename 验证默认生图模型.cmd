@echo off
cd /d "%~dp0"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\verify-default-model.ps1" %*
if errorlevel 1 echo Model verification stopped. Check the message above.
pause
