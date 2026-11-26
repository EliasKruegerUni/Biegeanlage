@echo off
setlocal

echo Setze Pfad
set "PROJEKT=C:\dev\biegbert"
set "PY=%PROJEKT%\.venv\Scripts\python.exe"

cd /d "%PROJEKT%"

rem Backend in eigenem Fenster starten (Arbeitsordner: backend, Python aus der venv)
echo Starte Backend...
start "Biegbert Backend" /d "%PROJEKT%\backend" "%PY%" -m biegbert

rem Warten bis der Server antwortet, dann Firefox-Fenster oeffnen und anordnen
echo Oeffne Firefox-Fenster...
"%PY%" "%~dp0fenster.py"

endlocal
