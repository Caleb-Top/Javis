@echo off
cd /d D:\Javis

set OLLAMA_MODELS=D:\Javis\ollama_models
set PYTHON_CMD=python
set PYTHONW_CMD=pythonw

if exist "C:\Users\34247\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe" set PYTHON_CMD=C:\Users\34247\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe
if exist "C:\Users\34247\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe" set PYTHONW_CMD=C:\Users\34247\AppData\Local\hermes\hermes-agent\venv\Scripts\pythonw.exe

if "%PYTHON_CMD%"=="python" (
    where python >nul 2>&1
    if errorlevel 1 (
        for %%p in ("%LOCALAPPDATA%\Programs\Python\Python311\python.exe" "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" "C:\Python311\python.exe" "C:\Python312\python.exe") do if exist %%p set PYTHON_CMD=%%p
    )
)

echo [Javis] Starting background services...
start /B "" %PYTHONW_CMD% core\tray.py

:loop
echo [Javis] Starting web server on port 8080...
%PYTHONW_CMD% main.py
echo [Javis] Server stopped. Restarting in 3s...
timeout /t 3 /nobreak >nul
goto loop
