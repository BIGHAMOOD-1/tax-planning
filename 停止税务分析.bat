@echo off
chcp 936 >nul
echo 正在停止「智能税务筹划」服务（:8000）...
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8000" ^| findstr "LISTENING"') do (
  taskkill /f /pid %%p >nul 2>&1
)
echo 已停止（若原先在运行）。
ping -n 3 127.0.0.1 >nul
