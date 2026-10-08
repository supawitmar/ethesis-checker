@echo off
chcp 65001 >nul
rem ดึงรุ่นใหม่ของระบบตรวจเล่มจาก GitHub (ดับเบิลคลิก) — งานจริงอยู่ที่ tools\run_local.py --update
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\run_local.py --update
) else (
  python tools\run_local.py --update
)
if errorlevel 9009 echo ไม่พบ Python ในเครื่องนี้ — ติดตั้ง Python 3.10 ขึ้นไปจาก python.org แล้วเปิดใหม่
pause
