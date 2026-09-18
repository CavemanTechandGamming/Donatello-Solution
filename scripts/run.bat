@echo off
REM ----------------------------------------------------------------------------
REM  run.bat - launch Donatello Solution (uses local .venv)
REM ----------------------------------------------------------------------------

cd /d "%~dp0\.."

if not exist ".venv\Scripts\python.exe" (
    echo Virtual environment not found. Creating it now...
    call "scripts\setup_env.bat"
    if errorlevel 1 (
        echo.
        pause
        exit /b 1
    )
)

echo Starting Donatello Solution ...
".venv\Scripts\python.exe" -m src
set EXITCODE=%ERRORLEVEL%
if not %EXITCODE%==0 (
    echo.
    echo App exited with error code %EXITCODE%.
    pause
)
exit /b %EXITCODE%
