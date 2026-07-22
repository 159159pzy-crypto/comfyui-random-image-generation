@echo off
setlocal
cd /d "%~dp0"
set "ANIMA_PYTHON=F:\comfyui\.venv\Scripts\python.exe"
if not exist "%ANIMA_PYTHON%" (
  echo [ERROR] Python not found: %ANIMA_PYTHON%
  pause
  exit /b 1
)
"%ANIMA_PYTHON%" run.py
if errorlevel 1 pause
