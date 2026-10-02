# -*- coding: utf-8 -*-
"""เส้นทางส่งมอบ: เวอร์ชันของโค้ดที่ระบบกำลังรันอยู่ + ไฟล์ CI ที่เฝ้าดูมัน

เหตุที่มี (2 ต.ค. 2569): รายงานบนระบบจริงยังเป็นโค้ดเก่าโดยไม่มีอะไรบอก เจ้าหน้าที่จึงแจ้ง
นักศึกษาตามถ้อยคำเก่า — ท้ายหน้าเว็บกับ /health จึงต้องบอกรหัส commit ที่รันอยู่เสมอ
ส่วน CI รันชุดทดสอบบน Linux ทุกครั้งที่ push ซึ่งเครื่องที่พัฒนา (Windows) ไม่เคยเทียบให้
"""
import os
import platform
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

os.environ.setdefault("MAX_UPLOAD_MB", "1")
os.environ.setdefault("APP_PASSWORD", "test-password")

from fastapi.testclient import TestClient

import checker
import main

try:
    import yaml
except ImportError:                                  # pragma: no cover
    yaml = None

CODE = Path(__file__).resolve().parents[1]
FULL_SHA = "0123456789abcdef0123456789abcdef01234567"
OTHER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


class ResolveAppVersionTests(unittest.TestCase):
    """รหัส commit ที่แสดง มาจากตัวแปรสภาพแวดล้อมก่อน แล้วค่อยถาม git — และเป็นรหัส 7 ตัวเท่านั้น"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.repo = Path(folder.name)                # โฟลเดอร์ว่าง ไม่มี .git

    def resolve(self, environ):
        return main._resolve_app_version(environ, self.repo)

    def test_the_platform_commit_is_cut_to_seven_characters(self):
        self.assertEqual(self.resolve({"RENDER_GIT_COMMIT": FULL_SHA}), "0123456")

    def test_the_commit_the_builder_passes_in_beats_the_platform_one(self):
        env = {"GIT_COMMIT": FULL_SHA, "RENDER_GIT_COMMIT": OTHER_SHA}
        self.assertEqual(self.resolve(env), "0123456")

    def test_an_empty_build_arg_falls_through_to_the_platform_value(self):
        """Dockerfile ตั้ง GIT_COMMIT เป็นค่าว่างเมื่อไม่ได้ส่ง --build-arg มา ต้องไม่ทับค่าของ Render"""
        env = {"GIT_COMMIT": "", "RENDER_GIT_COMMIT": OTHER_SHA}
        self.assertEqual(self.resolve(env), "fedcba9")

    def test_case_and_surrounding_spaces_are_normalised(self):
        self.assertEqual(self.resolve({"GIT_COMMIT": "  ABCDEF0123456  "}), "abcdef0")

    def test_anything_that_is_not_a_commit_is_never_shown(self):
        """ค่าแปลกปลอมต้องไม่หลุดลงหน้าเว็บหรือ JSON — ได้ "unknown" และไม่ถาม git (ไม่มี .git)"""
        junk = ["latest", "abc123", "<script>alert(1)</script>", "g" * 40,
                "abc1234; rm -rf /", "0123456 and more"]
        with mock.patch.object(main.subprocess, "run", side_effect=AssertionError("ไม่ควรเรียก git")):
            for value in junk:
                with self.subTest(value=value):
                    self.assertEqual(self.resolve({"GIT_COMMIT": value}), main.APP_VERSION_UNKNOWN)

    def test_without_env_or_dot_git_it_does_not_call_git(self):
        """image Docker ไม่มี .git — ไม่ต้องเสียเวลาเรียก git ทุกครั้งที่ระบบเริ่ม"""
        with mock.patch.object(main.subprocess, "run", side_effect=AssertionError("ไม่ควรเรียก git")):
            self.assertEqual(self.resolve({}), main.APP_VERSION_UNKNOWN)

    def test_with_dot_git_it_asks_git_for_the_short_hash(self):
        (self.repo / ".git").mkdir()
        # --short=7 คืนยาวกว่า 7 ตัวได้เมื่อรหัสสั้นชนกัน (repo ใหญ่) ต้องตัดให้เหลือ 7 เหมือนกรณีตัวแปร
        done = subprocess.CompletedProcess(args=[], returncode=0, stdout="abcdef012\n", stderr="")
        with mock.patch.object(main.subprocess, "run", return_value=done) as run:
            self.assertEqual(self.resolve({}), "abcdef0")
        args, kwargs = run.call_args
        self.assertIn("--short=7", args[0])
        self.assertEqual(Path(kwargs["cwd"]), self.repo)
        self.assertIsNotNone(kwargs.get("timeout"), "ต้องมี timeout ไม่ให้ git ค้างแล้วระบบไม่ขึ้น")

    def test_a_failing_git_never_breaks_startup(self):
        (self.repo / ".git").mkdir()
        outcomes = [
            FileNotFoundError("ไม่มี git ในเครื่อง"),
            subprocess.TimeoutExpired("git", 3),
            subprocess.CompletedProcess([], 128, stdout="", stderr="fatal: not a git repository"),
            subprocess.CompletedProcess([], 0, stdout="ไม่ใช่รหัส commit\n", stderr=""),
            # git จบแบบล้มเหลวแล้วยังพิมพ์อะไรหน้าตาเหมือนรหัส — ไม่เชื่อ
            subprocess.CompletedProcess([], 1, stdout="abcdef0\n", stderr="warning: ..."),
        ]
        for outcome in outcomes:
            with self.subTest(outcome=repr(outcome)[:50]):
                patch = (mock.patch.object(main.subprocess, "run", side_effect=outcome)
                         if isinstance(outcome, Exception)
                         else mock.patch.object(main.subprocess, "run", return_value=outcome))
                with patch:
                    self.assertEqual(self.resolve({}), main.APP_VERSION_UNKNOWN)

    def test_this_checkout_gives_the_same_hash_as_git(self):
        try:
            real = subprocess.run(["git", "rev-parse", "--short=7", "HEAD"], cwd=CODE,
                                  capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            self.skipTest("ไม่มี git ในเครื่องนี้")
        if real.returncode != 0:
            self.skipTest("โฟลเดอร์นี้ไม่ใช่ git checkout")
        self.assertEqual(main._resolve_app_version({}, CODE), real.stdout.strip()[:7])

    def test_the_running_app_holds_a_commit_or_unknown(self):
        self.assertRegex(main.APP_VERSION, r"^([0-9a-f]{7}|unknown)$")


class HealthEndpointTests(unittest.TestCase):
    def test_health_tells_the_running_version_without_logging_in(self):
        """Render ใช้ /health เช็กว่าบริการขึ้น และหลัง deploy เปิดดูเองได้โดยไม่ต้องล็อกอิน"""
        response = TestClient(main.app).get("/health")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "ok")
        self.assertEqual(body["version"], main.APP_VERSION)
        self.assertEqual(body["python"], platform.python_version())
        self.assertRegex(body["started"], r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\+07:00$")


class VersionFooterTests(unittest.TestCase):
    """เจ้าหน้าที่ต้องเห็นเวอร์ชันที่ท้ายหน้าที่ใช้งานทุกวัน ไม่ต้องไปเปิด /health"""

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        response = cls.client.post("/login", data={"password": "test-password", "next": "/"},
                                   follow_redirects=False)
        if response.status_code != 303:
            raise RuntimeError("Test login failed")

    def tearDown(self):
        with main.JOBS_LOCK:
            main.JOBS.clear()

    def _seed_clean_report(self):
        with main.JOBS_LOCK:
            main.JOBS["demo"] = {
                "stage": "เสร็จ", "done": True, "error": None,
                "report": checker.check_result(checker.Report()),
                "pdf_name": "book.pdf", "approved": {}, "ts": time.time(),
            }
        return "demo"

    def test_the_upload_page_shows_the_version_and_the_start_time(self):
        html = self.client.get("/").text
        self.assertIn("เวอร์ชันระบบ", html)
        self.assertIn(f"<b>{main.APP_VERSION}</b>", html)
        self.assertIn(main.APP_STARTED_AT.strftime("%d/%m/%Y %H:%M"), html)

    def test_the_report_page_shows_it_in_both_languages(self):
        html = self.client.get(f"/result/{self._seed_clean_report()}").text
        self.assertIn('data-th="เวอร์ชันระบบ" data-en="System version"', html)
        self.assertIn('data-th="เริ่มทำงาน" data-en="running since"', html)
        self.assertIn(f"<b>{main.APP_VERSION}</b>", html)
        self.assertIn(main.APP_STARTED_AT.strftime("%d/%m/%Y %H:%M"), html)

    def test_the_login_page_does_not_show_it(self):
        """ตั้งใจให้เหลือที่เดียวที่เปิดดูได้โดยไม่ล็อกอิน คือ /health (รหัส 7 ตัว ไว้เทียบ commit)"""
        html = TestClient(main.app).get("/login").text
        self.assertNotIn("เวอร์ชันระบบ", html)
        self.assertNotIn(f"<b>{main.APP_VERSION}</b>", html)


WORKFLOW = CODE / ".github" / "workflows" / "ci.yml"


def _workflow_commands(text):
    """คำสั่งที่ workflow สั่งรันจริง (บรรทัด run:) — ไม่นับคอมเมนต์ที่อธิบายว่าทำไมไม่รันบางด่าน"""
    return [m.group(1).strip() for m in re.finditer(r"(?m)^\s*-?\s*run:\s*(.+?)\s*$", text)]


class CiWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.is_file(), "ไม่พบ .github/workflows/ci.yml")
        self.text = WORKFLOW.read_text(encoding="utf-8")
        self.commands = _workflow_commands(self.text)

    def test_runs_the_unit_tests_and_the_i18n_lint(self):
        self.assertIn("python -B -m unittest discover -s tests", self.commands)
        self.assertIn("python tools/check_i18n.py --lint", self.commands)

    def test_does_not_run_the_gates_that_pass_without_any_book(self):
        """ไม่มีวิทยานิพนธ์จริง ทั้งสามตัว "ข้าม" แล้วจบแบบผ่าน — CI เขียวทั้งที่ไม่ได้ตรวจอะไร

        เมื่อมีชุดเล่มสมมติที่เก็บใน repo ได้ ให้ใส่ด่านเหล่านี้เข้า CI แล้วแก้เทสต์นี้พร้อมกัน
        """
        for gate in ("regress_books", "--corpus", "smoke_web"):
            with self.subTest(gate=gate):
                self.assertFalse(any(gate in command for command in self.commands),
                                 f"{gate} ต้องใช้เล่มจริง ถ้าไม่มีเล่มจะข้ามเงียบ ๆ แล้ว CI ดูเหมือนผ่าน")

    def test_tests_the_python_version_the_dockerfile_runs(self):
        dockerfile = (CODE / "Dockerfile").read_text(encoding="utf-8")
        wanted = re.search(r"(?m)^FROM python:(\d+\.\d+)", dockerfile).group(1)
        matrix = re.search(r"python-version:\s*\[([^\]]*)\]", self.text).group(1)
        versions = [v.strip().strip("\"'") for v in matrix.split(",")]
        self.assertIn(wanted, versions, "Python ใน Dockerfile ต้องอยู่ในรายการที่ CI ทดสอบ")

    def test_needs_no_secrets_and_cannot_write_to_the_repo(self):
        self.assertNotIn("secrets.", self.text)
        self.assertRegex(self.text, r"(?m)^permissions:\s*\n\s+contents: read\s*$")
        self.assertNotRegex(self.text, r"(?m)^\s+[a-z-]+: write\s*$")

    @unittest.skipIf(yaml is None, "ไม่มี PyYAML")
    def test_is_valid_yaml_with_the_job_steps(self):
        data = yaml.safe_load(self.text)
        triggers = data.get("on", data.get(True))        # YAML 1.1 อ่านคีย์ on เป็น True
        self.assertIn("push", triggers)
        steps = data["jobs"]["checks"]["steps"]
        self.assertTrue(any("checkout" in str(step.get("uses", "")) for step in steps))
        self.assertTrue(any("setup-python" in str(step.get("uses", "")) for step in steps))


class DockerImageTests(unittest.TestCase):
    def test_the_workflow_folder_stays_out_of_the_image(self):
        """image มีแค่ *.py + templates/ + requirements — โฟลเดอร์ของ CI ไม่ต้องเข้าไป"""
        lines = (CODE / ".dockerignore").read_text(encoding="utf-8").splitlines()
        self.assertIn(".github/", [line.strip() for line in lines])

    def test_the_build_can_be_given_the_commit(self):
        """.git ไม่อยู่ใน image ถ้าไม่ส่งรหัส commit เข้ามาตอน build ท้ายหน้าเว็บจะขึ้นว่า unknown"""
        dockerfile = (CODE / "Dockerfile").read_text(encoding="utf-8")
        self.assertRegex(dockerfile, r'(?m)^ARG GIT_COMMIT=""\s*$')
        self.assertRegex(dockerfile, r"(?m)^ENV GIT_COMMIT=\$\{GIT_COMMIT\}\s*$")
        # ตัวแปรที่ main.py อ่านต้องเป็นชื่อเดียวกับที่ Dockerfile ตั้ง
        self.assertIn("GIT_COMMIT", main._COMMIT_ENV_NAMES)


if __name__ == "__main__":
    unittest.main()
