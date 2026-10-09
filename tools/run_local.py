# -*- coding: utf-8 -*-
"""เปิดระบบตรวจเล่มในเครื่องนี้ — ตัวที่ "เปิดระบบตรวจเล่ม.bat" กับ "อัปเดตระบบตรวจเล่ม.bat" เรียก

ทำไมต้องรันในเครื่อง (8 ต.ค. 2569): เล่มมีข้อมูลส่วนบุคคลของนักศึกษา (ชื่อ รหัส หน้าลงนาม
เนื้อหาที่ยังไม่เผยแพร่) ตาม PDPA ไม่ควรอัปขึ้นตรวจบนคลาวด์ต่างประเทศ เจ้าของจึงย้ายจาก
Render มาใช้ในเครื่องตัวเอง 2 เครื่อง — ไฟล์เล่มไม่ออกจากเครื่อง ผลตรวจเหมือนบนคลาวด์ทุกอย่าง
(โค้ดเดียวกัน แพ็กเกจตรึงเวอร์ชันใน requirements.txt) ส่วนปุ่ม "บันทึกลงชีท" ยังใช้ได้ถ้าตั้ง
SHEET_WEBHOOK_URL/SHEET_WEBHOOK_TOKEN ใน .env ของเครื่องนั้น

เปิดระบบ (ไม่มีอาร์กิวเมนต์):
  1. สร้าง .venv และติดตั้งแพ็กเกจ — ครั้งแรก และทุกครั้งที่ requirements.txt เปลี่ยน
  2. ยังไม่มีรหัสผ่านใน .env ให้ตั้ง (ถามในหน้าต่าง) และเติม SESSION_SECRET แบบสุ่มให้
  3. รัน uvicorn ที่ 127.0.0.1 เท่านั้น — เครื่องอื่นในเครือข่ายเข้าไม่ได้ (ห้ามเปลี่ยนเป็น 0.0.0.0)
  4. เปิดเบราว์เซอร์เมื่อระบบพร้อม · ถ้าเปิดค้างอยู่แล้วจากหน้าต่างอื่น แค่เปิดเบราว์เซอร์ให้
--update: ติดตั้งรุ่นล่าสุดที่ออกแล้ว (แท็ก v*) ด้วย fast-forward แล้วแสดงว่าเปลี่ยนอะไร
          จาก CHANGELOG.md (ไม่แตะเครื่องที่มีไฟล์แก้ค้างอยู่)
"""
import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
HOST = "127.0.0.1"
FIRST_PORT = 8000
PORT_TRIES = 10
MIN_PYTHON = (3, 10)
VENV = CODE / ".venv"
ENV_FILE = CODE / ".env"
ENV_EXAMPLE = CODE / ".env.example"
REQUIREMENTS = CODE / "requirements.txt"
# ลายนิ้วมือของ requirements.txt ที่ติดตั้งลง .venv ไปแล้ว — ไม่ตรง = ติดตั้งใหม่
STAMP_NAME = "ethesis-requirements.sha256"
MIN_PASSWORD = 8
# uvicorn อ่าน .env ด้วย python-dotenv: ค่าที่ไม่ใส่เครื่องหมายคำพูด ช่องว่างตามด้วย # คือคอมเมนต์
# และ ${...} ถูกแทนค่า — รหัสที่ตั้งผ่านตัวนี้จึงห้ามมีอักษรเหล่านี้ ค่าที่อ่านกลับจะได้ตรงกับที่พิมพ์
PASSWORD_FORBIDDEN = set("'\"\\#$`")
READY_SECONDS = 90


def say(text=""):
    print(text, flush=True)


def venv_python(venv=VENV):
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def requirements_hash(path=REQUIREMENTS):
    # git บน Windows แปลงท้ายบรรทัดเป็น CRLF — ไม่ให้ติดตั้งใหม่เพียงเพราะเครื่องคนละแบบ
    return hashlib.sha256(Path(path).read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def ensure_venv(venv=VENV, requirements=REQUIREMENTS, run=subprocess.run):
    """สร้าง .venv ถ้ายังไม่มี และติดตั้งแพ็กเกจเมื่อ requirements.txt เปลี่ยน คืน python ของ .venv"""
    python = venv_python(venv)
    if not python.exists():
        say("ติดตั้งครั้งแรก: สร้างสภาพแวดล้อม Python ของระบบ (ทำครั้งเดียว)")
        run([sys.executable, "-m", "venv", str(venv)], check=True)
    stamp = Path(venv) / STAMP_NAME
    wanted = requirements_hash(requirements)
    try:
        if stamp.read_text(encoding="utf-8").strip() == wanted:
            return python
    except OSError:
        pass
    say("ติดตั้งแพ็กเกจที่ระบบต้องใช้ (ต้องต่ออินเทอร์เน็ต ใช้เวลาไม่กี่นาที)")
    run([str(python), "-m", "pip", "install", "--disable-pip-version-check",
         "-r", str(requirements)], check=True)
    # เขียนหลังติดตั้งสำเร็จเท่านั้น — ติดตั้งค้างกลางทาง รอบหน้าจะติดตั้งใหม่
    stamp.write_text(wanted, encoding="utf-8")
    return python


def read_env(path=ENV_FILE):
    """อ่าน .env แบบเดียวกับ python-dotenv เท่าที่ไฟล์ของระบบนี้ใช้ (KEY=ค่า, คอมเมนต์, คำพูด)"""
    path = Path(path)
    return read_env_text(path.read_text(encoding="utf-8-sig")) if path.exists() else {}


def read_env_text(text):
    values = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] in "'\"" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = re.split(r"\s+#", value, maxsplit=1)[0].strip()
        values[key.strip()] = value
    return values


def password_problem(password):
    """ข้อความบอกว่ารหัสนี้ใช้ไม่ได้เพราะอะไร หรือ None ถ้าใช้ได้"""
    if len(password) < MIN_PASSWORD:
        return f"รหัสผ่านต้องยาวอย่างน้อย {MIN_PASSWORD} ตัวอักษร"
    if any(ch.isspace() for ch in password):
        return "รหัสผ่านห้ามมีช่องว่าง"
    bad = sorted(PASSWORD_FORBIDDEN.intersection(password))
    if bad:
        return "รหัสผ่านห้ามมีเครื่องหมาย " + " ".join(bad)
    return None


def _set_line(text, key, value):
    """แทนบรรทัด KEY=... ที่ไม่ใช่คอมเมนต์ ถ้าไม่มีให้ต่อท้าย"""
    pattern = re.compile(rf"(?m)^[ \t]*(?:export[ \t]+)?{re.escape(key)}[ \t]*=.*$")
    line = f"{key}={value}"
    if pattern.search(text):
        return pattern.sub(lambda _: line, text, count=1)
    if text and not text.endswith("\n"):
        text += "\n"
    return text + line + "\n"


def write_env(password=None, path=ENV_FILE, example=ENV_EXAMPLE):
    """ตั้ง APP_PASSWORD (ถ้าส่งมา) และเติม SESSION_SECRET แบบสุ่มเมื่อยังไม่มี

    ยังไม่มี .env ให้ตั้งต้นจาก .env.example เพื่อให้คำอธิบายตัวแปรอื่นอยู่ในไฟล์ด้วย
    บรรทัดอื่นในไฟล์ไม่แตะ (เช่น SHEET_WEBHOOK_URL ที่เจ้าของใส่เอง)
    """
    path, example = Path(path), Path(example)
    if path.exists():
        text = path.read_text(encoding="utf-8-sig")
    elif example.exists():
        text = example.read_text(encoding="utf-8-sig")
    else:
        text = ""
    if password is not None:
        text = _set_line(text, "APP_PASSWORD", password)
    if not read_env_text(text).get("SESSION_SECRET"):
        text = _set_line(text, "SESSION_SECRET", secrets.token_urlsafe(48))
    path.write_text(text, encoding="utf-8", newline="\n")


def ask_password(prompt=getpass.getpass):
    say("ตั้งรหัสผ่านเข้าระบบของเครื่องนี้ (พิมพ์แล้วจะไม่ขึ้นบนจอ เป็นเรื่องปกติ)")
    while True:
        first = prompt("รหัสผ่าน: ")
        problem = password_problem(first)
        if problem:
            say(problem + " — ลองใหม่")
            continue
        if prompt("พิมพ์ซ้ำอีกครั้ง: ") != first:
            say("สองครั้งไม่ตรงกัน — ลองใหม่")
            continue
        return first


def ensure_env(path=ENV_FILE, example=ENV_EXAMPLE, prompt=getpass.getpass):
    values = read_env(path)
    password = None if values.get("APP_PASSWORD") else ask_password(prompt)
    if password is not None or not values.get("SESSION_SECRET"):
        write_env(password, path, example)
        if password is not None:
            say("บันทึกรหัสผ่านไว้ในไฟล์ .env ของเครื่องนี้แล้ว (ไฟล์นี้ไม่ขึ้น GitHub)")
    values = read_env(path)
    if values.get("COOKIE_SECURE", "").strip().lower() in {"1", "true", "yes", "on"}:
        say("คำเตือน: .env ตั้ง COOKIE_SECURE ไว้ — รันในเครื่องผ่าน http จะล็อกอินไม่ติด ให้ลบบรรทัดนั้นออก")
    if not (values.get("SHEET_WEBHOOK_URL") and values.get("SHEET_WEBHOOK_TOKEN")):
        say("หมายเหตุ: ยังไม่ได้ตั้ง SHEET_WEBHOOK_URL / SHEET_WEBHOOK_TOKEN ใน .env "
            "— ตรวจเล่มได้ตามปกติ แต่ปุ่ม \"บันทึกลงชีท\" จะไม่ขึ้น")
    return values


def checker_running(port, host=HOST, timeout=2):
    """พอร์ตนี้มีระบบตรวจเล่ม (ตัวไหนก็ได้) เปิดอยู่แล้วหรือไม่ — ดูจาก /health"""
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("status") == "ok" and "started" in data


def port_free(port, host=HOST):
    # Windows ยอมให้ bind 127.0.0.1 ซ้อนกับโปรแกรมที่ฟัง 0.0.0.0 อยู่แล้ว จึงต้องลองต่อด้วย
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.5)
        if probe.connect_ex((host, port)) == 0:
            return False
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((host, port))
        except OSError:
            return False
    return True


def choose_port(first=FIRST_PORT, tries=PORT_TRIES, running=checker_running, free=port_free):
    """("running", พอร์ต) ถ้าระบบเปิดอยู่แล้ว · ("free", พอร์ต) พอร์ตว่างตัวแรก · None ถ้าเต็มหมด"""
    for port in range(first, first + tries):
        if running(port):
            return "running", port
        if free(port):
            return "free", port
    return None


def server_command(python, port, env_file=ENV_FILE):
    return [str(python), "-m", "uvicorn", "main:app", "--host", HOST, "--port", str(port),
            "--env-file", str(env_file)]


def _open_when_ready(url, port, process):
    deadline = time.monotonic() + READY_SECONDS
    while time.monotonic() < deadline and process.poll() is None:
        if checker_running(port, timeout=1):
            say(f"\nระบบพร้อมแล้ว: {url}")
            say("ปิดหน้าต่างนี้ = ปิดระบบ (ไฟล์เล่มที่ตรวจไว้อยู่ในเครื่องนี้เท่านั้น)\n")
            webbrowser.open(url)
            return
        time.sleep(0.5)


def serve(python, port, open_browser=True):
    url = f"http://{HOST}:{port}/"
    process = subprocess.Popen(server_command(python, port), cwd=CODE)
    if open_browser:
        threading.Thread(target=_open_when_ready, args=(url, port, process), daemon=True).start()
    try:
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
        return 0


def start(open_browser=True, prompt=getpass.getpass):
    ensure_env(prompt=prompt)
    try:
        python = ensure_venv()
    except (subprocess.CalledProcessError, OSError):
        say("ติดตั้งไม่สำเร็จ — ตรวจว่าต่ออินเทอร์เน็ตอยู่ แล้วเปิดใหม่อีกครั้ง")
        return 1
    found = choose_port()
    if found is None:
        say(f"พอร์ต {FIRST_PORT}-{FIRST_PORT + PORT_TRIES - 1} ถูกใช้หมด ปิดโปรแกรมอื่นแล้วลองใหม่")
        return 1
    state, port = found
    url = f"http://{HOST}:{port}/"
    if state == "running":
        say(f"ระบบเปิดอยู่แล้วในอีกหน้าต่างหนึ่ง: {url}")
        if open_browser:
            webbrowser.open(url)
        return 0
    say(f"กำลังเปิดระบบที่ {url} ...")
    return serve(python, port, open_browser)


def _git(git, *args):
    return subprocess.run([git, *args], cwd=CODE, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


# รุ่นที่ออกให้เครื่องอื่นติดตั้ง = git tag ชื่อ v<หลัก>.<รอง> คู่กับหัวข้อใน CHANGELOG.md
# "## อัปเดต 1.1 — 10 ต.ค. 2569" (เจ้าของขอ 9 ต.ค. 2569 ให้ปรับกฎแล้วออกเป็นอัปเดตมีเลขรุ่นและวันที่)
# ตัวอัปเดตดึงเฉพาะรุ่นที่ออกแล้ว — งานที่ push ขึ้น main แต่ยังไม่ออกรุ่นไม่ลงเครื่องที่ใช้งาน
RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)$")
CHANGELOG_HEADING = re.compile(r"(?m)^## อัปเดต (\d+)\.(\d+)\s*[—-]\s*(.+?)\s*$")


def release_tags(git_run):
    """รุ่นทั้งหมดที่มี เรียงจากเก่าไปใหม่: [((หลัก, รอง), "v1.0"), ...]"""
    listed = git_run("tag", "--list", "v*")
    found = []
    for name in listed.stdout.split():
        match = RELEASE_TAG.match(name.strip())
        if match:
            found.append(((int(match.group(1)), int(match.group(2))), name.strip()))
    return sorted(found)


def changelog_entries(text):
    """[((หลัก, รอง), "วันที่", "เนื้อหา"), ...] ตามลำดับในไฟล์"""
    heads = list(CHANGELOG_HEADING.finditer(text))
    entries = []
    for i, head in enumerate(heads):
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        entries.append(((int(head.group(1)), int(head.group(2))), head.group(3),
                        text[head.end():end].strip()))
    return entries


def _label(version):
    return f"{version[0]}.{version[1]}"


def _say_notes(after, upto, path=None):
    try:
        text = Path(path or CODE / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return
    for version, date, body in changelog_entries(text):
        if (after is None or version > after) and version <= upto:
            say(f"\n=== อัปเดต {_label(version)} — {date} ===")
            say(body)


def update(which=shutil.which, git_run=None):
    git = which("git")
    if not git or not (CODE / ".git").exists():
        say("อัปเดตไม่ได้: ไม่พบ git หรือโฟลเดอร์นี้ไม่ได้มาจาก git clone (ดู DEPLOY.md หัวข้อ 4)")
        return 1
    git_run = git_run or (lambda *args: _git(git, *args))
    status = git_run("status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0:
        say("อัปเดตไม่ได้: git อ่านสถานะโฟลเดอร์ไม่ได้\n" + status.stderr.strip())
        return 1
    if status.stdout.strip():
        say("ไม่อัปเดตให้ เพราะมีไฟล์ในโฟลเดอร์ถูกแก้ค้างอยู่ (กันงานที่แก้ไว้หาย):")
        say(status.stdout.rstrip())
        return 1
    fetched = git_run("fetch", "--tags", "origin")
    if fetched.returncode != 0:
        say("ดึงรุ่นใหม่ไม่สำเร็จ — ตรวจอินเทอร์เน็ต หรือการล็อกอิน GitHub ของเครื่องนี้")
        say((fetched.stdout + fetched.stderr).strip())
        return 1
    releases = release_tags(git_run)
    if not releases:
        say("ยังไม่มีอัปเดตใหม่ — เครื่องนี้ใช้รุ่นปัจจุบันต่อได้เลย (ยังไม่เคยออกอัปเดต ดู CHANGELOG.md)")
        return 0
    newest, tag = releases[-1]
    have = None
    for version, name in reversed(releases):
        if git_run("merge-base", "--is-ancestor", name, "HEAD").returncode == 0:
            have = version
            break
    if have == newest:
        say(f"เครื่องนี้เป็นรุ่นล่าสุดแล้ว: อัปเดต {_label(newest)}")
        return 0
    merged = git_run("merge", "--ff-only", tag)
    if merged.returncode != 0:
        say(f"ติดตั้งอัปเดต {_label(newest)} ไม่ได้ — เครื่องนี้มี commit ของตัวเองที่ไม่อยู่ในรุ่นที่ออก")
        say((merged.stdout + merged.stderr).strip())
        return 1
    try:
        ensure_venv()
    except (subprocess.CalledProcessError, OSError):
        say("ติดตั้งโค้ดแล้ว แต่ติดตั้งแพ็กเกจไม่สำเร็จ — เปิดระบบอีกครั้งตอนต่ออินเทอร์เน็ต")
        return 1
    head = git_run("rev-parse", "--short=7", "HEAD").stdout.strip()
    say(f"ติดตั้งอัปเดต {_label(newest)} แล้ว (รหัส {head} ตรงกับท้ายหน้าเว็บ)")
    _say_notes(have, newest)
    say("\nถ้าระบบเปิดค้างอยู่ ให้ปิดหน้าต่างของระบบแล้วเปิดใหม่ จึงจะใช้รุ่นใหม่")
    return 0


def _utf8_output():
    # ส่งออกไปไฟล์/ท่อ (ไม่ใช่หน้าต่าง console) Python บน Windows ใช้ cp1252 แล้วพังที่อักษรไทยตัวแรก
    # (เจอจริงตอนทดสอบ 8 ต.ค. 2569) — หน้าต่าง console เขียนอักษรไทยได้เองอยู่แล้วจึงไม่แตะ
    for stream in (sys.stdout, sys.stderr):
        try:
            if not stream.isatty():
                stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError):
            pass


def main(argv=None):
    _utf8_output()
    parser = argparse.ArgumentParser(description="เปิด/อัปเดตระบบตรวจเล่มในเครื่องนี้")
    parser.add_argument("--update", action="store_true", help="ดึงรุ่นใหม่จาก GitHub")
    parser.add_argument("--no-browser", action="store_true", help="ไม่เปิดเบราว์เซอร์ให้")
    args = parser.parse_args(argv)
    if sys.version_info < MIN_PYTHON:
        say("ต้องใช้ Python {}.{} ขึ้นไป (เครื่องนี้เป็น {}) — ติดตั้งจาก python.org".format(
            *MIN_PYTHON, sys.version.split()[0]))
        return 1
    if args.update:
        return update()
    return start(open_browser=not args.no_browser)


if __name__ == "__main__":
    sys.exit(main())
