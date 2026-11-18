@echo off
setlocal
title jobfilter setup check
cd /d "%~dp0"
set "JFPY=%LOCALAPPDATA%\jobfilter\venv\Scripts\python.exe"

if not exist "%JFPY%" (
  echo The jobfilter environment is not installed yet.
  echo First double-click "Start jobfilter (Windows).bat".
  echo.
  pause
  exit /b 1
)

"%JFPY%" -m jobfilter doctor --json "out\setup_status.json"
set "JFCODE=%errorlevel%"
echo.
if "%JFCODE%"=="0" (
  echo Setup check complete: every required check is OK.
) else (
  echo Setup check complete: review and fix each MISSING line above.
)
pause
exit /b %JFCODE%
