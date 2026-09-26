#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ผูกเลขเวอร์ชันของ tools/sheet_webhook.gs ไว้กับเนื้อไฟล์

สคริปต์ในชีทต้องวางเองและ deploy เอง ระบบจึงเตือนเมื่อชีทตอบกลับมาด้วยเลขเวอร์ชัน
คนละตัวกับที่ repo มี — ตัวเตือนนั้นเชื่อถือได้ก็ต่อเมื่อ "แก้ไฟล์แล้วขยับเลขเสมอ"
ถ้าลืมขยับ เลขจะยังตรงกันทั้งที่โค้ดคนละตัว แล้วเจ้าหน้าที่จะเห็นว่า "deploy แล้ว
ไม่มีอะไรเปลี่ยน" โดยไม่มีอะไรฟ้อง (เจอจริง ก.ย. 2569 รอบ setRowHeight)

    python tools/sheet_webhook_lock.py          # ตรวจว่าตรงไหม
    python tools/sheet_webhook_lock.py --save   # บันทึกค่าใหม่หลังขยับเลขเวอร์ชันแล้ว
"""
import hashlib
import io
import json
import re
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
SCRIPT = TOOLS / "sheet_webhook.gs"
LOCK = TOOLS / "sheet_webhook.lock.json"
_VERSION_LINE = re.compile(r"^var SCRIPT_VERSION = '([^']+)';\s*$", re.M)


def version_and_hash():
    """เลขเวอร์ชันในไฟล์ กับลายนิ้วมือของ "เนื้อไฟล์ที่ไม่รวมบรรทัดเลขเวอร์ชัน"

    ไม่รวมบรรทัดเลขเวอร์ชัน เพราะการขยับเลขอย่างเดียวไม่ใช่การแก้พฤติกรรม
    """
    text = io.open(SCRIPT, encoding="utf-8", newline="").read()
    found = _VERSION_LINE.search(text)
    if not found:
        raise SystemExit("หาบรรทัด var SCRIPT_VERSION ในไฟล์ไม่เจอ")
    body = _VERSION_LINE.sub("", text)
    return found.group(1), hashlib.sha256(body.encode("utf-8")).hexdigest()


def saved():
    return json.load(io.open(LOCK, encoding="utf-8"))


def problems():
    """รายการปัญหา ('' = ตรงกันดี)"""
    version, digest = version_and_hash()
    lock = saved()
    out = []
    if lock.get("version") != version:
        out.append(f"เลขเวอร์ชันในไฟล์ ({version}) ไม่ตรงกับที่บันทึกไว้ ({lock.get('version')})")
    if lock.get("sha256") != digest:
        out.append("เนื้อไฟล์เปลี่ยนไปจากตอนบันทึกครั้งล่าสุด")
    return out


def main():
    if "--save" in sys.argv:
        version, digest = version_and_hash()
        io.open(LOCK, "w", encoding="utf-8", newline="\n").write(
            json.dumps({"version": version, "sha256": digest},
                       ensure_ascii=False, indent=2) + "\n")
        print(f"บันทึกแล้ว: {version}")
        return 0
    left = problems()
    for line in left:
        print("  " + line)
    if left:
        print("แก้ไฟล์ .gs แล้วต้อง 1) ขยับ var SCRIPT_VERSION "
              "2) รัน python tools/sheet_webhook_lock.py --save")
        return 1
    print("เลขเวอร์ชันตรงกับเนื้อไฟล์")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
