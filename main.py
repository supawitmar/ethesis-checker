#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Thesis Staff Checker — standalone web app (no Claude/LLM required).
Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import tempfile
import threading
import time
import traceback
import uuid
import os
import asyncio
import hmac
import secrets
from pathlib import Path

import pdfplumber
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

import llm_assist
from checker import run_check
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
SESSION_TOKEN = secrets.token_urlsafe(32)
SESSION_MAX_AGE = 8 * 60 * 60

ZONE_LABEL = {"RED": "🔴 ไม่ผ่าน", "ORANGE": "🟠 รอยืนยัน", "YELLOW": "🟡 ข้อสังเกต"}

# in-memory job store — {job_id: {stage, done, error, report, pdf_name, ts}}
JOBS = {}
JOB_TTL = 1800
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
    token = request.cookies.get(SESSION_COOKIE, "")
    return bool(token) and hmac.compare_digest(token, SESSION_TOKEN)


def _safe_next(path):
    return path if path.startswith("/") and not path.startswith("//") else "/"


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
    now = time.time()
    with JOBS_LOCK:
        expired = [k for k, v in JOBS.items()
                   if v.get("done") and now - v["ts"] > JOB_TTL]
        for k in expired:
            JOBS.pop(k, None)


def _run_job(job_id, tmp_path, approved, chapters_mode):
    def progress(msg):
        _update_job(job_id, stage=msg)

    try:
        report = run_check(tmp_path, approved, chapters_mode=chapters_mode, progress=progress)
        report["context"]["ai_assist"] = llm_assist.enabled()
        if llm_assist.enabled():
            # ชั้นเสริมเท่านั้น — ถ้า AI ล้มเหลว รายงานจากกฎเดิมต้องออกครบตามปกติ
            try:
                progress("AI ช่วยกลั่นกรองรายการก้ำกึ่ง")
                llm_assist.review_borderline(report)
            except Exception:
                print(f"job {job_id}: llm review failed\n{traceback.format_exc()}", flush=True)
            try:
                progress("AI สรุปคำแนะนำสำหรับนักศึกษา")
                report["student_summary"] = llm_assist.student_summary(report, approved)
            except Exception:
                print(f"job {job_id}: llm summary failed\n{traceback.format_exc()}", flush=True)
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
        SESSION_TOKEN,
        max_age=SESSION_MAX_AGE,
        httponly=True,
        secure=bool(os.getenv("RENDER")),
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
    degree: str = Form(""),
    degree_th: str = Form(""),
    degree_abbr: str = Form(""),
    exam_date: str = Form(""),
    year: str = Form(""),
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
        "student_id": student_id.strip(), "degree": degree.strip(), "degree_th": degree_th.strip(),
        "degree_abbr": degree_abbr.strip(),
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
        "report": job["report"], "zone_label": ZONE_LABEL,
        "pdf_name": job["pdf_name"], "student": job.get("approved") or {},
    })


@app.get("/health")
def health():
    return {"status": "ok"}
