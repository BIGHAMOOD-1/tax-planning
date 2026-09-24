@echo off
chcp 936 >nul
setlocal enabledelayedexpansion
cd /d "%~dp0"

echo ============================================
echo   智能税务筹划系统 · 一键启动
echo ============================================

where python >nul 2>&1
if errorlevel 1 ( echo [错误] 未找到 python，请先安装 Python 3.11 并加入 PATH。 & pause & exit /b 1 )

if not exist "frontend\dist\index.html" (
  echo [1/3] 首次运行：安装并构建前端（需要 Node/npm，约 1-2 分钟）...
  pushd frontend
  call npm install || ( echo [错误] npm install 失败 & popd & pause & exit /b 1 )
  call npm run build || ( echo [错误] npm run build 失败 & popd & pause & exit /b 1 )
  popd
) else (
  echo [1/3] 前端已构建，跳过。
)

netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not errorlevel 1 (
  echo [2/3] 服务已在运行（:8000）。
  goto open
)

echo [2/3] 启动后端服务...
start "智能税务筹划服务" /min python run_server.py

set /a tries=0
:wait
netstat -ano | findstr ":8000" | findstr "LISTENING" >nul
if not errorlevel 1 goto open
set /a tries+=1
if !tries! geq 40 ( echo [警告] 服务启动超时，请检查日志。 & pause & exit /b 1 )
ping -n 2 127.0.0.1 >nul
goto wait

:open
echo [3/3] 打开浏览器 http://127.0.0.1:8000
start "" http://127.0.0.1:8000
echo.
echo 完成。服务在后台窗口运行（最小化的「智能税务筹划服务」），关闭它即停止服务。
echo 本窗口 3 秒后自动关闭。
ping -n 4 127.0.0.1 >nul
endlocal
