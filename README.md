# E-Thesis Staff Checker (standalone web app)

ตรวจเล่ม e-thesis (PDF) เทียบข้อมูลอนุมัติจากบัณฑิตวิทยาลัย — รันเป็นเว็บแอปได้เอง
ไม่ต้องเปิดแชทกับ Claude ทุกครั้ง (ตรรกะการตัดสินทั้งหมดเขียนเป็นกฎในโค้ด `checker.py`
ส่วนรายชื่อกรรมการ ตำแหน่งวางชื่อในตารางลายเซ็น และข้อความมุมล่างขวาของหน้าลงนาม
ยังเป็น checklist ให้เจ้าหน้าที่ตรวจด้วยตา)

## รันในเครื่อง

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

เปิด http://localhost:8000

ค่าควบคุมการใช้งาน (environment variables):

- `APP_PASSWORD` — รหัสผ่านเข้าใช้งานระบบ (จำเป็น ต้องตั้งใน Render และห้ามเขียนลง GitHub)
- `MAX_UPLOAD_MB` — ขนาดไฟล์สูงสุด หน่วย MB (ค่าเริ่มต้น 25)
- `MAX_ACTIVE_JOBS` — จำนวนงานตรวจพร้อมกันสูงสุด (ค่าเริ่มต้น 2)

เมื่อ deploy บน Render ให้เพิ่ม `APP_PASSWORD` ในหน้า Environment ของบริการ โดยเลือกเก็บเป็น secret
หากไม่กำหนดค่านี้ ระบบจะแสดงสถานะว่ายังไม่ได้ตั้งรหัสผ่านและไม่อนุญาตให้เข้าหน้าตรวจ

ก่อนสร้างงาน ระบบตรวจว่าไฟล์มีส่วนหัว `%PDF-`, เปิดด้วย `pdfplumber` ได้, มีอย่างน้อย 1 หน้า
และดึงข้อความจากหน้าตัวอย่างได้ หากเป็นไฟล์เสีย มีรหัสผ่าน หรือเป็นภาพสแกนทั้งเล่ม ระบบจะปฏิเสธไฟล์

## ทดสอบ

```bash
pip install -r requirements-dev.txt
python -B -m unittest discover -s tests -v
```

## รันด้วย Docker

```bash
docker build -t ethesis-checker .
docker run -p 8000:8000 ethesis-checker
```

## Deploy ให้ใช้งานออนไลน์ (ฟรี/ราคาถูก)

ตัวเลือกที่ deploy ง่ายสำหรับแอป FastAPI + Docker แบบนี้:

1. **Render.com** — สมัครฟรี, เชื่อม GitHub repo, เลือก "Web Service", Render
   จะ build จาก Dockerfile ให้อัตโนมัติ (มี free tier ที่ sleep เมื่อไม่ใช้งาน)
2. **Railway.app** — คล้าย Render, deploy จาก GitHub หรือ CLI (`railway up`)
3. **Fly.io** — `fly launch` จะอ่าน Dockerfile ให้เอง เหมาะถ้าต้องการ custom domain

ขั้นตอนทั่วไป (ตัวอย่าง Render):
1. Push โฟลเดอร์นี้ขึ้น GitHub repo
2. ที่ Render → New → Web Service → เชื่อม repo
3. Render ตรวจเจอ Dockerfile อัตโนมัติ → กด Deploy
4. ได้ URL สาธารณะ เช่น `https://ethesis-checker.onrender.com`

## โครงสร้างไฟล์

- `main.py` — FastAPI app: หน้าแบบฟอร์ม (`/`) + endpoint ตรวจ (`/check`)
- `checker.py` — กฎตรวจทั้งหมด (พอร์ตจาก Claude Skill เดิม, เพิ่ม fuzzy matching
  ด้วย `difflib` แทน Claude judgment)
- `templates/index.html` — ฟอร์มกรอกข้อมูล + อัปโหลด PDF
- `templates/report.html` — หน้าแสดงผลตรวจ (โซนสี 🔴🟠🟡)

## ข้อจำกัดเทียบกับชุด Claude Skill เดิม

- ไม่ตรวจ margin/line-spacing/font (เหมือนเดิม — ชุดเจ้าหน้าที่ตรวจ PDF ไม่ได้ตรวจสิ่งเหล่านี้)
- ไม่ตรวจรายชื่อ/บทบาทกรรมการ ตำแหน่งในตารางลายเซ็น และข้อความมุมล่างขวาโดยอัตโนมัติ
  รายงานจะแสดง checklist ให้เจ้าหน้าที่ตรวจเอง
- ไม่ตรวจ plagiarism, PDF/A, embedded fonts และความคมชัดของภาพ
- Fuzzy matching ใช้ `difflib` (ในตัว Python) แทน rapidfuzz — แม่นยำน้อยกว่าเล็กน้อยแต่ไม่ต้องพึ่ง
  internet ตอนติดตั้ง ถ้าต้องการความแม่นยำสูงขึ้น เปลี่ยนไปใช้ `rapidfuzz.fuzz.partial_ratio`
  ได้โดยแก้ `fuzzy_contains()` ใน `checker.py`
- ผลคำว่า “ผ่าน” หมายถึงผ่านเฉพาะรายการที่ระบบตรวจได้ รายการนอกขอบเขตจะแสดงท้ายรายงานเสมอ
