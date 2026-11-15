@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
set "WORKBENCH_PYTHON=python"
if exist ".venv\Scripts\python.exe" set "WORKBENCH_PYTHON=.venv\Scripts\python.exe"
echo 请先保持 start-workbench.cmd 的服务窗口打开。
echo 默认运行 D7：需求、追问、方程确认、校验、mock 黑盒、基态风险。
echo 本例不调用真实优化器；完成后请打开输出的浏览器链接。
echo.
"%WORKBENCH_PYTHON%" scripts\demo_walkthrough.py %*
set "WORKBENCH_DEMO_EXIT=%errorlevel%"
if not "%WORKBENCH_DEMO_EXIT%"=="0" echo 演示未完成，请查看上方错误和证据目录中的 REPORT.json。
pause
endlocal & exit /b %WORKBENCH_DEMO_EXIT%
