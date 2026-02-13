@echo off
REM Batch script para quick tests
REM Usage: quick_test.bat nano "your prompt here"

if "%1"=="" (
    python quick_test.py
    pause
    exit /b
)

if "%2"=="" (
    python quick_test.py
    pause
    exit /b
)

python quick_test.py %*
pause
