@echo off
rem 兼容旧用法：双击本文件即可构建（内部调用 build.ps1）
setlocal
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
if errorlevel 1 (
    echo.
    echo 构建失败，请查看上方输出。
    pause
    exit /b 1
)
echo.
pause
