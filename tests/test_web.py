import hashlib
import hmac
import json
import os
import shutil
import subprocess
import sys
import time
import unittest
from unittest import mock
from pathlib import Path

os.environ.setdefault("MAX_UPLOAD_MB", "1")
os.environ.setdefault("APP_PASSWORD", "test-password")

from fastapi.testclient import TestClient

import main


FORM = {
    "doc_type": "THESIS",
    "format": "1",
    "program_language": "international",
    "chapters_mode": "strict",
    "title_en": "TEST TITLE",
    "student_name": "TEST STUDENT",
    "student_id": "6000000 TEST/M",
    "degree_cover_en": "MASTER OF ENGINEERING",
    "degree_sig_en": "Master of Engineering",
    "degree_abbr_en": "M.Eng.",
    "exam_date": "17 July 2026",
    "year": "2026",
}


def make_pdf(text="Hello PDF"):
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
        b"<< /Length " + str(len(stream)).encode("ascii") + b" >>\nstream\n" + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    pdf = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for number, obj in enumerate(objects, start=1):
        offsets.append(len(pdf))
        pdf.extend(f"{number} 0 obj\n".encode("ascii"))
        pdf.extend(obj)
        pdf.extend(b"\nendobj\n")
    xref = len(pdf)
    pdf.extend(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    pdf.extend(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        pdf.extend(f"{offset:010d} 00000 n \n".encode("ascii"))
    pdf.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("ascii")
    )
    return bytes(pdf)


class StaffButtonsReachTheSummaryEndpoint(unittest.TestCase):
    """ปุ่ม "โครงสร้างหน้าลงนามผิด" บนหน้ารายงานต้องไปถึงข้อความสรุปจริง

    หน้าเว็บส่ง POST /summary/<job> พร้อม id ของจุดที่เจ้าหน้าที่กด แล้วเอาข้อความ
    ที่ได้กลับมาใส่กล่องคัดลอก ถ้าปลายทางไม่อ่านคีย์ staff ปุ่มจะกดได้แต่ไม่มีอะไร
    เกิดขึ้น ซึ่งเป็นอาการที่มองไม่ออกจากหน้าจอจนกว่าจะอ่านข้อความที่คัดลอกมาทั้งก้อน
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        response = cls.client.post(
            "/login",
            data={"password": "test-password", "next": "/"},
            follow_redirects=False,
        )
        if response.status_code != 303:
            raise RuntimeError("Test login failed")

    def tearDown(self):
        with main.JOBS_LOCK:
            main.JOBS.clear()

    def _seed_clean_report(self):
        """เล่มที่ระบบไม่พบจุดผิดเลย — กรณีที่ปุ่มนี้ต้องทำงานให้ได้แน่ ๆ"""
        import checker
        with main.JOBS_LOCK:
            main.JOBS["demo"] = {
                "stage": "เสร็จ", "done": True, "error": None,
                "report": checker.check_result(checker.Report()),
                "pdf_name": "book.pdf", "approved": {}, "ts": time.time(),
            }
        return "demo"

    def _summary(self, **payload):
        job = self._seed_clean_report()
        response = self.client.post(f"/summary/{job}", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["plain"]

    def test_pressing_nothing_leaves_the_summary_alone(self):
        """เล่มที่ผ่านแต่ยังไม่กด ได้แค่บรรทัดผลตรวจ ขั้นตอนถัดไปผูกกับการกดปุ่ม"""
        text = self._summary(failed=[], passed=[])
        self.assertIn("ไม่พบจุดที่ต้องแก้ไข", text)
        self.assertNotIn("ขั้นตอนถัดไป", text)

    def test_pressing_the_button_adds_the_wording(self):
        import checker
        text = self._summary(failed=[], passed=[], staff=["SIGNATURE_LAYOUT_WRONG"])
        wording = checker.STAFF_CHOICE_BY_ID["SIGNATURE_LAYOUT_WRONG"][1]["text"]
        for line in wording.split(chr(10)):
            if line.strip():
                self.assertIn(line.strip(), text)
        self.assertTrue(text.startswith("ผลการตรวจ: ไม่ผ่าน"), text[:40])

    def test_the_fee_answer_is_appended_without_being_counted(self):
        """เล่มนี้ไม่มีจุดต้องแก้ ถ้อยคำที่ต่อท้ายจึงเป็นชุด "เสร็จสิ้นแล้ว"
        ไม่ใช่ชุด "แก้แล้วส่งกลับ" — และยังไม่ถูกนับเป็นจุดที่ต้องแก้เหมือนเดิม
        """
        text = self._summary(failed=[], passed=[], staff=["PASS_FEE_NONE"])
        # ถ้อยคำชุดผ่านบอกเองว่าเสร็จสิ้นและยังมีขั้นตอนต่อ จึงไม่มีบรรทัด
        # "ไม่พบจุดที่ต้องแก้ไข" ซ้ำอีก
        self.assertNotIn("ไม่พบจุดที่ต้องแก้ไข", text)
        self.assertIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", text)
        self.assertNotIn("กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง", text)
        self.assertIn("https://bit.ly/4cwqxAd", text)
        self.assertTrue(text.rstrip().endswith("supawit.mar@mahidol.ac.th"))

    def test_the_endpoint_returns_the_verdict_its_own_text_says(self):
        """หน้ารายงานใช้ค่า verdict นี้เลือกหัวข้อค่าปรับที่จะโชว์

        ถ้าสองค่าหลุดจากกัน เจ้าหน้าที่จะเห็นหัวข้อที่กดแล้วฝั่งเซิร์ฟเวอร์ทิ้งทุกครั้ง
        """
        job = self._seed_clean_report()
        for staff in ([], ["PASS_FEE_NONE"], ["SIGNATURE_LAYOUT_WRONG"]):
            response = self.client.post(f"/summary/{job}",
                                        json={"failed": [], "passed": [],
                                              "staff": staff})
            self.assertEqual(response.status_code, 200, response.text)
            body = response.json()
            self.assertEqual(body["plain"].split(chr(10))[0],
                             "ผลการตรวจ: " + body["verdict"], staff)

    def test_the_report_page_marks_which_verdict_each_topic_needs(self):
        """หน้ารายงานซ่อนหัวข้อจาก data-applies ถ้า attribute นี้ไม่ถูกฝังมา
        สคริปต์จะปล่อยผ่านทุกหัวข้อ แล้วหัวข้อค่าปรับสองอันจะโชว์พร้อมกัน
        """
        job = self._seed_clean_report()
        html = self.client.get(f"/result/{job}").text
        self.assertIn('data-applies="pass"', html)
        self.assertIn('data-applies="not_pass"', html)
        # หัวข้อที่ใช้ได้ทุกผลตรวจต้องได้ค่าว่าง ไม่ใช่คำว่า Undefined จาก Jinja
        self.assertIn('data-applies=""', html)
        self.assertNotIn("data-applies=\"Undefined", html)

    def test_the_endpoint_hands_back_the_three_numbers(self):
        """หน้ารายงานเขียนตัวเลขสามกล่องจากค่านี้ ไม่ได้นับเอง

        ถ้าปลายทางไม่ส่ง counts มา ตัวเลขจะค้างที่ค่าตอนโหลดหน้าโดยไม่มีอะไรบอก
        ทั้งที่เจ้าหน้าที่กดไปแล้ว
        """
        job = self._seed_clean_report()
        body = self.client.post(f"/summary/{job}",
                                json={"failed": [], "passed": [],
                                      "staff": ["SIGNATURE_LAYOUT_WRONG"]}).json()
        self.assertEqual(body["counts"], {"RED": 1, "ORANGE": 0, "YELLOW": 0})

    def test_the_report_page_can_find_the_three_numbers_to_rewrite(self):
        """สคริปต์หาช่องตัวเลขด้วย id ถ้า id หาย ปุ่มจะกดได้แต่ตัวเลขไม่ขยับ"""
        job = self._seed_clean_report()
        html = self.client.get(f"/result/{job}").text
        for box in ("stat-red", "stat-orange", "stat-yellow"):
            self.assertIn(f'<b id="{box}">', html)
        self.assertIn("renderZoneCounts(data.counts)", html)

    def test_the_page_sends_accepted_notices_too(self):
        """ปุ่ม ✓ ของข้อสังเกตเคยไม่ถูกส่งไปเลย กดแล้วไม่มีอะไรเกิดขึ้นสักอย่าง

        เซิร์ฟเวอร์รองรับคีย์ YELLOW:n ในช่อง passed อยู่แล้ว (ตัวเลขกล่องข้อสังเกต
        ลดลง) ที่ขาดคือหน้าเว็บไม่เคยเก็บมาส่ง เพราะตัวเลือกจำกัดไว้แค่สีส้ม
        """
        job = self._seed_clean_report()
        html = self.client.get(f"/result/{job}").text
        block = html.split("function collectPassed()", 1)[1][:400]
        self.assertIn('[data-zone="YELLOW"]', block)
        self.assertIn('[data-zone="ORANGE"]', block)
        # ห้ามฝังชื่อโซนไว้ตายตัว ไม่งั้นคีย์ของสีเหลืองจะถูกส่งเป็น ORANGE:n
        self.assertNotIn("'ORANGE:' +", block)

    def test_the_purple_list_has_no_pass_fail_buttons(self):
        """เจ้าหน้าที่สั่งเอาออก (ก.ย. 2569) — ปุ่ม ✗ ของการ์ดสีม่วงเคยกดได้แต่ไม่ส่ง
        อะไรไปเลย เพราะ setPF เรียกอัปเดตสรุปเฉพาะการ์ด .issue กับ .sf

        ปุ่มของข้อสีส้ม/เหลืองและของหัวข้อที่เจ้าหน้าที่ตัดสินเองต้องอยู่ครบเหมือนเดิม
        การกด "ไม่ผ่าน" ตรงนั้นมีค่าเท่าสีแดง จะเอาออกไม่ได้
        """
        import checker
        rep = checker.Report()
        rep.add_human("หน้าลงนาม 1 (หน้า i)", "โปรดทานรายชื่อกรรมการเอง")
        with main.JOBS_LOCK:
            main.JOBS["purple"] = {
                "stage": "เสร็จ", "done": True, "error": None,
                "report": checker.check_result(rep),
                "pdf_name": "book.pdf", "approved": {}, "ts": time.time(),
            }
        html = self.client.get("/result/purple").text
        # ต้องมีการ์ดสีม่วงจริง ไม่งั้นเทสต์ผ่านลอย ๆ โดยไม่ได้ตรวจอะไรเลย
        self.assertIn('<div class="hc">', html)
        # ตัดแค่ตัวการ์ด — ถ้าปล่อยยาวจะไปเจอ setPF ในสคริปต์ท้ายหน้าแล้วตกทุกครั้ง
        card = html.split('<div class="hc">', 1)[1].split('note-view', 1)[0]
        self.assertNotIn("setPF", card)
        # การ์ดของหัวข้อที่เจ้าหน้าที่ตัดสินเองยังต้องมีปุ่มอยู่
        self.assertIn('<div class="hc sf"', html)
        self.assertIn("setPF", html.split('<div class="hc sf"', 1)[1][:1500])

    def test_a_junk_value_from_the_page_is_ignored(self):
        text = self._summary(failed=[], passed=[], staff=["nope", 1, None])
        self.assertTrue(text.startswith("ผลการตรวจ: ผ่าน"), text[:40])
        self.assertNotIn("ค่าปรับ", text)


class TheResultStaysUsableForAWholeWorkingDay(unittest.TestCase):
    """ปุ่มเจ้าหน้าที่ต้องยังกดได้หลังเปิดรายงานค้างไว้นาน และหลังตรวจเล่มถัดไป

    เจ้าหน้าที่แจ้ง (ก.ย. 2569) ว่ากดปุ่ม "โครงสร้างหน้าลงนาม" กับ "ค่าปรับ" ไม่ได้
    แล้วได้ข้อความ "อัปเดตข้อความสรุปไม่สำเร็จ ... กรุณากดปุ่มนั้นซ้ำอีกครั้ง"
    ต้นเหตุคือ JOB_TTL 30 นาที บวกกับการล้างของเก่าตอนเริ่มตรวจเล่มถัดไป — ผลตรวจ
    ของเล่มที่เปิดค้างไว้หายจากเซิร์ฟเวอร์ POST /summary จึงได้ 404 และคำแนะนำ
    "กดซ้ำอีกครั้ง" ก็ไม่มีวันสำเร็จ
    """

    def tearDown(self):
        with main.JOBS_LOCK:
            main.JOBS.clear()

    def _put(self, key, age_seconds, done=True):
        with main.JOBS_LOCK:
            main.JOBS[key] = {"stage": "เสร็จ", "done": done, "error": None,
                              "report": {}, "pdf_name": f"{key}.pdf",
                              "approved": {}, "ts": time.time() - age_seconds}

    def test_an_hour_old_result_survives_the_next_check(self):
        self._put("old", 3600)
        main._prune_jobs()
        self.assertIn("old", main.JOBS)

    def test_a_result_older_than_the_ttl_is_dropped(self):
        self._put("ancient", main.JOB_TTL + 60)
        main._prune_jobs()
        self.assertNotIn("ancient", main.JOBS)

    def test_the_ttl_covers_a_working_day(self):
        """ควบคุมเชิงลบ: ค่าเดิม 1800 วินาที (30 นาที) สั้นกว่าการตรวจเล่มเดียวจบ"""
        self.assertGreaterEqual(main.JOB_TTL, 8 * 3600)

    def test_only_the_oldest_are_dropped_when_there_are_too_many(self):
        for i in range(main.MAX_KEPT_JOBS + 3):
            self._put(f"j{i:03d}", 1000 - i)      # เลขมาก = ใหม่กว่า
        main._prune_jobs()
        self.assertEqual(len(main.JOBS), main.MAX_KEPT_JOBS)
        newest = f"j{main.MAX_KEPT_JOBS + 2:03d}"
        self.assertIn(newest, main.JOBS)
        self.assertNotIn("j000", main.JOBS)

    def test_a_job_still_running_is_never_dropped(self):
        self._put("running", main.JOB_TTL * 10, done=False)
        for i in range(main.MAX_KEPT_JOBS + 3):
            self._put(f"j{i:03d}", 10)
        main._prune_jobs()
        self.assertIn("running", main.JOBS)


class TheReportPageSaysWhyTheButtonFailed(unittest.TestCase):
    """404 = ผลตรวจไม่อยู่แล้ว กดซ้ำกี่ครั้งก็ไม่สำเร็จ ต้องไม่บอกให้กดซ้ำ"""

    def setUp(self):
        from pathlib import Path
        self.html = (Path(main.__file__).resolve().parent
                     / "templates" / "report.html").read_text(encoding="utf-8")

    def test_the_page_treats_a_missing_result_as_its_own_case(self):
        self.assertIn("if (r.status === 404) return Promise.reject(new Error('gone'));",
                      self.html)

    def test_that_case_has_wording_in_both_languages(self):
        self.assertIn("gone: {th:", self.html)
        self.assertIn("ผลตรวจนี้ไม่อยู่บนเซิร์ฟเวอร์แล้ว", self.html)
        self.assertIn("This result is no longer on the server", self.html)

    def test_the_missing_result_message_does_not_tell_them_to_press_again(self):
        gone = self.html[self.html.index("gone: {th:"):self.html.index("other: {th:")]
        self.assertNotIn("กดปุ่มนั้นซ้ำ", gone)
        self.assertIn("อัปโหลดไฟล์ตรวจใหม่", gone)

    def test_the_handler_picks_the_message_by_error_name(self):
        """ควบคุมเชิงลบ: ของเดิมเลือกได้แค่ login กับ other ข้อความใหม่จะไม่มีวันโผล่"""
        self.assertIn("var kind = (err && COPY_ERROR[err.message]) "
                      "? err.message : 'other';", self.html)


class WebSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        response = cls.client.post(
            "/login",
            data={"password": "test-password", "next": "/"},
            follow_redirects=False,
        )
        if response.status_code != 303:
            raise RuntimeError("Test login failed")

    def tearDown(self):
        with main.JOBS_LOCK:
            main.JOBS.clear()

    def test_health_and_home(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/").status_code, 200)

    def test_home_requires_login(self):
        anonymous = TestClient(main.app)
        response = anonymous.get("/", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/login"))

    def test_expired_session_returns_json_not_login_html(self):
        """เซสชันหมดอายุระหว่างกรอกฟอร์ม ต้องได้ 401 JSON ไม่ใช่หน้า login

        ถ้าตอบเป็น redirect 303 fetch จะตามไปได้ HTML กลับมา แล้ว resp.json()
        พังเป็น "Unexpected token '<', \"<!doctype \"... is not valid JSON"
        ซึ่งเจ้าหน้าที่อ่านไม่รู้เรื่องและดูเหมือนระบบตรวจเล่มไม่ได้
        """
        anonymous = TestClient(main.app)
        for path in ("/check", "/parse-ethesis", "/summary/anything"):
            with self.subTest(path=path):
                response = anonymous.post(path, follow_redirects=False)
                self.assertEqual(response.status_code, 401)
                self.assertTrue(response.json()["login_required"])
                self.assertIn("เข้าสู่ระบบ", response.json()["detail"])

    def test_page_navigation_still_redirects_to_login(self):
        # การเปิดหน้าเว็บตรง ๆ ต้องยังพาไปหน้า login เหมือนเดิม ไม่ใช่โชว์ JSON
        anonymous = TestClient(main.app)
        for path in ("/", "/result/anything"):
            with self.subTest(path=path):
                response = anonymous.get(path, follow_redirects=False)
                self.assertEqual(response.status_code, 303)
                self.assertTrue(response.headers["location"].startswith("/login"))

    def test_session_survives_process_restart(self):
        """คุกกี้ต้องยังใช้ได้หลัง Render cold start ไม่งั้นฟอร์มที่กรอกค้างไว้จะสูญ

        กุญแจเซ็นคำนวณจาก APP_PASSWORD (+ SESSION_SECRET) จึงได้เท่าเดิมทุกครั้งที่
        process เริ่มใหม่ ไม่ได้เก็บเซสชันไว้ในหน่วยความจำ
        """
        restarted = main.derive_session_key(main.APP_PASSWORD,
                                            os.environ.get("SESSION_SECRET", ""))
        self.assertEqual(main.SESSION_KEY, restarted)
        # เปลี่ยนรหัสผ่าน = เตะทุกเซสชันออก
        self.assertNotEqual(main.SESSION_KEY,
                            main.derive_session_key("another-password"))

    def test_every_login_gets_its_own_cookie(self):
        """/code-review (ก.ย. 2569): ของเดิมทุกคนได้คุกกี้ค่าเดียวกันตลอดไป"""
        self.assertNotEqual(main.new_session_token(), main.new_session_token())

    def test_the_server_expires_the_cookie_itself(self):
        """ของเดิม 8 ชั่วโมงมีผลแค่ในเบราว์เซอร์ คุกกี้ที่หลุดไปใช้ได้ไม่มีวันหมด"""
        issued = 1_800_000_000
        token = main.new_session_token(now=issued)
        self.assertTrue(main.session_token_valid(token, now=issued + 60))
        self.assertTrue(main.session_token_valid(token, now=issued + main.SESSION_MAX_AGE - 1))
        self.assertFalse(main.session_token_valid(token, now=issued + main.SESSION_MAX_AGE))

    def test_a_tampered_cookie_is_rejected(self):
        """ควบคุมเชิงลบ — ยืดอายุด้วยการแก้เวลาในคุกกี้เองต้องไม่ได้"""
        issued, nonce, signature = main.new_session_token(now=1_800_000_000).split(".")
        self.assertFalse(main.session_token_valid(
            f"{int(issued) + 86_400}.{nonce}.{signature}", now=1_800_086_400))
        for junk in ("", "abc", "1.2", "x.y.z", "1.2.3.4"):
            self.assertFalse(main.session_token_valid(junk), junk)

    def test_the_old_fixed_cookie_no_longer_works(self):
        """คุกกี้แบบเดิมคำนวณจากรหัสผ่านตรง ๆ เอาไปเดารหัสผ่านออฟไลน์ได้"""
        old = hmac.new(main.APP_PASSWORD.encode("utf-8"),
                       b"ethesis-session-v1", hashlib.sha256).hexdigest()
        self.assertFalse(main.session_token_valid(old))
        client = TestClient(main.app)
        client.cookies.set(main.SESSION_COOKIE, old)
        self.assertEqual(client.get("/", follow_redirects=False).status_code, 303)

    def test_the_cookie_from_login_opens_the_app(self):
        client = TestClient(main.app)
        login = client.post("/login", data={"password": main.APP_PASSWORD, "next": "/"},
                            follow_redirects=False)
        self.assertEqual(login.status_code, 303)
        self.assertTrue(main.session_token_valid(client.cookies.get(main.SESSION_COOKIE)))
        self.assertEqual(client.get("/", follow_redirects=False).status_code, 200)

    def test_wrong_password_is_rejected(self):
        anonymous = TestClient(main.app)
        response = anonymous.post(
            "/login",
            data={"password": "wrong-password", "next": "/"},
            follow_redirects=False,
        )
        self.assertEqual(response.status_code, 401)

    def test_login_cookie_is_protected(self):
        anonymous = TestClient(main.app)
        response = anonymous.post(
            "/login",
            data={"password": "test-password", "next": "/"},
            follow_redirects=False,
        )
        cookie = response.headers["set-cookie"].lower()
        self.assertIn("httponly", cookie)
        self.assertIn("samesite=strict", cookie)

    def test_rejects_content_without_pdf_header(self):
        response = self.client.post(
            "/check",
            data=FORM,
            files={"pdf": ("fake.pdf", b"not a pdf", "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)

    def test_rejects_missing_strict_reference_field(self):
        form = {**FORM, "degree_sig_en": ""}
        response = self.client.post(
            "/check",
            data=form,
            files={"pdf": ("test.pdf", make_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("ชื่อปริญญา", response.json()["detail"])

    def test_rejects_corrupt_pdf(self):
        response = self.client.post(
            "/check",
            data=FORM,
            files={"pdf": ("broken.pdf", b"%PDF-1.4\nbroken", "application/pdf")},
        )
        self.assertEqual(response.status_code, 422)

    def test_accepts_readable_pdf(self):
        response = self.client.post(
            "/check",
            data=FORM,
            files={"pdf": ("readable.pdf", make_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200)
        job_id = response.json()["job_id"]
        for _ in range(100):
            job = main._get_job(job_id)
            if job and job["done"]:
                break
            time.sleep(0.01)
        self.assertTrue(main._get_job(job_id)["done"])

    def test_rejects_pdf_without_extractable_text(self):
        response = self.client.post(
            "/check",
            data=FORM,
            files={"pdf": ("image-only.pdf", make_pdf(""), "application/pdf")},
        )
        self.assertEqual(response.status_code, 422)

    def test_rejects_oversized_upload(self):
        content = b"%PDF-1.4\n" + b"x" * (1024 * 1024 + 1)
        response = self.client.post(
            "/check",
            data=FORM,
            files={"pdf": ("large.pdf", content, "application/pdf")},
        )
        self.assertEqual(response.status_code, 413)

    def test_report_lists_items_not_checked(self):
        report = {
            "verdict": "ผ่าน",
            "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []},
            "info": [],
            "human_checklist": [],
            "not_checked": ["scope marker"],
        }
        with main.JOBS_LOCK:
            main.JOBS["report-test"] = {
                "stage": "done", "done": True, "error": None, "report": report,
                "pdf_name": "test.pdf", "approved": {}, "ts": time.time(),
            }
        response = self.client.get("/result/report-test")
        self.assertEqual(response.status_code, 200)
        self.assertIn("scope marker", response.text)

    def test_check_accepts_committees_json(self):
        # ส่ง committees_json ที่พังก็ไม่ทำให้ /check ล้ม
        form = {**FORM, "committees_json": "{ broken json", "faculty": "คณะทดสอบ"}
        response = self.client.post(
            "/check", data=form,
            files={"pdf": ("readable.pdf", make_pdf(), "application/pdf")},
        )
        self.assertEqual(response.status_code, 200)

    def test_error_page_does_not_expose_internal_details(self):
        with main.JOBS_LOCK:
            main.JOBS["error-test"] = {
                "stage": "done", "done": True, "error": "secret traceback",
                "report": None, "pdf_name": "test.pdf", "approved": {},
                "ts": time.time(),
            }
        response = self.client.get("/result/error-test")
        self.assertEqual(response.status_code, 500)
        self.assertNotIn("secret traceback", response.text)


if __name__ == "__main__":
    unittest.main()


class ImportingASecondStudentClearsTheFirst(unittest.TestCase):
    """/code-review (ก.ย. 2569): นำเข้าข้อมูลนักศึกษาคนที่สองแล้วรายชื่อกรรมการคนแรกค้าง

    applyParsed เขียนทับเฉพาะช่องที่ข้อมูลใหม่มีค่า ไฟล์ของคนที่สองที่ไม่มีรายชื่อกรรมการ
    ช่องซ่อน committees_json จึงค้างรายชื่อของคนแรก แล้วเล่มของคนที่สองถูกนับกรรมการ
    เทียบกับรายชื่อคนอื่น ได้ข้อแดง "รายชื่อไม่ครบ/เกิน" ที่โชว์ชื่อของอีกคน
    """

    SOURCE = Path(__file__).resolve().parents[1] / "templates" / "index.html"

    SCRIPT = r"""
const vm = require('vm');
const fields = {'committees-json': {value: ''}, 'faculty-field': {value: ''},
                'program-field': {value: ''}, 'committee-box': {style: {display: 'none'}},
                'committee-summary': {innerHTML: ''}, 'title-th': {value: ''},
                'name-th': {value: ''}, 'abbr-th': {value: ''}, 'title-en': {value: ''},
                'doc-type': {tagName: 'SELECT', selectedIndex: 0, value: '',
                             options: [{defaultSelected: true}, {}, {}]}};
const byName = {faculty: 'faculty-field', program: 'program-field',
                committees_json: 'committees-json', title_th: 'title-th',
                student_name_th: 'name-th', degree_abbr_th: 'abbr-th', title_en: 'title-en',
                doc_type: 'doc-type'};
global.Event = class { constructor(type) { this.type = type; } };
global.document = {
  getElementById: id => fields[id] || null,
  querySelectorAll: () => [],
  querySelector: sel => {
    const f = fields[byName[(sel.match(/name="([^"]+)"/) || [])[1]]];
    if (!f) return null;
    f.classList = {add() {}, remove() {}};
    f.dispatchEvent = () => {};
    return f;
  }
};
vm.runInThisContext(require('fs').readFileSync(0, 'utf8'));
const read = () => [fields['committees-json'].value, fields['faculty-field'].value,
                    fields['program-field'].value, fields['committee-box'].style.display];
const visible = () => [fields['title-th'].value, fields['name-th'].value,
                       fields['abbr-th'].value, fields['title-en'].value,
                       fields['doc-type'].selectedIndex];
applyParsed({committees: {exam: [{name: 'A One'}]}, faculty: 'Faculty A', program: 'Program A',
             title_th: 'ชื่อเรื่องของ A', student_name_th: 'นาย เอ',
             degree_abbr_th: 'วท.ม. (A)', title_en: 'A TITLE'});
fields['doc-type'].selectedIndex = 2;
const first = read(), firstVisible = visible();
applyParsed({title_en: 'Student B'});
console.log(JSON.stringify({first, second: read(), firstVisible, secondVisible: visible()}));
"""

    def _block(self):
        html = self.SOURCE.read_text(encoding="utf-8")
        start = html.index("const IMPORT_LABELS")
        end = html.index("// ---- กล่องลากวางไฟล์ (คลิกก็ได้ ลากมาวางก็ได้) ----")
        return html[start:end]

    def _node_output(self):
        run = subprocess.run(["node", "-e", self.SCRIPT], input=self._block(),
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        return json.loads(run.stdout)

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_the_hidden_fields_are_cleared_by_the_next_import(self):
        out = self._node_output()
        # ควบคุมเชิงบวก — คนแรกต้องเติมได้จริง ไม่งั้นเทสต์ข้างล่างผ่านลอย ๆ
        self.assertIn("A One", out["first"][0])
        self.assertEqual(out["first"][1:], ["Faculty A", "Program A", ""])
        # คนที่สองไม่มีรายชื่อกรรมการ ต้องไม่เหลือของคนแรก
        self.assertEqual(out["second"], ["", "", "", "none"])

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_every_imported_field_is_cleared_not_only_the_hidden_ones(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569): ล้างทุกช่องที่ระบบเติมให้ ทุกครั้งที่นำเข้าข้อมูลใหม่

        วัดกับเล่มจริง: เล่มไทย-อังกฤษที่ชื่อเรื่องไทย/ชื่อไทย/ตัวย่อปริญญาไทยของคนก่อน
        ค้างอยู่ ได้ข้อแดงเพิ่มจาก 3 เป็น 6 ทั้งที่เล่มไม่ผิด
        """
        out = self._node_output()
        # ควบคุมเชิงบวก — คนแรกเติมได้จริง
        self.assertEqual(out["firstVisible"],
                         ["ชื่อเรื่องของ A", "นาย เอ", "วท.ม. (A)", "A TITLE", 2])
        # คนที่สองมีแค่ชื่อเรื่องอังกฤษ ช่องอื่นต้องว่าง และช่องเลือกกลับไปค่าตั้งต้น
        self.assertEqual(out["secondVisible"], ["", "", "", "Student B", 0])

    def test_coming_back_to_the_form_always_clears_it(self):
        """ "ทุกครั้งที่จะตรวจ ต้องกดตรวจเล่มใหม่อยู่แล้ว ก็ล้างค่าตรงนั้นเลย"

        ต้องผูกกับ pageshow ไม่ใช่แค่ตอนโหลด — กดย้อนกลับจากหน้ารายงาน เบราว์เซอร์คืนหน้า
        จากแคชพร้อมค่าเดิมทุกช่อง และหน้าจอ "กำลังตรวจ" ค้างทับอยู่
        """
        html = self.SOURCE.read_text(encoding="utf-8")
        self.assertIn("window.addEventListener('pageshow', resetCheckForm);", html)
        body = html.split("function resetCheckForm() {", 1)[1].split("\n}", 1)[0]
        for step in ("document.getElementById('f').reset();", "clearImportedFields();",
                     "document.getElementById('overlay').style.display = 'none';",
                     "updateConditionalRequirements();"):
            self.assertIn(step, body)


class TheTableBadgeMatchesTheCardColour(unittest.TestCase):
    """ป้ายในตารางผลเทียบต้องตรงกับสีของการ์ดที่คู่กัน (เจ้าหน้าที่สั่ง ก.ย. 2569)

    ที่มา: แถวรหัสนักศึกษาบนบทคัดย่อไทยที่ฟอนต์ทำตัวเลขเพี้ยนขึ้น "❓ รอยืนยัน" สีส้ม แต่ไม่มี
    การ์ดส้มให้กด เจ้าหน้าที่ถามว่าทำไมไม่เหมือนสีส้มอื่น แล้วสั่งว่า "ถ้ามันเพี้ยนแบบนี้ งั้นควร
    ใส่สีส้ม แก้กฎการตรวจให้สัมพันธ์กันด้วย" — ข้อฟอนต์เพี้ยนจึงเป็นการ์ดส้ม และป้ายในตาราง
    บอกสีของการ์ดที่คู่กัน
    """

    ROWS = (("รหัสนักศึกษา", "บทคัดย่อไทย (หน้า vi)", "pending", "ระบบอ่านตัวเลขบนหน้านี้ไม่ออก"),
            ("ชื่อปริญญา", "หน้าลงนาม 1 (หน้า i)", "notice", "ต่างเฉพาะวรรคตอน/ช่องว่าง"),
            ("ชื่อเรื่อง (ตาม บฑ.1)", "บทคัดย่อภาษาไทย", "skipped", "เล่มไม่มีหน้าบทคัดย่อภาษานี้"))

    def _render(self, rows):
        import jinja2
        import checker
        rep = checker.Report()
        for topic, loc, status, detail in rows:
            rep.add_verification(topic, loc, status, detail)
        env = jinja2.Environment(loader=jinja2.FileSystemLoader(
            str(Path(__file__).resolve().parents[1] / "templates")), autoescape=True)
        return env.get_template("report.html").render(
            report=checker.check_result(rep, {"n_pages": 10}), zone_label=main.ZONE_LABEL,
            job_id="t", pdf_name="book.pdf", student={})

    @staticmethod
    def _group(html, topic):
        start = html.index(topic, html.index('class="vf-group"'))
        return html[start:html.index("</details>", start)]

    def test_each_status_shows_the_colour_of_its_card(self):
        html = self._render(self.ROWS)
        self.assertIn("❓ รอยืนยัน", self._group(html, "รหัสนักศึกษา"))
        self.assertIn("ข้อสังเกต", self._group(html, "ชื่อปริญญา"))
        self.assertIn("— ไม่ได้ตรวจ", self._group(html, "ชื่อเรื่อง (ตาม บฑ.1)"))

    def test_a_missing_page_is_not_called_pending(self):
        """ควบคุมเชิงลบ — แถวที่ไม่มีการ์ดคู่กันต้องไม่ใช้คำว่า "รอยืนยัน" อีก"""
        group = self._group(self._render(self.ROWS[2:]), "ชื่อเรื่อง (ตาม บฑ.1)")
        self.assertNotIn("รอยืนยัน", group)
        self.assertNotIn("ข้อสังเกต", group)

    def test_a_group_with_passes_and_a_missing_page_says_checked_pages_match(self):
        rows = (("ชื่อเรื่อง (ตาม บฑ.1)", "หน้าปก", "pass", ""),) + self.ROWS[2:]
        self.assertIn("✓ ตรงทุกหน้าที่ตรวจ", self._group(self._render(rows), "ชื่อเรื่อง (ตาม บฑ.1)"))

    def test_the_badge_colours_follow_the_zones(self):
        html = self._render(self.ROWS)
        rule = lambda sel: html.split(sel + " {", 1)[1].split("}", 1)[0]
        self.assertIn("--orange", rule(".vf-badge.pending"))
        self.assertIn("--yellow", rule(".vf-badge.notice"))
        self.assertNotIn("--orange", rule(".vf-badge.skipped"))


class AForeignStudentsNameFillsTheThaiNameField(unittest.TestCase):
    """นักศึกษาต่างชาติ: ช่องชื่อไทยของ eThesis เป็นชื่ออังกฤษ ต้องดึงมาใส่ช่องชื่อไทย (ก.ย. 2569)

    เจ้าหน้าที่: "ก็ให้ดึงชื่อไทย ซึ่งก็จะเป็นชื่อภาษาอังกฤษ มา เพื่อไม่ให้ช่องว่าง" — หลักสูตรไทย/
    ไทย-อังกฤษบังคับกรอกช่องนี้ ถ้าว่างระบบไม่ให้ตรวจ ("แบบฟอร์มก่อนตรวจถ้ากรอกไม่ครบ ระบบไม่ตรวจ")
    """

    SOURCE = Path(__file__).resolve().parents[1] / "templates" / "index.html"

    def test_the_pdf_importer_fills_both_names(self):
        import ethesis_import
        for lines, want in (
                (["ชื่อ-สกุล", "MR. JOHN SMITH", "JOHN SMITH"], ("JOHN SMITH", "JOHN SMITH")),
                (["ชื่อ-สกุล MS. ANNA LEE"], ("ANNA LEE", "ANNA LEE")),
                (["ชื่อ-สกุล", "นาย โฆษิต เที่ยงตรง", "KOSITH THEINGTRONG"],
                 ("โฆษิต เที่ยงตรง", "KOSITH THEINGTRONG"))):
            self.assertEqual(ethesis_import.student_names(lines), want, lines)

    def test_a_thai_student_keeps_the_thai_name(self):
        """ควบคุมเชิงลบ — นักศึกษาไทยต้องไม่ถูกเขียนทับด้วยชื่ออังกฤษ"""
        import ethesis_import
        th, en = ethesis_import.student_names(["ชื่อ-สกุล", "น.ส. ธัญชนก โสภาคดิษฐ",
                                               "THANCHANOK SOPARDIT"])
        self.assertEqual((th, en), ("ธัญชนก โสภาคดิษฐ", "THANCHANOK SOPARDIT"))

    def test_an_empty_thai_name_is_still_refused(self):
        """ควบคุมเชิงลบ — กติกา "กรอกไม่ครบ ระบบไม่ตรวจ" ต้องยังอยู่"""
        from ethesis_rules import FRONT_MATTER_RULES
        for program in ("thai", "thai_english"):
            self.assertIn("student_name_th",
                          FRONT_MATTER_RULES["required_form_fields"][program], program)


class TheFormImportsFromTheEthesisFileOnly(unittest.TestCase):
    """หน้าอัปโหลดนำเข้าข้อมูลได้ทางเดียว คือแนบไฟล์ eThesis PDF (เจ้าหน้าที่สั่ง ก.ย. 2569)

    "ระบบตรวจสอบ เอาการก็อปวาง เพื่อกรอกข้อมูลออก คงไว้เพียงการแนบเอกสารเพื่ออ่านข้อมูลพอ"
    ของเดิมมีสองทาง และแต่ละทางมีตัวอ่านของตัวเอง (JS ในหน้าเว็บ กับ ethesis_import.py) ซึ่ง
    หลุดจากกันจริงแล้ว — ตารางตัวย่อปริญญาของทางวางข้อความขาด D.N.S. / D.P.A. / Dr. P.H. / M.P.A.
    """

    SOURCE = Path(__file__).resolve().parents[1] / "templates" / "index.html"

    def test_the_paste_box_and_its_parser_are_gone(self):
        html = self.SOURCE.read_text(encoding="utf-8")
        for gone in ('id="ethesis-source"', 'id="parse-ethesis"', 'id="tab-text"',
                     "parseEthesisText", "formatDegreeAbbreviation", "ethSourceHtml",
                     "addEventListener('paste'", "วางข้อความ"):
            self.assertNotIn(gone, html, gone)

    def test_the_file_import_is_still_there(self):
        """ควบคุมเชิงลบ — ทางแนบไฟล์ต้องยังครบ"""
        html = self.SOURCE.read_text(encoding="utf-8")
        for kept in ('id="ethesis-pdf"', 'id="parse-ethesis-pdf"', "fetch('/parse-ethesis'",
                     "function applyParsed(parsed)", "wireDrop('drop-ethesis'"):
            self.assertIn(kept, html, kept)

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_the_page_script_still_runs(self):
        html = self.SOURCE.read_text(encoding="utf-8")
        script = html.split("<script>", 1)[1].split("</script>", 1)[0]
        run = subprocess.run(["node", "-e", "new Function(require('fs').readFileSync(0, 'utf8'))"],
                             input=script, capture_output=True, text=True, encoding="utf-8",
                             timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)

    def test_the_upload_page_still_renders(self):
        client = TestClient(main.app)
        client.post("/login", data={"password": "test-password", "next": "/"},
                    follow_redirects=False)
        page = client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="parse-ethesis-pdf"', page.text)
        self.assertNotIn('id="ethesis-source"', page.text)


class AnIncompleteFormIsNeverChecked(unittest.TestCase):
    """ไม่ครบ = ไม่ตรวจ (เจ้าหน้าที่สั่ง ก.ย. 2569 "ถ้าไม่ครบ ต้องไม่ตรวจนะ")

    กันสามชั้น: เบราว์เซอร์ (required) · สคริปต์ก่อนส่ง (readyToCheck) · เซิร์ฟเวอร์ (/check ตอบ 400)
    หน้าจอบอกในแถบล่างว่าขาดขั้นไหน ช่องที่ขาดขึ้นกรอบแดง และเลื่อนไปช่องแรกให้
    """

    SOURCE = Path(__file__).resolve().parents[1] / "templates" / "index.html"

    @classmethod
    def setUpClass(cls):
        cls.html = cls.SOURCE.read_text(encoding="utf-8")
        cls.client = TestClient(main.app)
        cls.client.post("/login", data={"password": "test-password", "next": "/"},
                        follow_redirects=False)

    def tearDown(self):
        with main.JOBS_LOCK:
            paths = [job.get("pdf_path") for job in main.JOBS.values()]
            main.JOBS.clear()
        for path in paths:
            main._remove_book(path)

    def test_the_script_checks_before_it_sends(self):
        handler = self.html.split("document.getElementById('f').addEventListener('submit'", 1)[1]
        guard = handler.index("if (!readyToCheck()) return;")
        self.assertLess(guard, handler.index("fetch('/check'"))

    def test_the_browser_still_enforces_required_fields(self):
        """ควบคุมเชิงลบ — ห้ามปิดการตรวจ required ของเบราว์เซอร์ (ชั้นแรก)"""
        form_tag = self.html.split('<form id="f"', 1)[1].split(">", 1)[0]
        self.assertNotIn("novalidate", form_tag)

    def test_reading_the_form_never_fires_invalid_events(self):
        """checkValidity() ยิงเหตุการณ์ invalid ทุกช่องที่ว่าง ซึ่งตัวดัก invalid ถือเป็นการกดตรวจเล่ม

        ของที่ commit ไปรอบแรกเรียก checkValidity() ในตัวนับความพร้อม ตัวดักจึงเรียกซ้ำวนไม่รู้จบ
        วัดในเบราว์เซอร์ได้ราว 1,950 ครั้งต่อวินาทีตั้งแต่เปิดหน้า ข้อความ "ยังกรอกไม่ครบ" ขึ้นเอง
        ทั้งที่ยังไม่ได้กด และหน้าจอถูกดึงกลับไปที่ช่องแรกที่ขาดตลอดเวลา
        """
        import re
        code = re.sub(r"//[^\n]*", "", self.html.split("<script>", 1)[1])
        self.assertNotIn(".checkValidity(", code)
        self.assertNotIn(".reportValidity(", code)
        self.assertIn("validity.valid", code)

    def test_the_bar_says_what_is_missing(self):
        self.assertIn('<p class="bar-msg" id="progress-msg" role="alert" hidden></p>', self.html)
        self.assertIn("addEventListener('invalid'", self.html)
        self.assertIn("'ยังกรอกไม่ครบ: '", self.html)

    def test_the_server_refuses_a_thai_book_without_thai_fields(self):
        form = {**FORM, "program_language": "thai"}
        response = self.client.post("/check", data=form,
                                    files={"pdf": ("test.pdf", make_pdf(), "application/pdf")})
        self.assertEqual(response.status_code, 400)
        detail = response.json()["detail"]
        self.assertIn("กรุณากรอกข้อมูลอ้างอิงให้ครบก่อนตรวจ", detail)
        self.assertEqual(main.JOBS, {})

    def test_the_server_refuses_a_missing_book_file(self):
        response = self.client.post("/check", data=FORM)
        self.assertGreaterEqual(response.status_code, 400)
        self.assertLess(response.status_code, 500)
        self.assertEqual(main.JOBS, {})

    def test_every_checklist_step_points_at_a_real_section(self):
        import re
        steps = re.findall(r'<li data-sec="([^"]+)">', self.html)
        self.assertEqual(steps, ["sec-files", "sec-type", "sec-title", "sec-student"])
        for sec in steps:
            self.assertIn(f'id="{sec}"', self.html)


class TheApprovedDocumentTypeComesFromTheEthesisFile(unittest.TestCase):
    """ประเภทเล่มที่อนุมัติยึดตามไฟล์ eThesis ก่อนช่องที่เลือกเอง (เจ้าหน้าที่สั่ง ก.ย. 2569 "ให้ฟ้องด้วย")

    เล่ม 6838776: eThesis อนุมัติเป็นสารนิพนธ์ เล่มเขียน INDEPENDENT STUDY แล้วช่องประเภทเล่ม
    ถูกเปลี่ยนให้ตรงกับเล่ม ระบบจึงเทียบเล่มกับค่าที่เพิ่งเปลี่ยน และไม่ฟ้องเรื่องนี้เลย
    """

    SOURCE = Path(__file__).resolve().parents[1] / "templates" / "index.html"

    @classmethod
    def setUpClass(cls):
        cls.html = cls.SOURCE.read_text(encoding="utf-8")
        cls.client = TestClient(main.app)
        cls.client.post("/login", data={"password": "test-password", "next": "/"},
                        follow_redirects=False)

    def tearDown(self):
        with main.JOBS_LOCK:
            paths = [job.get("pdf_path") for job in main.JOBS.values()]
            main.JOBS.clear()
        for path in paths:
            main._remove_book(path)

    def _approved_type(self, key="doc_type", **fields):
        seen = []

        def capture(path, approved, **kwargs):
            seen.append(approved.get(key))
            raise RuntimeError("stop")

        with mock.patch.object(main, "run_check", side_effect=capture):
            response = self.client.post("/check", data={**FORM, **fields},
                                        files={"pdf": ("b.pdf", make_pdf(), "application/pdf")})
            if response.status_code != 200:
                return response.status_code
            job_id = response.json()["job_id"]
            for _ in range(500):
                job = main._get_job(job_id)
                if job and job["done"]:
                    break
                time.sleep(0.01)
        return seen[0]

    def test_the_ethesis_type_wins_over_the_dropdown(self):
        self.assertEqual(self._approved_type(doc_type="INDEPENDENT STUDY",
                                             ethesis_doc_type="THEMATIC PAPER"), "THEMATIC PAPER")

    def test_the_type_chosen_on_the_form_goes_along_too(self):
        """"ถ้าในแบบฟอร์มที่กรอก/ดึงมาจากระบบ กับในเล่มไม่ตรงกันก็ต้องแจ้ง" — ค่าที่เลือกห้ามหายไป"""
        self.assertEqual(self._approved_type("doc_type_form", doc_type="INDEPENDENT STUDY",
                                             ethesis_doc_type="THEMATIC PAPER"), "INDEPENDENT STUDY")

    def test_the_dropdown_is_used_when_the_file_gives_no_type(self):
        """ควบคุมเชิงบวก — ไม่มีค่าจากไฟล์ ช่องที่เลือกยังใช้งานได้ตามเดิม"""
        self.assertEqual(self._approved_type(doc_type="INDEPENDENT STUDY"), "INDEPENDENT STUDY")

    def test_an_unknown_type_from_the_file_is_refused(self):
        self.assertEqual(self._approved_type(ethesis_doc_type="DISSERTATION"), 400)

    def test_the_page_sends_the_type_from_the_file(self):
        self.assertIn('<input type="hidden" name="ethesis_doc_type" id="ethesis-doc-type">', self.html)
        self.assertIn("'ethesis-doc-type'", self.html.split("const HIDDEN_IMPORT_FIELDS", 1)[1]
                      .split("\n", 1)[0])

    SCRIPT = r"""
const vm = require('vm');
const opts = [{value: '', defaultSelected: true, textContent: 'กรุณาเลือกประเภทเล่ม'},
              {value: 'THESIS', textContent: 'วิทยานิพนธ์ (Thesis)'},
              {value: 'THEMATIC PAPER', textContent: 'สารนิพนธ์ (Thematic Paper)'},
              {value: 'INDEPENDENT STUDY', textContent: 'การค้นคว้าอิสระ (Independent Study)'}];
const select = {tagName: 'SELECT', options: opts, selectedIndex: 0,
  get value() { return opts[this.selectedIndex].value; },
  set value(v) { const i = opts.findIndex(o => o.value === v); this.selectedIndex = i < 0 ? 0 : i; },
  classList: {add() {}, remove() {}}, dispatchEvent() {}};
const fields = {'ethesis-doc-type': {value: ''}, 'doc-type-note': {hidden: true, textContent: ''}};
global.Event = class { constructor(type) { this.type = type; } };
global.document = {
  getElementById: id => fields[id] || null,
  querySelectorAll: () => [],
  querySelector: sel => (/name="doc_type"/.test(sel) ? select : null)
};
vm.runInThisContext(require('fs').readFileSync(0, 'utf8'));
const note = () => [fields['ethesis-doc-type'].value, fields['doc-type-note'].hidden,
                    fields['doc-type-note'].textContent];
const out = {};
applyParsed({doc_type: 'THEMATIC PAPER'});
out.imported = note();
select.value = 'INDEPENDENT STUDY';
updateDocTypeNote();
out.changed = note();
clearImportedFields();
out.cleared = note();
console.log(JSON.stringify(out));
"""

    @unittest.skipUnless(shutil.which("node"), "ไม่มี node ในเครื่องนี้")
    def test_the_page_says_so_when_the_dropdown_differs_from_the_file(self):
        start = self.html.index("const IMPORT_LABELS")
        end = self.html.index("// ---- กล่องลากวางไฟล์ (คลิกก็ได้ ลากมาวางก็ได้) ----")
        run = subprocess.run(["node", "-e", self.SCRIPT], input=self.html[start:end],
                             capture_output=True, text=True, encoding="utf-8", timeout=30)
        self.assertEqual(run.returncode, 0, run.stderr)
        out = json.loads(run.stdout)
        # นำเข้าแล้วช่องตรงกับไฟล์ — ไม่มีข้อความเตือน
        self.assertEqual(out["imported"], ["THEMATIC PAPER", True, ""])
        # เปลี่ยนช่องให้ต่างจากไฟล์ — บอกว่าระบบยังเทียบกับประเภทตามไฟล์
        self.assertEqual(out["changed"][:2], ["THEMATIC PAPER", False])
        self.assertIn("สารนิพนธ์ (Thematic Paper)", out["changed"][2])
        self.assertIn("ทั้งสองค่า", out["changed"][2])
        # เริ่มเล่มใหม่ — ค่าจากไฟล์เดิมต้องไม่ค้าง
        self.assertEqual(out["cleared"], ["", True, ""])

    def test_the_dropdown_change_updates_the_note(self):
        self.assertIn("document.querySelector('select[name=\"doc_type\"]')"
                      ".addEventListener('change', updateDocTypeNote);", self.html)


class TheReportPageKeepsItsNewLayout(unittest.TestCase):
    """หน้ารายงานออกแบบใหม่ (ก.ย. 2569): แถบบน · การ์ดผลตรวจ · การ์ดแยกทีละส่วน หัวการ์ดมีสีตามเรื่อง"""

    @classmethod
    def setUpClass(cls):
        import checker
        report = {"verdict": "ผ่าน", "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []},
                  "info": [{"topic": "t", "detail": "d"}], "human_checklist": [{"item": "i", "why": "w"}],
                  "not_checked": ["x"], "verification": [{"topic": "ชื่อเรื่อง", "checks": [{"location": "หน้าปก", "status": "pass"}]}],
                  "section_order": checker.SUMMARY_SECTION_ORDER, "staff_findings": list(checker.STAFF_CHECKS),
                  "plain_summary": "ผลการตรวจ: ผ่าน", "context": {}}
        with main.JOBS_LOCK:
            main.JOBS["layout"] = {"stage": "done", "done": True, "error": None, "report": report,
                                   "pdf_name": "b.pdf", "approved": {}, "ts": time.time()}
        client = TestClient(main.app)
        client.post("/login", data={"password": "test-password", "next": "/"}, follow_redirects=False)
        cls.html = client.get("/result/layout").text
        with main.JOBS_LOCK:
            main.JOBS.pop("layout", None)

    def test_the_top_bar_has_the_language_switch_and_a_new_check_link(self):
        top = self.html.split('<header class="topbar">', 1)[1].split("</header>", 1)[0]
        self.assertIn('id="langbtn"', top)
        self.assertIn('class="top-new" href="/"', top)
        self.assertIn('action="/logout"', top)

    def test_every_section_card_has_a_colour(self):
        import re
        cards = re.findall(r'<section class="panel ([^"]+)"', self.html)
        self.assertIn("result-card", cards)
        for tone in ("tone-blue", "tone-purple", "tone-lavender", "tone-teal", "tone-slate"):
            self.assertIn(tone, cards)
        self.assertEqual(sum(c.startswith("zone-panel") for c in cards), 3)

    def test_an_empty_zone_can_turn_grey(self):
        """หัวโซนที่ไม่มีข้อเป็นสีเทาด้วย :has(> .empty) — .empty ต้องเป็นลูกตรงของการ์ด"""
        self.assertIn(".zone-panel:has(> .empty)", self.html)
        zone = self.html.split('<section class="panel zone-panel RED">', 1)[1].split("</section>", 1)[0]
        self.assertRegex(zone, r'</h2>\s*<div class="empty"')

    def test_the_result_card_follows_the_verdict(self):
        for kind in ("fail", "pass", "pending"):
            self.assertIn(f".result-card:has(.verdict-pill.{kind})", self.html)

    def test_printing_does_not_push_whole_cards_to_a_new_page(self):
        """เจ้าหน้าที่บันทึกรายงานเป็น PDF — การ์ดทั้งใบห้าม break-inside:avoid

        ตอนออกแบบใหม่รอบแรกตั้งไว้ การ์ดที่ไม่พอดีหน้าถูกดันไปหน้าใหม่ทั้งใบ ทิ้งที่ว่างครึ่งหน้า
        เล่ม M.C.T.M. จากเดิมพิมพ์ 5 หน้า (A4) กลายเป็น 7 หน้า กันไว้ได้แค่ก้อนเล็ก (ข้อ/แถว)
        """
        import re
        css = re.sub(r"/\*.*?\*/", "", self.html.split("<style>", 1)[1].split("</style>", 1)[0], flags=re.S)
        whole_cards = {".panel", ".copybox", ".zone-panel", ".cat-group", ".vf-group"}
        for selectors, body in re.findall(r"([^{}]+)\{([^{}]*)\}", css):
            if re.search(r"break-inside\s*:\s*avoid", body):
                names = {s.strip() for s in selectors.split(",")}
                self.assertFalse(names & whole_cards, selectors.strip())
        # หัวการ์ดไม่ค้างท้ายหน้าโดยไม่มีเนื้อหาตามมา
        self.assertRegex(css, r"\.panel > h2\.sec[^{}]*\{[^{}]*break-after\s*:\s*avoid")
        # ชุดบีบระยะตอนพิมพ์ต้องมาหลังชุดจอเล็ก — กระดาษ A4 หักขอบแล้วตกช่วง max-width:720px ได้
        self.assertGreater(css.rindex("@media print"), css.index("@media (max-width:720px)"))


class TheCheckedBookCanBeOpenedFromTheReport(unittest.TestCase):
    """เปิดไฟล์รูปเล่มที่ตรวจได้จากหน้ารายงาน (เจ้าหน้าที่ขอ ก.ย. 2569)

    "ในหน้าสรุปผล สามารถเพิ่มให้ดูไฟล์รูปเล่มที่แนบได้ไหม" — ของเดิมลบไฟล์ทันทีที่ตรวจเสร็จ
    ตอนนี้ไฟล์อยู่เท่าอายุผลตรวจ อยู่หลังด่านล็อกอิน และลบตามเมื่อผลตรวจถูกทิ้งหรือเกินงบดิสก์
    """

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)
        response = cls.client.post(
            "/login", data={"password": "test-password", "next": "/"},
            follow_redirects=False)
        if response.status_code != 303:
            raise RuntimeError("Test login failed")

    def tearDown(self):
        with main.JOBS_LOCK:
            paths = [job.get("pdf_path") for job in main.JOBS.values()]
            main.JOBS.clear()
        for path in paths:
            main._remove_book(path)

    def _check(self, content, name="readable.pdf"):
        response = self.client.post(
            "/check", data=FORM, files={"pdf": (name, content, "application/pdf")})
        self.assertEqual(response.status_code, 200, response.text)
        job_id = response.json()["job_id"]
        for _ in range(500):
            job = main._get_job(job_id)
            if job and job["done"]:
                return job_id
            time.sleep(0.01)
        self.fail("check did not finish")

    def _put(self, key, age_seconds, size=100):
        main.BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        path = main.BOOKS_DIR / f"{main.BOOK_PREFIX}{key}.pdf"
        path.write_bytes(b"%PDF-1.4\n" + b"x" * (size - 9))
        with main.JOBS_LOCK:
            main.JOBS[key] = {"stage": "เสร็จ", "done": True, "error": None,
                              "report": {}, "pdf_name": f"{key}.pdf", "approved": {},
                              "pdf_path": str(path), "ts": time.time() - age_seconds}
        return path

    def test_the_report_opens_the_same_file_that_was_checked(self):
        content = make_pdf()
        job_id = self._check(content)
        page = self.client.get(f"/result/{job_id}")
        self.assertIn(f'href="/book/{job_id}" target="_blank" rel="noopener"', page.text)
        response = self.client.get(f"/book/{job_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertEqual(response.content, content)
        # inline = เปิดในแท็บ ไม่ใช่ดาวน์โหลด และห้ามแคชงานที่ยังไม่เผยแพร่
        self.assertTrue(response.headers["content-disposition"].startswith("inline"))
        self.assertIn("no-store", response.headers["cache-control"])

    def test_the_button_has_both_languages(self):
        job_id = self._check(make_pdf())
        page = self.client.get(f"/result/{job_id}").text
        self.assertIn('data-th="📄 เปิดดูไฟล์รูปเล่ม" data-en="📄 Open the thesis file"', page)

    def test_a_thai_file_name_is_kept(self):
        job_id = self._check(make_pdf(), name="เล่มที่ 4.pdf")
        disposition = self.client.get(f"/book/{job_id}").headers["content-disposition"]
        self.assertIn("filename*=utf-8''", disposition)
        self.assertIn("%E0%B9%80%E0%B8%A5%E0%B9%88%E0%B8%A1", disposition)   # "เล่ม"

    def test_the_file_needs_a_login(self):
        job_id = self._check(make_pdf())
        stranger = TestClient(main.app)
        response = stranger.get(f"/book/{job_id}", follow_redirects=False)
        self.assertEqual(response.status_code, 303)
        self.assertTrue(response.headers["location"].startswith("/login"))

    def test_an_unknown_job_says_the_file_is_gone(self):
        response = self.client.get("/book/does-not-exist")
        self.assertEqual(response.status_code, 404)
        self.assertIn("ไม่พบไฟล์รูปเล่มแล้ว", response.text)

    def test_no_button_when_the_file_is_gone(self):
        path = self._put("gone", 10)
        path.unlink()
        with main.JOBS_LOCK:
            main.JOBS["gone"]["report"] = {
                "verdict": "ผ่าน", "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []},
                "info": [], "human_checklist": [], "not_checked": []}
        page = self.client.get("/result/gone")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("/book/gone", page.text)

    def test_the_file_goes_when_the_result_goes(self):
        path = self._put("old", main.JOB_TTL + 60)
        main._prune_jobs()
        self.assertNotIn("old", main.JOBS)
        self.assertFalse(path.exists())

    def test_an_hour_old_file_is_kept(self):
        path = self._put("recent", 3600)
        main._prune_jobs()
        self.assertTrue(path.exists())

    def test_the_disk_budget_drops_the_oldest_file_but_keeps_the_report(self):
        paths = {key: self._put(key, age) for key, age in (("a", 300), ("b", 200), ("c", 100))}
        with mock.patch.object(main, "MAX_KEPT_BOOKS_BYTES", 250):
            main._prune_jobs()
        self.assertTrue(paths["c"].exists())
        self.assertTrue(paths["b"].exists())
        self.assertFalse(paths["a"].exists())
        self.assertIn("a", main.JOBS)
        self.assertIsNone(main.JOBS["a"]["pdf_path"])

    def test_a_failed_check_does_not_keep_the_file(self):
        with mock.patch.object(main, "run_check", side_effect=RuntimeError("boom")):
            job_id = self._check(make_pdf())
        job = main._get_job(job_id)
        self.assertTrue(job["error"])
        self.assertIsNone(job["pdf_path"])
        self.assertEqual(self.client.get(f"/book/{job_id}").status_code, 404)

    def test_the_upload_lands_in_the_books_folder(self):
        job_id = self._check(make_pdf())
        path = Path(main._get_job(job_id)["pdf_path"])
        self.assertEqual(path.parent, main.BOOKS_DIR)
        self.assertTrue(path.name.startswith(main.BOOK_PREFIX))

    def test_an_expired_file_cannot_be_opened_without_a_new_check(self):
        """bug test ก.ย. 2569: เดิมทิ้งของหมดอายุเฉพาะตอนมีคนกดตรวจเล่มใหม่

        ถ้าไม่มีใครตรวจต่อ ไฟล์วิทยานิพนธ์ยังเปิดผ่าน /book ได้เกิน 12 ชั่วโมงที่บอกไว้
        """
        path = self._put("stale", main.JOB_TTL + 60)
        self.assertEqual(self.client.get("/book/stale").status_code, 404)
        self.assertFalse(path.exists())

    def test_an_expired_report_cannot_be_opened_either(self):
        self._put("stale", main.JOB_TTL + 60)
        self.assertEqual(self.client.get("/result/stale").status_code, 404)

    def test_a_sweeper_runs_on_its_own(self):
        """ไม่มีใครเปิดหน้าไหนเลย ไฟล์ก็ต้องถูกลบตามเวลา"""
        names = {thread.name for thread in main.threading.enumerate()}
        self.assertIn("book-sweeper", names)
        self.assertLessEqual(main.BOOK_SWEEP_SECONDS, 3600)

    def test_an_orphan_file_is_swept_but_an_upload_in_progress_is_not(self):
        main.BOOKS_DIR.mkdir(parents=True, exist_ok=True)
        orphan = main.BOOKS_DIR / f"{main.BOOK_PREFIX}orphan.pdf"
        uploading = main.BOOKS_DIR / f"{main.BOOK_PREFIX}uploading.pdf"
        for path in (orphan, uploading):
            path.write_bytes(b"%PDF-1.4\n")
        old = time.time() - main.ORPHAN_BOOK_SECONDS - 60
        os.utime(orphan, (old, old))
        try:
            main._prune_jobs()
            self.assertFalse(orphan.exists())
            self.assertTrue(uploading.exists())
        finally:
            main._remove_book(uploading)

    def _run_folder(self, name, age_seconds):
        folder = main.BOOKS_ROOT / name
        folder.mkdir(parents=True, exist_ok=True)
        book = folder / f"{main.BOOK_PREFIX}x.pdf"
        book.write_bytes(b"%PDF-1.4\n")
        stamp = time.time() - age_seconds
        os.utime(book, (stamp, stamp))
        os.utime(folder, (stamp, stamp))
        return folder, book

    def test_leftovers_from_a_finished_run_are_cleared(self):
        folder, book = self._run_folder("run-finished-test", main.ORPHAN_BOOK_SECONDS + 60)
        main._clear_leftover_books()
        self.assertFalse(book.exists())
        self.assertFalse(folder.exists())

    def test_another_running_process_keeps_its_books(self):
        """bug test ก.ย. 2569: รันเทสต์ขณะเซิร์ฟเวอร์เปิดอยู่ ไฟล์ของเซิร์ฟเวอร์หาย 2 ใน 3 ไฟล์

        เดิมทุก process ใช้โฟลเดอร์เดียวกัน และตอนเริ่มลบ book-*.pdf ทิ้งทั้งหมด
        """
        folder, book = self._run_folder("run-alive-test", 60)
        try:
            main._clear_leftover_books()
            self.assertTrue(book.exists())
        finally:
            main._remove_book_folder(folder)

    def test_an_idle_but_running_process_is_not_mistaken_for_a_finished_one(self):
        """ไม่มีใครอัปโหลดเกินชั่วโมง ไม่ได้แปลว่า process จบแล้ว — heartbeat แตะโฟลเดอร์ทุกรอบ"""
        stamp = time.time() - main.ORPHAN_BOOK_SECONDS - 60
        os.utime(main.BOOKS_DIR, (stamp, stamp))
        main._heartbeat()
        self.assertLess(time.time() - main.BOOKS_DIR.stat().st_mtime, 60)
        self.assertLess(main.BOOK_SWEEP_SECONDS, main.ORPHAN_BOOK_SECONDS)

    def test_our_own_folder_is_never_cleared_as_a_leftover(self):
        path = self._put("mine", 10)
        stamp = time.time() - main.ORPHAN_BOOK_SECONDS - 60
        os.utime(main.BOOKS_DIR, (stamp, stamp))
        os.utime(path, (stamp, stamp))
        main._clear_leftover_books()
        self.assertTrue(path.exists())
        main._heartbeat()

    def test_each_process_gets_its_own_folder(self):
        self.assertEqual(main.BOOKS_DIR.parent, main.BOOKS_ROOT)
        self.assertTrue(main.BOOKS_DIR.name.startswith("run-"))

    def test_a_process_that_exits_cleans_up_its_own_books(self):
        """Render ส่ง SIGTERM ตอน deploy ใหม่ ปิดตามปกติแล้วไฟล์ต้องไม่ค้างรอตัวกวาด"""
        script = ("import main; p = main.BOOKS_DIR / (main.BOOK_PREFIX + 'x.pdf'); "
                  "p.write_bytes(b'%PDF-1.4'); print(main.BOOKS_DIR)")
        run = subprocess.run([sys.executable, "-c", script], cwd=Path(main.__file__).parent,
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-500:])
        folder = Path(run.stdout.strip().splitlines()[-1])
        self.assertEqual(folder.parent, main.BOOKS_ROOT)
        self.assertFalse(folder.exists())

    def test_files_from_before_the_split_are_cleared_but_nothing_else(self):
        main.BOOKS_ROOT.mkdir(parents=True, exist_ok=True)
        ours = main.BOOKS_ROOT / f"{main.BOOK_PREFIX}leftover.pdf"
        other = main.BOOKS_ROOT / "not-ours.pdf"
        ours.write_bytes(b"%PDF-1.4\n")
        other.write_bytes(b"%PDF-1.4\n")
        try:
            main._clear_leftover_books()
            self.assertFalse(ours.exists())
            self.assertTrue(other.exists())
        finally:
            other.unlink(missing_ok=True)
