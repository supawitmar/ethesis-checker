@echo off
chcp 65001 >nul
rem ปิดระบบตรวจเล่มที่รันเบื้องหลัง (ดับเบิลคลิก) — งานจริงอยู่ที่ tools\run_local.py --stop
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\run_local.py --stop
) else (
  python tools\run_local.py --stop
)
if errorlevel 9009 echo ไม่พบ Python ในเครื่องนี้ — ติดตั้ง Python 3.10 ขึ้นไปจาก python.org แล้วเปิดใหม่
if errorlevel 1 (pause) else (timeout /t 3 >nul)
