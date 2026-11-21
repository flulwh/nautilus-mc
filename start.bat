@echo off
REM =====================================================================
REM  Nautilus one-click launcher (Windows). Just double-click this file.
REM
REM  Kept ASCII-only on purpose: cmd.exe parses .bat files using the OEM
REM  code page, so Chinese text here would show up garbled. All user-facing
REM  messages live in start.py, which prints UTF-8 correctly.
REM =====================================================================
setlocal
cd /d "%~dp0"

set "PY="
where py >nul 2>nul && set "PY=py -3"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY where python3 >nul 2>nul && set "PY=python3"

if not defined PY (
  echo [x] Python 3.11+ not found.
  echo.
  echo     Install it from https://www.python.org/downloads/
  echo     or run:  winget install Python.Python.3.12
  echo.
  echo     Then run this file again.
  pause
  exit /b 1
)

%PY% -u "%~dp0start.py" %*
set "RC=%ERRORLEVEL%"

if not "%RC%"=="0" (
  echo.
  echo Exit code: %RC%
  pause
)
exit /b %RC%
