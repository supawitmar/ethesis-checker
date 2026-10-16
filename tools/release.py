# -*- coding: utf-8 -*-
"""ออกรุ่นอัปเดตให้ทุกเครื่องติดตั้ง — python tools/release.py 1.1

เจ้าของขอ (9 ต.ค. 2569): ปรับกฎเมื่อไหร่ ให้ออกเป็น "อัปเดต 1.1 วันที่ ..." ที่ทุกเครื่องโหลดไปติดตั้งได้
ขั้นตอนเต็มอยู่ใน CHANGELOG.md หัวไฟล์ — ตัวนี้ทำเฉพาะส่วนที่พลาดง่าย:
  - ตรวจว่าโฟลเดอร์ไม่มีไฟล์แก้ค้าง (รุ่นที่ออกต้องเป็นโค้ดที่ commit และผ่านด่านแล้วเท่านั้น)
  - ตรวจว่าหัวข้อบนสุดของ CHANGELOG.md คือ "## อัปเดต <เลขนี้> — <วันที่>"
  - เลขใหม่ต้องมากกว่ารุ่นล่าสุดที่ออก และยังไม่เคยใช้
  - สร้างแท็ก v<เลข> (annotated) ที่ HEAD — ไม่ push เอง (เจ้าของสั่ง push เท่านั้น)
  - สร้างแพ็กเกจอัปเดตแบบ manual dist/อัปเดต <เลข>.zip (เจ้าของขอ 16 ต.ค. 2569 ให้มีเหมือนระบบปรับเอกสาร):
    โค้ดของแท็กนั้น (git archive) + "อ่านก่อน" บอกว่าเปลี่ยนอะไรและวิธีติดตั้ง — ส่งทาง LINE/อีเมล/แฟลชไดรฟ์ได้
    แตกไฟล์แล้วดับเบิลคลิก ติดตั้งอัปเดตจากโฟลเดอร์นี้.bat · สร้างใหม่ของรุ่นที่ออกแล้ว: python tools/release.py --package 1.2.1
"""
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_local  # noqa: E402

VERSION = re.compile(r"^(\d+)\.(\d+)(?:\.(\d+))?$")


def _git(code, *args):
    return subprocess.run(["git", *args], cwd=code, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def release(version_text, code=run_local.CODE, say=run_local.say):
    match = VERSION.match(version_text.strip())
    if not match:
        say(f"เลขรุ่นต้องเป็นแบบ 1.3 หรือ 1.2.1 (ได้ {version_text!r})")
        return 1
    version = run_local.version_of(match)
    tag = f"v{version_text.strip()}"
    code = Path(code)

    status = _git(code, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stdout.strip():
        say("ออกรุ่นไม่ได้: มีไฟล์ที่ยังไม่ได้ commit — commit และผ่านด่านตรวจก่อน")
        say(status.stdout.rstrip() or status.stderr.strip())
        return 1

    try:
        text = (code / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        say("ออกรุ่นไม่ได้: ไม่พบ CHANGELOG.md")
        return 1
    entries = run_local.changelog_entries(text)
    if not entries or entries[0][0] != version:
        top = f"อัปเดต {run_local._label(entries[0][0])}" if entries else "(ไม่มีหัวข้อ)"
        say(f"ออกรุ่นไม่ได้: หัวข้อบนสุดของ CHANGELOG.md ต้องเป็น \"## อัปเดต {version_text} — วันที่\""
            f" (ตอนนี้คือ {top})")
        return 1
    if not entries[0][2]:
        say("ออกรุ่นไม่ได้: หัวข้อนี้ยังไม่มีรายการว่าเปลี่ยนอะไร")
        return 1

    existing = run_local.release_tags(lambda *args: _git(code, *args))
    if any(name == tag for _, name in existing):
        say(f"ออกรุ่นไม่ได้: มีแท็ก {tag} อยู่แล้ว")
        return 1
    if existing and version <= existing[-1][0]:
        say(f"ออกรุ่นไม่ได้: เลขต้องมากกว่ารุ่นล่าสุด ({existing[-1][1]})")
        return 1

    date = entries[0][1]
    tagged = _git(code, "tag", "-a", tag, "-m", f"อัปเดต {version_text} — {date}")
    if tagged.returncode != 0:
        say("สร้างแท็กไม่สำเร็จ\n" + tagged.stderr.strip())
        return 1
    head = _git(code, "rev-parse", "--short=7", "HEAD").stdout.strip()
    say(f"ออกอัปเดต {version_text} — {date} ที่ {head} แล้ว (แท็ก {tag} ยังอยู่ในเครื่องนี้)")
    package = build_package(version_text, code=code, say=say)
    if package:
        say(f"แพ็กเกจอัปเดตแบบ manual: {package}")
    say(f"ส่งขึ้น GitHub: git push origin main {tag}")
    return 0


README = """ระบบตรวจเล่ม — อัปเดต {version} ({date})
================================================

สิ่งที่เปลี่ยนในรุ่นนี้
{notes}

วิธีอัปเดต (ไม่ต้องใช้ Git และไม่ต้องต่ออินเทอร์เน็ต)
  1. แตกไฟล์ ZIP นี้ก่อน (คลิกขวา → Extract All)
  2. เปิดโฟลเดอร์ที่ได้ แล้วดับเบิลคลิก ติดตั้งอัปเดตจากโฟลเดอร์นี้.bat
  3. ตัวติดตั้งหาระบบที่ติดตั้งไว้ในเครื่องเอง (ไม่เจอ = ให้ลากโฟลเดอร์ระบบมาวางในหน้าต่าง)
     บอกรุ่นเดิม → รุ่นใหม่ ให้กด Enter ยืนยัน แล้ว ปิดระบบ → ติดตั้งทับ → เปิดระบบรุ่นใหม่ให้เอง
  4. เสร็จแล้วลบโฟลเดอร์ที่แตกไว้ได้ — หัวจอของระบบจะขึ้นป้าย v{version}

ไม่ถูกเขียนทับ: รหัสผ่านและค่าการบันทึกลงชีท (.env) และแพ็กเกจ Python ที่ติดตั้งไว้ (.venv)

เครื่องที่ต่ออินเทอร์เน็ตอัปเดตได้อีกทาง: กด "อัปเดตเลย" บนแถบ "มีอัปเดตใหม่" ในหน้าแรกของระบบ
หรือดับเบิลคลิก อัปเดตระบบตรวจเล่ม.bat ในโฟลเดอร์ระบบ
"""


def build_package(version_text, code=run_local.CODE, say=run_local.say):
    """dist/อัปเดต <เลข>.zip = โค้ดของแท็ก v<เลข> + ไฟล์ "อ่านก่อน" — คืน path หรือ None"""
    code = Path(code)
    tag = f"v{version_text.strip()}"
    folder = f"ระบบตรวจเล่ม อัปเดต {version_text}"
    entries = []
    shown = _git(code, "show", f"{tag}:CHANGELOG.md")
    if shown.returncode == 0:
        entries = [e for e in run_local.changelog_entries(shown.stdout)
                   if run_local._label(e[0]) == version_text.strip()]
    if not entries:
        say(f"สร้างแพ็กเกจไม่ได้: ไม่พบแท็ก {tag} หรือหัวข้อ อัปเดต {version_text} ใน CHANGELOG.md ของแท็กนั้น")
        return None
    _version, date, body = entries[0]
    dist = code / "dist"
    dist.mkdir(exist_ok=True)
    target = dist / f"อัปเดต {version_text}.zip"
    with tempfile.TemporaryDirectory() as work:
        raw = Path(work) / "raw.zip"
        made = _git(code, "archive", "--format=zip", f"--prefix={folder}/", "-o", str(raw), tag)
        if made.returncode != 0:
            say("สร้างแพ็กเกจไม่สำเร็จ\n" + made.stderr.strip())
            return None
        readme = README.format(version=version_text, date=date, notes=body.strip())
        with zipfile.ZipFile(raw, "a", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(f"{folder}/อ่านก่อน - อัปเดต {version_text}.txt",
                             "\ufeff" + readme.replace("\n", "\r\n"))
        target.write_bytes(raw.read_bytes())
    return target


if __name__ == "__main__":
    run_local._utf8_output()
    if len(sys.argv) == 3 and sys.argv[1] == "--package":
        sys.exit(0 if build_package(sys.argv[2]) else 1)
    if len(sys.argv) != 2:
        run_local.say("ใช้: python tools/release.py <เลขรุ่น เช่น 1.3 หรือ 1.2.1>  ·  python tools/release.py --package <เลขรุ่น>")
        sys.exit(2)
    sys.exit(release(sys.argv[1]))
