#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Thesis Staff Checker — standalone web app (no Claude/LLM required).
Run:  uvicorn main:app --host 0.0.0.0 --port 8000
"""
import atexit
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

import re
import urllib.error
import urllib.request

import pdfplumber
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from checker import (
    plain_summary,
    run_check,
    sheet_row,
    sheet_staff_pending,
    sheet_undecided,
    summary_verdict,
    zone_counts,
)
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

# ปุ่ม "บันทึกลงชีท" บนหน้ารายงาน — ยิงไปที่ Apps Script ที่ติดอยู่กับชีทบันทึกการตรวจ
# ไม่ตั้งสองค่านี้ = ปุ่มไม่ขึ้น และระบบทำงานเหมือนเดิมทุกอย่าง
# ค่าทั้งสองเป็นความลับ ห้ามส่งกลับไปให้หน้าเว็บหรือเขียนลง log
def _clean_url(value):
    """ค่าที่วางมาจากช่องตั้งค่ามักติดช่องว่าง เครื่องหมายคำพูด หรือ / ปิดท้ายมาด้วย"""
    return str(value or "").strip().strip('"\'').rstrip("/")


SHEET_WEBHOOK_URL = _clean_url(os.getenv("SHEET_WEBHOOK_URL", ""))
SHEET_WEBHOOK_TOKEN = os.getenv("SHEET_WEBHOOK_TOKEN", "").strip()
SHEET_ENABLED = bool(SHEET_WEBHOOK_URL and SHEET_WEBHOOK_TOKEN)
# Apps Script ที่ไม่ได้ถูกเรียกมานานต้องตื่นก่อน ครั้งแรกของวันจึงช้ากว่าปกติมาก
SHEET_TIMEOUT = 25
# ช่องวันที่บนหน้าแบบฟอร์มเป็น <input type="date"> จึงส่งมาเป็น yyyy-mm-dd เสมอ
# ค่าที่ผิดรูปแบบทิ้งไปเงียบ ๆ ดีกว่าส่งขยะไปให้ชีทตีความเอง (วันที่สลับ วัน/เดือน ได้)
_ISO_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _iso_date(value):
    text = str(value or "").strip()
    return text if _ISO_DATE.match(text) else ""


def _date_label(value):
    """yyyy-mm-dd -> dd/mm/yyyy สำหรับแสดงบนหัวรายงาน (ค่าว่างหรือผิดรูป = "")

    เขียนแบบเดียวกับช่อง Queue ในชีทบันทึกการตรวจ (วัน/เดือน/ปี ค.ศ.) เจ้าหน้าที่
    จะได้เทียบกับแถวในชีทได้ตรง ๆ ไม่ต้องแปลงในหัวเอง
    """
    iso = _iso_date(value)
    if not iso:
        return ""
    year, month, day = iso.split("-")
    return f"{day}/{month}/{year}"

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
DOC_TYPES = {"THESIS", "THEMATIC PAPER", "INDEPENDENT STUDY"}


def _positive_env_int(name, default):
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


MAX_UPLOAD_BYTES = _positive_env_int("MAX_UPLOAD_MB", 25) * 1024 * 1024
MAX_ACTIVE_JOBS = _positive_env_int("MAX_ACTIVE_JOBS", 2)
UPLOAD_CHUNK_BYTES = 1024 * 1024
JOB_SLOTS = threading.BoundedSemaphore(MAX_ACTIVE_JOBS)

# ไฟล์รูปเล่มที่ตรวจแล้ว เก็บไว้ให้เปิดดูจากหน้ารายงาน (เจ้าหน้าที่ขอ ก.ย. 2569
# "ในหน้าสรุปผล สามารถเพิ่มให้ดูไฟล์รูปเล่มที่แนบได้ไหม") ของเดิมลบทิ้งทันทีที่ตรวจเสร็จ
#
# อายุไฟล์ผูกกับผลตรวจ — ผลตรวจถูกทิ้งเมื่อไหร่ ไฟล์ถูกลบตาม (JOB_TTL / MAX_KEPT_JOBS)
# และกันพื้นที่ดิสก์ด้วยงบรวม MAX_KEPT_BOOKS_MB: เกินงบให้ลบไฟล์ของเล่มเก่าก่อน รายงาน
# ยังเปิดได้ แค่ไม่มีปุ่มเปิดไฟล์ (ไม่ตั้งงบ เล่ม 25 MB x 40 เล่ม = 1 GB)
# แต่ละ process มีโฟลเดอร์ของตัวเอง (run-xxxx ใต้ ethesis-books) ไฟล์ชื่อขึ้นต้น "book-"
# (bug test ก.ย. 2569: เดิมทุก process ใช้โฟลเดอร์เดียวกัน แล้วตอนเริ่มลบ book-*.pdf ทิ้งทั้งหมด
# รันเทสต์ขณะเซิร์ฟเวอร์เปิดอยู่ ไฟล์ของเล่มที่เซิร์ฟเวอร์เก็บไว้หายไปด้วย วัดได้จริง 2 ใน 3 ไฟล์)
# โฟลเดอร์ของ process ที่จบไปแล้วถูกลบเมื่อเงียบเกิน ORPHAN_BOOK_SECONDS — process ที่ยังทำงาน
# แตะโฟลเดอร์ตัวเองทุกรอบของตัวกวาด (heartbeat) จึงไม่ถูกนับว่าจบ แม้จะไม่มีใครอัปโหลดเลย
# (ผลตรวจอยู่ในหน่วยความจำ restart แล้วหายหมด ไฟล์ของรอบก่อนจึงไม่มีรายงานไหนชี้ถึงแล้ว)
#
# ต้องมีตัวกวาดตามเวลาด้วย ไม่ใช่รอให้มีคนตรวจเล่มถัดไป (bug test ก.ย. 2569): เดิม
# _prune_jobs ถูกเรียกจาก /check ที่เดียว ถ้าไม่มีใครตรวจเล่มใหม่ ไฟล์วิทยานิพนธ์ที่ยังไม่
# เผยแพร่ค้างบนเซิร์ฟเวอร์และเปิดผ่าน /book ได้เกิน 12 ชั่วโมงที่บอกไว้ (บน Render แบบ
# ไม่ sleep ค้างได้เป็นวัน ๆ) — ตัวกวาดทำงานทุก BOOK_SWEEP_SECONDS และ /book กับ /result
# ทิ้งของหมดอายุก่อนตอบเสมอ
BOOKS_ROOT = Path(tempfile.gettempdir()) / "ethesis-books"
BOOKS_ROOT.mkdir(parents=True, exist_ok=True)
BOOKS_DIR = Path(tempfile.mkdtemp(prefix="run-", dir=BOOKS_ROOT))
BOOK_PREFIX = "book-"
MAX_KEPT_BOOKS_BYTES = _positive_env_int("MAX_KEPT_BOOKS_MB", 500) * 1024 * 1024
BOOK_SWEEP_SECONDS = 10 * 60
# ไฟล์ที่ไม่มีผลตรวจไหนชี้ถึง (เช่น Windows ลบไม่ได้เพราะกำลังเปิดอ่านอยู่) ลบเมื่อเก่ากว่านี้
# ต้องนานพอไม่ให้ไปโดนไฟล์ที่กำลังอัปโหลด ซึ่งยังไม่ได้ผูกกับ job จนกว่าจะอ่านเสร็จ
ORPHAN_BOOK_SECONDS = 60 * 60


def _remove_book(path):
    """ลบไฟล์รูปเล่ม — ไฟล์ที่กำลังถูกเปิดอ่านอยู่ (Windows) ลบไม่ได้ ปล่อยไว้ก่อน"""
    if not path:
        return
    try:
        Path(path).unlink(missing_ok=True)
    except OSError:
        pass


def _remove_book_folder(folder):
    for path in folder.glob(f"{BOOK_PREFIX}*.pdf"):
        _remove_book(path)
    try:
        folder.rmdir()
    except OSError:
        pass


def _last_touched(folder):
    times = [folder.stat().st_mtime]
    for path in folder.iterdir():
        try:
            times.append(path.stat().st_mtime)
        except OSError:
            pass
    return max(times)


def _heartbeat():
    """บอก process อื่นว่าโฟลเดอร์นี้ยังมีเจ้าของอยู่"""
    try:
        BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        os.utime(BOOKS_DIR)
    except OSError:
        pass


def _clear_leftover_books(now=None):
    """ลบโฟลเดอร์ของ process ที่จบไปแล้ว (เงียบเกิน ORPHAN_BOOK_SECONDS) — ของตัวเองและของ
    process ที่ยังทำงานอยู่ห้ามแตะ รวมถึงไฟล์แบบเก่าที่วางตรงใต้ BOOKS_ROOT (ก่อนแยกโฟลเดอร์)"""
    now = time.time() if now is None else now
    for folder in BOOKS_ROOT.glob("run-*"):
        if folder == BOOKS_DIR or not folder.is_dir():
            continue
        try:
            if now - _last_touched(folder) <= ORPHAN_BOOK_SECONDS:
                continue
        except OSError:
            continue
        _remove_book_folder(folder)
    for path in BOOKS_ROOT.glob(f"{BOOK_PREFIX}*.pdf"):
        _remove_book(path)


def _sweep_orphan_books(referenced, now):
    """ลบไฟล์ของเราที่ไม่มีผลตรวจไหนชี้ถึงแล้ว และเก่ากว่า ORPHAN_BOOK_SECONDS"""
    for path in BOOKS_DIR.glob(f"{BOOK_PREFIX}*.pdf"):
        try:
            old = now - path.stat().st_mtime > ORPHAN_BOOK_SECONDS
        except OSError:
            continue
        if old and str(path) not in referenced:
            _remove_book(path)


def _sweep_books_forever():
    while True:
        time.sleep(BOOK_SWEEP_SECONDS)
        try:
            _heartbeat()
            _prune_jobs()
            _clear_leftover_books()
        except Exception:
            traceback.print_exc()


_clear_leftover_books()
threading.Thread(target=_sweep_books_forever, name="book-sweeper", daemon=True).start()
# ปิด process ตามปกติ (Render ส่ง SIGTERM ตอน deploy ใหม่) ลบไฟล์ของตัวเองทิ้งเลย ไม่ต้องรอ
atexit.register(_remove_book_folder, BOOKS_DIR)


def _is_authenticated(request):
    return session_token_valid(request.cookies.get(SESSION_COOKIE, ""))


def _safe_next(path):
    return path if path.startswith("/") and not path.startswith("//") else "/"


# ปลายทางที่หน้าเว็บเรียกด้วย fetch เท่านั้น (ไม่ใช่การเปิดหน้าเว็บตรง ๆ)
JSON_ENDPOINTS = ("/check", "/parse-ethesis", "/summary/", "/progress/", "/sheet/")


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
    remove = []
    with JOBS_LOCK:
        done = [(v["ts"], k) for k, v in JOBS.items() if v.get("done")]
        drop = {k for ts, k in done if now - ts > JOB_TTL}
        # เกินจำนวนที่เก็บได้ ให้ทิ้งของเก่าที่สุดก่อน
        keep = sorted((pair for pair in done if pair[1] not in drop), reverse=True)
        drop.update(k for _ts, k in keep[MAX_KEPT_JOBS:])
        for k in drop:
            remove.append(JOBS.pop(k, {}).get("pdf_path"))
        # ไฟล์รูปเล่มรวมกันเกินงบดิสก์ ลบไฟล์ของเล่มเก่าก่อน ผลตรวจยังอยู่
        used = 0
        for _ts, k in keep[:MAX_KEPT_JOBS]:
            job = JOBS[k]
            path = job.get("pdf_path")
            if not path:
                continue
            try:
                used += Path(path).stat().st_size
            except OSError:
                job["pdf_path"] = None
                continue
            if used > MAX_KEPT_BOOKS_BYTES:
                remove.append(path)
                job["pdf_path"] = None
        referenced = {str(Path(j["pdf_path"])) for j in JOBS.values() if j.get("pdf_path")}
    for path in remove:
        _remove_book(path)
    _sweep_orphan_books(referenced, now)


def _run_job(job_id, tmp_path, approved, chapters_mode):
    def progress(msg):
        _update_job(job_id, stage=msg)

    try:
        report = run_check(tmp_path, approved, chapters_mode=chapters_mode, progress=progress)
        _update_job(job_id, report=report, stage="เสร็จสิ้น")
    except Exception:
        tb = traceback.format_exc()
        print(f"job {job_id} failed\n{tb}", flush=True)
        # ตรวจไม่สำเร็จ = ไม่มีหน้ารายงานให้เปิดไฟล์ ไม่ต้องเก็บไว้
        _update_job(job_id, error="ระบบไม่สามารถอ่านหรือตรวจไฟล์นี้ได้", pdf_path=None)
        _remove_book(tmp_path)
    finally:
        # ตรวจสำเร็จ ไฟล์อยู่ต่อให้เปิดดูจากหน้ารายงาน จนผลตรวจถูกทิ้ง (ดู _prune_jobs)
        _update_job(job_id, done=True, ts=time.time())
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
    ethesis_doc_type: str = Form(""),
    queue_date: str = Form(""),
    checked_date: str = Form(""),
):
    _prune_jobs()
    if doc_type not in DOC_TYPES or (ethesis_doc_type and ethesis_doc_type not in DOC_TYPES):
        raise HTTPException(status_code=400, detail="ประเภทเล่มไม่ถูกต้อง")
    if format not in {"1", "2"}:
        raise HTTPException(status_code=400, detail="รูปแบบเล่มไม่ถูกต้อง")
    if program_language not in {"international", "thai", "thai_english"}:
        raise HTTPException(status_code=400, detail="ประเภทหลักสูตรไม่ถูกต้อง")
    if chapters_mode not in {"strict", "free"}:
        raise HTTPException(status_code=400, detail="โหมดตรวจชื่อบทไม่ถูกต้อง")
    # วันดำเนินการ: เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าไม่กรอกสองช่องนี้ = ไม่ตรวจ
    # ตรวจซ้ำฝั่งเซิร์ฟเวอร์ ไม่เชื่อ required ในหน้าเว็บอย่างเดียว
    queue_date, checked_date = _iso_date(queue_date), _iso_date(checked_date)
    if not queue_date or not checked_date:
        raise HTTPException(status_code=400,
                            detail="กรุณาระบุวันดำเนินการให้ครบ: วันที่เข้าคิว, วันที่ตรวจ")

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

    # ประเภทเล่มที่อนุมัติ = ค่าที่เจ้าหน้าที่เลือกในแบบฟอร์มเสมอ (เจ้าหน้าที่สั่ง ก.ย. 2569
    # "ถ้าข้อมูลที่อ่านมา แล้วเจ้าหน้าที่เปลี่ยนด้วยมือ ให้เชื่อเจ้าหน้าที่ และดำเนินการตรวจ")
    # ค่าจากไฟล์ eThesis ใช้เติมฟอร์มเท่านั้น ถ้าต่างจากที่เลือก จะขึ้นเป็นข้อมูลประกอบในรายงาน
    # ให้เจ้าหน้าที่เห็นว่าไฟล์เขียนอะไร แต่ไม่ใช้ตัดสินเล่ม
    approved = {
        "doc_type": doc_type, "doc_type_ethesis": ethesis_doc_type, "format": format,
        "program_language": program_language, **form_values,
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
        BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(prefix=BOOK_PREFIX, suffix=".pdf",
                                         dir=BOOKS_DIR, delete=False) as tmp:
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
                # เก็บไว้กับงาน ไม่ปนกับข้อมูลอนุมัติที่ใช้ตัดสินเล่ม — ใช้ตอนบันทึกลงชีท
                "queue_date": queue_date, "checked_date": checked_date,
                "pdf_path": tmp_path, "ts": time.time(),
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


def _post_to_sheet(payload):
    """ยิงข้อมูลหนึ่งแถวไปที่ Apps Script แล้วคืนคำตอบที่แปลงเป็น dict แล้ว

    Apps Script ตอบกลับผ่าน redirect เสมอ (302 ไป script.googleusercontent.com)
    urllib ตามให้เองอยู่แล้ว ข้อความผิดพลาดที่คืนไปหน้าเว็บต้องไม่มี URL หรือโทเค็น
    """
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        SHEET_WEBHOOK_URL, data=body,
        # ต้องบอกชื่อผู้เรียก — ปลายทางของ Google ปฏิเสธคำขอที่ไม่มี User-Agent บางกรณี
        headers={"Content-Type": "application/json; charset=utf-8",
                 "User-Agent": "ethesis-checker"})
    with urllib.request.urlopen(request, timeout=SHEET_TIMEOUT) as response:
        raw = response.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except ValueError:
        # ชีทตอบเป็นหน้า HTML = ส่วนใหญ่คือ deploy ผิดแบบ หรือสิทธิ์ไม่ถึง
        return {"ok": False, "code": "bad_answer",
                "error": "ชีทตอบกลับมาในรูปแบบที่อ่านไม่ได้ "
                         "กรุณาตรวจการ deploy ของสคริปต์ในชีท"}


def _sheet_url_problem(url=None):
    """ค่า SHEET_WEBHOOK_URL ผิดรูปตั้งแต่แรกหรือไม่ (คืนข้อความบอกทางแก้ ไม่งั้นคืน "")

    Web app URL ของ Apps Script หน้าตาเป็น
        https://script.google.com/macros/s/<รหัส deployment>/exec
    ค่าที่หยิบมาผิดช่องบ่อยที่สุดคือ "Deployment ID" (ไม่ใช่ลิงก์) ลิงก์หน้าแก้สคริปต์
    และลิงก์ทดสอบที่ลงท้าย /dev ทั้งสามแบบยิงไปแล้วได้ 404 หรือหน้าล็อกอิน
    ซึ่งอ่านแล้วเดาไม่ออกว่าตั้งค่าผิดตรงไหน — ห้ามใส่ค่าจริงลงในข้อความ
    """
    text = SHEET_WEBHOOK_URL if url is None else _clean_url(url)
    if not text.lower().startswith("https://"):
        return "ค่า SHEET_WEBHOOK_URL ต้องเป็นลิงก์ที่ขึ้นต้นด้วย https://"
    if "/macros/s/" not in text:
        return ("ค่า SHEET_WEBHOOK_URL ไม่ใช่ Web app URL ของ Apps Script — ต้องเป็น "
                "https://script.google.com/macros/s/.../exec (คัดลอกจาก Deploy > "
                "Manage deployments ช่อง Web app URL ไม่ใช่ช่อง Deployment ID)")
    if text.endswith("/dev"):
        return ("ค่า SHEET_WEBHOOK_URL เป็นลิงก์ทดสอบที่ลงท้าย /dev ซึ่งต้องล็อกอินก่อน "
                "ให้ใช้ลิงก์ที่ลงท้าย /exec จาก Deploy > Manage deployments")
    if not text.endswith("/exec"):
        return "ค่า SHEET_WEBHOOK_URL ต้องลงท้ายด้วย /exec"
    return ""


def _sheet_failure(err):
    """แปลงข้อผิดพลาดตอนยิงไปหาชีทเป็น (ข้อความ, รหัส) ที่บอกได้ว่าต้องไปแก้ตรงไหน

    เดิมทุกกรณีได้ข้อความเดียวกันว่า "ติดต่อชีทไม่ได้" ซึ่งแยกไม่ออกว่า deploy ผิดแบบ
    ตั้ง URL ผิด หรือเน็ตมีปัญหา — คนละทางแก้กันทั้งนั้น

    ห้ามใส่ URL หรือโทเค็นลงในข้อความ (ข้อความนี้ทั้งขึ้นหน้าจอและลง log)
    """
    tail = " ผลตรวจยังอยู่ กดบันทึกใหม่ได้"
    if isinstance(err, urllib.error.HTTPError):
        status = getattr(err, "code", 0)
        if status in (401, 403):
            return (f"ชีทปฏิเสธคำขอ (รหัส {status}) — ตอน Deploy ต้องตั้ง Who has access "
                    "เป็น Anyone และใช้ URL ที่ลงท้าย /exec ไม่ใช่ /dev", "sheet_denied")
        if status == 404:
            return (f"ไม่พบสคริปต์ตาม URL ที่ตั้งไว้ (รหัส {status}) — ลิงก์ถูกรูปแบบแล้ว "
                    "แต่ Google ไม่รู้จัก มักเกิดจากลบ deployment ทิ้งหรือคัดลอกลิงก์ของ "
                    "deployment เก่า ให้ไป Deploy > Manage deployments แล้วคัดลอก "
                    "Web app URL อันปัจจุบันมาตั้งใหม่", "sheet_not_found")
        return (f"ชีทตอบกลับด้วยรหัส {status}" + tail, "sheet_http")
    if isinstance(err, TimeoutError) or isinstance(getattr(err, "reason", None), TimeoutError):
        return (f"ชีทไม่ตอบภายใน {SHEET_TIMEOUT} วินาที" + tail, "timeout")
    if isinstance(err, ValueError):
        # urlopen โยน ValueError เมื่อ URL ไม่ใช่ลิงก์ (เช่นติดเครื่องหมายคำพูดมาด้วย)
        return ("ค่า SHEET_WEBHOOK_URL ไม่ใช่ลิงก์ที่ถูกต้อง ต้องขึ้นต้นด้วย https:// "
                "และลงท้ายด้วย /exec", "bad_url")
    return ("ติดต่อชีทไม่ได้ (เครือข่ายมีปัญหา)" + tail, "network")


@app.post("/sheet/{job_id}")
async def save_to_sheet(job_id: str, request: Request):
    """บันทึกผลตรวจลงชีท "บันทึกการตรวจ E-thesis" (เจ้าหน้าที่กดเอง ไม่บังคับ)

    เซิร์ฟเวอร์คิดค่าทุกช่องเอง หน้าเว็บส่งมาได้แค่คำตัดสินของเจ้าหน้าที่ (ชุดเดียวกับ
    ที่ใช้สร้างข้อความสรุป) จะได้ไม่มีทางที่ชีทกับข้อความที่ส่งนักศึกษาจะขัดกันเอง
    """
    if not SHEET_ENABLED:
        return JSONResponse({"error": "ระบบยังไม่ได้ตั้งค่าการเชื่อมกับชีท",
                             "code": "not_configured"}, status_code=503)
    # ค่าผิดรูปตั้งแต่แรก = ยิงไปกี่ครั้งก็ไม่ผ่าน บอกตั้งแต่ยังไม่ยิงจะได้ไม่เดาจากรหัส HTTP
    url_problem = _sheet_url_problem()
    if url_problem:
        return JSONResponse({"error": url_problem, "code": "bad_url"}, status_code=503)
    job = _get_job(job_id)
    if not job or not job.get("report"):
        return JSONResponse({"error": "ไม่พบผลตรวจ (อาจหมดอายุ) กรุณาตรวจเล่มใหม่",
                             "code": "gone"}, status_code=404)
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    failed = [str(k) for k in (payload.get("failed") or [])][:200]
    passed = [str(k) for k in (payload.get("passed") or [])][:200]
    staff = [str(k) for k in (payload.get("staff") or [])][:50]
    report = job["report"]

    # เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้ตัดสินข้อสีส้ม/เหลืองให้ครบก่อน แล้วค่อยกดปุ่มนี้
    left = sheet_undecided(report, failed, passed)
    if left:
        return JSONResponse({"error": f"ยังมีข้อที่ยังไม่ได้ตัดสิน {len(left)} ข้อ "
                                      "กรุณากดผ่านหรือไม่ผ่านให้ครบก่อนบันทึกลงชีท",
                             "code": "undecided", "undecided": len(left)}, status_code=400)
    # หัวข้อที่ระบบตรวจเองไม่ได้ (โครงสร้างหน้าลงนาม และค่าปรับ) ต้องกดให้ครบเช่นกัน
    # ไม่งั้นช่องผลการพิจารณาจะได้ "เสร็จสิ้น" ทั้งที่อาจต้องเป็น "แจ้งค่าปรับ"
    pending = sheet_staff_pending(report, failed, passed, staff)
    if pending:
        names = ", ".join(str(check.get("item") or "") for check in pending)
        return JSONResponse({"error": f"ยังไม่ได้เลือกหัวข้อของเจ้าหน้าที่: {names} "
                                      "กรุณาเลือกให้ครบก่อนบันทึกลงชีท",
                             "code": "staff_pending",
                             "pending": [str(check.get("item") or "") for check in pending]},
                            status_code=400)

    student_id = str((job.get("approved") or {}).get("student_id") or "").strip()
    if not student_id:
        return JSONResponse({"error": "ไม่มีรหัสนักศึกษาในข้อมูลอนุมัติ จึงหาแถวในชีทไม่ได้",
                             "code": "no_student_id"}, status_code=400)
    row = sheet_row(report, failed, passed, staff)
    try:
        answer = await asyncio.to_thread(_post_to_sheet, {
            "token": SHEET_WEBHOOK_TOKEN,
            "student_id": student_id,
            # กรอกไว้ตั้งแต่หน้าแบบฟอร์มแล้ว (ขั้น "วันดำเนินการ") หน้ารายงานส่งมาไม่ได้
            # วันที่เข้าคิวใช้ชี้ว่าแถวไหน (นักศึกษาคนเดียวส่งได้หลายรอบในเดือนเดียวกัน)
            # ส่วนวันที่ตรวจเขียนลงชีทให้เลย ตามที่เจ้าหน้าที่กรอก
            "queue_date": _iso_date(job.get("queue_date")),
            "checked_date": _iso_date(job.get("checked_date")),
            "decision": row["decision"],
            "pass_or_not": row["pass_or_not"],
            "flags": row["flags"],
            "note": row["note"],
            "overwrite": bool(payload.get("overwrite")),
        })
    except Exception as err:                      # noqa: BLE001 — ต้องไม่ล้มทั้งหน้า
        message, code = _sheet_failure(err)
        # ลง log ของเซิร์ฟเวอร์ด้วย เจ้าหน้าที่จะได้ส่งสาเหตุมาได้โดยไม่ต้องส่ง URL
        print(f"[sheet] {code}: {type(err).__name__}", flush=True)
        return JSONResponse({"error": message, "code": code}, status_code=502)
    if not answer.get("ok"):
        return JSONResponse({"error": str(answer.get("error") or "บันทึกลงชีทไม่สำเร็จ"),
                             "code": str(answer.get("code") or "sheet_error"),
                             "needs_overwrite": bool(answer.get("needs_overwrite")),
                             "queues": [str(q) for q in (answer.get("queues") or [])][:20],
                             "current": str(answer.get("current") or "")}, status_code=409
                            if answer.get("needs_overwrite") else 400)
    return {"ok": True, "tab": str(answer.get("tab") or ""), "row": answer.get("row"),
            "queue": str(answer.get("queue") or ""),
            # ช่องที่แท็บนั้นไม่มีหัวตาราง = ติ๊กไม่ลง ต้องบอก ไม่ใช่ปล่อยให้หายเงียบ ๆ
            "missing": [str(name) for name in (answer.get("missing") or [])][:20],
            "decision": row["decision"], "flags": row["flags"], "note": row["note"]}


@app.get("/result/{job_id}", response_class=HTMLResponse)
def result(request: Request, job_id: str):
    _prune_jobs()   # รายงานหมดอายุแล้วต้องไม่เปิดได้ แม้ตัวกวาดยังไม่ถึงรอบ
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
        # วันดำเนินการที่เจ้าหน้าที่กรอกไว้ตอนสั่งตรวจ — แสดงบนหัวรายงานให้เทียบกับชีทได้
        "queue_date": _date_label(job.get("queue_date")),
        "checked_date": _date_label(job.get("checked_date")),
        "has_book": _book_path(job) is not None,
        "sheet_enabled": SHEET_ENABLED,
    })


def _book_path(job):
    """ไฟล์รูปเล่มของผลตรวจนี้ที่ยังอยู่บนดิสก์ หรือ None"""
    path = (job or {}).get("pdf_path")
    return Path(path) if path and Path(path).is_file() else None


@app.get("/book/{job_id}")
def book_file(job_id: str):
    """เปิดไฟล์รูปเล่มที่ตรวจ ในแท็บใหม่ด้วยตัวอ่าน PDF ของเบราว์เซอร์

    อยู่หลังด่านล็อกอินเหมือนทุกหน้า (require_login) และหาไฟล์จาก job เท่านั้น ไม่รับ
    ชื่อไฟล์จากคำขอ จึงเปิดไฟล์อื่นบนเครื่องไม่ได้ ห้ามแคช เพราะเป็นงานที่ยังไม่เผยแพร่
    """
    _prune_jobs()   # ไฟล์หมดอายุแล้วต้องไม่เปิดได้ แม้ตัวกวาดยังไม่ถึงรอบ
    job = _get_job(job_id)
    path = _book_path(job)
    if path is None:
        return HTMLResponse(
            "<h3>ไม่พบไฟล์รูปเล่มแล้ว</h3>"
            f"<p>ระบบเก็บไฟล์ไว้ไม่เกิน {JOB_TTL // 3600} ชั่วโมงหลังตรวจ และเก็บเฉพาะเล่มล่าสุด "
            "กรุณาตรวจเล่มนี้ใหม่อีกครั้งเพื่อเปิดดูไฟล์</p>"
            "<a href='/'>&larr; กลับไปตรวจใหม่</a>", status_code=404)
    return FileResponse(
        path, media_type="application/pdf",
        filename=job.get("pdf_name") or "book.pdf", content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@app.get("/health")
def health():
    return {"status": "ok"}
