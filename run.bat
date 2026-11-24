@echo off
chcp 65001 >nul
title AI 打工人直播间
cd /d "%~dp0"

rem 直播伴侣要求管理员权限。本程序也提权，才能自动启动它并用鼠标点「开始直播」。
net session >nul 2>&1
if %errorLevel% neq 0 (
  echo 正在请求管理员权限（直播伴侣需要）...
  mshta "javascript:var sh=new ActiveXObject('Shell.Application');sh.ShellExecute('%~f0','','','runas',1);close()"
  exit /b
)

echo ============================================
echo   AI 打工人直播间 - 无人值守自动开播
echo ============================================
echo.
echo   控制台: http://127.0.0.1:8735/admin
echo   舞台页: http://127.0.0.1:8735/stage
echo.
if "%LLM_API_KEY%"=="" (
  echo [提示] 没检测到 LLM_API_KEY，也没在 config.yaml 里填 llm.api_key。
  echo        没有 Key 也能开播，互动会用规则脑兜底。
  echo.
)
rem 解释器必须显式指定。PATH 里同时存在 .workbuddy 和 .workbuddy-ai
rem 两个 3.13 解释器，后者缺 rapidocr_onnxruntime / onnxruntime / pycaw，
rem 靠 "python" 裸命令会随机命中，导致 OCR 和音频闪避静默失效。
set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
if not exist "%PY%" set "PY=python"
echo   解释器: %PY%
echo.
"%PY%" -m app %*
echo.
echo 程序已退出。按任意键关闭窗口。
pause >nul
