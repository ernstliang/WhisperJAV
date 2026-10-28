@echo off
REM ==============================================================================
REM WhisperJAV Web UI - Quick Start
REM ==============================================================================
REM
REM Starts the local browser UI for transcription and translation.
REM Double-click this file, or run it from a terminal.
REM
REM Usage:
REM   start-web.bat                 Start (reuses an instance already running)
REM   start-web.bat restart         Stop the running instance, then start
REM   start-web.bat stop            Stop the running instance and exit
REM   start-web.bat --port 9000     Any other args pass through to whisperjav-web
REM
REM The server binds 127.0.0.1 only. It has no authentication and can browse the
REM whole filesystem, so exposing it needs an explicit --allow-remote.
REM ==============================================================================

setlocal EnableDelayedExpansion

set "REPO=%~dp0"
set "PY=%REPO%.venv\Scripts\python.exe"
set "PORT=8317"
if not "%WHISPERJAV_WEB_PORT%"=="" set "PORT=%WHISPERJAV_WEB_PORT%"

echo.
echo ==============================================================
echo   WhisperJAV Web UI
echo ==============================================================
echo.

if not exist "%PY%" (
    echo [ERROR] Virtual environment not found:
    echo         %PY%
    echo.
    echo Create it from the repo root with:
    echo         uv sync --python 3.12 --extra all
    echo.
    pause
    exit /b 1
)

REM --- Dispatch the verb, if any -------------------------------------------
set "VERB=%~1"
set "PASSTHRU=%*"
if /I "%VERB%"=="restart" (
    set "PASSTHRU="
    call :stop_server || exit /b 1
    goto :launch
)
if /I "%VERB%"=="stop" (
    call :stop_server || exit /b 1
    echo Done.
    exit /b 0
)

REM --- Reuse an instance that is already up ---------------------------------
call :is_up
if "!UP!"=="1" (
    echo Already running on http://127.0.0.1:%PORT%/
    echo Opening the browser. Use "start-web.bat restart" to reload changed
    echo server code ^(pipeline and frontend changes need no restart^).
    start "" "http://127.0.0.1:%PORT%/"
    exit /b 0
)

:launch
echo Starting on http://127.0.0.1:%PORT%/
echo Press Ctrl+C to stop. Stopping also cancels any running job.
echo.
"%PY%" -m whisperjav.webserver --port %PORT% %PASSTHRU%
exit /b %ERRORLEVEL%


REM --- Helpers ---------------------------------------------------------------

:is_up
set "UP=0"
curl -s -m 2 -o nul "http://127.0.0.1:%PORT%/api/health" >nul 2>&1
if not errorlevel 1 set "UP=1"
exit /b 0

:stop_server
call :is_up
if "!UP!"=="0" (
    echo Nothing running on port %PORT%.
    exit /b 0
)

REM Refuse to kill a server that is mid-job: shutdown terminates the whole
REM process tree, so an in-flight transcription would be lost.
curl -s -m 5 "http://127.0.0.1:%PORT%/api/jobs" 2>nul | findstr /C:"\"state\": \"running\"" /C:"\"state\":\"running\"" >nul
if not errorlevel 1 (
    echo [WARNING] A job is still running on this server.
    echo           Stopping now would kill it and lose the work in progress.
    echo.
    set /p "ANSWER=Stop anyway? [y/N] "
    if /I not "!ANSWER!"=="y" (
        echo Aborted.
        exit /b 1
    )
)

echo Stopping the instance on port %PORT% ...
powershell -NoProfile -Command ^
  "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object { $_.CommandLine -like '*whisperjav.webserver*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }" >nul 2>&1

REM Give the lifespan hook a moment to kill child process trees.
powershell -NoProfile -Command "Start-Sleep -Milliseconds 1500" >nul 2>&1
call :is_up
if "!UP!"=="1" (
    echo [ERROR] Port %PORT% is still answering. Something else may be using it.
    exit /b 1
)
echo Stopped.
exit /b 0
