#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ไล่ใช้งานจริงผ่าน HTTP ทั้งเส้นทาง แล้วยืนยันว่าปุ่มบนหน้ารายงานยังทำงาน

ด่านที่ห้า (เจ้าหน้าที่สั่งเพิ่ม ก.ย. 2569) — สี่ด่านเดิมตรวจ "กฎ" กับ "คำแปล"
แต่ไม่มีด่านไหนกดปุ่มจริง บั๊กที่หลุดมาแล้วทั้งหมดอยู่ตรงรอยต่อระหว่างหน้าเว็บกับ
เซิร์ฟเวอร์ ซึ่งเทสต์ระดับหน่วยมองไม่เห็น

    python tools/smoke_web.py           # ใช้เล่มทดสอบเล่มแรกที่หาเจอ
    python tools/smoke_web.py 3         # ระบุเล่ม

สิ่งที่ไล่จริง
    เข้าสู่ระบบ -> อ่านไฟล์ eThesis -> สั่งตรวจ -> รอจนเสร็จ -> เปิดหน้ารายงาน
    -> กดปุ่มของเจ้าหน้าที่ผ่าน POST /summary แล้วตรวจว่าผลที่ได้ขยับตามที่กด

เล่มทดสอบเป็นวิทยานิพนธ์จริงของนักศึกษา จึงไม่อยู่ใน repo — เครื่องที่ไม่มีข้อมูล
ทดสอบจะข้ามด่านนี้พร้อมบอกวิธีเตรียม ไม่พัง (กติกาเดียวกับ regress_books.py)
"""
import json
import os
import sys
import time
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
PROJECT = CODE.parent
TESTDATA = Path(os.environ.get("ETHESIS_TEST_DIR") or (PROJECT / "test"))
BOOKS = TESTDATA / "testing"
ETHESIS = TESTDATA / "ethesis"

# รหัสผ่านของด่านนี้เท่านั้น ไม่เกี่ยวกับรหัสจริงของระบบ — ตั้งก่อน import main
os.environ.setdefault("APP_PASSWORD", "smoke-test-password")
os.environ.setdefault("MAX_UPLOAD_MB", "50")
sys.path.insert(0, str(CODE))
os.chdir(CODE)


def available_books():
    return [p.stem for p in sorted(BOOKS.glob("*.pdf"))
            if (ETHESIS / f"{p.stem}.pdf").exists()] if BOOKS.exists() else []


def check(label, ok, detail=""):
    print(f"   {'ผ่าน ' if ok else 'ตก   '} {label}" + (f" — {detail}" if detail else ""))
    return ok


def main():
    wanted = next((a for a in sys.argv[1:] if a.isdigit()), None)
    books = available_books()
    if not books:
        print("ข้ามด่านนี้ — ไม่มีเล่มทดสอบในเครื่องนี้")
        print(f"  มองหาที่: {BOOKS}  และ  {ETHESIS}")
        print("  เล่มทดสอบเป็นวิทยานิพนธ์จริงของนักศึกษา จึงไม่อยู่ใน repo")
        return 0
    book = wanted if wanted in books else books[0]

    from fastapi.testclient import TestClient          # noqa: E402
    import main as app_main                            # noqa: E402
    import ethesis_import                              # noqa: E402

    client = TestClient(app_main.app, raise_server_exceptions=False)
    passed = []
    print(f"ไล่เส้นทางการใช้งานจริงด้วยเล่มทดสอบ {book}")

    login = client.post("/login",
                        data={"password": os.environ["APP_PASSWORD"], "next": "/"},
                        follow_redirects=False)
    passed.append(check("เข้าสู่ระบบ", login.status_code == 303, str(login.status_code)))

    eth = ETHESIS / f"{book}.pdf"
    with open(eth, "rb") as fh:
        parsed = client.post("/parse-ethesis",
                             files={"pdf": (eth.name, fh, "application/pdf")})
    passed.append(check("อ่านไฟล์ eThesis", parsed.status_code == 200,
                        str(parsed.status_code)))
    fields = parsed.json().get("data") or {}
    passed.append(check("ได้ชื่อนักศึกษาจากไฟล์ eThesis", bool(fields.get("student_name")),
                        str(fields.get("student_name"))))

    real = ethesis_import.parse_ethesis_pdf(str(eth))
    form = {k: ("" if v is None else str(v)) for k, v in fields.items()
            if not isinstance(v, (dict, list))}
    form["committees_json"] = json.dumps(fields.get("committees") or {},
                                         ensure_ascii=False)
    form["doc_type"] = real.get("doc_type") or "THESIS"
    form["program_language"] = real.get("program_language") or "thai_english"
    form["format"] = str(fields.get("format") or "1")
    form["chapters_mode"] = "strict"

    with open(BOOKS / f"{book}.pdf", "rb") as fh:
        started = client.post("/check", data=form,
                              files={"pdf": (f"{book}.pdf", fh, "application/pdf")})
    passed.append(check("สั่งตรวจ", started.status_code == 200, str(started.status_code)))
    job = started.json().get("job_id")
    passed.append(check("ได้รหัสงาน", bool(job)))
    if not job:
        return 1

    for _ in range(3000):
        if client.get(f"/progress/{job}").json().get("done"):
            break
        time.sleep(0.1)
    else:
        return 1 if not check("ตรวจเสร็จ", False, "หมดเวลารอ") else 0
    passed.append(check("ตรวจเสร็จ", True))

    page = client.get(f"/result/{job}")
    html = page.text
    passed.append(check("เปิดหน้ารายงานได้", page.status_code == 200,
                        str(page.status_code)))
    for box in ("stat-red", "stat-orange", "stat-yellow"):
        passed.append(check(f"หน้ารายงานมีช่องตัวเลข {box}",
                            f'<b id="{box}">' in html))
    passed.append(check("หน้ารายงานเขียนตัวเลขจากเซิร์ฟเวอร์",
                        "renderZoneCounts(data.counts)" in html))
    passed.append(check("ปุ่มผ่านของข้อสังเกตถูกส่งไปด้วย",
                        '[data-zone="YELLOW"]' in html.split("function collectPassed()", 1)[-1][:400]))

    base = client.post(f"/summary/{job}",
                       json={"failed": [], "passed": [], "staff": []})
    passed.append(check("ปลายทางข้อความสรุปตอบกลับ", base.status_code == 200,
                        str(base.status_code)))
    body = base.json()
    passed.append(check("ส่งตัวเลขสามกล่องกลับมา", isinstance(body.get("counts"), dict),
                        str(body.get("counts"))))
    passed.append(check("ผลตรวจตรงกับบรรทัดแรกของข้อความสรุป",
                        body["plain"].split("\n")[0] == "ผลการตรวจ: " + body["verdict"],
                        body["plain"].split("\n")[0]))

    pressed = client.post(f"/summary/{job}",
                          json={"failed": [], "passed": [],
                                "staff": ["SIGNATURE_LAYOUT_WRONG"]}).json()
    passed.append(check("กดปุ่มเจ้าหน้าที่แล้วถ้อยคำเข้าข้อความสรุป",
                        "ในหน้าลงนาม" in pressed["plain"]))
    passed.append(check("กดปุ่มเจ้าหน้าที่แล้วตัวเลขต้องแก้เพิ่มขึ้น",
                        pressed["counts"]["RED"] > body["counts"]["RED"],
                        f'{body["counts"]["RED"]} -> {pressed["counts"]["RED"]}'))

    zones = app_main.JOBS[job]["report"]["issues_by_zone"]
    if zones.get("YELLOW"):
        ok = client.post(f"/summary/{job}",
                         json={"failed": [], "passed": ["YELLOW:0"], "staff": []}).json()
        passed.append(check("กดผ่านข้อสังเกตแล้วตัวเลขข้อสังเกตลดลง",
                            ok["counts"]["YELLOW"] < body["counts"]["YELLOW"],
                            f'{body["counts"]["YELLOW"]} -> {ok["counts"]["YELLOW"]}'))
        fail = client.post(f"/summary/{job}",
                           json={"failed": ["YELLOW:0"], "passed": [], "staff": []}).json()
        passed.append(check("กดไม่ผ่านข้อสังเกตแล้วนับเป็นต้องแก้",
                            fail["counts"]["RED"] > body["counts"]["RED"],
                            f'{body["counts"]["RED"]} -> {fail["counts"]["RED"]}'))

    gone = client.post("/summary/ไม่มีงานนี้", json={})
    passed.append(check("งานที่ไม่มีอยู่ต้องได้ 404 ไม่ใช่ 500",
                        gone.status_code == 404, str(gone.status_code)))

    print()
    if all(passed):
        print(f"ผ่านครบ {len(passed)} ข้อ")
        return 0
    print(f"ตก {passed.count(False)} ข้อ จาก {len(passed)} ข้อ")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
