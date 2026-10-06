# -*- coding: utf-8 -*-
"""ตัดคำนำหน้าชื่อนักศึกษาตั้งแต่ตอนอ่านไฟล์ eThesis เข้าฟอร์ม (เจ้าหน้าที่สั่ง 6 ต.ค. 2569)

เจ้าหน้าที่: *"หลักการเขียนในเล่ม มันต้องเขียนแบบไม่มี คำนำหน้า ถ้างั้นแก้ไข ให้การอ่านฟอร์มจากไฟล์ ที่แนบเข้าไป
ให้ตัดคำนำหน้านามทิ้งไปก่อนตรวจเลย"* — เล่มจริงพิมพ์ชื่ออังกฤษบนบทคัดย่อเป็นชื่อล้วนถูกตามกติกา แต่ระบบฟ้องแดงว่า
ต้องแก้เป็น "VDC Lt Col ..." เพราะตัวนำเข้ารู้จักแค่ Mr/Mrs/Miss/Ms/Dr และตัวตรวจไม่รู้จัก VDC
(ชื่อในเทสต์เป็นชื่อสมมติทั้งหมด)
"""
import inspect
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checker as checker_module  # noqa: E402
import ethesis_import  # noqa: E402
from thai_text import strip_student_title  # noqa: E402


def _names(*lines):
    return ethesis_import.student_names(["ชื่อ-สกุล", *lines])


class TheFormGetsNamesWithoutTitles(unittest.TestCase):
    def test_a_unit_and_rank_before_the_english_name(self):
        """แบบเดียวกับไฟล์ eThesis ของเล่มที่แจ้งมา: ยศย่อแบบมีจุดฝั่งไทย · หน่วย+ยศฝั่งอังกฤษ"""
        self.assertEqual(_names("ก.ท. สมหญิง ตัวอย่าง", "VDC Lt Col SOMYING TUAYANG"),
                         ("สมหญิง ตัวอย่าง", "SOMYING TUAYANG"))

    def test_professional_titles_seen_in_real_files(self):
        self.assertEqual(_names("ทพญ. สมหญิง ตัวอย่าง", "Dental Surgeon SOMYING TUAYANG"),
                         ("สมหญิง ตัวอย่าง", "SOMYING TUAYANG"))
        self.assertEqual(_names("พ.ญ. สมหญิง ตัวอย่าง", "DR. SOMYING TUAYANG"),
                         ("สมหญิง ตัวอย่าง", "SOMYING TUAYANG"))

    def test_the_titles_the_importer_already_knew(self):
        self.assertEqual(_names("น.ส. สมหญิง ตัวอย่าง", "Miss SOMYING TUAYANG"),
                         ("สมหญิง ตัวอย่าง", "SOMYING TUAYANG"))
        self.assertEqual(_names("นาย สมชาย ใจดี", "Mr. SOMCHAI JAIDEE"), ("สมชาย ใจดี", "SOMCHAI JAIDEE"))
        self.assertEqual(_names("ว่าที่ ร.ต. สมชาย ใจดี", "SOMCHAI JAIDEE"), ("สมชาย ใจดี", "SOMCHAI JAIDEE"))

    def test_an_english_title_nobody_listed_yet(self):
        """คำนำหน้าอังกฤษที่ตัวตัดยังไม่รู้จัก ตัดตามจำนวนคำของชื่อไทย (คำที่เกินมีตัวเล็ก/จุด ชื่อที่เหลือตัวใหญ่ล้วน)"""
        self.assertEqual(_names("ว่าที่ร้อยตรี สมชาย ใจดี", "Acting Sub Lt. SOMCHAI JAIDEE"),
                         ("สมชาย ใจดี", "SOMCHAI JAIDEE"))

    def test_a_foreign_student_without_a_thai_name(self):
        self.assertEqual(_names("Miss ANNA LEE", "ANNA LEE"), ("ANNA LEE", "ANNA LEE"))


class TheFallbackNeverCutsAName(unittest.TestCase):
    """ทางสำรองตัดตามจำนวนคำ — ต้องไม่ตัดคำที่เป็นชื่อจริงทิ้ง"""

    def test_a_three_word_english_name_in_capitals_is_kept(self):
        self.assertEqual(_names("นาย สมชาย ใจดี", "SOMCHAI JAI DEE")[1], "SOMCHAI JAI DEE")

    def test_a_mixed_case_english_name_is_kept(self):
        self.assertEqual(_names("นาย สมชาย ใจดี", "Somchai Jai Dee")[1], "Somchai Jai Dee")

    def test_a_name_particle_is_not_a_title(self):
        """คำเชื่อมนามสกุลตัวเล็ก (van/de/bin) อยู่ในส่วนที่เหลือ = ไม่ใช่ตัวใหญ่ล้วน = ไม่ตัด"""
        self.assertEqual(_names("นาย สมชาย ใจดี", "SOMCHAI van JAIDEE")[1], "SOMCHAI van JAIDEE")

    def test_without_a_thai_name_nothing_is_counted(self):
        self.assertEqual(ethesis_import._english_name_without_title("Acting Sub Lt. JOHN SMITH", "JOHN SMITH"),
                         "Acting Sub Lt. JOHN SMITH")


class TheSharedTitleStripper(unittest.TestCase):
    def test_unit_before_rank(self):
        self.assertEqual(strip_student_title("VDC Lt Col SOMYING TUAYANG"), "SOMYING TUAYANG")
        self.assertEqual(strip_student_title("Pol. Lt. Col. SOMCHAI JAIDEE"), "SOMCHAI JAIDEE")

    def test_acting_rank_written_short(self):
        self.assertEqual(strip_student_title("ว่าที่ ร.ต. สมชาย ใจดี"), "สมชาย ใจดี")
        self.assertEqual(strip_student_title("ว่าที่ร.ต.หญิง สมหญิง ใจดี"), "สมหญิง ใจดี")

    def test_a_name_that_starts_like_an_honorific_survives(self):
        """นาย/นาง/นางสาว ตัดได้ครั้งเดียว — ชื่อที่ขึ้นต้นด้วยคำเดียวกันต้องอยู่ครบ"""
        self.assertEqual(strip_student_title("นางสาว นางนวล ใจดี"), "นางนวล ใจดี")
        self.assertEqual(strip_student_title("นาย นายิกา ใจดี"), "นายิกา ใจดี")

    def test_words_that_only_start_like_a_title_are_names(self):
        for name in ("MISSAKORN SOMCHAI", "VDCHAI JAIDEE", "COLIN SMITH", "ศิริพร ใจดี", "ดรุณี ใจดี"):
            self.assertEqual(strip_student_title(name), name, name)

    def test_the_checker_uses_the_same_stripper(self):
        self.assertIs(checker_module._strip_student_title, strip_student_title)
        self.assertEqual(checker_module.strip_name_prefix("VDC Lt Col SOMYING TUAYANG"), "SOMYING TUAYANG")
        self.assertEqual(checker_module.strip_name_prefix("ก.ท. สมหญิง ตัวอย่าง"), "สมหญิง ตัวอย่าง")

    def test_the_importer_has_no_title_list_of_its_own(self):
        source = inspect.getsource(ethesis_import)
        self.assertNotIn("THAI_PREFIX", source)
        self.assertNotIn("EN_PREFIX", source)


if __name__ == "__main__":
    unittest.main()
