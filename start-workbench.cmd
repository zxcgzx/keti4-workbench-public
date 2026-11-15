@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "WORKBENCH_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "WORKBENCH_PYTHON=.venv\Scripts\python.exe"
echo 运行方式智能调整平台B - 浏览器地址 http://localhost:8014
echo 请保持本窗口打开；按 Ctrl+C 可停止服务。
echo 如果已有工作台占用 8014，请直接使用已有服务。
echo.
"%WORKBENCH_PYTHON%" run.py --port 8014
if errorlevel 1 echo 启动失败。请检查端口或先执行 python -m pip install -r requirements.txt。
pause
endlocal
