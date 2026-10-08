@echo off
chcp 65001 >nul
rem เปิดระบบตรวจเล่มในเครื่องนี้ (ดับเบิลคลิก) — งานจริงอยู่ที่ tools\run_local.py
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\run_local.py %*
) else (
  python tools\run_local.py %*
)
if errorlevel 9009 echo ไม่พบ Python ในเครื่องนี้ — ติดตั้ง Python 3.10 ขึ้นไปจาก python.org แล้วเปิดใหม่
if errorlevel 1 pause
