@echo off
REM ============================================================
REM  Steam Endfield Updater - CLI launcher
REM  This file MUST stay pure ASCII.
REM  cmd.exe reads .bat using the system ANSI codepage (GBK on a
REM  Chinese Windows). Non-ASCII bytes can corrupt the command
REM  parser and break the whole script. Chinese messages are
REM  printed by endfield_updater.py instead.
REM ============================================================
setlocal

set "SCRIPT=%~dp0endfield_updater.py"
set "PY="

where python >nul 2>&1 && set "PY=python"
if not defined PY (
    where py >nul 2>&1 && set "PY=py -3"
)
if not defined PY (
    echo [ERROR] Python not found. Please install Python 3.10+.
    exit /b 1
)

%PY% "%SCRIPT%" %*
endlocal
