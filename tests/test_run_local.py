# -*- coding: utf-8 -*-
"""ตัวเปิดระบบในเครื่อง (tools/run_local.py + ไฟล์ .bat สองตัว)

เหตุที่มี (8 ต.ค. 2569): ตาม PDPA ไม่อัปเล่มขึ้นตรวจบนคลาวด์ เจ้าของย้ายมารันในเครื่องตัวเอง
2 เครื่อง — สิ่งที่ห้ามพลาดคือระบบต้องฟังแค่ 127.0.0.1 (เครื่องอื่นในเครือข่ายเข้าไม่ได้)
และรหัสผ่านที่ตั้งผ่านตัวเปิด ต้องอ่านกลับใน uvicorn ได้ค่าเดียวกับที่พิมพ์
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "tools"))

import release  # noqa: E402
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


class UpdateGuardTests(unittest.TestCase):
    def setUp(self):
        # ไม่พึ่งว่าโฟลเดอร์ที่รันเทสต์มาจาก git clone หรือไม่ (สำเนาโค้ดที่ใช้ทดสอบไม่มี .git)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        (Path(folder.name) / ".git").mkdir()
        patcher = mock.patch.object(run_local, "CODE", Path(folder.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_does_not_touch_a_folder_with_local_edits(self):
        calls = []

        def git(*args):
            calls.append(args)
            return subprocess.CompletedProcess([], 0, stdout=" M checker.py\n", stderr="")
        with mock.patch.object(run_local, "say"):
            self.assertEqual(run_local.update(which=lambda _: "git", git_run=git), 1)
        self.assertEqual([c[0] for c in calls], ["status"])

    def test_without_git_it_explains_instead_of_crashing(self):
        with mock.patch.object(run_local, "say"):
            self.assertEqual(run_local.update(which=lambda _: None), 1)


CHANGELOG_10 = "# บันทึก\n\n## อัปเดต 1.0 — 9 ต.ค. 2569\n\n- รุ่นแรก\n"


def changelog_with(version, note):
    return f"# บันทึก\n\n## อัปเดต {version} — 10 ต.ค. 2569\n\n- {note}\n\n" + CHANGELOG_10.split("\n\n", 1)[1]


def main_release(repo):
    os.environ.setdefault("APP_PASSWORD", "test-password")
    import main
    return main._resolve_app_release(repo)


class GitRepos(unittest.TestCase):
    """repo git จริงในโฟลเดอร์ชั่วคราว: origin (GitHub) · dev (เครื่องพัฒนา) · machine (เครื่องที่ใช้งาน)"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.origin, self.dev, self.machine = root / "origin.git", root / "dev", root / "machine"
        self.git(root, "init", "--bare", "-q", str(self.origin))
        self.git(root, "clone", "-q", str(self.origin), str(self.dev))
        for key, value in (("user.name", "test"), ("user.email", "test@example.com"),
                           ("commit.gpgsign", "false"), ("tag.gpgsign", "false")):
            self.git(self.dev, "config", key, value)
        self.commit("CHANGELOG.md", CHANGELOG_10, "first")
        self.git(self.dev, "push", "-q", "origin", "HEAD:main")
        self.git(self.origin, "symbolic-ref", "HEAD", "refs/heads/main")
        self.git(root, "clone", "-q", str(self.origin), str(self.machine))

    def git(self, repo, *args):
        return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True,
                              text=True, encoding="utf-8").stdout.strip()

    def commit(self, name, text, message):
        (self.dev / name).write_text(text, encoding="utf-8", newline="\n")
        self.git(self.dev, "add", name)
        self.git(self.dev, "commit", "-q", "-m", message)

    def release(self, version):
        self.assertEqual(release.release(version, code=self.dev, say=lambda *_: None), 0)
        self.git(self.dev, "push", "-q", "origin", "HEAD:main", f"v{version}")

    def update_machine(self):
        said = []
        with mock.patch.object(run_local, "CODE", self.machine), \
                mock.patch.object(run_local, "say", side_effect=lambda t="": said.append(str(t))), \
                mock.patch.object(run_local, "ensure_venv") as installed:
            code = run_local.update()
        return code, "\n".join(said), installed.called

    def head(self, repo):
        return self.git(repo, "rev-parse", "HEAD")

    def tagged(self, version):
        return self.git(self.dev, "rev-parse", f"v{version}^{{commit}}")


@unittest.skipIf(shutil.which("git") is None, "ไม่มี git")
class ReleaseFlowTests(GitRepos):
    """ออกรุ่นบนเครื่องพัฒนา → push → เครื่องที่ใช้งานกดอัปเดต"""

    def test_nothing_is_installed_before_the_first_release(self):
        self.commit("rules.txt", "new rule\n", "unreleased work")
        self.git(self.dev, "push", "-q", "origin", "HEAD:main")
        before = self.head(self.machine)
        code, said, installed = self.update_machine()
        self.assertEqual(code, 0, said)
        self.assertEqual(self.head(self.machine), before)
        self.assertFalse(installed)

    def test_installs_the_release_and_shows_what_changed(self):
        self.commit("rules.txt", "rule 1\n", "rules")
        self.release("1.0")
        code, said, installed = self.update_machine()
        self.assertEqual(code, 0, said)
        self.assertEqual(self.head(self.machine), self.tagged("1.0"))
        self.assertIn("อัปเดต 1.0 — 9 ต.ค. 2569", said)
        self.assertIn("รุ่นแรก", said)
        self.assertTrue(installed)

    def test_work_pushed_after_a_release_waits_for_the_next_release(self):
        self.release("1.0")
        self.update_machine()
        released = self.head(self.machine)
        self.commit("rules.txt", "half done\n", "unreleased work")
        self.git(self.dev, "push", "-q", "origin", "HEAD:main")
        code, said, _ = self.update_machine()
        self.assertEqual(code, 0, said)
        self.assertEqual(self.head(self.machine), released)
        self.assertIn("รุ่นล่าสุดแล้ว: อัปเดต 1.0", said)

    def test_a_machine_several_releases_behind_gets_every_note_it_missed(self):
        self.release("1.0")
        self.update_machine()
        self.commit("CHANGELOG.md", changelog_with("1.1", "กฎข้อ ก"), "release 1.1")
        self.release("1.1")
        text = changelog_with("1.2", "กฎข้อ ข").replace("# บันทึก\n\n", "# บันทึก\n\n", 1)
        text = text.replace("## อัปเดต 1.0", "## อัปเดต 1.1 — 10 ต.ค. 2569\n\n- กฎข้อ ก\n\n## อัปเดต 1.0", 1)
        self.commit("CHANGELOG.md", text, "release 1.2")
        self.release("1.2")
        code, said, _ = self.update_machine()
        self.assertEqual(code, 0, said)
        self.assertIn("กฎข้อ ก", said)
        self.assertIn("กฎข้อ ข", said)
        self.assertNotIn("รุ่นแรก", said)              # 1.0 ติดตั้งไปแล้ว ไม่ต้องบอกซ้ำ
        self.assertEqual(self.head(self.machine), self.tagged("1.2"))

    def test_a_machine_with_its_own_commits_is_left_alone(self):
        for key, value in (("user.name", "m"), ("user.email", "m@example.com"),
                           ("commit.gpgsign", "false")):
            self.git(self.machine, "config", key, value)
        (self.machine / "local.txt").write_text("x\n", encoding="utf-8")
        self.git(self.machine, "add", "local.txt")
        self.git(self.machine, "commit", "-q", "-m", "local")
        mine = self.head(self.machine)
        self.commit("CHANGELOG.md", changelog_with("1.1", "x"), "1.1")
        self.release("1.1")
        code, said, installed = self.update_machine()
        self.assertEqual(code, 1, said)
        self.assertEqual(self.head(self.machine), mine)
        self.assertFalse(installed)

    def test_a_running_background_server_is_restarted_on_the_new_release(self):
        self.commit("rules.txt", "rule\n", "rules")
        self.release("1.0")
        (self.machine / ".run").mkdir()
        (self.machine / ".run" / "server.json").write_text(
            json.dumps({"pid": 1, "port": 8123, "stop_token": "k"}), encoding="utf-8")
        with mock.patch.object(run_local, "checker_running", return_value=True), \
                mock.patch.object(run_local, "stop", return_value=0) as stopped, \
                mock.patch.object(run_local, "start", return_value=0) as started:
            code, said, _ = self.update_machine()
        self.assertEqual(code, 0, said)
        self.assertTrue(stopped.called)
        started.assert_called_once_with(open_browser=False)

    def test_the_footer_reads_the_release_from_the_tag(self):
        self.assertEqual(main_release(self.dev), "")
        self.release("1.0")
        self.assertEqual(main_release(self.dev), "1.0")
        self.commit("rules.txt", "after\n", "after the release")
        self.assertEqual(main_release(self.dev), "1.0")
        self.git(self.dev, "tag", "backup-before-cleanup")             # แท็กอื่นที่ไม่ใช่รุ่น
        self.assertEqual(main_release(self.dev), "1.0")
        self.assertEqual(main_release(self.origin.parent), "")      # ไม่มี .git


@unittest.skipIf(shutil.which("git") is None, "ไม่มี git")
class ReleaseToolGuardTests(GitRepos):
    """ตัวออกรุ่นปฏิเสธทุกกรณีที่จะได้รุ่นที่เลข วันที่ หรือรายการไม่ตรงกับโค้ด"""

    def refused(self, version):
        before = self.git(self.dev, "tag", "--list")
        self.assertEqual(release.release(version, code=self.dev, say=lambda *_: None), 1)
        self.assertEqual(self.git(self.dev, "tag", "--list"), before)

    def test_refuses_a_bad_number(self):
        for bad in ("1", "v1.0", "1.0.1", "หนึ่ง"):
            with self.subTest(bad=bad):
                self.refused(bad)

    def test_refuses_when_the_changelog_top_is_another_version(self):
        self.refused("1.1")

    def test_refuses_uncommitted_files(self):
        (self.dev / "CHANGELOG.md").write_text(CHANGELOG_10 + "แก้ค้าง\n", encoding="utf-8")
        self.refused("1.0")

    def test_refuses_an_entry_with_no_notes(self):
        self.commit("CHANGELOG.md", "## อัปเดต 1.0 — 9 ต.ค. 2569\n", "empty")
        self.refused("1.0")

    def test_refuses_reusing_or_going_back(self):
        self.release("1.0")
        self.refused("1.0")
        self.commit("CHANGELOG.md", "## อัปเดต 0.9 — 9 ต.ค. 2569\n\n- x\n", "back")
        self.refused("0.9")


class ChangelogFileTests(unittest.TestCase):
    def test_the_example_in_the_instructions_is_not_read_as_a_release(self):
        """ตัวอย่างหัวข้อในวิธีออกอัปเดตเยื้องไว้ — ถ้าชิดซ้ายจะกลายเป็น "อัปเดต 1.0" ที่ไม่เคยออก"""
        text = (CODE / "CHANGELOG.md").read_text(encoding="utf-8")
        instructions = text[:text.rindex("</details>")]
        self.assertIn("## อัปเดต 1.0", instructions)
        self.assertEqual(run_local.changelog_entries(instructions), [])

    def test_the_real_changelog_parses_newest_first_with_notes(self):
        entries = run_local.changelog_entries((CODE / "CHANGELOG.md").read_text(encoding="utf-8"))
        versions = [version for version, _, _ in entries]
        self.assertEqual(versions, sorted(versions, reverse=True))
        self.assertEqual(len(set(versions)), len(versions))
        for version, date, body in entries:
            with self.subTest(version=version):
                self.assertRegex(date, r"\d{1,2} \S+ \d{4}")
                self.assertTrue(body)


class FakeProcess:
    def __init__(self, pid=4321, exited=False):
        self.pid, self.exited, self.killed = pid, exited, False

    def poll(self):
        return 1 if self.exited else None

    def kill(self):
        self.killed = True


class BackgroundTests(unittest.TestCase):
    """เจ้าของขอ 15 ต.ค. 2569: ระบบรันเบื้องหลังโดยไม่มีหน้าต่างดำ — รหัสผ่านยังต้องมีเหมือนเดิม"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.dir = Path(folder.name)
        patcher = mock.patch.object(run_local, "CODE", self.dir)
        patcher.start()
        self.addCleanup(patcher.stop)
        said = mock.patch.object(run_local, "say")
        self.said = said.start()
        self.addCleanup(said.stop)

    def told(self):
        return " ".join(str(c.args[0]) for c in self.said.call_args_list if c.args)

    def write_state(self, port=8123, token="key", pid=4321):
        (self.dir / ".run").mkdir(exist_ok=True)
        (self.dir / ".run" / "server.json").write_text(
            json.dumps({"pid": pid, "port": port, "stop_token": token}), encoding="utf-8")

    def test_starts_without_a_window_and_remembers_how_to_stop_it(self):
        seen = {}

        def popen(command, **kwargs):
            seen["command"], seen["kwargs"] = command, kwargs
            return FakeProcess(pid=999)
        run_local.start_background(Path("py"), 8123, popen=popen)
        kwargs = seen["kwargs"]
        if os.name == "nt":
            self.assertTrue(kwargs["creationflags"] & subprocess.CREATE_NO_WINDOW)
        else:
            self.assertTrue(kwargs["start_new_session"])
        self.assertEqual(seen["command"][seen["command"].index("--host") + 1], "127.0.0.1")
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["stderr"], subprocess.STDOUT)
        state = run_local.read_state()
        self.assertEqual((state["pid"], state["port"]), (999, 8123))
        self.assertGreaterEqual(len(state["stop_token"]), 32)
        self.assertEqual(kwargs["env"]["ETHESIS_STOP_TOKEN"], state["stop_token"])
        self.assertTrue((self.dir / ".run" / "server.log").exists())

    def test_a_new_stop_key_every_start(self):
        popen = lambda command, **kwargs: FakeProcess()           # noqa: E731
        run_local.start_background(Path("py"), 8123, popen=popen)
        first = run_local.read_state()["stop_token"]
        run_local.start_background(Path("py"), 8123, popen=popen)
        self.assertNotEqual(run_local.read_state()["stop_token"], first)

    def test_wait_ready_gives_up_when_the_server_dies(self):
        self.assertFalse(run_local.wait_ready(8123, FakeProcess(exited=True), seconds=5,
                                              running=lambda p: False))
        self.assertTrue(run_local.wait_ready(8123, FakeProcess(), seconds=5, running=lambda p: True))

    def start_with(self, ready):
        process = FakeProcess()
        with mock.patch.object(run_local, "ensure_env"), \
                mock.patch.object(run_local, "ensure_venv", return_value=Path("py")), \
                mock.patch.object(run_local, "choose_port", return_value=("free", 8123)), \
                mock.patch.object(run_local, "start_background",
                                  side_effect=lambda *a: (self.write_state(), process)[1]), \
                mock.patch.object(run_local, "wait_ready", return_value=ready), \
                mock.patch.object(run_local.webbrowser, "open") as opened:
            code = run_local.start()
        return code, process, opened

    def test_start_returns_once_the_page_is_ready_and_opens_the_browser(self):
        code, process, opened = self.start_with(ready=True)
        self.assertEqual(code, 0)
        opened.assert_called_once_with("http://127.0.0.1:8123/")
        self.assertIn("ปิดระบบตรวจเล่ม.bat", self.told())
        self.assertIsNotNone(run_local.read_state())

    def test_a_server_that_never_comes_up_shows_its_log_and_cleans_up(self):
        (self.dir / ".run").mkdir()
        (self.dir / ".run" / "server.log").write_text("Traceback: something broke\n", encoding="utf-8")
        code, process, opened = self.start_with(ready=False)
        self.assertEqual(code, 1)
        self.assertIn("something broke", self.told())
        self.assertTrue(process.killed)
        self.assertFalse(opened.called)
        self.assertIsNone(run_local.read_state())

    def test_stop_asks_the_server_with_its_key(self):
        self.write_state(token="secret-key")
        alive = {8123: True}
        asked = []

        def request_stop(port, token):
            asked.append((port, token))
            alive[port] = False
        kill = mock.Mock()
        code = run_local.stop(request_stop=request_stop, kill=kill,
                              running=lambda p: alive.get(p, False), seconds=2)
        self.assertEqual(code, 0)
        self.assertEqual(asked, [(8123, "secret-key")])
        self.assertFalse(kill.called)
        self.assertIsNone(run_local.read_state())

    def test_stop_falls_back_to_killing_its_own_process(self):
        self.write_state(pid=777)
        alive = {8123: True}
        kill = mock.Mock(side_effect=lambda pid: alive.update({8123: False}))
        code = run_local.stop(request_stop=lambda *a: False, kill=kill,
                              running=lambda p: alive.get(p, False), seconds=0.5)
        self.assertEqual(code, 0)
        kill.assert_called_once_with(777)

    def test_stop_reports_when_nothing_helped(self):
        self.write_state()
        code = run_local.stop(request_stop=lambda *a: False, kill=lambda pid: None,
                              running=lambda p: p == 8123, seconds=0.3)
        self.assertEqual(code, 1)

    def test_stop_with_nothing_running(self):
        self.assertEqual(run_local.stop(running=lambda p: False), 0)
        self.assertIn("ไม่ได้เปิดอยู่", self.told())

    def test_stop_does_not_touch_a_server_it_did_not_start(self):
        kill = mock.Mock()
        request_stop = mock.Mock()
        code = run_local.stop(request_stop=request_stop, kill=kill, running=lambda p: p == 8000)
        self.assertEqual(code, 1)
        self.assertFalse(kill.called or request_stop.called)

    def test_a_broken_state_file_counts_as_not_running(self):
        (self.dir / ".run").mkdir()
        (self.dir / ".run" / "server.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(run_local.read_state())


class LocalStopEndpointTests(unittest.TestCase):
    """/local-stop ปิดระบบได้เฉพาะเมื่อตัวเปิดระบบส่งกุญแจมา และลบไฟล์เล่มที่เก็บไว้ก่อนออก"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("APP_PASSWORD", "test-password")
        import main
        from fastapi.testclient import TestClient
        cls.main = main
        cls.client = TestClient(main.app)                 # ไม่ได้ล็อกอิน — ตัวปิดระบบไม่มีคุกกี้

    def post(self, token=None):
        headers = {} if token is None else {"X-Stop-Token": token}
        return self.client.post("/local-stop", headers=headers)

    def test_does_not_exist_unless_the_launcher_set_a_key(self):
        with mock.patch.object(self.main, "LOCAL_STOP_TOKEN", ""), \
                mock.patch.object(self.main.threading, "Timer") as timer:
            self.assertEqual(self.post("anything").status_code, 404)
            self.assertEqual(self.post("").status_code, 404)
        self.assertFalse(timer.called)

    def test_a_wrong_or_missing_key_is_refused(self):
        with mock.patch.object(self.main, "LOCAL_STOP_TOKEN", "right-key"), \
                mock.patch.object(self.main.threading, "Timer") as timer, \
                mock.patch.object(self.main, "_remove_book_folder") as removed:
            self.assertEqual(self.post("wrong-key").status_code, 403)
            self.assertEqual(self.post().status_code, 403)
        self.assertFalse(timer.called or removed.called)

    def test_the_right_key_removes_kept_books_then_exits(self):
        with mock.patch.object(self.main, "LOCAL_STOP_TOKEN", "right-key"), \
                mock.patch.object(self.main.threading, "Timer") as timer, \
                mock.patch.object(self.main, "_remove_book_folder") as removed:
            response = self.post("right-key")
        self.assertEqual(response.status_code, 200)
        removed.assert_called_once_with(self.main.BOOKS_DIR)
        timer.assert_called_once()
        self.assertIs(timer.call_args.args[1], self.main.os._exit)

    def test_a_get_cannot_stop_it(self):
        with mock.patch.object(self.main, "LOCAL_STOP_TOKEN", "right-key"), \
                mock.patch.object(self.main.threading, "Timer") as timer:
            self.assertNotEqual(self.client.get("/local-stop").status_code, 200)
        self.assertFalse(timer.called)


class IdleStopTests(unittest.TestCase):
    """เจ้าของตกลง 15 ต.ค. 2569: ระบบปิดเองเมื่อไม่มีหน้าไหนของระบบเปิดอยู่ ~10 นาที
    แต่ห้ามปิดระหว่างตรวจ และปิดแบบเดียวกับ /local-stop (ลบไฟล์เล่มที่เก็บไว้ก่อนออก)"""

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("APP_PASSWORD", "test-password")
        import main
        from fastapi.testclient import TestClient
        cls.main = main
        cls.client = TestClient(main.app)                 # ไม่ได้ล็อกอิน — หน้าล็อกอินก็ต้องส่งสัญญาณได้

    def setUp(self):
        for target in (mock.patch.dict(self.main._activity, {"last": 1000.0, "in_flight": 0}),
                       mock.patch.dict(self.main.JOBS, {}, clear=True)):
            target.start()
            self.addCleanup(target.stop)
        self.limit = self.main.IDLE_STOP_SECONDS

    def test_waits_ten_minutes_by_default(self):
        self.assertEqual(self.limit, 10 * 60)

    def test_stops_only_after_the_whole_quiet_period(self):
        self.assertFalse(self.main._idle_should_stop(1000.0 + self.limit - 1))
        self.assertTrue(self.main._idle_should_stop(1000.0 + self.limit))

    def test_never_stops_while_a_book_is_being_checked(self):
        self.main.JOBS["job"] = {"done": False}
        later = 1000.0 + self.limit * 5
        self.assertFalse(self.main._idle_should_stop(later))
        # ปิดหน้าเว็บระหว่างตรวจ: นับ 10 นาทีหลังตรวจเสร็จ ไม่ใช่ปิดทันทีที่เสร็จ
        self.main.JOBS["job"]["done"] = True
        self.assertFalse(self.main._idle_should_stop(later + 1))
        self.assertTrue(self.main._idle_should_stop(later + self.limit))

    def test_never_stops_during_an_upload_or_any_unfinished_request(self):
        self.main._activity["in_flight"] = 1
        self.assertFalse(self.main._idle_should_stop(1000.0 + self.limit * 5))

    def test_the_page_heartbeat_needs_no_login_and_counts_as_use(self):
        self.main._activity["last"] = 0.0
        response = self.client.post("/alive")
        self.assertEqual(response.status_code, 200)
        self.assertGreater(self.main._activity["last"], 0.0)
        self.assertEqual(self.main._activity["in_flight"], 0)

    def test_the_launchers_health_probe_is_not_use(self):
        self.client.get("/health")
        self.assertEqual(self.main._activity["last"], 1000.0)

    def test_waking_the_computer_from_sleep_starts_the_count_again(self):
        """เครื่อง sleep นานกว่า 10 นาที ตื่นมาแล้วต้องไม่ปิดใส่หน้าเว็บที่ยังไม่ทันส่งสัญญาณ"""
        before = 1000.0 + 5
        after = before + self.limit * 6
        self.assertFalse(self.main._idle_tick(before, after))
        self.assertEqual(self.main._activity["last"], after)
        step = self.main.IDLE_CHECK_SECONDS
        self.assertFalse(self.main._idle_tick(after, after + step))

    def test_the_watcher_stops_the_checker_once_it_has_been_quiet(self):
        step = self.main.IDLE_CHECK_SECONDS
        times = iter(1000.0 + step * n for n in range(1000))
        stop = mock.Mock()
        self.main._watch_idle_forever(sleep=lambda _s: None, clock=lambda: next(times), stop=stop)
        stop.assert_called_once_with()
        self.assertGreaterEqual(self.main._activity["last"], 1000.0)

    def test_stopping_removes_kept_books_like_the_stop_button(self):
        with mock.patch.object(self.main, "_remove_book_folder") as removed,                 mock.patch.object(self.main.os, "_exit") as exited,                 mock.patch("builtins.print"):
            self.main._stop_when_idle()
        removed.assert_called_once_with(self.main.BOOKS_DIR)
        exited.assert_called_once_with(0)

    def test_every_page_sends_the_heartbeat(self):
        self.assertIn("fetch('/alive'", self.client.get("/login").text)
        for name in ("index.html", "login.html", "report.html"):
            with self.subTest(page=name):
                text = (CODE / "templates" / name).read_text(encoding="utf-8")
                self.assertIn('{% include "_alive.html" %}', text)

    def test_only_the_background_launcher_turns_it_on(self):
        """แบบ --foreground / รันเอง ไม่มีกุญแจ = ไม่ปิดเอง (ปิดที่หน้าต่างเหมือนเดิม)"""
        script = ("import threading, main; "
                  "print(any(t.name == 'idle-stop' for t in threading.enumerate()))")
        for token, expected in (("", "False"), ("some-key", "True")):
            with self.subTest(token=bool(token)):
                env = dict(os.environ, APP_PASSWORD="test-password", ETHESIS_STOP_TOKEN=token)
                run = subprocess.run([sys.executable, "-c", script], cwd=CODE, env=env,
                                     capture_output=True, text=True, timeout=120)
                self.assertEqual(run.returncode, 0, run.stderr[-500:])
                self.assertEqual(run.stdout.strip().splitlines()[-1], expected)


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
             "อัปเดตระบบตรวจเล่ม.bat": "tools\\run_local.py --update",
             "ปิดระบบตรวจเล่ม.bat": "tools\\run_local.py --stop"}

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
