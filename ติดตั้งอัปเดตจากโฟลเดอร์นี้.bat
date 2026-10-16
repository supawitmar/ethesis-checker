@echo off
chcp 65001 >nul
rem อัปเดตแบบ manual: ติดตั้งรุ่นในโฟลเดอร์นี้ (ที่แตกจากไฟล์ ZIP) ทับระบบตรวจเล่มที่ติดตั้งไว้ — ไม่ต้องใช้ Git/อินเทอร์เน็ต
rem งานจริงอยู่ที่ tools\run_local.py --install-here
cd /d "%~dp0"
where py >nul 2>nul
if %errorlevel%==0 (
  py -3 tools\run_local.py --install-here
) else (
  python tools\run_local.py --install-here
)
if errorlevel 9009 echo ไม่พบ Python ในเครื่องนี้ — ติดตั้ง Python 3.10 ขึ้นไปจาก python.org แล้วเปิดใหม่
pause
