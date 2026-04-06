@echo off
chcp 65001 >nul
title 投資團隊儀表板
cd /d "%~dp0"
echo.
echo  正在啟動投資團隊儀表板...
echo  瀏覽器將自動開啟 http://localhost:5678
echo.
python dashboard_server.py
pause
