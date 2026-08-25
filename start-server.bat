@echo off
REM Double-click this to start the scouting server.
REM Leave the window open - closing it stops the server.
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

python -u server\hub.py %*
if errorlevel 1 (
  echo.
  echo   The server stopped unexpectedly. The message above says why.
  echo.
  pause
)
