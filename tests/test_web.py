import hashlib
import hmac
import json
import os
import shutil
import subprocess
import time
import unittest
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

    applyParsed เขียนทับเฉพาะช่องที่ข้อมูลใหม่มีค่า วิธีวางข้อความไม่อ่านรายชื่อกรรมการเลย
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
        end = html.index("// ---- แท็บเลือกวิธีนำเข้า ----")
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
                     "ethSourceHtml = '';", "document.getElementById('overlay').style.display = 'none';",
                     "updateConditionalRequirements();"):
            self.assertIn(step, body)
