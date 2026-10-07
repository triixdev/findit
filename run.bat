@echo off
cd /d "%~dp0"
if not exist token.txt set /p HFT=Paste your Hugging Face token, starting with hf_, then press Enter. Or just press Enter to skip: 
if not exist token.txt if defined HFT >token.txt echo %HFT%
where py >nul 2>nul
if %errorlevel%==0 (set PY=py) else (set PY=python)
echo Installing requirements (only needed the first time)...
%PY% -m pip install -q -r requirements.txt
echo Starting FindIt...
%PY% app.py
pause
