# -*- coding: utf-8 -*-
"""ตัวสะกดไทยต้องตรงทุกตัว รวม ำ/า วรรณยุกต์ สระบน-ล่าง และการันต์ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569)

ถ้อยคำเดิม: "ข้อสามข้อสี่ต้องถือว่าผิด ... ถือว่าสะกดผิด" · "ข้อสามข้อสี่ ผิดก็ต้องเป็นสีแดง" ·
"รวมไปถึงวรรณยุกต์สำหรับภาษาไทย" (ข้อสาม/สี่ = เล่มพิมพ์ "นา" ที่ถูกเป็น "นำ" และกลับกัน)

ของเดิมตัดสินด้วย norm() ซึ่งตัดตัวเล็กบน/ล่างทิ้งและมอง ำ เป็น า เล่มที่สะกดผิดระดับนี้จึงผ่าน — norm() ยังใช้ "หา"
ข้อความเหมือนเดิม (ฟอนต์บางตัวทำวรรณยุกต์หายจริง หาด้วยตัวเทียบเข้มจะหาหัวข้อไม่เจอ) แต่จุดที่ตัดสินใช้ spelling_key
และข้อที่ระบบยืนยันไม่ได้ว่าอ่านถูก (ฟอนต์ของเล่มเพี้ยน / ข้อความมีร่องรอยอ่านเพี้ยน) เป็นสีส้ม ไม่ใช่แดง
"""
import inspect
import re
import sys
import unittest
from pathlib import Path

import checker as checker_module
from checker import (
    MARKS_UNRELIABLE_FIX,
    Report,
    analyze_diff,
    chapter_title_verdict,
    compare_reference_text,
    compare_values,
    degree_differs_only_in_spacing,
    exact_reference_status,
    mismatch_detail,
    norm,
    same_spelling,
    spelled_in,
)

AM, AA, NIKH = chr(0x0E33), chr(0x0E32), chr(0x0E4D)
MAI_EK, SARA_II = chr(0x0E48), chr(0x0E35)
TONE_FIRST = "ท" + MAI_EK + SARA_II + "ปรึกษา"       # "ที่ปรึกษา" ที่ไฟล์เก็บวรรณยุกต์ก่อนสระ — ตาเห็นเหมือนกัน


class SpellingKeyKeepsEveryThaiMark(unittest.TestCase):
    def test_sara_am_and_sara_aa_are_different_letters(self):
        self.assertFalse(same_spelling("การนำเสนอ", "การนาเสนอ"))
        # ตัวเทียบแบบหยาบยังมองว่าตรง — ใช้ "หา" ข้อความต่อไปได้ แต่ห้ามใช้ตัดสิน
        self.assertEqual(norm("การนำเสนอ"), norm("การนาเสนอ"))

    def test_every_way_a_file_stores_sara_am_is_still_sara_am(self):
        for form in ("การนำเสนอ", "การน" + NIKH + AA + "เสนอ", "การน" + NIKH + " " + AA + "เสนอ"):
            with self.subTest(form=form.encode("unicode_escape")):
                self.assertTrue(same_spelling(form, "การนำเสนอ"))

    def test_tone_marks_count(self):
        self.assertFalse(same_spelling("ทีปรึกษา", "ที่ปรึกษา"))
        self.assertFalse(same_spelling("ที้ปรึกษา", "ที่ปรึกษา"))

    def test_vowels_above_and_below_and_the_karan_count(self):
        self.assertFalse(same_spelling("กัน", "กิน"))
        self.assertFalse(same_spelling("วิจย", "วิจัย"))
        self.assertFalse(same_spelling("ศาสตร", "ศาสตร์"))

    def test_mark_order_spaces_punctuation_and_letter_case_do_not_count(self):
        """ควบคุมเชิงลบ — สิ่งที่ตาเห็นเหมือนกัน (หรือที่กฎเดิมไม่นับ) ต้องยังตรง"""
        self.assertTrue(same_spelling(TONE_FIRST, "ที่ปรึกษา"))
        self.assertTrue(same_spelling("ปริญญา วิทยาศาสตรมหาบัณฑิต (สาขา)", "ปริญญาวิทยาศาสตรมหาบัณฑิต(สาขา)"))
        self.assertTrue(same_spelling("Ph.D. Program", "PHD PROGRAM"))

    def test_searching_stays_on_letter_boundaries(self):
        """ "ยา" ต้องไม่ไปเจอใน "ย่าง" — ย ตัวนั้นมีไม้เอก"""
        self.assertFalse(spelled_in("ยา", "ย่าง"))
        self.assertTrue(spelled_in("ย่า", "ย่าง"))
        self.assertTrue(spelled_in("", "อะไรก็ได้"))

    def test_a_word_missing_its_last_mark_is_not_found_inside_the_complete_word(self):
        """ข้อมูลอนุมัติ "ศาสตร" (ตกการันต์) กับเล่มที่พิมพ์ "ศาสตร์" ถูก — ถ้าค้นข้อความย่อยตรง ๆ ไม่แบ่งตัวอักษร
        "ศาสตร" อยู่ใน "ศาสตร์" พอดี จะนับว่าตรงทั้งที่สะกดต่างกัน (ข้อ 4 ที่เจ้าหน้าที่ยก: ข้อมูลกับเล่มต่างกันฝั่งไหนก็ผิด)"""
        self.assertFalse(spelled_in("คณะสาธารณสุขศาสตร", "คณบดี คณะสาธารณสุขศาสตร์"))
        self.assertFalse(spelled_in("กร", "กรุงเทพ"))
        self.assertTrue(spelled_in("คณะสาธารณสุขศาสตร์", "คณบดี คณะสาธารณสุขศาสตร์"))

    def test_taking_the_marks_away_gives_exactly_the_loose_key(self):
        """ตัวเทียบเข้มกับ norm() ชี้ "ตำแหน่ง" เดียวกันเสมอ ต่างกันแค่ตัวเล็กบน/ล่างกับ ำ"""
        for text in ("การนำเทคโนโลยีจดจำใบหน้า (Facial Recognition)", "น" + NIKH + chr(0x0E49) + AA,
                     "ศิลปศาสตรมหาบัณฑิต สาขาวิชา", TONE_FIRST, "สํสกฤต", "  ", "Ph.D."):
            with self.subTest(text=text.encode("unicode_escape")):
                letters = "".join(unit[0] for unit in checker_module._spelling_units(text))
                self.assertEqual(letters.replace(AM, AA), norm(text))


class TheJudgementIsStrictButStillExplains(unittest.TestCase):
    def test_a_title_that_differs_only_in_sara_am_is_a_typo(self):
        compared = compare_values("การนาเสนอข้อมูลสุขภาพ", "การนำเสนอข้อมูลสุขภาพ", "title")
        self.assertEqual(compared["status"], "typo")
        self.assertTrue(compared["marks_only"])
        self.assertLess(compared["score"], 1.0)

    def test_a_short_heading_is_still_a_typo_so_the_report_can_point_at_the_letter(self):
        compared = compare_values("บทนา", "บทนำ", "toc_heading")
        self.assertEqual(compared["status"], "typo")

    def test_a_name_missing_its_karan(self):
        compared = compare_values("สมศักดิ ใจดี", "สมศักดิ์ ใจดี", "student_name")
        self.assertEqual((compared["status"], compared["marks_only"]), ("typo", True))

    def test_marks_stored_in_another_order_are_exact(self):
        self.assertEqual(compare_values(TONE_FIRST, "ที่ปรึกษา", "title")["status"], "exact")

    def test_an_english_punctuation_difference_is_unchanged(self):
        """ควบคุมเชิงลบ — อังกฤษที่ต่างแค่วรรคตอนเป็น typo เหมือนเดิม และไม่ใช่เรื่องตัวเล็กบน/ล่าง"""
        compared = compare_values("M.Sc. (Biology)", "MSc (Biology)", "degree")
        self.assertEqual(compared["status"], "typo")
        self.assertNotIn("marks_only", compared)

    def test_a_page_with_one_wrong_mark_is_not_a_match(self):
        page = "ชื่อเรื่อง\nการนาเสนอข้อมูลสุขภาพ\nสมชาย ใจดี"
        self.assertEqual(exact_reference_status(page, "การนำเสนอข้อมูลสุขภาพ"), (False, "text"))
        self.assertNotEqual(compare_reference_text(page, "การนำเสนอข้อมูลสุขภาพ", "title")["status"], "exact")
        self.assertNotEqual(compare_reference_text(page, "การนำเสนอข้อมูลสุขภาพ", "student_name")["status"],
                            "exact")

    def test_a_page_spelt_right_is_a_match(self):
        page = "ชื่อเรื่อง\nการนำเสนอข้อมูลสุขภาพ\nสมชาย ใจดี"
        self.assertEqual(exact_reference_status(page, "การนำเสนอข้อมูลสุขภาพ"), (True, "text"))
        self.assertEqual(compare_reference_text(page, "การนำเสนอข้อมูลสุขภาพ", "title")["status"], "exact")

    def test_the_explanation_names_the_syllable(self):
        analysis = analyze_diff("การนาเสนอข้อมูล", "การนำเสนอข้อมูล")
        self.assertEqual(analysis.text, 'ต่างที่ "นา" ต้องเป็น "นำ"')
        self.assertTrue(analysis.marks_only)
        self.assertEqual(len(analysis.steps), 1)
        self.assertIn('"การนาเสนอ', analysis.steps[0])
        self.assertIn('"การนำเสนอ', analysis.steps[0])

    def test_a_missing_tone_mark_is_explained(self):
        analysis = analyze_diff("อาจารย์ทีปรึกษาหลัก", "อาจารย์ที่ปรึกษาหลัก")
        self.assertEqual(analysis.text, 'ต่างที่ "ที" ต้องเป็น "ที่"')
        self.assertTrue(analysis.marks_only)

    def test_mark_order_alone_is_no_difference(self):
        analysis = analyze_diff(TONE_FIRST + "หลัก", "ที่ปรึกษาหลัก")
        self.assertEqual((analysis.text, analysis.steps), ("", ()))

    def test_mark_order_is_not_shown_as_a_difference_next_to_a_real_one(self):
        """เล่มเก็บ "ที่" คนละลำดับ และมีคำเกินจริงหนึ่งคำ — ต้องบอกแค่คำที่เกิน ไม่มี 'ต่างที่ "ที่" ต้องเป็น "ที่"'
        ที่ตาดูไม่ออกว่าต่างตรงไหน"""
        analysis = analyze_diff(TONE_FIRST + "หลักใหม่", "ที่ปรึกษาหลัก")
        self.assertEqual(analysis.text, 'มี "ใหม่" เกินมา')
        self.assertFalse(analysis.marks_only)

    def test_a_real_word_difference_is_not_marks_only(self):
        self.assertFalse(analyze_diff("การนำเสนอข้อมูลใหม่", "การนำเสนอข้อมูล").marks_only)

    def test_a_degree_that_differs_in_a_mark_is_not_just_spacing(self):
        """ข้อเหลือง "ต่างเฉพาะวรรคตอน/ช่องว่าง" ผ่านได้ — สะกดวรรณยุกต์ผิดต้องไม่หลุดเข้ากิ่งนี้"""
        degree = "วิทยาศาสตรมหาบัณฑิต (โรคติดเชื้อ)"
        self.assertTrue(degree_differs_only_in_spacing(degree, "ปริญญาวิทยาศาสตรมหาบัณฑิต(โรคติดเชื้อ)"))
        self.assertFalse(degree_differs_only_in_spacing(degree, "ปริญญาวิทยาศาสตรมหาบัณฑิต (โรคติดเชือ)"))


class EachCheckJudgesSpellingStrictly(unittest.TestCase):
    """ทุกจุดที่ตัดสินว่าเล่มตรงข้อความที่ถูกต้อง — แต่ละจุดคงสีเดิมของตัวเอง"""

    SIG_DEGREE = "วิทยาศาสตรมหาบัณฑิต (โรคติดเชื้อและวิทยาการระบาด)"

    def _signature_page(self, sentence):
        dots = "…………………………………………………………………………...…………"
        return "\n".join([
            "ก", "วิทยานิพนธ์", "เรื่อง", "ชื่อเรื่องตัวอย่างของวิทยานิพนธ์", sentence,
            "ปริญญา" + self.SIG_DEGREE, "วันที่ 18 กันยายน 2569",
            "คณะกรรมการที่ปรึกษาวิทยานิพนธ์", "อาจารย์ที่ปรึกษาหลัก", dots,
            "สมชาย ใจดี, ผู้ช่วยศาสตราจารย์ ตัวอย่าง", dots, "คณบดี", "คณบดี",
            "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล", "คณะสาธารณสุขศาสตร์ มหาวิทยาลัยมหิดล"])

    def _signature(self, sentence):
        rep = Report()
        checker_module._report_signature_template(
            rep, "หน้าลงนาม 1 (หน้า ก)", self._signature_page(sentence),
            checker_module.SIGNATURE_TEMPLATE_TH, self.SIG_DEGREE, True)
        return rep

    def test_the_signature_sentence_with_a_missing_tone_mark_is_red_and_points_at_it(self):
        rep = self._signature("ได้รับการพิจารณาใหนับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร")
        issue = rep.zones["RED"][0]
        self.assertIn('ต้องเป็น "ให้"', issue["diff_tail"])
        # ประโยคครบทุกคำ — ต้องไม่บอกว่าขาดท่อนแรก (เคยเข้ากิ่งท่อนท้ายแล้วได้ 'ขาด "ได้รับการพิจารณาให้"')
        self.assertNotIn("ขาด", issue["found"])
        self.assertEqual(rep.verification[0]["checks"][0]["status"], "fail")

    def test_the_signature_sentence_spelt_right_passes(self):
        rep = self._signature("ได้รับการพิจารณาให้นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร")
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(rep.verification[0]["checks"][0]["status"], "pass")

    def test_the_faculty_box_with_a_missing_karan_is_red_like_any_spelling_slip(self):
        """เจ้าหน้าที่ ต.ค. 2569: การันต์ต่างในช่องประธานหลักสูตร/คณบดี "สะกดผิดแบบนี้ควรแดง" """
        rep = Report()
        checker_module._check_signature_institution(
            rep, "exam", "คณบดี บัณฑิตวิทยาลัย คณบดี คณะสาธารณสุขศาสตร", {"faculty": "คณะสาธารณสุขศาสตร์"},
            False, "หน้าลงนาม 2 ", " (หน้า ข)")
        self.assertEqual((len(rep.zones["RED"]), len(rep.zones["ORANGE"])), (1, 0))
        card = rep.zones["RED"][0]
        self.assertEqual(card["diff_tail"], 'ต่างที่ "ร" ต้องเป็น "ร์"')
        self.assertIn('ศาสตร์"', card["fix_steps"][0])          # วิธีแก้ยกทั้งคำมาให้เทียบ

    def test_the_faculty_box_spelt_right_passes(self):
        rep = Report()
        checker_module._check_signature_institution(
            rep, "exam", "คณบดี บัณฑิตวิทยาลัย คณบดี คณะสาธารณสุขศาสตร์", {"faculty": "คณะสาธารณสุขศาสตร์"},
            False, "หน้าลงนาม 2 ", " (หน้า ข)")
        self.assertEqual(rep.zones["ORANGE"], [])

    def test_a_subject_misspelt_in_a_mark_still_counts_as_the_programme_line(self):
        """ชื่อสาขาผิดแค่วรรณยุกต์ยังนับว่ามีบรรทัดหลักสูตร — ชื่อปริญญาในช่องเดียวกันต้องถูกตรวจต่อ"""
        rep = Report()
        checker_module._check_signature_institution(
            rep, "advisory", "ประธานหลักสูตร วิทยาศาสตรมหาบัณฑิด สาขาวิชาโรคติดเชือ",
            {"degree_cover_th": "วิทยาศาสตรมหาบัณฑิต (โรคติดเชื้อ)"}, False, "หน้าลงนาม 1 ", " (หน้า ก)")
        self.assertEqual([("ชื่อสาขา" in i["found"], "ชื่อปริญญา" in i["found"]) for i in rep.zones["RED"]],
                         [(True, False), (False, True)])

    def test_the_abstract_committee_heading_with_a_missing_tone_mark_is_red(self):
        rep = Report()
        checker_module._report_abstract_committee_heading(
            rep, "บทคัดย่อ\nคณะกรรมการทีปรึกษาวิทยานิพนธ์: สมชาย ใจดี, ปร.ด.", "THESIS", False,
            "บทคัดย่อภาษาไทย (หน้า ง)")
        self.assertEqual(len(rep.zones["RED"]), 1)
        self.assertIn('ต้องเป็น "ที่"', rep.zones["RED"][0]["diff_tail"])

    def test_the_abstract_committee_heading_spelt_right_passes(self):
        rep = Report()
        checker_module._report_abstract_committee_heading(
            rep, "บทคัดย่อ\nคณะกรรมการที่ปรึกษาวิทยานิพนธ์: สมชาย ใจดี, ปร.ด.", "THESIS", False,
            "บทคัดย่อภาษาไทย (หน้า ง)")
        self.assertEqual(rep.zones["RED"], [])

    def test_the_biography_heading_and_its_contents_line(self):
        rep = Report()
        checker_module._report_biography_heading(rep, "ประวัตผู้วิจัย", "ประวัติผู้วิจัย", "ประวัติผู้วิจัย (หน้า 90)")
        checker_module._report_toc_biography_heading(rep, "ประวัติผูวิจัย 90", "ประวัติผู้วิจัย", "สารบัญ (หน้า ช)")
        checker_module._report_biography_heading(rep, "ประวัติผู้วิจัย", "ประวัติผู้วิจัย", "ประวัติผู้วิจัย (หน้า 90)")
        self.assertEqual(len(rep.zones["RED"]), 2)

    def test_a_chapter_title_with_sara_aa_for_sara_am_is_wrong(self):
        self.assertIsNone(chapter_title_verdict("บทนำ", 1, 1))
        kind, compared, expected = chapter_title_verdict("บทนา", 1, 1)
        self.assertEqual((kind, expected, compared["marks_only"]), ("wrong", "บทนำ", True))

    def test_a_wrapped_chapter_title_is_judged_on_the_whole_title(self):
        self.assertIsNone(chapter_title_verdict("วรรณกรรมและงานวิจัย", 2, 1, "ที่เกี่ยวข้อง 9"))
        kind, compared, _ = chapter_title_verdict("วรรณกรรมและงานวิจัย", 2, 1, "ทีเกี่ยวข้อง 9")
        self.assertEqual(kind, "wrong")
        # ยกชื่อที่ต่อครบมาอ้าง ไม่ใช่แค่บรรทัดแรก
        self.assertEqual(compared["actual"], "วรรณกรรมและงานวิจัย ทีเกี่ยวข้อง")
        self.assertTrue(compared["marks_only"])

    def test_the_inline_checks_judge_with_the_strict_key(self):
        """หน้าปก (ข้อความบังคับ) กับชื่อท้ายกิตติกรรมประกาศอยู่ใน run_check ตรง ๆ จึงตรวจจากซอร์ส (แบบเดียวกับ
        EveryDamagedPageIsReported) ส่วนการทำงานจริงตรวจด้วยเล่มจริงตอนพัฒนา"""
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("not spelled_in(expected_text, cover_text)", source)
        self.assertIn("exact_at_end = spelled_in(expected_ack_name, ack_tail_text)", source)
        self.assertIn("ชื่อผู้เขียนท้ายกิตติกรรมประกาศเขียนว่า", source)


class MarkDifferencesTheSystemCannotTrustAreOrange(unittest.TestCase):
    """ห้ามฟันธงสิ่งที่ยืนยันไม่ได้: การอ่านที่เพี้ยนต้องไม่กลายเป็นคำตัดสินว่าเล่มไม่ผ่าน (SKILL.md หลักการข้อ 1)"""

    @staticmethod
    def _title_card(rep, printed, approved):
        compared = compare_values(printed, approved, "title")
        rep.add("RED", "front_matter", "หน้าปก", mismatch_detail("ชื่อเรื่อง", compared, approved),
                f'ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: "{approved}"', "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ",
                "FORM.APPROVED_MATCH")
        return compared

    def test_a_mark_slip_on_a_book_with_a_good_font_is_red(self):
        rep = Report()
        self._title_card(rep, "การนาเสนอข้อมูล", "การนำเสนอข้อมูล")
        self.assertEqual(len(rep.zones["RED"]), 1)

    def test_a_mark_slip_on_a_book_with_a_damaged_font_is_orange(self):
        rep = Report()
        rep.marks_unreliable = True
        compared = self._title_card(rep, "การนาเสนอข้อมูล", "การนำเสนอข้อมูล")
        self.assertEqual(rep.zones["RED"], [])
        card = rep.zones["ORANGE"][0]
        self.assertEqual((card["rule_id"], card["fix"]), ("FORM.FONT_UNREADABLE", MARKS_UNRELIABLE_FIX))
        self.assertEqual(rep.mismatch_status(compared["marks_only"], compared["actual"]), "pending")

    def test_a_real_word_mistake_on_a_book_with_a_damaged_font_is_still_red(self):
        rep = Report()
        rep.marks_unreliable = True
        compared = self._title_card(rep, "การนำเสนอข้อมูลใหม่", "การนำเสนอข้อมูล")
        self.assertEqual(len(rep.zones["RED"]), 1)
        self.assertEqual(rep.mismatch_status(compared.get("marks_only"), compared["actual"]), "fail")

    def test_text_that_shows_a_misreading_is_orange_even_on_a_good_book(self):
        """วงกลมของ ำ กลายเป็นช่องว่าง ("บทน า") — ข้อความแบบนี้ไม่มีทางพิมพ์จริง"""
        rep = Report()
        compared = self._title_card(rep, "บทน า", "บทนำ")
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(len(rep.zones["ORANGE"]), 1)
        self.assertEqual(rep.mismatch_status(compared["marks_only"], compared["actual"]), "pending")

    def test_the_mai_yamok_space_is_not_a_misreading(self):
        """ควบคุมเชิงลบ — เว้นวรรคหน้าไม้ยมก ("ต่าง ๆ") เป็นการพิมพ์ปกติ"""
        self.assertFalse(checker_module._has_read_defect("ปัจจัยต่าง ๆ ที่เกี่ยวข้อง"))
        self.assertTrue(checker_module._has_read_defect("บทน า"))
        self.assertTrue(checker_module._has_read_defect("บทท ี่ 1"))

    def test_run_check_flags_the_book_when_any_page_has_a_damaged_font(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("rep.marks_unreliable = bool(font_damaged)", source)


class TheNewWordingHasEnglish(unittest.TestCase):
    """ข้อความใหม่ต้องมีคำแปลเต็มประโยค ไม่เหลือไทยนอกเครื่องหมายคำพูด (เล่มทดสอบไม่เคยผลิตข้อความพวกนี้)"""

    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
        import check_i18n
        cls.i18n = check_i18n
        _block, cls.pairs = check_i18n.load_tr()

    def _thai_left(self, thai):
        english = self.i18n.tr_en(thai, self.pairs)
        outside_quotes = re.sub(r'"[^"]*"', "", english)
        return [w for w in re.findall(r"[ก-๙]+", outside_quotes)
                if w not in self.i18n.THAI_PAGE_LETTERS and w not in self.i18n.KEEP_THAI]

    def test_the_orange_card_instruction(self):
        self.assertIsNotNone(self.i18n.whole_rule(MARKS_UNRELIABLE_FIX, self.pairs))
        self.assertEqual(self._thai_left(MARKS_UNRELIABLE_FIX), [])

    def test_the_acknowledgement_name_line_with_and_without_its_difference(self):
        base = 'ชื่อผู้เขียนท้ายกิตติกรรมประกาศเขียนว่า "สมชาย ใจดี"'
        self.assertIsNotNone(self.i18n.whole_rule(base, self.pairs))
        self.assertEqual(self._thai_left(base), [])
        self.assertEqual(self._thai_left(base + ' ต่างที่ "ดี" ต้องเป็น "ดี่"'), [])


if __name__ == "__main__":
    unittest.main()
