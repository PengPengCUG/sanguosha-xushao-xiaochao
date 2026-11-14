@echo off
setlocal
cd /d "%~dp0"
set "PYW="
if exist "%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe" set "PYW=%LOCALAPPDATA%\Programs\Python\Python312\pythonw.exe"
if not defined PYW for /f "delims=" %%i in ('where pythonw 2^>nul') do if not defined PYW set "PYW=%%i"
if not defined PYW (
  echo [ERROR] pythonw.exe not found. Install Python 3 with PATH enabled.
  pause
  exit /b 1
)
del "pj_error.txt" >nul 2>nul
start "" "%PYW%" "%~dp0pj_overlay.py"
exit /b 0
