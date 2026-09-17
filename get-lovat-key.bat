@echo off
REM Double-click this to get a Lovat API key.
REM Copy the `profile` request in your browser first - the window will say how.
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
  echo.
  echo   Python 3 is not installed, or was installed without "Add python.exe to PATH".
  echo.
  echo   Get it from https://www.python.org/downloads/ and tick that box on the
  echo   first screen of the installer, then run this again.
  echo.
  pause
  exit /b 1
)

python -u server\lovat_key.py %*
echo.
pause
