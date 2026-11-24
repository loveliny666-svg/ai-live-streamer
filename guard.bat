@echo off
cd /d "%~dp0"
title AI Live Guard

rem Keep this file pure ASCII. cmd reads .bat as bytes + current codepage;
rem Chinese comments here shift the parse offset after chcp and break lines.
rem
rem Loop-restart the app: if it dies (network blip, model timeout, Chrome
rem crash) it comes back in 8s. Press Ctrl+C or close this window to stop.
rem
rem No UAC here on purpose. Non-admin can still click the companion button in
rem the current setup; requesting elevation would just add a confirm dialog.
rem If the companion ever runs elevated and clicks stop working, right-click
rem this file and "Run as administrator".

rem The interpreter must be explicit. PATH holds both .workbuddy and
rem .workbuddy-ai 3.13 builds; the -ai one lacks rapidocr_onnxruntime /
rem onnxruntime / pycaw, so a bare "python" randomly hits it and silently
rem kills OCR + audio ducking.
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"

set /a restarts=0
echo ============================================
echo   AI Live Guard
echo   interpreter: %PY%
echo   admin: http://127.0.0.1:8735/admin
echo ============================================

:loop
set /a restarts+=1
if %restarts% gtr 50 (
  echo.
  echo [guard] restarted 50 times, likely a hard failure ^(port in use^).
  echo [guard] giving up, check the errors above.
  pause
  exit /b 1
)
echo.
echo [guard] start #%restarts%  %date% %time%
"%PY%" -m app %*
echo.
echo [guard] exit code %errorlevel%, restarting in 8s...
"%SystemRoot%\System32\timeout.exe" /t 8 /nobreak >nul
goto loop
