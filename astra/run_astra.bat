@echo off
setlocal
cd /d "%~dp0"

where py >nul 2>&1
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

if not exist ".venv\Scripts\python.exe" (
  echo [Astra] Creando entorno virtual...
  %PY% -m venv .venv
  if errorlevel 1 goto :error
)

call .venv\Scripts\activate.bat
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
if errorlevel 1 goto :error

if not exist data mkdir data

echo.
echo [Astra] Abriendo Finance Radar en http://localhost:8501
python -m streamlit run app.py --server.headless false
exit /b 0

:error
echo.
echo [Astra] No se pudo iniciar. Verifica Python 3.10 o superior.
pause
exit /b 1
