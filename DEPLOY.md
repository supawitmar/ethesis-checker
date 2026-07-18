# คู่มือติดตั้งและ Deploy — ระบบตรวจรูปเล่ม e-thesis

สรุปขั้นตอนสำหรับนำระบบขึ้นใช้งาน (รันในเครื่อง หรือ deploy บนคลาวด์)

---

## 1. สิ่งที่ต้องมี
- Python 3.10 ขึ้นไป
- แพ็กเกจตาม `requirements.txt` (fastapi, uvicorn, pdfplumber, python-multipart, jinja2, anthropic)

## 2. ติดตั้ง
```bash
cd code
python -m venv .venv
# Windows PowerShell:
.venv\Scripts\Activate.ps1
# macOS/Linux:
# source .venv/bin/activate
pip install -r requirements.txt
```

## 3. ตั้งค่า Environment Variables

| ตัวแปร | จำเป็น | ค่าเริ่มต้น | ทำอะไร |
|---|---|---|---|
| `APP_PASSWORD` | **ใช่** | (ว่าง) | รหัสผ่านล็อกอินเจ้าหน้าที่ — ถ้าไม่ตั้งจะเข้าใช้ระบบไม่ได้ |
| `ANTHROPIC_API_KEY` | ไม่ | — | เปิดฟีเจอร์ AI เสริม (ไม่ตั้ง = ปิด AI, ระบบตรวจทำงานครบ) |
| `LLM_ASSIST` | ไม่ | เปิด | ตั้ง `off` เพื่อปิด AI แม้มีคีย์ |
| `LLM_ASSIST_MODEL` | ไม่ | claude-opus-4-8 | เปลี่ยนรุ่นโมเดล |
| `MAX_UPLOAD_MB` | ไม่ | 25 | ขนาดไฟล์อัปโหลดสูงสุด |
| `MAX_ACTIVE_JOBS` | ไม่ | 2 | จำนวนงานตรวจพร้อมกันสูงสุด |

รันในเครื่อง: คัดลอก `.env.example` เป็น `.env` แล้วใส่ค่า (`.env` ถูก `.gitignore` แล้ว ห้าม commit)

## 4. รันในเครื่อง
```bash
uvicorn main:app --env-file .env --host 0.0.0.0 --port 8000
```
เปิดเบราว์เซอร์ที่ http://localhost:8000 แล้วล็อกอินด้วยรหัสใน `APP_PASSWORD`

## 5. Deploy บนคลาวด์ (เช่น Render)
1. ชี้ Root/Build ไปที่โฟลเดอร์ `code/`
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   (แพลตฟอร์มกำหนดพอร์ตผ่าน `$PORT` เอง)
4. ตั้ง Environment Variable `APP_PASSWORD` (และ `ANTHROPIC_API_KEY` ถ้าจะใช้ AI)
   ในหน้าตั้งค่าของแพลตฟอร์มโดยตรง — ไม่ต้องใช้ไฟล์ `.env`

## 6. หมายเหตุด้านความปลอดภัย
- คุกกี้ session ตั้ง flag `secure` เมื่อรันบน Render (ตรวจจาก env `RENDER`)
  ถ้า deploy บนโฮสต์ HTTPS อื่น อาจต้องปรับเพิ่ม
- `APP_PASSWORD` เป็นด่านล็อกอินชั้นเดียว — ถ้าเปิดสู่อินเทอร์เน็ต ควรใช้รหัสที่ยาว/สุ่ม
- ห้าม commit ไฟล์ `.env` หรือคีย์ใด ๆ ขึ้น git

## 7. ทดสอบว่าระบบทำงาน
```bash
python -m unittest discover -s tests -p "test_*.py"
```
