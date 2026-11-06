@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\run-local.ps1" -Action Stop
if errorlevel 1 pause
