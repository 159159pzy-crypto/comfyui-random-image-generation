@echo off
setlocal
cd /d "%~dp0"
rem Set the ANIMA_PYTHON environment variable to override the default python path.
if not defined ANIMA_PYTHON set "ANIMA_PYTHON=F:\comfyui\.venv\Scripts\python.exe"
if not exist "%ANIMA_PYTHON%" (
  echo [ERROR] Python not found: %ANIMA_PYTHON%
  echo Set the ANIMA_PYTHON environment variable to your python.exe path.
  pause
  exit /b 1
)
"%ANIMA_PYTHON%" run.py
if errorlevel 1 pause
