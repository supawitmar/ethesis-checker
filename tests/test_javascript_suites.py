# -*- coding: utf-8 -*-
"""รันชุดทดสอบ JavaScript ให้ไปพร้อมกับ pytest

สคริปต์ของหน้าเว็บ (templates/index.html, templates/report.html) มีตรรกะที่ python
แตะไม่ถึง — การอ่านข้อมูลจากไฟล์ eThesis และการแปล/ซ่อนหัวข้อในหน้ารายงาน เทสต์ของ
สองส่วนนี้เขียนเป็นสคริปต์ node แยกไว้ แต่ไม่มีอะไรเรียกให้รันอัตโนมัติ

ผลคือ tests/test_ethesis_parser.js พังค้างอยู่กว่าเดือน (parser เปลี่ยนชื่อช่องไปแล้ว
แต่เทสต์ยังคาดชื่อชุดเก่า) โดยไม่มีใครรู้ ไฟล์นี้ผูกมันเข้ากับ pytest เพื่อไม่ให้เงียบอีก

เครื่องที่ไม่มี node จะข้ามไป ไม่ใช่ฟ้องว่าพัง (เช่นตอน deploy ที่มีแต่ python)
"""
import shutil
import subprocess
import unittest
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]
TESTS = CODE / "tests"
NODE = shutil.which("node")


@unittest.skipIf(NODE is None, "ไม่มี node ในเครื่องนี้")
class JavaScriptSuites(unittest.TestCase):
    def _run(self, name):
        script = TESTS / name
        self.assertTrue(script.is_file(), f"ไม่พบ {name}")
        result = subprocess.run(
            [NODE, str(script)], cwd=str(CODE),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        if result.returncode != 0:
            self.fail(f"{name} ไม่ผ่าน\n"
                      f"--- stdout ---\n{result.stdout}\n"
                      f"--- stderr ---\n{result.stderr}")

    def test_ethesis_parser(self):
        """ตัวอ่านข้อมูลจากไฟล์ eThesis ในหน้าอัปโหลด"""
        self._run("test_ethesis_parser.js")

    def test_report_summary(self):
        """การแปลข้อความสรุปและการซ่อนหัวข้อตามผลตรวจในหน้ารายงาน"""
        self._run("test_report_summary.js")

    def test_every_js_suite_here_is_wired_up(self):
        """เพิ่มไฟล์เทสต์ JS ใหม่แล้วลืมผูก = เงียบไปอีกตัว เทสต์นี้กันไว้"""
        wired = {"test_ethesis_parser.js", "test_report_summary.js"}
        found = {p.name for p in TESTS.glob("test_*.js")}
        self.assertEqual(found, wired,
                         "มีไฟล์เทสต์ JS ที่ยังไม่ได้ผูกเข้า pytest")


if __name__ == "__main__":
    unittest.main()
