import hashlib
import hmac
import os
import time
import unittest

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

        token ผูกกับ APP_PASSWORD จึงคำนวณได้เท่าเดิมทุกครั้งที่ process เริ่มใหม่
        """
        token = main.SESSION_TOKEN
        restarted = hmac.new(main.APP_PASSWORD.encode("utf-8"),
                             b"ethesis-session-v1", hashlib.sha256).hexdigest()
        self.assertEqual(token, restarted)
        # เปลี่ยนรหัสผ่าน = เตะทุกเซสชันออก
        other = hmac.new(b"another-password", b"ethesis-session-v1", hashlib.sha256).hexdigest()
        self.assertNotEqual(token, other)

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
