# -*- coding: utf-8 -*-
"""Generate an ASCII-only launcher (works regardless of console code page)."""
import io
import os

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BAT = os.path.join(BASE, "启动评鉴浮窗.bat")

lines = [
    "@echo off",
    "setlocal",
    'cd /d "%~dp0"',
    'set "PYW="',
    'if exist "%LOCALAPPDATA%\\Programs\\Python\\Python312\\pythonw.exe" '
    'set "PYW=%LOCALAPPDATA%\\Programs\\Python\\Python312\\pythonw.exe"',
    'if not defined PYW for /f "delims=" %%i in (\'where pythonw 2^>nul\') do '
    'if not defined PYW set "PYW=%%i"',
    "if not defined PYW (",
    "  echo [ERROR] pythonw.exe not found. Install Python 3 with PATH enabled.",
    "  pause",
    "  exit /b 1",
    ")",
    'del "pj_error.txt" >nul 2>nul',
    'start "" "%PYW%" "%~dp0pj_overlay.py"',
    "exit /b 0",
]
text = "\r\n".join(lines) + "\r\n"
# ASCII only -> no code-page problems at all
with io.open(BAT, "wb") as f:
    f.write(text.encode("ascii"))
print("wrote", BAT, os.path.getsize(BAT), "bytes (pure ASCII)")
