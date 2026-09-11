#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Thesis Staff Checker — standalone web app (no Claude/LLM required).
Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import json
import tempfile
import threading
import time
import traceback
import uuid
import os
import asyncio
import hashlib
import hmac
import secrets
from pathlib import Path

import pdfplumber
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from checker import plain_summary, run_check, summary_verdict, zone_counts
from ethesis_import import parse_ethesis_pdf
from ethesis_rules import FORM_FIELD_LABELS, FRONT_MATTER_RULES

BASE = Path(__file__).parent
app = FastAPI(title="E-Thesis Staff Checker")

STATIC_DIR = BASE / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=BASE / "templates")

APP_PASSWORD = os.getenv("APP_PASSWORD", "")
SESSION_COOKIE = "ethesis_session"
SESSION_MAX_AGE = 8 * 60 * 60
# คุกกี้เข้าสู่ระบบ = "เวลาที่ออก.ค่าสุ่ม.ลายเซ็น" เซิร์ฟเวอร์ตรวจลายเซ็นและอายุเองทุกครั้ง
#
# ของเดิมเป็นค่าตายตัวค่าเดียวที่คำนวณจาก APP_PASSWORD ตรง ๆ จึงมีปัญหาสามอย่าง
#   - ทุกคนได้คุกกี้ค่าเดียวกัน และเซิร์ฟเวอร์ไม่เคยดูอายุ — 8 ชั่วโมงกับการออกจากระบบ
#     มีผลแค่ในเบราว์เซอร์ คุกกี้ที่หลุดไปใช้ได้ตลอดจนกว่าจะเปลี่ยนรหัสผ่าน
#   - เอาคุกกี้ที่หลุดไปเดารหัสผ่านแบบออฟไลน์ได้ (ลอง HMAC ทีละรหัสจนตรง)
#
# ที่ยังต้องคงไว้: ต้องไม่เก็บเซสชันไว้ในหน่วยความจำ บน Render บริการจะ sleep แล้ว cold
# start ใหม่ ถ้าเซสชันหายตามไปด้วย เจ้าหน้าที่ที่กรอกฟอร์มค้างไว้จะกด "ตรวจเล่ม" ไม่ได้
# กุญแจเซ็นจึงคำนวณได้เท่าเดิมทุกครั้งที่ process เริ่ม และเปลี่ยนรหัสผ่าน = เตะทุกคนออก
#
# กุญแจผ่าน PBKDF2 200,000 รอบ การเดารหัสผ่านจากคุกกี้ที่หลุดจึงช้าลงราวสองแสนเท่า
# ถ้าตั้ง SESSION_SECRET ไว้ด้วย (ค่าสุ่มยาว ๆ เก็บเป็น secret บน Render) จะเดาไม่ได้เลย
_SESSION_KDF_ROUNDS = 200_000


def derive_session_key(password, secret=""):
    salt = ("ethesis-session-v2:" + (secret or "")).encode("utf-8")
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _SESSION_KDF_ROUNDS)


SESSION_KEY = (derive_session_key(APP_PASSWORD, os.getenv("SESSION_SECRET", ""))
               if APP_PASSWORD else secrets.token_bytes(32))


def _session_signature(payload):
    return hmac.new(SESSION_KEY, payload.encode("utf-8"), hashlib.sha256).hexdigest()


def new_session_token(now=None):
    issued = int(time.time() if now is None else now)
    payload = f"{issued}.{secrets.token_urlsafe(12)}"
    return f"{payload}.{_session_signature(payload)}"


def session_token_valid(token, now=None):
    try:
        issued_text, nonce, signature = (token or "").split(".")
        issued = int(issued_text)
    except ValueError:
        return False
    if not hmac.compare_digest(signature, _session_signature(f"{issued_text}.{nonce}")):
        return False
    age = (time.time() if now is None else now) - issued
    # เผื่อนาฬิกาเครื่องเหลื่อมกันเล็กน้อยหลัง cold start
    return -60 <= age < SESSION_MAX_AGE
# คุกกี้ต้องเป็น Secure เมื่อเสิร์ฟผ่าน HTTPS — Render ตั้งให้อัตโนมัติ ส่วน host อื่น
# ให้ตั้ง COOKIE_SECURE=1 เอง (ถ้ารันในเครื่องด้วย http ต้องเป็น 0 ไม่งั้นล็อกอินไม่ติด)
COOKIE_SECURE = (os.getenv("COOKIE_SECURE", "").lower() in ("1", "true", "yes")
                 or bool(os.getenv("RENDER")))

ZONE_LABEL = {"RED": "🔴 ไม่ผ่าน", "ORANGE": "🟠 รอยืนยัน", "YELLOW": "🟡 ข้อสังเกต"}

# in-memory job store — {job_id: {stage, done, error, report, pdf_name, ts}}
JOBS = {}
# ผลตรวจต้องอยู่ให้ครบวันทำงาน ไม่ใช่ครึ่งชั่วโมง — ปุ่ม "โครงสร้างหน้าลงนาม" กับ
# "ค่าปรับ" ต้องยิง /summary กลับมาที่ job เดิม เจ้าหน้าที่เปิดรายงานเล่มหนึ่งค้างไว้
# ไล่ดูทีละข้อ (เล่มร้อยกว่าหน้ามีหลายสิบข้อ) แล้วค่อยกดปุ่มตอนท้าย ของเดิมตั้งไว้
# 30 นาที พอเริ่มตรวจเล่มถัดไป job เก่าถูกลบ ปุ่มบนรายงานเล่มก่อนจึงตายเงียบ ๆ
JOB_TTL = 12 * 3600
# กันหน่วยความจำด้วย "จำนวนที่เก็บ" แทนการตั้งเวลาให้สั้น — รายงานเล่ม 140 หน้าหนัก
# 56 KB เก็บ 40 เล่มก็ราว 2 MB ซึ่งถูกกว่าการทำให้ปุ่มบนรายงานที่เปิดค้างไว้ใช้ไม่ได้
MAX_KEPT_JOBS = 40
JOBS_LOCK = threading.Lock()


def _positive_env_int(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


MAX_UPLOAD_BYTES = _positive_env_int("MAX_UPLOAD_MB", 25) * 1024 * 1024
MAX_ACTIVE_JOBS = _positive_env_int("MAX_ACTIVE_JOBS", 2)
UPLOAD_CHUNK_BYTES = 1024 * 1024
JOB_SLOTS = threading.BoundedSemaphore(MAX_ACTIVE_JOBS)


def _is_authenticated(request):
    return session_token_valid(request.cookies.get(SESSION_COOKIE, ""))


def _safe_next(path):
    return path if path.startswith("/") and not path.startswith("//") else "/"


# ปลายทางที่หน้าเว็บเรียกด้วย fetch เท่านั้น (ไม่ใช่การเปิดหน้าเว็บตรง ๆ)
JSON_ENDPOINTS = ("/check", "/parse-ethesis", "/summary/", "/progress/")


def _wants_json(request):
    path = request.url.path
    if any(path == p.rstrip("/") or path.startswith(p) for p in JSON_ENDPOINTS):
        return True
    return "application/json" in request.headers.get("accept", "")


@app.middleware("http")
async def require_login(request: Request, call_next):
    path = request.url.path
    if path in {"/health", "/login"} or path.startswith("/static/"):
        return await call_next(request)
    if not APP_PASSWORD:
        return HTMLResponse(
            "<h2>ระบบยังไม่ได้ตั้งรหัสผ่าน</h2>"
            "<p>กรุณากำหนด Environment Variable ชื่อ APP_PASSWORD แล้ว restart ระบบ</p>",
            status_code=503,
        )
    if not _is_authenticated(request):
        # คำขอที่หน้าเว็บยิงด้วย fetch (ตรวจเล่ม/อ่านไฟล์ eThesis/สรุปใหม่) ต้องได้ JSON
        # ไม่ใช่ redirect ไปหน้า login เพราะ fetch จะตาม redirect แล้วได้ HTML กลับมา
        # ทำให้ resp.json() พังเป็น "Unexpected token '<'" ซึ่งผู้ใช้อ่านไม่รู้เรื่อง
        if _wants_json(request):
            return JSONResponse(
                {"detail": "เซสชันหมดอายุ กรุณาเข้าสู่ระบบใหม่", "login_required": True},
                status_code=401,
            )
        return RedirectResponse(url=f"/login?next={path}", status_code=303)
    return await call_next(request)


def _pdf_readability_issue(pdf_path):
    """Return a user-facing issue when the PDF cannot support text-based checks."""
    try:
        with pdfplumber.open(pdf_path) as document:
            n_pages = len(document.pages)
            if n_pages == 0:
                return "ไฟล์ PDF ไม่มีหน้าเอกสาร"

            sample_indexes = set(range(min(5, n_pages)))
            sample_indexes.update({n_pages // 2, n_pages - 1})
            for index in sorted(sample_indexes):
                if (document.pages[index].extract_text() or "").strip():
                    return None
            return "ไฟล์ PDF เปิดได้ แต่ระบบไม่สามารถอ่านข้อความได้ อาจเป็นเอกสารสแกนหรือรูปภาพ"
    except Exception:
        return "ไฟล์ PDF เปิดอ่านไม่ได้ ไฟล์อาจเสียหายหรือมีรหัสผ่าน"


def _update_job(job_id, **values):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        if job:
            job.update(values)


def _get_job(job_id):
    with JOBS_LOCK:
        job = JOBS.get(job_id)
        return dict(job) if job else None


def _prune_jobs():
    """ทิ้งผลตรวจเก่า โดยเก็บ "เล่มล่าสุด" ไว้เสมอ

    ทิ้งเฉพาะงานที่ตรวจเสร็จแล้ว งานที่ยังตรวจอยู่ห้ามแตะ
    """
    now = time.time()
    with JOBS_LOCK:
        done = [(v["ts"], k) for k, v in JOBS.items() if v.get("done")]
        drop = {k for ts, k in done if now - ts > JOB_TTL}
        # เกินจำนวนที่เก็บได้ ให้ทิ้งของเก่าที่สุดก่อน
        keep = sorted((pair for pair in done if pair[1] not in drop), reverse=True)
        drop.update(k for _ts, k in keep[MAX_KEPT_JOBS:])
        for k in drop:
            JOBS.pop(k, None)


def _run_job(job_id, tmp_path, approved, chapters_mode):
    def progress(msg):
        _update_job(job_id, stage=msg)

    try:
        report = run_check(tmp_path, approved, chapters_mode=chapters_mode, progress=progress)
        _update_job(job_id, report=report, stage="เสร็จสิ้น")
    except Exception:
        tb = traceback.format_exc()
        print(f"job {job_id} failed\n{tb}", flush=True)
        _update_job(job_id, error="ระบบไม่สามารถอ่านหรือตรวจไฟล์นี้ได้")
    finally:
        _update_job(job_id, done=True, ts=time.time())
        Path(tmp_path).unlink(missing_ok=True)
        JOB_SLOTS.release()


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "/"):
    if APP_PASSWORD and _is_authenticated(request):
        return RedirectResponse(url="/", status_code=303)
    return templates.TemplateResponse(request=request, name="login.html", context={
        "error": "",
        "configured": bool(APP_PASSWORD),
        "next_path": _safe_next(next),
    })


@app.post("/login", response_class=HTMLResponse)
async def login(request: Request, password: str = Form(...), next: str = Form("/")):
    if not APP_PASSWORD:
        return templates.TemplateResponse(request=request, name="login.html", context={
            "error": "ระบบยังไม่ได้ตั้งค่า APP_PASSWORD",
            "configured": False,
            "next_path": "/",
        }, status_code=503)
    if not hmac.compare_digest(password, APP_PASSWORD):
        await asyncio.sleep(0.5)
        return templates.TemplateResponse(request=request, name="login.html", context={
            "error": "รหัสผ่านไม่ถูกต้อง",
            "configured": True,
            "next_path": _safe_next(next),
        }, status_code=401)

    response = RedirectResponse(url=_safe_next(next), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        new_session_token(),
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=COOKIE_SECURE,
        samesite="strict",
    )
    return response


@app.post("/logout")
def logout():
    response = RedirectResponse(url="/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/parse-ethesis")
async def parse_ethesis(pdf: UploadFile = File(...)):
    """อ่านไฟล์ eThesis PDF แล้วคืนค่าที่ดึงได้เพื่อเติมแบบฟอร์ม (ตัวช่วยเท่านั้น)

    ค่าที่คืนไม่ถูกนำไปตรวจโดยตรง — เจ้าหน้าที่ต้องตรวจทานทุกช่องก่อนกดตรวจเล่ม
    """
    tmp_path = None
    try:
        total, header = 0, b""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
            while chunk := await pdf.read(UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"ไฟล์มีขนาดเกิน {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                    )
                if len(header) < 1024:
                    header += chunk[:1024 - len(header)]
                tmp.write(chunk)
        if total == 0:
            raise HTTPException(status_code=400, detail="ไฟล์ที่อัปโหลดไม่มีข้อมูล")
        if b"%PDF-" not in header:
            raise HTTPException(status_code=400, detail="ไฟล์ที่อัปโหลดไม่ใช่ไฟล์ PDF")
        try:
            data = await asyncio.to_thread(parse_ethesis_pdf, tmp_path)
        except Exception:
            raise HTTPException(status_code=422, detail="อ่านไฟล์ eThesis PDF ไม่สำเร็จ")
        return JSONResponse({"data": data})
    finally:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)


@app.post("/check")
async def check(
    pdf: UploadFile = File(...),
    doc_type: str = Form(...),
    format: str = Form(...),
    program_language: str = Form(...),
    title_en: str = Form(""),
    title_th: str = Form(""),
    student_name: str = Form(""),
    student_name_th: str = Form(""),
    student_id: str = Form(""),
    degree_cover_en: str = Form(""),
    degree_cover_th: str = Form(""),
    degree_sig_en: str = Form(""),
    degree_sig_th: str = Form(""),
    degree_abbr_en: str = Form(""),
    degree_abbr_th: str = Form(""),
    exam_date: str = Form(""),
    year: str = Form(""),
    faculty: str = Form(""),
    program: str = Form(""),
    committees_json: str = Form(""),
    chapters_mode: str = Form("strict"),
):
    _prune_jobs()
    if doc_type not in {"THESIS", "THEMATIC PAPER", "INDEPENDENT STUDY"}:
        raise HTTPException(status_code=400, detail="ประเภทเล่มไม่ถูกต้อง")
    if format not in {"1", "2"}:
        raise HTTPException(status_code=400, detail="รูปแบบเล่มไม่ถูกต้อง")
    if program_language not in {"international", "thai", "thai_english"}:
        raise HTTPException(status_code=400, detail="ประเภทหลักสูตรไม่ถูกต้อง")
    if chapters_mode not in {"strict", "free"}:
        raise HTTPException(status_code=400, detail="โหมดตรวจชื่อบทไม่ถูกต้อง")

    form_values = {
        "title_en": title_en.strip(), "title_th": title_th.strip(),
        "student_name": student_name.strip(), "student_name_th": student_name_th.strip(),
        "student_id": student_id.strip(),
        "degree_cover_en": degree_cover_en.strip(), "degree_cover_th": degree_cover_th.strip(),
        "degree_sig_en": degree_sig_en.strip(), "degree_sig_th": degree_sig_th.strip(),
        "degree_abbr_en": degree_abbr_en.strip(), "degree_abbr_th": degree_abbr_th.strip(),
        "exam_date": exam_date.strip(), "year": year.strip(),
    }
    required_fields = FRONT_MATTER_RULES["required_form_fields"][program_language]
    missing = [FORM_FIELD_LABELS[name] for name in required_fields if not form_values[name]]
    if missing:
        raise HTTPException(
            status_code=400,
            detail="กรุณากรอกข้อมูลอ้างอิงให้ครบก่อนตรวจ: " + ", ".join(missing),
        )
    if not JOB_SLOTS.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="มีงานตรวจเต็มจำนวน กรุณารอสักครู่แล้วลองใหม่")

    approved = {
        "doc_type": doc_type, "format": format, "program_language": program_language,
        **form_values,
    }
    if faculty.strip():
        approved["faculty"] = faculty.strip()
    # ชื่อหลักสูตร: ไม่ได้ใช้ตรวจ แต่แสดงบนหัวรายงานให้เจ้าหน้าที่อ้างอิงได้
    if program.strip():
        approved["program"] = program.strip()
    # committees_json = ข้อมูลกรรมการที่ดึงจาก eThesis (แปลงเป็น JSON ในฟอร์ม) — กันพัง
    if committees_json.strip():
        try:
            committees = json.loads(committees_json)
            if isinstance(committees, dict) and (committees.get("advisory") or committees.get("exam")):
                approved["committees"] = committees
        except (ValueError, TypeError):
            pass

    tmp_path = None
    try:
        total = 0
        header = b""
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
            while chunk := await pdf.read(UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > MAX_UPLOAD_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=f"ไฟล์มีขนาดเกิน {MAX_UPLOAD_BYTES // (1024 * 1024)} MB",
                    )
                if len(header) < 1024:
                    header += chunk[:1024 - len(header)]
                tmp.write(chunk)

        if total == 0:
            raise HTTPException(status_code=400, detail="ไฟล์ที่อัปโหลดไม่มีข้อมูล")
        if b"%PDF-" not in header:
            raise HTTPException(status_code=400, detail="ไฟล์ที่อัปโหลดไม่ใช่ไฟล์ PDF")

        # โพรบว่าอ่านข้อความได้ไหม (สุ่มไม่กี่หน้า เร็วแม้ไฟล์ใหญ่) แล้วปฏิเสธทันที
        readability_issue = await asyncio.to_thread(_pdf_readability_issue, tmp_path)
        if readability_issue:
            raise HTTPException(status_code=422, detail=readability_issue)

        job_id = uuid.uuid4().hex
        with JOBS_LOCK:
            JOBS[job_id] = {
                "stage": "รอเริ่มตรวจ...", "done": False, "error": None,
                "report": None, "pdf_name": pdf.filename, "approved": approved,
                "ts": time.time(),
            }
        threading.Thread(
            target=_run_job,
            args=(job_id, tmp_path, approved, chapters_mode),
            daemon=True,
        ).start()
        return JSONResponse({"job_id": job_id})
    except Exception:
        if tmp_path:
            Path(tmp_path).unlink(missing_ok=True)
        JOB_SLOTS.release()
        raise


@app.get("/progress/{job_id}")
def progress(job_id: str):
    job = _get_job(job_id)
    if not job:
        return JSONResponse({"error": "job not found"}, status_code=404)
    return {"stage": job["stage"], "done": job["done"],
            "error": bool(job["error"])}


@app.post("/summary/{job_id}")
async def rebuild_summary(job_id: str, request: Request):
    """สร้างข้อความสรุปใหม่ตามผลพิจารณาของเจ้าหน้าที่

    ส้ม/เหลืองที่เจ้าหน้าที่กด "ไม่ผ่าน" จะถูกรวมเข้าไปเป็นรายการที่ต้องแก้ด้วย
    """
    job = _get_job(job_id)
    if not job or not job.get("report"):
        return JSONResponse({"error": "job not found"}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    failed = [str(k) for k in (payload.get("failed") or [])][:200]
    passed = [str(k) for k in (payload.get("passed") or [])][:200]
    # จุดที่ระบบตรวจเองไม่ได้ แล้วเจ้าหน้าที่กด "ผิด" (เช่น โครงสร้างหน้าลงนาม)
    staff = [str(k) for k in (payload.get("staff") or [])][:50]
    report = job["report"]
    # ส่งผลตรวจที่ปรับแล้วกลับไปด้วย หน้ารายงานใช้เลือกว่าจะโชว์หัวข้อค่าปรับอันไหน
    # (เล่มผ่านกับเล่มที่ต้องแก้ใช้ถ้อยคำคนละชุด) คำนวณฝั่งเดียวกับข้อความสรุปเสมอ
    # ตัวเลขสามกล่องบนหัวรายงานต้องตามคำตัดสินด้วย ไม่ใช่ค้างที่ผลของระบบ —
    # คิดฝั่งเซิร์ฟเวอร์ที่เดียวกับข้อความสรุป ไม่ให้หน้าเว็บนับเอง (นับคนละทางเมื่อไหร่
    # เจ้าหน้าที่จะเห็นตัวเลขที่ขัดกับข้อความสรุปโดยไม่มีอะไรบอกว่าอันไหนถูก)
    return {"plain": plain_summary(report, failed, passed, staff),
            "verdict": summary_verdict(report, failed=failed, passed=passed,
                                       staff=staff),
            "counts": zone_counts(report, failed=failed, passed=passed,
                                  staff=staff)}


@app.get("/result/{job_id}", response_class=HTMLResponse)
def result(request: Request, job_id: str):
    job = _get_job(job_id)
    if not job:
        return HTMLResponse("<h3>ไม่พบผลตรวจ (อาจหมดอายุ)</h3><a href='/'>← ตรวจใหม่</a>", status_code=404)
    if not job["done"]:
        return HTMLResponse("<h3>ยังตรวจไม่เสร็จ</h3><a href='javascript:history.back()'>← กลับ</a>", status_code=202)
    if job["error"]:
        return HTMLResponse(
            "<h2>เกิดข้อผิดพลาดระหว่างตรวจ</h2>"
            "<p>ระบบไม่สามารถอ่านหรือตรวจไฟล์นี้ได้ กรุณาตรวจว่าไฟล์ PDF เปิดได้ตามปกติแล้วลองใหม่</p>"
            f"<p>รหัสงาน: <code>{job_id}</code></p>"
            "<a href='/'>&larr; กลับไปตรวจใหม่</a>", status_code=500)
    return templates.TemplateResponse(request=request, name="report.html", context={
        "report": job["report"], "zone_label": ZONE_LABEL, "job_id": job_id,
        "pdf_name": job["pdf_name"], "student": job.get("approved") or {},
    })


@app.get("/health")
def health():
    return {"status": "ok"}
