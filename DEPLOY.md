# คู่มือติดตั้งและ Deploy — ระบบตรวจรูปเล่ม e-thesis

สรุปขั้นตอนสำหรับนำระบบขึ้นใช้งาน (รันในเครื่อง หรือ deploy บนคลาวด์)

---

## 1. สิ่งที่ต้องมี
- Python 3.10 ขึ้นไป
- แพ็กเกจตาม `requirements.txt` — fastapi, uvicorn, pdfplumber, python-multipart,
  jinja2, anthropic

> ตั้งแต่นโยบาย ส.ค. 2569 ระบบ **ไม่เทียบชื่อกรรมการ** แล้ว (นับจำนวนอย่างเดียว)
> จึงถอด `pythainlp` และ `onnxruntime` ออก ซึ่งมีไว้ถอดชื่อไทยเป็นอังกฤษเพื่อการเทียบ
> อัปเดตจากเวอร์ชันเก่าให้รัน `pip install -r requirements.txt` ตามปกติ

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
| `ANTHROPIC_API_KEY` | ไม่ | — | ใช้เรียบเรียงข้อความสรุปส่งนักศึกษาเท่านั้น ไม่ตั้งก็ตรวจได้ครบทุกกฎ |
| `LLM_ASSIST` | ไม่ | เปิด | ตั้ง `off` เพื่อปิด AI แม้มีคีย์ |
| `LLM_ASSIST_MODEL` | ไม่ | claude-opus-4-8 | เปลี่ยนรุ่นโมเดล |
| `LLM_TIMEOUT_SECONDS` | ไม่ | 90 | timeout ต่อการเรียก AI 1 ครั้ง (กันงานค้างกินโควตา) |
| `LLM_MAX_RETRIES` | ไม่ | 1 | จำนวนครั้งที่ลองใหม่เมื่อ API ล้มเหลว |
| `MAX_UPLOAD_MB` | ไม่ | 25 | ขนาดไฟล์อัปโหลดสูงสุด |
| `MAX_ACTIVE_JOBS` | ไม่ | 2 | จำนวนงานตรวจพร้อมกันสูงสุด |
| `COOKIE_SECURE` | ไม่ | ตามโฮสต์ | บังคับคุกกี้เป็น Secure — บน Render เปิดให้อัตโนมัติ โฮสต์ HTTPS อื่นตั้ง `1` เอง |

> **ไม่ตั้ง `ANTHROPIC_API_KEY` ระบบตรวจได้ครบทุกกฎ** — คีย์มีผลกับ
> "ข้อความสรุปที่เรียบเรียงด้วย AI" อย่างเดียว ไม่เกี่ยวกับผลตรวจใด ๆ

รันในเครื่อง: คัดลอก `.env.example` เป็น `.env` แล้วใส่ค่า (`.env` ถูก `.gitignore` แล้ว ห้าม commit)

## 4. รันในเครื่อง
```bash
uvicorn main:app --env-file .env --host 0.0.0.0 --port 8000
```
เปิดเบราว์เซอร์ที่ http://localhost:8000 แล้วล็อกอินด้วยรหัสใน `APP_PASSWORD`

## 5. Deploy บนคลาวด์ (เช่น Render)
1. ชี้ Root/Build ไปที่โฟลเดอร์ `code/`
2. Build: `pip install -r requirements.txt`
3. Start: `uvicorn main:app --host 0.0.0.0 --port $PORT --workers 1`
   (แพลตฟอร์มกำหนดพอร์ตผ่าน `$PORT` เอง)
4. ตั้ง Environment Variables ในหน้า **Environment** ของ Render โดยตรง
   (ไม่ต้องใช้ไฟล์ `.env` และห้าม commit คีย์ขึ้น git)

   | Key | Value |
   |---|---|
   | `APP_PASSWORD` | รหัสผ่านเจ้าหน้าที่ (ยาว/สุ่ม) |
   | `ANTHROPIC_API_KEY` | `sk-ant-...` — ไม่บังคับ ใช้เรียบเรียงข้อความสรุปเท่านั้น |

   ที่เหลือมีค่าเริ่มต้นอยู่แล้ว ไม่ต้องตั้ง · `COOKIE_SECURE` ไม่ต้องตั้งบน Render
   (ตรวจจาก env `RENDER` ที่แพลตฟอร์มใส่ให้เอง)
5. กด **Manual Deploy → Clear build cache & deploy** เมื่ออัปเดตโค้ดใหม่
6. เช็คว่าขึ้นแล้ว: เปิด `https://<ชื่อบริการ>.onrender.com/health` ต้องได้ `{"status":"ok"}`

> เปลี่ยน `APP_PASSWORD` เมื่อไหร่ เจ้าหน้าที่ทุกคนถูกเตะออกจากระบบทันที (ตั้งใจให้เป็นแบบนี้)
> ส่วนการเพิ่ม/แก้ Environment Variable บน Render จะ restart บริการให้เองอัตโนมัติ

> ⚠️ **ต้องรัน worker เดียว (`--workers 1`)** สถานะงานตรวจเก็บในหน่วยความจำ
> ของโปรเซส ถ้ามีหลาย worker/instance คำขอเช็คสถานะ (`/progress`) อาจไปตกคน
> ละโปรเซสที่ไม่มีงานนั้น ทำให้ขึ้น "ขาดการเชื่อมต่อกับระบบตรวจ" ทั้งที่ตรวจอยู่

## 6. หมายเหตุด้านความปลอดภัย
- คุกกี้ session ตั้ง flag `secure` อัตโนมัติเมื่อรันบน Render (ตรวจจาก env `RENDER`)
  ถ้า deploy บนโฮสต์ HTTPS อื่น ให้ตั้ง `COOKIE_SECURE=1` เอง
  (รันในเครื่องผ่าน http ห้ามตั้ง ไม่งั้นล็อกอินไม่ติด)
- `APP_PASSWORD` เป็นด่านล็อกอินชั้นเดียว — ถ้าเปิดสู่อินเทอร์เน็ต ควรใช้รหัสที่ยาว/สุ่ม
- token ของ session คำนวณจาก `APP_PASSWORD` (HMAC-SHA256) ไม่ได้สุ่มใหม่ทุกครั้ง
  ที่ process เริ่ม เพราะ Render จะ sleep แล้ว cold start เมื่อไม่มีคนใช้สักพัก
  ถ้าสุ่มใหม่ คุกกี้เดิมจะใช้ไม่ได้ทันทีและเจ้าหน้าที่ที่กรอกฟอร์มค้างไว้จะกดตรวจเล่มไม่ได้
  → **เปลี่ยน `APP_PASSWORD` เมื่อไหร่ ทุกคนถูกเตะออกจากระบบทันที** (ตั้งใจให้เป็นแบบนี้)
- ห้าม commit ไฟล์ `.env` หรือคีย์ใด ๆ ขึ้น git
- **`.dockerignore` กัน `.env` ไม่ให้ติดเข้า image** — Dockerfile ใช้ `COPY . .` ถ้าไม่มี
  ไฟล์นี้ การ `docker build` **ในเครื่อง** จะฝัง `.env` (มี `APP_PASSWORD` และ
  `ANTHROPIC_API_KEY`) ลงไปใน layer ซึ่ง **ลบทีหลังไม่ได้** พอ `docker push` ขึ้น
  registry หรือส่ง image ให้คนอื่น คีย์ก็หลุดตามไปด้วย
  (บน Render ไม่เคยเจอปัญหานี้ เพราะ clone จาก git ซึ่งไม่มี `.env` อยู่แล้ว)
  · ไฟล์นี้ยังตัด `tests/` `tools/` `*.md` `.git/` ออกด้วย เหลือเข้า image 12 ไฟล์
  คือ `*.py` + `templates/` + `requirements*.txt` ซึ่งพอสำหรับรันจริงครบทุกกฎ

## 7. ทดสอบว่าระบบทำงาน
```bash
python -m unittest discover -s tests -p "test_*.py"
```
