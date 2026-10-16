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
import zipfile
from pathlib import Path
from unittest import mock

CODE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(CODE / "tools"))

import release  # noqa: E402
sys.path.insert(0, str(CODE))
import release_check  # noqa: E402

os.environ["LOCALAPPDATA"] = tempfile.mkdtemp(prefix="ethesis-test-appdata-")
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


@unittest.skipIf(shutil.which("git") is None, "ไม่มี git")
class ReleaseCheckTests(GitRepos):
    """แถบ "มีอัปเดตใหม่" ถาม release_check ว่ามีรุ่นที่ออกแล้วแต่เครื่องนี้ยังไม่มีไหม (เจ้าของขอ 16 ต.ค. 2569)"""

    def check(self):
        return release_check.check(self.machine)

    def test_nothing_to_offer_before_any_release(self):
        self.assertFalse(self.check()["available"])

    def test_offers_a_release_the_machine_does_not_have_with_its_notes(self):
        self.commit("rules.txt", "rule 1\n", "rules")
        self.release("1.0")
        found = self.check()
        self.assertTrue(found["available"])
        self.assertEqual((found["current"], found["latest"]), ("", "1.0"))
        self.assertEqual(found["notes"], ["รุ่นแรก"])

    def test_nothing_to_offer_once_installed(self):
        self.release("1.0")
        self.update_machine()
        found = self.check()
        self.assertFalse(found["available"])
        self.assertEqual(found["current"], "1.0")

    def test_only_the_notes_the_machine_has_not_seen(self):
        self.release("1.0")
        self.update_machine()
        self.commit("CHANGELOG.md", changelog_with("1.1", "กฎข้อ ก"), "release 1.1")
        self.release("1.1")
        found = self.check()
        self.assertEqual((found["current"], found["latest"]), ("1.0", "1.1"))
        self.assertEqual(found["notes"], ["กฎข้อ ก"])

    def test_unreleased_work_on_main_is_never_offered(self):
        self.release("1.0")
        self.update_machine()
        self.commit("rules.txt", "half done\n", "unreleased work")
        self.git(self.dev, "push", "-q", "origin", "HEAD:main")
        self.assertFalse(self.check()["available"])

    def test_no_internet_means_no_bar_and_no_crash(self):
        self.git(self.machine, "remote", "set-url", "origin", str(self.machine.parent / "gone.git"))
        found = self.check()
        self.assertFalse(found["available"])
        self.assertTrue(found["error"])

    def test_a_folder_without_git_has_nothing_to_offer(self):
        self.assertFalse(release_check.check(self.machine.parent)["available"])


class ReleaseCheckFormatTests(unittest.TestCase):
    def test_reads_tags_and_changelog_the_same_way_as_the_updater(self):
        self.assertEqual(release_check.RELEASE_TAG.pattern, run_local.RELEASE_TAG.pattern)
        self.assertEqual(release_check.CHANGELOG_HEADING.pattern, run_local.CHANGELOG_HEADING.pattern)

    def test_a_wrapped_item_is_read_as_one_note(self):
        text = "## อัปเดต 1.2 — 16 ต.ค. 2569\n\n- บรรทัดแรก\n  ต่อบรรทัดสอง\n- ข้อสอง\n\n## อัปเดต 1.1 — 15 ต.ค. 2569\n\n- เก่า\n"
        self.assertEqual(release_check.changelog_notes(text, (1, 1, 0), (1, 2, 0)), ["บรรทัดแรก ต่อบรรทัดสอง", "ข้อสอง"])


class UpdateFromTheAppTests(unittest.TestCase):
    """ตัวอัปเดตที่ปุ่ม "อัปเดตเลย" เปิด ไม่มีหน้าต่าง — ผลต้องไปถึงหน้าเว็บทาง .run/update.json"""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        for target in (mock.patch.object(run_local, "CODE", Path(folder.name)),
                       mock.patch.object(run_local, "say")):
            target.start()
            self.addCleanup(target.stop)

    def state(self):
        return json.loads((run_local.run_dir() / "update.json").read_text(encoding="utf-8"))

    def test_a_failure_is_written_with_what_the_updater_said(self):
        def failing(**_):
            run_local.say("ดึงรุ่นใหม่ไม่สำเร็จ — ตรวจอินเทอร์เน็ต")
            return 1
        with mock.patch.object(run_local, "update", side_effect=failing):
            self.assertEqual(run_local.update_from_app(), 1)
        self.assertEqual(self.state()["state"], "failed")
        self.assertIn("ตรวจอินเทอร์เน็ต", self.state()["message"])

    def test_success_is_written_as_done(self):
        with mock.patch.object(run_local, "update", return_value=0):
            self.assertEqual(run_local.update_from_app(), 0)
        self.assertEqual(self.state()["state"], "done")

    def test_a_crash_still_reaches_the_page(self):
        with mock.patch.object(run_local, "update", side_effect=RuntimeError("boom")):
            self.assertEqual(run_local.update_from_app(), 1)
        self.assertEqual(self.state()["state"], "failed")
        self.assertIn("boom", self.state()["message"])

    def test_the_fallback_kill_spares_the_updater_itself(self):
        """ตัวอัปเดตเป็นลูกของระบบ — taskkill /T ฆ่าทั้งต้นจะฆ่าตัวอัปเดตไปด้วย"""
        with mock.patch.object(run_local, "update", return_value=0) as updated:
            run_local.update_from_app()
        kill = updated.call_args.kwargs["kill"]
        with mock.patch.object(run_local.os, "name", "nt"), \
                mock.patch.object(run_local.subprocess, "run") as ran:
            kill(1234)
        self.assertNotIn("/T", ran.call_args.args[0])

    def test_the_launcher_accepts_the_flag(self):
        with mock.patch.object(run_local, "update_from_app", return_value=0) as from_app:
            self.assertEqual(run_local.main(["--update", "--from-app"]), 0)
        from_app.assert_called_once_with()


# ---------- อัปเดตโดยไม่ใช้ Git (เครื่องที่ทำงานติดตั้งจาก ZIP ลง Git ไม่ได้ — 16 ต.ค. 2569) ----------

def program_tree(folder, version="1.2.1", extra=None):
    """โฟลเดอร์โปรแกรมขั้นต่ำที่ตัวติดตั้งยอมรับ (หน้าตาเหมือนโฟลเดอร์ที่แตกจาก ZIP ของ GitHub)"""
    folder = Path(folder)
    files = {"main.py": "# main\n", "checker.py": "# checker\n", "tools/run_local.py": "# launcher\n",
             "templates/index.html": "<p>index</p>\n",
             "CHANGELOG.md": f"# บันทึก\n\n## อัปเดต {version} — 16 ต.ค. 2569\n\n- แก้ให้ติดตั้งจาก ZIP ได้\n"
                             "  และอ่านเลขรุ่นได้\n\n## อัปเดต 1.2 — 16 ต.ค. 2569\n\n- แถบมีอัปเดตใหม่\n"}
    files.update(extra or {})
    for rel, text in files.items():
        (folder / rel).parent.mkdir(parents=True, exist_ok=True)
        (folder / rel).write_text(text, encoding="utf-8", newline="\n")
    return folder


def program_zip(target, version="1.2.1", extra=None, comment=b"0123456789abcdef0123456789abcdef01234567"):
    with tempfile.TemporaryDirectory() as work:
        root = program_tree(Path(work) / f"ethesis-checker-{version}", version, extra)
        with zipfile.ZipFile(target, "w") as archive:
            for path in root.rglob("*"):
                if path.is_file():
                    archive.write(path, f"{root.name}/{path.relative_to(root).as_posix()}")
            archive.comment = comment
    return target


class VersionNumberTests(unittest.TestCase):
    """เลขย่อย 1.2.1 = แก้เล็กน้อยไม่เพิ่มความสามารถ (เจ้าของสั่ง 16 ต.ค. 2569)"""

    def test_three_part_versions_sort_after_their_minor_release(self):
        self.assertLess(release_check.parse_label("1.2"), release_check.parse_label("1.2.1"))
        self.assertLess(release_check.parse_label("1.2.1"), release_check.parse_label("1.3"))
        self.assertEqual(release_check.parse_label("1.2"), release_check.parse_label("1.2.0"))

    def test_labels_drop_a_zero_patch(self):
        for version, text in (((1, 2, 0), "1.2"), ((1, 2, 1), "1.2.1"), ((1, 1), "1.1")):
            with self.subTest(text=text):
                self.assertEqual(run_local._label(version), text)
                self.assertEqual(release_check.label(version), text)

    def test_tags_and_headings_with_a_patch_number(self):
        found = run_local.release_tags(lambda *_: subprocess.CompletedProcess([], 0, stdout="v1.2\nv1.2.1\nv1.1\n"))
        self.assertEqual([name for _, name in found], ["v1.1", "v1.2", "v1.2.1"])
        entries = run_local.changelog_entries("## อัปเดต 1.2.1 — 16 ต.ค. 2569\n\n- ข้อ\n")
        self.assertEqual(entries[0][:2], ((1, 2, 1), "16 ต.ค. 2569"))

    def test_the_web_page_reads_a_patch_tag(self):
        os.environ.setdefault("APP_PASSWORD", "test-password")
        import main
        self.assertEqual(main._RELEASE_TAG.match("v1.2.1").group(1), "1.2.1")


class InstallTreeTests(TempFolder):
    """ตัวติดตั้งตัวเดียวที่ทั้งอัปเดตจากเครื่องและแบบ manual ใช้"""

    def setUp(self):
        super().setUp()
        self.dest = program_tree(self.dir / "installed", "1.2", {"old_only.py": "# gone in 1.2.1\n"})
        (self.dest / ".env").write_text("APP_PASSWORD=secret\n", encoding="utf-8")
        (self.dest / ".venv").mkdir()
        (self.dest / ".venv" / "keep.txt").write_text("venv\n", encoding="utf-8")
        self.src = program_tree(self.dir / "new", "1.2.1", {"new_file.py": "# new\n"})

    def test_installs_the_new_files_and_records_the_release(self):
        result = release_check.install_tree(self.src, self.dest, commit="ABCDEF0123456")
        self.assertEqual(result["release"], "1.2.1")
        self.assertTrue((self.dest / "new_file.py").is_file())
        self.assertEqual(release_check.installed_release(self.dest), "1.2.1")
        self.assertEqual(release_check.installed_commit(self.dest), "abcdef0")

    def test_never_touches_the_password_or_the_packages(self):
        (self.src / ".env").write_text("APP_PASSWORD=from-zip\n", encoding="utf-8")
        (self.src / ".venv").mkdir()
        (self.src / ".venv" / "keep.txt").write_text("other\n", encoding="utf-8")
        release_check.install_tree(self.src, self.dest)
        self.assertEqual((self.dest / ".env").read_text(encoding="utf-8"), "APP_PASSWORD=secret\n")
        self.assertEqual((self.dest / ".venv" / "keep.txt").read_text(encoding="utf-8"), "venv\n")

    def test_removes_only_files_the_previous_install_put_there(self):
        release_check.install_tree(program_tree(self.dir / "v12", "1.2", {"old_only.py": "# x\n"}), self.dest)
        (self.dest / "staff_note.txt").write_text("ของเจ้าหน้าที่\n", encoding="utf-8")
        result = release_check.install_tree(self.src, self.dest)
        self.assertIn("old_only.py", result["removed"])
        self.assertFalse((self.dest / "old_only.py").exists())
        self.assertTrue((self.dest / "staff_note.txt").exists())

    def test_the_first_install_over_a_zip_copy_deletes_nothing(self):
        result = release_check.install_tree(self.src, self.dest)
        self.assertEqual(result["removed"], [])
        self.assertTrue((self.dest / "old_only.py").exists())

    def test_unchanged_files_are_not_rewritten(self):
        """ไฟล์ .bat ที่กำลังรันอยู่ต้องไม่ถูกเขียนทับถ้าเนื้อหาเหมือนเดิม (cmd อ่านไฟล์ทีละบรรทัด)"""
        release_check.install_tree(self.src, self.dest)
        result = release_check.install_tree(self.src, self.dest)
        self.assertEqual(result["changed"], [])

    def test_refuses_a_developer_checkout_and_an_incomplete_folder(self):
        (self.dest / ".git").mkdir()
        with self.assertRaises(ValueError):
            release_check.install_tree(self.src, self.dest)
        (self.dest / ".git").rmdir()
        (self.src / "checker.py").unlink()
        with self.assertRaises(ValueError):
            release_check.install_tree(self.src, self.dest)

    def test_a_zip_from_github_installs_with_its_commit(self):
        archive = program_zip(self.dir / "v1.2.1.zip")
        work = self.dir / "work"
        result = release_check.install_zip(archive, self.dest, work)
        self.assertEqual(result["release"], "1.2.1")
        self.assertEqual(release_check.installed_commit(self.dest), "0123456")

    def test_a_zip_that_writes_outside_its_folder_is_refused(self):
        archive = self.dir / "bad.zip"
        with zipfile.ZipFile(archive, "w") as bad:
            bad.writestr("x/../../evil.py", "boom")
        with self.assertRaises(ValueError):
            release_check.install_zip(archive, self.dest, self.dir / "work")
        self.assertFalse((self.dir / "evil.py").exists())

    def test_the_release_shown_without_git(self):
        """ไม่มี installed.json = หัวข้อบนสุดของ CHANGELOG.md · มี = ตามที่ตัวติดตั้งบันทึก"""
        self.assertEqual(release_check.installed_release(self.dest), "1.2")
        os.environ.setdefault("APP_PASSWORD", "test-password")
        import main
        self.assertEqual(main._resolve_app_release(self.dest), "1.2")
        release_check.install_tree(self.src, self.dest, commit="abcdef1")
        self.assertEqual(main._resolve_app_release(self.dest), "1.2.1")
        self.assertEqual(main._resolve_app_version({}, self.dest), "abcdef1")


class FakeGitHub:
    """GitHub ปลอม: รายการแท็ก · CHANGELOG ของแท็ก · ZIP ของแท็ก — ไม่ออกเน็ตจริง"""

    def __init__(self, folder, tags=("v1.2", "v1.2.1"), online=True):
        self.folder, self.tags, self.online, self.urls = Path(folder), tags, online, []

    def __call__(self, url, timeout=None):
        self.urls.append(url)
        if not self.online:
            raise OSError("no network")
        if "/tags?" in url:
            return json.dumps([{"name": t, "commit": {"sha": "fedcba9876543210fedcba9876543210fedcba98"}}
                               for t in self.tags]).encode("utf-8")
        if url.endswith("/CHANGELOG.md"):
            return (self.folder / "CHANGELOG.md").read_bytes()
        if url.endswith(".zip"):
            return program_zip(self.folder / "dl.zip", "1.2.1").read_bytes()
        raise OSError(url)


class UpdateFromGitHubTests(TempFolder):
    """อัปเดตระบบตรวจเล่ม.bat / ปุ่ม "อัปเดตเลย" บนเครื่องที่ไม่มี .git"""

    def setUp(self):
        super().setUp()
        self.install = program_tree(self.dir / "installed", "1.2")
        (self.install / ".env").write_text("APP_PASSWORD=secret\n", encoding="utf-8")
        program_tree(self.dir, "1.2.1")                     # CHANGELOG ของรุ่นใหม่ที่ GitHub ปลอมส่ง
        for target in (mock.patch.object(run_local, "CODE", self.install),
                       mock.patch.object(run_local, "ensure_venv"),
                       mock.patch.object(run_local, "read_state", return_value=None)):
            target.start()
            self.addCleanup(target.stop)
        self.said = []
        said = mock.patch.object(run_local, "say", side_effect=lambda t="": self.said.append(str(t)))
        said.start()
        self.addCleanup(said.stop)

    def test_downloads_and_installs_the_newest_release(self):
        github = FakeGitHub(self.dir)
        self.assertEqual(run_local.update(get=github), 0, self.said)
        self.assertEqual(release_check.installed_release(self.install), "1.2.1")
        self.assertEqual((self.install / ".env").read_text(encoding="utf-8"), "APP_PASSWORD=secret\n")
        text = "\n".join(self.said)
        self.assertIn("ติดตั้งอัปเดต 1.2.1 แล้ว", text)
        self.assertIn("แก้ให้ติดตั้งจาก ZIP ได้", text)

    def test_nothing_to_download_when_already_on_the_newest(self):
        github = FakeGitHub(self.dir, tags=("v1.1", "v1.2"))
        self.assertEqual(run_local.update(get=github), 0)
        self.assertIn("รุ่นล่าสุดแล้ว", "\n".join(self.said))
        self.assertFalse(any(u.endswith(".zip") for u in github.urls))

    def test_no_internet_points_to_the_manual_way(self):
        self.assertEqual(run_local.update(get=FakeGitHub(self.dir, online=False)), 1)
        self.assertIn("ติดตั้งอัปเดตจากโฟลเดอร์นี้.bat", "\n".join(self.said))
        self.assertEqual(release_check.installed_release(self.install), "1.2")

    def test_the_banner_offers_it_on_a_zip_install(self):
        found = release_check.check(self.install, get=FakeGitHub(self.dir))
        self.assertTrue(found["available"])
        self.assertEqual((found["current"], found["latest"]), ("1.2", "1.2.1"))
        self.assertEqual(found["notes"], ["แก้ให้ติดตั้งจาก ZIP ได้ และอ่านเลขรุ่นได้"])
        offline = release_check.check(self.install, get=FakeGitHub(self.dir, online=False))
        self.assertFalse(offline["available"])
        self.assertTrue(offline["error"])


class InstallHereTests(TempFolder):
    """อัปเดตแบบ manual: แตก ZIP แล้วดับเบิลคลิก ติดตั้งอัปเดตจากโฟลเดอร์นี้.bat"""

    def setUp(self):
        super().setUp()
        self.target = program_tree(self.dir / "Documents" / "ethesis-checker-main" / "ethesis-checker-main", "1.1")
        (self.target / ".env").write_text("APP_PASSWORD=secret\n", encoding="utf-8")
        (self.target / ".venv").mkdir()
        self.source = program_tree(self.dir / "Downloads" / "ระบบตรวจเล่ม อัปเดต 1.2.1", "1.2.1")
        for target in (mock.patch.object(run_local, "CODE", self.source),
                       mock.patch.object(run_local, "say")):
            target.start()
            self.addCleanup(target.stop)
        self.run = mock.Mock(return_value=subprocess.CompletedProcess([], 0))

    def install(self, answers, finder=None):
        answers = iter(answers)
        return run_local.install_here(ask=lambda _prompt="": next(answers), python="py",
                                      finder=finder or (lambda _src: [self.target]), run=self.run)

    def test_stops_installs_and_reopens_the_system(self):
        self.assertEqual(self.install([""]), 0)
        self.assertEqual(release_check.installed_release(self.target), "1.2.1")
        self.assertEqual((self.target / ".env").read_text(encoding="utf-8"), "APP_PASSWORD=secret\n")
        commands = [c.args[0] for c in self.run.call_args_list]
        self.assertEqual(commands[0][-1], "--stop")
        self.assertEqual(commands[1], ["py", str(self.target / "tools" / "run_local.py")])

    def test_answering_no_changes_nothing(self):
        self.assertEqual(self.install(["n"]), 1)
        self.assertEqual(release_check.installed_release(self.target), "1.1")
        self.assertFalse(self.run.called)

    def test_asks_for_the_folder_when_it_cannot_find_one(self):
        self.assertEqual(self.install([f'"{self.target}"', ""], finder=lambda _src: []), 0)
        self.assertEqual(release_check.installed_release(self.target), "1.2.1")

    def test_finds_the_system_in_use_but_not_extracted_copies(self):
        program_tree(self.dir / "Downloads" / "old update 1.1", "1.1")            # แตกไว้ ไม่เคยใช้ (ไม่มี .env)
        dev = program_tree(self.dir / "Desktop" / "dev", "1.2")
        (dev / ".env").write_text("x\n", encoding="utf-8")
        (dev / ".git").mkdir()
        found = run_local.find_installs(self.source, roots=[self.dir / "Documents", self.dir / "Downloads",
                                                            self.dir / "Desktop"], remembered=[])
        self.assertEqual(found, [self.target.resolve()])

    def test_the_place_the_launcher_remembered_is_found_first(self):
        run_local.remember_install(self.target)
        self.assertEqual(run_local.find_installs(self.source, roots=[]), [self.target.resolve()])


@unittest.skipIf(shutil.which("git") is None, "ไม่มี git")
class ManualPackageTests(GitRepos):
    """release.py สร้าง dist/อัปเดต <เลข>.zip ไว้ส่งต่อ (LINE/อีเมล/แฟลชไดรฟ์)"""

    def test_a_patch_release_gets_its_tag_and_package(self):
        self.commit("CHANGELOG.md", changelog_with("1.0.1", "แก้บั๊ก"), "patch")
        self.release("1.0.1")
        self.assertEqual(self.git(self.dev, "describe", "--tags"), "v1.0.1")
        package = self.dev / "dist" / "อัปเดต 1.0.1.zip"
        with zipfile.ZipFile(package) as archive:
            names = archive.namelist()
            readme = [n for n in names if n.endswith("อ่านก่อน - อัปเดต 1.0.1.txt")]
            self.assertTrue(readme)
            self.assertIn("แก้บั๊ก", archive.read(readme[0]).decode("utf-8-sig"))
            self.assertIn("ระบบตรวจเล่ม อัปเดต 1.0.1/CHANGELOG.md", names)


class IdleStopTests(unittest.TestCase):
    """เจ้าของตกลง 15 ต.ค. 2569: ปิดหน้าระบบหมดแล้ว 1 นาที ระบบปิดเอง · เปิดหน้าค้างไว้ (สลับแท็บได้)
    ไม่ปิด · ห้ามปิดระหว่างตรวจ · ปิดแบบเดียวกับ /local-stop (ลบไฟล์เล่มที่เก็บไว้ก่อนออก)"""

    PAGE = "pageAAAA1111"

    @classmethod
    def setUpClass(cls):
        os.environ.setdefault("APP_PASSWORD", "test-password")
        import main
        from fastapi.testclient import TestClient
        cls.main = main
        cls.client = TestClient(main.app)                 # ไม่ได้ล็อกอิน — หน้าล็อกอินก็ต้องส่งสัญญาณได้

    def setUp(self):
        for target in (mock.patch.dict(self.main._activity, {"last": 1000.0, "in_flight": 0}),
                       mock.patch.dict(self.main._open_pages, {}, clear=True),
                       mock.patch.dict(self.main.JOBS, {}, clear=True)):
            target.start()
            self.addCleanup(target.stop)
        self.limit = self.main.IDLE_STOP_SECONDS
        self.lost = self.main.PAGE_LOST_SECONDS

    def open_page(self, page=PAGE, bye=False):
        url = f"/alive?page={page}" + ("&bye=1" if bye else "")
        self.assertEqual(self.client.post(url).status_code, 200)

    def test_stops_one_minute_after_the_last_page_closes(self):
        self.assertEqual(self.limit, 60)
        self.assertLess(self.limit, self.lost)

    def test_stops_only_after_the_whole_quiet_period(self):
        self.assertFalse(self.main._idle_should_stop(1000.0 + self.limit - 1))
        self.assertTrue(self.main._idle_should_stop(1000.0 + self.limit))

    def test_an_open_page_in_a_background_tab_keeps_it_running(self):
        """แท็บที่ซ่อนอยู่ เบราว์เซอร์หน่วงสัญญาณเหลือนาทีละครั้ง — ยังไม่บอกลา = ยังเปิดอยู่ ห้ามปิด"""
        self.open_page()
        seen = self.main._open_pages[self.PAGE]
        self.assertFalse(self.main._idle_should_stop(seen + self.limit * 5))
        self.assertFalse(self.main._idle_should_stop(seen + self.lost - 1))

    def test_closing_the_page_starts_the_one_minute_count(self):
        self.open_page()
        self.open_page(bye=True)
        self.assertEqual(self.main._open_pages, {})
        closed = self.main._activity["last"]
        # เวลาจริงของเครื่อง + 60 แล้วลบกลับได้ 59.999... บางค่า (CI Linux ตก 15 ต.ค. 2569) — ห่างจากขอบ 1 วินาที
        self.assertFalse(self.main._idle_should_stop(closed + self.limit - 1))
        self.assertTrue(self.main._idle_should_stop(closed + self.limit + 1))

    def test_one_page_closing_while_another_is_open_does_not_stop_it(self):
        self.open_page()
        self.open_page("pageBBBB2222")
        self.open_page(bye=True)
        last = self.main._activity["last"]
        self.assertFalse(self.main._idle_should_stop(last + self.limit * 3))

    def test_a_page_that_vanished_without_saying_goodbye_is_given_up_on(self):
        """เบราว์เซอร์ถูกฆ่า/ค้าง ส่งลาไม่ทัน — เงียบเกิน PAGE_LOST_SECONDS ถือว่าปิดแล้ว"""
        self.open_page()
        seen = self.main._open_pages[self.PAGE]
        self.assertTrue(self.main._idle_should_stop(seen + self.lost + 1))
        self.assertEqual(self.main._open_pages, {})

    def test_never_stops_while_a_book_is_being_checked(self):
        self.main.JOBS["job"] = {"done": False}
        later = 1000.0 + self.lost * 5
        self.assertFalse(self.main._idle_should_stop(later))
        self.main.JOBS["job"]["done"] = True
        self.assertFalse(self.main._idle_should_stop(later + 1))
        self.assertTrue(self.main._idle_should_stop(later + self.limit))

    def test_never_stops_during_an_upload_or_any_unfinished_request(self):
        self.main._activity["in_flight"] = 1
        self.assertFalse(self.main._idle_should_stop(1000.0 + self.lost * 5))

    def test_the_page_signal_needs_no_login_and_counts_as_use(self):
        self.main._activity["last"] = 0.0
        self.open_page()
        self.assertGreater(self.main._activity["last"], 0.0)
        self.assertEqual(self.main._activity["in_flight"], 0)
        self.assertIn(self.PAGE, self.main._open_pages)

    def test_odd_page_codes_are_ignored(self):
        for page in ("", "short", "has space here", "x" * 65, "<script>alert(1)"):
            with self.subTest(page=page):
                self.assertEqual(self.client.post("/alive", params={"page": page}).status_code, 200)
        self.assertEqual(self.main._open_pages, {})

    def test_the_launchers_health_probe_is_not_use(self):
        self.client.get("/health")
        self.assertEqual(self.main._activity["last"], 1000.0)

    def test_waking_the_computer_from_sleep_starts_the_count_again(self):
        """เครื่อง sleep นาน ตื่นมาแล้วต้องไม่ปิดใส่หน้าเว็บที่ยังไม่ทันส่งสัญญาณ"""
        self.main._open_pages[self.PAGE] = 1000.0
        before = 1000.0 + 5
        after = before + self.lost * 3
        self.assertFalse(self.main._idle_tick(before, after))
        self.assertEqual(self.main._activity["last"], after)
        self.assertEqual(self.main._open_pages[self.PAGE], after)
        self.assertFalse(self.main._idle_tick(after, after + self.main.IDLE_CHECK_SECONDS))

    def test_the_watcher_stops_the_checker_once_it_has_been_quiet(self):
        step = self.main.IDLE_CHECK_SECONDS
        times = iter(1000.0 + step * n for n in range(1000))
        stop = mock.Mock()
        self.main._watch_idle_forever(sleep=lambda _s: None, clock=lambda: next(times), stop=stop)
        stop.assert_called_once_with()

    def test_stopping_removes_kept_books_like_the_stop_button(self):
        with mock.patch.object(self.main, "_remove_book_folder") as removed,                 mock.patch.object(self.main.os, "_exit") as exited,                 mock.patch("builtins.print"):
            self.main._stop_when_idle()
        removed.assert_called_once_with(self.main.BOOKS_DIR)
        exited.assert_called_once_with(0)

    def test_every_page_says_hello_and_goodbye(self):
        page = self.client.get("/login").text
        self.assertIn("fetch('/alive?page='", page)
        self.assertIn("sendBeacon('/alive?page=' + page + '&bye=1')", page)
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
             "ปิดระบบตรวจเล่ม.bat": "tools\\run_local.py --stop",
             "ติดตั้งอัปเดตจากโฟลเดอร์นี้.bat": "tools\\run_local.py --install-here"}

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
