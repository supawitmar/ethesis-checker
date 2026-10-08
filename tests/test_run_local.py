# -*- coding: utf-8 -*-
"""ตัวเปิดระบบในเครื่อง (tools/run_local.py + ไฟล์ .bat สองตัว)

เหตุที่มี (8 ต.ค. 2569): ตาม PDPA ไม่อัปเล่มขึ้นตรวจบนคลาวด์ เจ้าของย้ายมารันในเครื่องตัวเอง
2 เครื่อง — สิ่งที่ห้ามพลาดคือระบบต้องฟังแค่ 127.0.0.1 (เครื่องอื่นในเครือข่ายเข้าไม่ได้)
และรหัสผ่านที่ตั้งผ่านตัวเปิด ต้องอ่านกลับใน uvicorn ได้ค่าเดียวกับที่พิมพ์
"""
import os
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "tools"))

import run_local  # noqa: E402

try:
    from dotenv import dotenv_values
except ImportError:                                  # pragma: no cover
    dotenv_values = None


class TempFolder(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        self.env = self.dir / ".env"
        self.example = self.dir / ".env.example"
        self.example.write_text(
            "# คำอธิบาย\nAPP_PASSWORD=\n# SESSION_SECRET=        # คอมเมนต์\n"
            "# SHEET_WEBHOOK_URL=\n", encoding="utf-8")


class ReadEnvTests(TempFolder):
    def test_reads_the_forms_the_env_files_use(self):
        self.env.write_text(
            "﻿# คอมเมนต์\nAPP_PASSWORD=abc12345   # ท้ายบรรทัด\n"
            "export SESSION_SECRET='x y'\nSHEET_WEBHOOK_URL=\"https://a/exec\"\nEMPTY=\n"
            "# COOKIE_SECURE=1\n", encoding="utf-8")
        self.assertEqual(run_local.read_env(self.env), {
            "APP_PASSWORD": "abc12345", "SESSION_SECRET": "x y",
            "SHEET_WEBHOOK_URL": "https://a/exec", "EMPTY": ""})

    def test_a_missing_file_is_empty(self):
        self.assertEqual(run_local.read_env(self.dir / "nope"), {})


class PasswordTests(unittest.TestCase):
    def test_rejects_what_dotenv_would_read_back_differently(self):
        for bad in ("short1", "has space1", "quote'1234", 'dq"12345', "back\\slash",
                    "hash#1234", "dollar${X}", "tick`1234"):
            with self.subTest(bad=bad):
                self.assertIsNotNone(run_local.password_problem(bad))

    def test_accepts_ordinary_and_thai_passwords(self):
        for good in ("staff-2569!", "รหัสผ่านไทย99", "A1b2C3d4@%^&*()"):
            with self.subTest(good=good):
                self.assertIsNone(run_local.password_problem(good))

    def test_asks_again_until_valid_and_matching(self):
        answers = iter(["short", "goodpass1", "different1", "goodpass1", "goodpass1"])
        with mock.patch.object(run_local, "say"):
            self.assertEqual(run_local.ask_password(lambda _: next(answers)), "goodpass1")


class WriteEnvTests(TempFolder):
    def test_first_run_starts_from_the_example_and_adds_a_secret(self):
        run_local.write_env("goodpass1", self.env, self.example)
        text = self.env.read_text(encoding="utf-8")
        self.assertIn("# คำอธิบาย", text)
        self.assertIn("# SHEET_WEBHOOK_URL=", text)            # คอมเมนต์ไม่ถูกแตะ
        values = run_local.read_env(self.env)
        self.assertEqual(values["APP_PASSWORD"], "goodpass1")
        self.assertGreaterEqual(len(values["SESSION_SECRET"]), 48)
        self.assertEqual(text.count("APP_PASSWORD="), 1)

    def test_keeps_other_lines_and_an_existing_secret(self):
        self.env.write_text("APP_PASSWORD=old\nSESSION_SECRET=keep\n"
                            "SHEET_WEBHOOK_URL=https://x/exec\n", encoding="utf-8")
        run_local.write_env("newpass12", self.env, self.example)
        self.assertEqual(run_local.read_env(self.env), {
            "APP_PASSWORD": "newpass12", "SESSION_SECRET": "keep",
            "SHEET_WEBHOOK_URL": "https://x/exec"})

    @unittest.skipIf(dotenv_values is None, "ไม่มี python-dotenv")
    def test_uvicorn_reads_back_exactly_what_was_typed(self):
        for password in ("staff-2569!", "รหัสผ่านไทย99", "A1b2C3d4@%^&*()", "a=b=c=d1"):
            with self.subTest(password=password):
                self.env.unlink(missing_ok=True)
                run_local.write_env(password, self.env, self.example)
                self.assertEqual(dotenv_values(self.env)["APP_PASSWORD"], password)

    def test_existing_password_is_not_asked_again(self):
        self.env.write_text("APP_PASSWORD=goodpass1\nSESSION_SECRET=s\n"
                            "SHEET_WEBHOOK_URL=u\nSHEET_WEBHOOK_TOKEN=t\n", encoding="utf-8")
        before = self.env.read_bytes()
        with mock.patch.object(run_local, "say") as said:
            run_local.ensure_env(self.env, self.example, prompt=self.fail)
        self.assertEqual(self.env.read_bytes(), before)
        self.assertFalse(said.called)

    def test_warns_when_the_sheet_button_or_login_will_not_work(self):
        self.env.write_text("APP_PASSWORD=goodpass1\nSESSION_SECRET=s\nCOOKIE_SECURE=1\n",
                            encoding="utf-8")
        with mock.patch.object(run_local, "say") as said:
            run_local.ensure_env(self.env, self.example, prompt=self.fail)
        told = " ".join(str(call.args[0]) for call in said.call_args_list)
        self.assertIn("COOKIE_SECURE", told)
        self.assertIn("SHEET_WEBHOOK_URL", told)


class VenvTests(TempFolder):
    def setUp(self):
        super().setUp()
        self.venv = self.dir / ".venv"
        self.req = self.dir / "requirements.txt"
        self.req.write_bytes(b"fastapi==0.115.0\r\n")
        self.calls = []

    def fake_run(self, command, check):
        self.calls.append(command)
        if "venv" in command:
            python = run_local.venv_python(self.venv)
            python.parent.mkdir(parents=True)
            python.write_text("")

    def test_installs_once_then_only_when_requirements_change(self):
        with mock.patch.object(run_local, "say"):
            run_local.ensure_venv(self.venv, self.req, run=self.fake_run)
            self.assertEqual(len(self.calls), 2)                 # สร้าง venv + pip
            run_local.ensure_venv(self.venv, self.req, run=self.fake_run)
            self.assertEqual(len(self.calls), 2)                 # ไม่มีอะไรเปลี่ยน
            self.req.write_bytes(b"fastapi==0.115.0\n")          # แค่ท้ายบรรทัดต่าง
            run_local.ensure_venv(self.venv, self.req, run=self.fake_run)
            self.assertEqual(len(self.calls), 2)
            self.req.write_bytes(b"fastapi==0.116.0\n")
            run_local.ensure_venv(self.venv, self.req, run=self.fake_run)
        self.assertEqual(len(self.calls), 3)
        self.assertIn("pip", self.calls[-1])

    def test_a_failed_install_is_retried_next_time(self):
        def failing(command, check):
            self.fake_run(command, check)
            if "pip" in command:
                raise subprocess.CalledProcessError(1, command)
        with mock.patch.object(run_local, "say"):
            with self.assertRaises(subprocess.CalledProcessError):
                run_local.ensure_venv(self.venv, self.req, run=failing)
            self.assertFalse((self.venv / run_local.STAMP_NAME).exists())


class ServerTests(unittest.TestCase):
    def test_listens_on_this_machine_only(self):
        command = run_local.server_command(Path("py"), 8000)
        self.assertEqual(command[command.index("--host") + 1], "127.0.0.1")
        self.assertNotIn("0.0.0.0", command)
        self.assertEqual(run_local.HOST, "127.0.0.1")

    def test_a_listening_port_is_not_free(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            self.assertFalse(run_local.port_free(port))
        self.assertTrue(run_local.port_free(port))

    def test_reuses_a_running_checker_or_skips_busy_ports(self):
        self.assertEqual(run_local.choose_port(9000, 3, running=lambda p: p == 9001,
                                               free=lambda p: False), ("running", 9001))
        self.assertEqual(run_local.choose_port(9000, 3, running=lambda p: False,
                                               free=lambda p: p == 9002), ("free", 9002))
        self.assertIsNone(run_local.choose_port(9000, 3, running=lambda p: False,
                                                free=lambda p: False))

    def test_something_else_on_the_port_is_not_the_checker(self):
        self.assertFalse(run_local.checker_running(1, timeout=0.2))


class UpdateTests(unittest.TestCase):
    def setUp(self):
        # ไม่พึ่งว่าโฟลเดอร์ที่รันเทสต์มาจาก git clone หรือไม่ (สำเนาโค้ดที่ใช้ทดสอบไม่มี .git)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        (Path(folder.name) / ".git").mkdir()
        patcher = mock.patch.object(run_local, "CODE", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def result(self, out="", code=0):
        return subprocess.CompletedProcess([], code, stdout=out, stderr="")

    def test_does_not_pull_over_local_edits(self):
        calls = []

        def git(*args):
            calls.append(args)
            return self.result(" M checker.py\n")
        with mock.patch.object(run_local, "say"):
            self.assertEqual(run_local.update(which=lambda _: "git", git_run=git), 1)
        self.assertNotIn(("pull", "--ff-only"), calls)

    def test_pulls_fast_forward_only_then_installs(self):
        calls = []

        def git(*args):
            calls.append(args)
            return self.result("abc1234\n" if args[0] == "rev-parse" else "")
        with mock.patch.object(run_local, "say"), \
                mock.patch.object(run_local, "ensure_venv") as installed:
            self.assertEqual(run_local.update(which=lambda _: "git", git_run=git), 0)
        self.assertIn(("pull", "--ff-only"), calls)
        self.assertTrue(installed.called)

    def test_without_git_it_explains_instead_of_crashing(self):
        with mock.patch.object(run_local, "say"):
            self.assertEqual(run_local.update(which=lambda _: None), 1)


class PipedOutputTests(unittest.TestCase):
    def test_thai_messages_do_not_crash_when_output_is_piped(self):
        """เดิมพังด้วย UnicodeEncodeError (cp1252) ทันทีที่พิมพ์ข้อความไทยลงท่อบน Windows"""
        env = {k: v for k, v in os.environ.items() if k not in {"PYTHONIOENCODING", "PYTHONUTF8"}}
        run = subprocess.run([sys.executable, str(CODE / "tools" / "run_local.py"), "--help"],
                             capture_output=True, env=env, timeout=60)
        self.assertEqual(run.returncode, 0, run.stderr.decode("utf-8", "replace"))
        self.assertIn("อัปเดต", run.stdout.decode("utf-8"))


class BatchFileTests(unittest.TestCase):
    FILES = {"เปิดระบบตรวจเล่ม.bat": "tools\\run_local.py %*",
             "อัปเดตระบบตรวจเล่ม.bat": "tools\\run_local.py --update"}

    def test_call_the_launcher_with_crlf_and_utf8(self):
        for name, call in self.FILES.items():
            with self.subTest(name=name):
                data = (CODE / name).read_bytes()
                self.assertFalse(data.startswith(b"\xef\xbb\xbf"), "BOM ทำให้บรรทัดแรกของ cmd พัง")
                self.assertNotIn(b"\n", data.replace(b"\r\n", b""), "ต้องเป็น CRLF ทั้งไฟล์")
                lines = data.decode("utf-8").splitlines()
                self.assertTrue(lines[0].isascii() and lines[1].startswith("chcp 65001"),
                                "ต้องสลับเป็น UTF-8 ก่อนบรรทัดที่มีอักษรไทย")
                self.assertTrue(all(line.isascii() for line in lines[:2]))
                self.assertIn(call, data.decode("utf-8"))

    def test_gitattributes_keeps_them_crlf_on_every_machine(self):
        text = (CODE / ".gitattributes").read_text(encoding="utf-8")
        self.assertRegex(text, r"(?m)^\*\.bat\s+text\s+eol=crlf\s*$")


if __name__ == "__main__":
    unittest.main()
