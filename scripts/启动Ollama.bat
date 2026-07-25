@echo off
set "PROJECT_ROOT=%~dp0.."
pushd "%PROJECT_ROOT%"
set "OLLAMA_MODELS=%CD%\ollama_models"
echo [Ollama] Model directory: %OLLAMA_MODELS%
start /B "" "%LOCALAPPDATA%\Ollama\ollama.exe" serve
echo [Ollama] Service started
echo [Ollama] API: http://localhost:11434
