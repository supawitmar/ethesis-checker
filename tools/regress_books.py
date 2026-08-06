#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""รันเล่มทดสอบจริงกับข้อมูล eThesis จริง แล้วเทียบกับ baseline

ใช้ก่อน commit ทุกครั้งที่แก้กฎ — ถ้าตัวเลขต่างจาก baseline ต้องอธิบายได้ว่าทำไม
ถ้าอธิบายไม่ได้ แปลว่ากฎใหม่ทำให้เล่มที่เคยถูกกลายเป็นผิด (false positive)

    python tools/regress_books.py              # รันทั้ง 3 เล่ม เทียบ baseline
    python tools/regress_books.py 2            # เฉพาะเล่ม 2
    python tools/regress_books.py --detail     # แสดงทุก finding
    python tools/regress_books.py --save       # บันทึกผลปัจจุบันเป็น baseline ใหม่

เล่มทดสอบเป็นวิทยานิพนธ์จริงของนักศึกษา จึง **ไม่อยู่ใน repo** (ห้าม commit)
เครื่องที่ไม่มีข้อมูลทดสอบจะข้ามด่านนี้พร้อมบอกวิธีเตรียม ไม่พัง
ตั้ง ETHESIS_TEST_DIR ชี้ไปโฟลเดอร์อื่นได้ ถ้าไม่ได้เก็บไว้ข้าง ๆ repo
"""
import json
import os
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]              # ...\code (git root)
PROJECT = CODE.parent                                   # ...\e-thesis checker
TESTDATA = Path(os.environ.get("ETHESIS_TEST_DIR") or (PROJECT / "test"))
BOOKS = TESTDATA / "testing"
ETHESIS = TESTDATA / "ethesis"
BASELINE = Path(__file__).resolve().parent / "baseline.json"

sys.path.insert(0, str(CODE))
os.chdir(CODE)          # ให้ .env / path สัมพัทธ์ของแอปทำงานเหมือนตอนรันจริง
import ethesis_import                                        # noqa: E402
import checker                                               # noqa: E402


def missing_test_data(wanted):
    """เล่มทดสอบที่ยังไม่มีในเครื่องนี้ (คู่ไหนขาดข้างใดข้างหนึ่งถือว่าขาด)"""
    return [n for n in wanted
            if not (BOOKS / f"{n}.pdf").exists() or not (ETHESIS / f"{n}.pdf").exists()]


def run_book(n):
    parsed = ethesis_import.parse_ethesis_pdf(str(ETHESIS / f"{n}.pdf"))
    return checker.run_check(str(BOOKS / f"{n}.pdf"), dict(parsed))


def main():
    args = [a for a in sys.argv[1:]]
    detail = "--detail" in args
    save = "--save" in args
    wanted = [a for a in args if a.isdigit()] or ["1", "2", "3"]

    absent = missing_test_data(wanted)
    if absent:
        print(f"ข้ามด่านนี้ — ไม่มีเล่มทดสอบ {', '.join(absent)} ในเครื่องนี้")
        print(f"  มองหาที่: {BOOKS}  และ  {ETHESIS}")
        print("  เล่มทดสอบเป็นวิทยานิพนธ์จริงของนักศึกษา จึงไม่อยู่ใน repo")
        print("  วางไฟล์เป็น <n>.pdf ให้ครบทั้งสองโฟลเดอร์ หรือตั้ง ETHESIS_TEST_DIR")
        print("  ชี้ไปโฟลเดอร์ที่เก็บไว้ แล้วรันใหม่")
        return 0

    baseline = {}
    if BASELINE.exists():
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))

    current, drift = {}, []
    for n in wanted:
        res = run_book(n)
        s = res["summary"]
        got = {"verdict": res["verdict"], "red": s["red"],
               "orange": s["orange"], "yellow": s["yellow"]}
        current[n] = got
        old = baseline.get(n)
        mark = ""
        if old and old != got:
            mark = (f"   <-- ต่างจาก baseline: {old['verdict']} "
                    f"R={old['red']} O={old['orange']} Y={old['yellow']}")
            drift.append(n)
        prog = (res.get("context") or {}).get("document_type") or "-"
        print(f"BOOK {n}: {got['verdict']:<10} R={got['red']} O={got['orange']} "
              f"Y={got['yellow']}  ({prog}){mark}")
        if detail:
            for zone in ("RED", "ORANGE", "YELLOW"):
                for it in res["issues_by_zone"][zone]:
                    print(f"   [{zone}] {it['rule_id']:<22} {it['location']}")
                    print(f"          {it['found'][:110]}")

    if save:
        merged = {**baseline, **current}
        BASELINE.write_text(json.dumps(merged, ensure_ascii=False, indent=2),
                            encoding="utf-8")
        print(f"\nบันทึก baseline ใหม่แล้ว: {BASELINE}")
        return 0

    if not baseline:
        print("\nยังไม่มี baseline — รัน --save เพื่อบันทึกผลปัจจุบันเป็นฐานเทียบ")
        return 0
    if drift:
        print(f"\nต่างจาก baseline: เล่ม {', '.join(drift)}")
        print("อธิบายให้ได้ว่าทำไมก่อน commit — ถ้าเป็นการเปลี่ยนที่ตั้งใจ ให้รัน --save")
        return 1
    print("\nตรงกับ baseline ทุกเล่ม")
    return 0


if __name__ == "__main__":
    sys.exit(main())
