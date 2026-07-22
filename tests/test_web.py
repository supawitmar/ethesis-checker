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
