@echo off
REM Rerun everything: install -> rebuild data_rag -> clear index caches -> tests -> start server + open browser
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8

echo [1/6] Python virtual environment (.venv)
if not exist .venv ( python -m venv .venv || goto :err )
call .venv\Scripts\activate.bat

echo [2/6] Install dependencies
python -m pip install -q --upgrade pip
python -m pip install -q -r requirements.txt pypdf pdfplumber pypdfium2 || goto :err

echo [3/6] Rebuild data_rag from data\ (Fields_4.0.csv + IBSL PDF)
python scripts\build_rag_dataset.py || goto :err
python scripts\build_ibsl_dataset.py || goto :err

echo [4/6] Clear index caches (database.sqlite is kept)
del /q storage\ibsl_field_index_cache.pkl storage\ibsl_index_cache.pkl storage\ibsl_eval_result.json 2>nul

echo [5/6] Unit tests
python -m unittest discover -s tests -v || goto :err

echo [6/6] Start server - http://localhost:8000  (Ctrl+C to stop)
start "" /b python scripts\open_when_ready.py
python run_server.py
goto :eof

:err
echo.
echo *** FAILED - see the error above ***
pause
