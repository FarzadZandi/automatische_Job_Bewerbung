@echo off
setlocal
title jobfilter
cd /d "%~dp0"
call :find_python
if not defined JFBOOT goto no_python
set "JFROOT=%LOCALAPPDATA%\jobfilter"
set "JFVENV=%JFROOT%\venv"
set "JFPY=%JFVENV%\Scripts\python.exe"
set "JFHASH=%JFVENV%\jobfilter_pyproject.sha256"
for /f %%H in ('powershell -NoProfile -Command "(Get-FileHash -Algorithm SHA256 -LiteralPath 'pyproject.toml').Hash"') do set "NEWHASH=%%H"
set "OLDHASH="
if exist "%JFHASH%" set /p OLDHASH=<"%JFHASH%"

if not exist "%JFPY%" set "JFFIRST=1"
if not exist "%JFPY%" goto install
if /i not "%OLDHASH%"=="%NEWHASH%" goto install
goto validate

:install
echo Preparing the jobfilter environment outside OneDrive. This may take a moment...
if not exist "%JFROOT%" mkdir "%JFROOT%"
if not exist "%JFPY%" (
  if /i "%JFBOOT_KIND%"=="py" (
    py -3 -m venv "%JFVENV%"
  ) else (
    "%JFBOOT%" -m venv "%JFVENV%"
  )
)
if errorlevel 1 goto failed
"%JFPY%" -m pip install -e ".[dev]" --quiet --disable-pip-version-check
if errorlevel 1 goto failed
>"%JFHASH%" echo %NEWHASH%
if not defined JFFIRST "%JFPY%" -m jobfilter doctor --json "out\setup_status.json" >nul 2>nul

:validate
"%JFPY%" -c "import jobfilter" >nul 2>nul
if errorlevel 1 goto install
rem Remove only this project's obsolete environment, and only after the external one imports successfully.
if exist ".venv" powershell -NoProfile -Command "$project=(Resolve-Path -LiteralPath '.').Path; $old=(Resolve-Path -LiteralPath '.venv').Path; if ((Split-Path -Parent $old) -ne $project) { exit 9 }; Remove-Item -LiteralPath $old -Recurse -Force"
if errorlevel 1 goto failed

if defined JFFIRST (
  echo.
  echo First-start setup check:
  "%JFPY%" -m jobfilter doctor --json "out\setup_status.json"
  echo.
  echo Review the OK and MISSING lines above. You can run this check later with "Check setup (Windows).bat" or the app button.
  pause
)

if exist "%JFVENV%\Scripts\pythonw.exe" (
  start "" "%JFVENV%\Scripts\pythonw.exe" -m jobfilter.window
  exit /b 0
)
"%JFPY%" -m jobfilter app
if errorlevel 1 pause
exit /b %errorlevel%

:failed
echo.
echo The jobfilter environment could not be prepared. Nothing in the project was deleted.
pause
exit /b 1

:no_python
if not exist "out" mkdir "out"
>"out\setup_status.json" echo {"python_found": false, "os": "Windows", "checked_at": "%DATE% %TIME%"}
echo.
echo Python 3.11 or newer was not found.
echo Download Python from https://www.python.org/downloads/windows/
echo During installation, select "Add python.exe to PATH".
echo Then start "Start jobfilter (Windows).bat" again.
echo.
echo The Microsoft Store Python alias is not sufficient.
pause
exit /b 1

:find_python
set "JFBOOT="
set "JFBOOT_KIND="
where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
  if not errorlevel 1 (
    set "JFBOOT=py"
    set "JFBOOT_KIND=py"
    exit /b 0
  )
)
for /f "delims=" %%P in ('where python 2^>nul') do if not defined JFBOOT call :try_python "%%~fP"
exit /b 0

:try_python
echo %~1 | findstr /i "\WindowsApps\" >nul
if not errorlevel 1 exit /b 0
"%~1" -c "import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)" >nul 2>nul
if errorlevel 1 exit /b 0
set "JFBOOT=%~1"
set "JFBOOT_KIND=exe"
exit /b 0
