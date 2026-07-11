@echo off
set OLLAMA_MODELS=D:\Javis\ollama_models
echo [Ollama] Model directory: %OLLAMA_MODELS%
start /B "" "%LOCALAPPDATA%\Ollama\ollama.exe" serve
echo [Ollama] Service started
echo [Ollama] API: http://localhost:11434
