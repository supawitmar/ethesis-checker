# -*- coding: utf-8 -*-
"""ข้อความสรุปต้อง "บอกวิธีแก้" ไม่ใช่แค่ชี้ว่าต่างตรงไหน (เจ้าหน้าที่สั่ง 6 ต.ค. 2569)

เล่มจริงที่เจ้าหน้าที่ส่งมา: ชื่อเรื่องไทยในบทคัดย่อมีคำว่า "การ" เกินมาหนึ่งคำ แต่ข้อความสรุป
บอกว่า 'ต่างที่ "<ชื่อเรื่องท่อนยาว>" และ ต่างที่ "<ท่อนยาวอีกท่อน>"' โดยไม่บอกว่าต้องแก้ยังไง
ท่อนที่สองคือรอยตัดบรรทัดล้วน ๆ (เล่มพิมพ์ "ในระบบ" กับ "บริหาร" ติดกัน แต่ขึ้นบรรทัดใหม่
ตรงนั้น) ระบบต่อบรรทัดด้วยช่องว่างจึงกลายเป็นจุดต่างที่ไม่มีอยู่จริง

เจ้าหน้าที่ให้แก้ 3 เรื่อง: บอกวิธีแก้ · กรณีเว้นวรรคก็ควรบอก · สระ ำ ต้องดูดีๆ

ชุดนี้ใช้ชื่อเรื่องสมมติที่มีโครงเดียวกับเล่มจริง (ไทยปนอังกฤษในวงเล็บ ตัดบรรทัดกลางคำไทย) ไม่ต้องใช้ PDF
"""
import random
import re
import sys
import unittest
from pathlib import Path

import checker as checker_module
from checker import (
    DetailText,
    Report,
    _display_graphemes,
    _join_wrapped,
    analyze_case_diff,
    analyze_diff,
    check_result,
    compare_reference_text,
    describe_diff,
    plain_summary,
    title_mismatch_detail,
)

# ชื่อเรื่องสมมติ: เล่มพิมพ์ "การ" เกินหลังคำว่า "เทคโนโลยี" (ในชื่อมี "การ" อยู่อีกที่หนึ่งที่ต้นชื่อ
# ตัวอ้างอิงตำแหน่งจึงจำเป็น) และตัดบรรทัดตรงกลางคำไทยหลังคำว่า "ในระบบ"
BOOK_TITLE = ("การนำเทคโนโลยีการวิเคราะห์ภาพ (Image Analysis Technology: IAT) "
              "มาใช้ในระบบบริหารลูกค้าสัมพันธ์ขององค์กรตัวอย่าง")
APPROVED_TITLE = ("การนำเทคโนโลยีวิเคราะห์ภาพ (Image Analysis Technology: IAT) "
                  "มาใช้ในระบบบริหารลูกค้าสัมพันธ์ขององค์กรตัวอย่าง")
ABSTRACT_PAGE = "\n".join([
    "vi",
    "การนำเทคโนโลยีการวิเคราะห์ภาพ (Image Analysis Technology: IAT) มาใช้ในระบบ",
    "บริหารลูกค้าสัมพันธ์ขององค์กรตัวอย่าง",
    "สมหญิง ตัวอย่าง 6700000 TEST/M",
    "วท.ม. (การจัดการเทคโนโลยีสารสนเทศ)",
    "คณะกรรมการที่ปรึกษาสารนิพนธ์: ที่ปรึกษา หนึ่ง, ปร.ด.",
    "บทคัดย่อ",
])


class TheSummaryTellsHowToFix(unittest.TestCase):
    """จุดต่างต้องกลายเป็นคำสั่งที่ลงมือทำได้ ไม่ใช่ข้อความยาวสองก้อนให้นักศึกษาเทียบเอง"""

    def test_the_extra_word_is_named_with_a_place_to_find_it(self):
        analysis = analyze_diff(BOOK_TITLE, APPROVED_TITLE, spaces=True)
        self.assertEqual(analysis.text, 'มี "การ" เกินมา')
        # "การ" มีอยู่สองที่ในชื่อเรื่อง จึงต้องบอกว่าตัวไหน (ตัวที่อยู่หลัง "เทคโนโลยี")
        self.assertEqual(analysis.steps, ('ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก',))

    def test_a_word_that_appears_once_needs_no_place(self):
        analysis = analyze_diff("ระเบียบวิธีการวิจัย", "ระเบียบวิธีวิจัย")
        self.assertEqual(analysis.steps, ('ให้ลบ "การ" ออก',))

    def test_a_missing_word_says_where_to_add_it(self):
        analysis = analyze_diff(
            "แนวทางการพัฒนาสิ่งอำนวยความสะดวกสนามกรีฑาสำหรับนักกีฬาคนพิการ",
            "แนวทางการพัฒนาสิ่งอำนวยความสะดวกสนามกีฬากรีฑาสำหรับนักกีฬาคนพิการ")
        self.assertEqual(analysis.text, 'ขาด "กีฬา"')
        self.assertEqual(analysis.steps, ('ให้เติม "กีฬา" หลัง "สนาม"',))

    def test_a_missing_word_at_the_very_start_points_at_what_follows(self):
        analysis = analyze_diff("นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
                                "ได้รับการพิจารณาให้นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร")
        self.assertEqual(analysis.steps, ('ให้เติม "ได้รับการพิจารณาให้" หน้า "นับเป็น"',))

    def test_a_single_letter_typo_shows_the_whole_word_before_and_after(self):
        """ตกตัวเดียว/สะกดผิดตัวเดียว: เห็นทั้งคำเทียบกันชัดกว่า 'ให้เติม "อ" หลัง ...'"""
        for found, expected, step in (
            ("อาชีวนามัย", "อาชีวอนามัย", 'ให้แก้ "อาชีวนามัย" เป็น "อาชีวอนามัย"'),
            ("ประวัติผู้จัย", "ประวัติผู้วิจัย", 'ให้แก้ "ประวัติผู้จัย" เป็น "ประวัติผู้วิจัย"'),
            ("ศิลปศาสตรมหาบัณทิต", "ศิลปศาสตรมหาบัณฑิต", 'ให้แก้ "หาบัณทิต" เป็น "หาบัณฑิต"'),
        ):
            with self.subTest(found=found):
                self.assertEqual(analyze_diff(found, expected).steps, (step,))

    def test_an_english_word_typo_inside_a_thai_title_is_named_as_a_word(self):
        analysis = analyze_diff("ความท้าทาย (Facial Recogniton Technology) ในไทย",
                                "ความท้าทาย (Facial Recognition Technology) ในไทย")
        self.assertEqual(analysis.text, 'ต่างที่ "Recogniton" ต้องเป็น "Recognition"')
        self.assertEqual(analysis.steps, ('ให้แก้ "Recogniton" เป็น "Recognition"',))

    def test_english_titles_get_steps_too(self):
        analysis = analyze_diff("RESEARCH METHODLOGY IN THE STUDY",
                                "RESEARCH METHODOLOGY IN THE STUDY")
        self.assertEqual(analysis.text, 'ต่างที่ "METHODLOGY" ต้องเป็น "METHODOLOGY"')
        self.assertEqual(analysis.steps, ('ให้แก้ "METHODLOGY" เป็น "METHODOLOGY"',))
        # ขาดคำ: ต้องบอกว่าเติมตรงไหนเสมอ และคำที่ซ้ำต้องบอกว่าตัวไหน
        self.assertEqual(analyze_diff("SELECTION OF SMART WAREHOUSE",
                                      "SELECTION OF THE SMART WAREHOUSE").steps,
                         ('ให้เติม "THE" หลัง "OF"',))
        self.assertEqual(analyze_diff("THE DATA ON THE DATA SET",
                                      "THE DATA ON THE DATUM SET").steps,
                         ('ให้แก้ "DATA" ที่อยู่หลัง "ON THE" เป็น "DATUM"',))

    def test_a_difference_that_is_only_spacing_says_so(self):
        """ "OFTHE" กับ "OF THE": บอกว่าเป็นเรื่องเว้นวรรค ไม่ใช่สะกดผิด"""
        analysis = analyze_diff("A STUDY OFTHE RESULTS FOR RELATIONSHP",
                                "A STUDY OF THE RESULTS FOR RELATIONSHIP")
        self.assertEqual(analysis.steps, ('ให้แก้การเว้นวรรค "OFTHE" เป็น "OF THE"',
                                          'ให้แก้ "RELATIONSHP" เป็น "RELATIONSHIP"'))

    def test_only_the_letter_case_differs(self):
        analysis = analyze_case_diff("UTILIZATION OF CAMELINA NAHEEL: A CULTIVAR OF CAMELINA SATIVA OILSEED",
                                     "UTILIZATION OF CAMELINA NAHEEL: A CULTIVAR OF Camelina sativa OILSEED")
        self.assertEqual(analysis.text,
                         'ต่างที่ตัวพิมพ์เล็ก-ใหญ่ "CAMELINA" ต้องเป็น "Camelina" และ "SATIVA" ต้องเป็น "sativa"')
        self.assertEqual(analysis.steps, ('ให้แก้ตัวพิมพ์เล็ก-ใหญ่ "CAMELINA" เป็น "Camelina"',
                                          'ให้แก้ตัวพิมพ์เล็ก-ใหญ่ "SATIVA" เป็น "sativa"'))

    def test_two_edits_are_two_steps(self):
        analysis = analyze_diff("การนำเทคโนโลยีการวิเคราะห์ภาพมาใช้ในระบบลูกค้าสัมพันธ์ขององค์กร",
                                "การนำเทคโนโลยีวิเคราะห์ภาพมาใช้ในระบบบริหารลูกค้าสัมพันธ์ขององค์กร")
        self.assertEqual(len(analysis.steps), 2)
        self.assertEqual(analysis.steps[0], 'ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก')
        self.assertIn('"บริหาร"', analysis.steps[1])

    def test_too_many_differences_give_no_steps(self):
        """ต่างกันมากจนไล่ทีละจุดไม่ช่วย — ให้ดูข้อความที่ถูกต้องในบรรทัด 'ต้องแก้เป็น' แทน"""
        found = "ก" + "ข".join(["ABC"] * 10)
        expected = "ก" + "ข".join(["XBC"] * 10)
        self.assertEqual(analyze_diff(found, expected).steps, ())

    # ชื่อเรื่องสมมติเจ็ดท่อน ยาวพอที่พิมพ์ผิดท่อนละหนึ่งจุดจะไม่ถูกรวมกันเป็นข้อเดียว
    FAR_APART = [
        "ประสิทธิผลของโปรแกรมพัฒนาความรอบรู้ในการดูแลแบบประคับประคอง",
        "ปัจจัยที่มีผลต่อการตัดสินใจใช้บริการสุขภาพทางไกลของผู้สูงอายุ",
        "แนวทางการพัฒนาสิ่งอำนวยความสะดวกสนามกีฬากรีฑาสำหรับนักกีฬา",
        "ระเบียบวิธีวิจัยเชิงคุณภาพน้ำหนักและความสำคัญของตัวชี้วัด",
        "ศิลปศาสตรมหาบัณฑิตสาขาวิชาการจัดการทรัพยากรมนุษย์และองค์การ",
        "การวิเคราะห์ภาพมาใช้ในระบบบริหารลูกค้าสัมพันธ์ขององค์กรตัวอย่าง",
        "การศึกษาผลของโปรแกรมการออกกำลังกายต่อสมรรถภาพทางกายของนักเรียน",
    ]

    def _far_apart_mistakes(self, count):
        parts = self.FAR_APART[:count]
        return "".join(part.replace("ร", "ล", 1) for part in parts), "".join(parts)

    def test_six_mistakes_far_apart_are_still_six_steps(self):
        """ควบคุมเชิงลบของเพดาน — หกจุดเป็นหกข้อ ไม่ถูกตัดทิ้ง"""
        found, expected = self._far_apart_mistakes(6)
        self.assertEqual(len(analyze_diff(found, expected).steps), 6)

    def test_seven_mistakes_far_apart_give_no_steps_but_the_card_still_names_them(self):
        found, expected = self._far_apart_mistakes(7)
        analysis = analyze_diff(found, expected)
        self.assertEqual(analysis.text.count("ต่างที่"), 7)
        self.assertEqual(analysis.steps, ())

    def test_nothing_to_say_when_the_texts_match(self):
        self.assertEqual(analyze_diff(APPROVED_TITLE, APPROVED_TITLE).text, "")
        self.assertEqual(analyze_diff(APPROVED_TITLE, APPROVED_TITLE).steps, ())


class WrappedLinesAreNotInventedSpaces(unittest.TestCase):
    """รอยตัดบรรทัดไม่ใช่ช่องว่าง — เล่มจริงเคยได้จุดต่างที่ไม่มีอยู่จริง"""

    def test_a_break_between_thai_letters_adds_no_space(self):
        self.assertEqual(_join_wrapped(["มาใช้ในระบบ", "บริหารลูกค้า"]), "มาใช้ในระบบบริหารลูกค้า")

    def test_a_break_after_a_hyphen_adds_no_space(self):
        self.assertEqual(_join_wrapped(["A HYBRID MULTI-", "CRITERIA DECISION"]),
                         "A HYBRID MULTI-CRITERIA DECISION")

    def test_other_breaks_keep_the_space(self):
        """ควบคุมเชิงลบ — อังกฤษแยกคำด้วยช่องว่างจริง รอยตัดระหว่างคำต้องยังมีช่องว่าง"""
        self.assertEqual(_join_wrapped(["PROCESS AUTOMATION", "IMPLEMENTATION"]),
                         "PROCESS AUTOMATION IMPLEMENTATION")
        self.assertEqual(_join_wrapped(["ชื่อเรื่อง", "Thesis title"]), "ชื่อเรื่อง Thesis title")

    def test_the_title_read_from_a_wrapped_page_matches_what_is_printed(self):
        compared = compare_reference_text(ABSTRACT_PAGE, APPROVED_TITLE, "title")
        compared = checker_module._title_as_printed(compared, ABSTRACT_PAGE, APPROVED_TITLE,
                                                    "สมหญิง ตัวอย่าง")
        self.assertIn("มาใช้ในระบบบริหารลูกค้า", compared["actual"])
        self.assertNotIn("ระบบ บริหาร", compared["actual"])

    def test_a_wrapped_title_gets_no_phantom_space_difference(self):
        """กรณีของจริง: ข้อความสรุปเคยมี 'ต่างที่ "มาใช้ในระบบ บริหาร..."' เพิ่มอีกท่อนจากรอยตัดบรรทัด"""
        compared = compare_reference_text(ABSTRACT_PAGE, APPROVED_TITLE, "title")
        compared = checker_module._title_as_printed(compared, ABSTRACT_PAGE, APPROVED_TITLE,
                                                    "สมหญิง ตัวอย่าง")
        detail = title_mismatch_detail("ชื่อเรื่องอีกภาษา", compared, APPROVED_TITLE)
        self.assertTrue(detail.endswith(' มี "การ" เกินมา'), detail)
        self.assertNotIn("ต่างที่", detail)
        self.assertNotIn("ช่องว่าง", detail)
        self.assertEqual(detail.fix_steps, ('ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก',))


class ARealExtraSpaceIsNamed(unittest.TestCase):
    """เจ้าหน้าที่: "กรณีที่เป็นเว้นวรรคก็ควรบอก" — บอกเฉพาะช่องว่างที่เล่มพิมพ์เกินจริง"""

    TYPED = BOOK_TITLE.replace("ในระบบบริหาร", "ในระบบ บริหาร")

    def test_a_space_typed_inside_a_thai_phrase_is_named(self):
        analysis = analyze_diff(self.TYPED, APPROVED_TITLE, spaces=True)
        self.assertEqual(
            analysis.text, 'มี "การ" เกินมา และ มีช่องว่างเกินระหว่าง "ในระบบ" กับ "บริหาร"')
        self.assertEqual(analysis.steps, ('ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก',
                                          'ให้ลบช่องว่างระหว่าง "ในระบบ" กับ "บริหาร"'))

    def test_spaces_stay_quiet_unless_the_caller_asks(self):
        """ผู้เรียกที่ยังต่อบรรทัดด้วยช่องว่างเอง ห้ามได้ช่องว่างปลอมมาบอกนักศึกษา"""
        self.assertEqual(describe_diff(self.TYPED, APPROVED_TITLE), 'มี "การ" เกินมา')
        self.assertEqual(analyze_diff(self.TYPED, APPROVED_TITLE).steps,
                         ('ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก',))

    def test_a_space_beside_english_is_not_reported(self):
        """ควบคุมเชิงลบ — รอยตัดระหว่างไทยกับอังกฤษแยกจากช่องว่างจริงไม่ได้ ไม่พูดถึง

        เล่มพิมพ์ "ภาพ (Image" แต่ข้อมูลอนุมัติเป็น "ภาพ(Image" — ช่องว่างข้างอักษรอังกฤษ
        """
        approved = APPROVED_TITLE.replace("ภาพ (Image", "ภาพ(Image")
        self.assertIn("ภาพ (Image", BOOK_TITLE)
        analysis = analyze_diff(BOOK_TITLE, approved, spaces=True)
        self.assertEqual(analysis.text, 'มี "การ" เกินมา')
        self.assertNotIn("ช่องว่าง", "".join(analysis.steps))

    def test_a_space_alone_is_still_not_a_finding(self):
        """ต่างกันแค่ช่องว่าง = ตรงกัน (norm ตัดช่องว่างทิ้ง) ไม่ใช่จุดผิดเหมือนเดิม"""
        self.assertEqual(analyze_diff(self.TYPED.replace("การวิเคราะห์", "วิเคราะห์"),
                                      APPROVED_TITLE, spaces=True).text, "")


class SaraAmIsHandledCarefully(unittest.TestCase):
    """สระ ำ: ตาเห็นเป็นตัวเดียวกับพยัญชนะ แต่เป็นอักขระสองตัวในไฟล์ (นิคหิต ํ + สระอา า)"""

    def test_sara_am_stays_with_its_consonant(self):
        self.assertEqual(_display_graphemes("น้ำ"), ["น้ำ"])
        self.assertEqual(_display_graphemes("จำใบ"), ["จำ", "ใบ"])
        self.assertEqual(_display_graphemes("สำหรับ"), ["สำ", "ห", "รั", "บ"])

    def test_the_old_splitter_is_untouched(self):
        """_closest_run ยังใช้ _graphemes ตัวเดิม (ชุดใหม่ใช้เฉพาะตอนอธิบายจุดต่าง)"""
        self.assertEqual(checker_module._graphemes("จำใบ"), ["จ", "ำ", "ใบ"])

    def test_a_decomposed_sara_am_is_not_reported_as_a_difference(self):
        """'นํา' (นิคหิต + สระอา) กับ 'นำ' ตาเห็นเหมือนกัน ห้ามขึ้นเป็น 'ต่างที่ "นํา" ต้องเป็น "นำ"'"""
        found = "การนํา" "เทคโนโลยีจําใบหน้าเพิ่ม"
        analysis = analyze_diff(found, "การนำเทคโนโลยีจำใบหน้า")
        self.assertEqual(analysis.text, 'มี "เพิ่ม" เกินมา')
        self.assertNotIn("ํ", analysis.text + "".join(analysis.steps))

    def test_sara_am_is_never_reported_on_its_own(self):
        """ขาด "ำ" ลอย ๆ ไม่บอกว่าอยู่ตรงไหน — ต้องยกทั้งพยางค์มา"""
        analysis = analyze_diff("ทาการศึกษา", "ทำการศึกษาวิจัย")
        self.assertNotIn('"ำ"', analysis.text)
        self.assertIn('ต่างที่ "ทา" ต้องเป็น "ทำ"', analysis.text)

    def test_a_wrong_vowel_inside_a_bigger_difference_shows_the_whole_syllable(self):
        analysis = analyze_diff("การนาเทคโนโลยีจดจำ", "การนำเทคโนโลยีจดจำใบหน้า")
        self.assertIn('ต่างที่ "นา" ต้องเป็น "นำ"', analysis.text)
        # ยกทั้งพยางค์ ("นา" → "นำ") พร้อมคำรอบข้าง ไม่ใช่ ำ ลอย ๆ (ความยาวของคำรอบข้างจะเปลี่ยนได้)
        self.assertTrue(analysis.steps[0].startswith('ให้แก้ "การนาเทค'), analysis.steps[0])
        self.assertIn('เป็น "การนำเทค', analysis.steps[0])


class AnchorsDoNotCutSyllables(unittest.TestCase):
    """ตัวอ้างอิงตำแหน่งที่ยกมาต้องไม่ขาดกลางพยางค์ ("นามั" ที่ขาดตัวสะกด ย อ่านไม่รู้เรื่อง)"""

    def test_the_end_is_extended_over_a_syllable_that_still_needs_its_final_consonant(self):
        graphemes = _display_graphemes("อนามัยดี")           # อ น า มั ย ดี
        self.assertEqual("".join(graphemes[:checker_module._snap_anchor_end(graphemes, 4)]), "อนามัย")

    def test_an_end_that_is_already_complete_is_left_alone(self):
        graphemes = _display_graphemes("อนามัยดี")
        self.assertEqual(checker_module._snap_anchor_end(graphemes, 5), 5)
        self.assertEqual(checker_module._snap_anchor_end(graphemes, len(graphemes)), len(graphemes))

    def test_the_start_never_lands_on_the_final_consonant_of_the_syllable_before(self):
        graphemes = _display_graphemes("ศึกษาตามหลักสูตรบัณฑิตวิทยาลัย")
        at = graphemes.index("ณ")                          # บั | ณ — ณ คือตัวสะกดของ บั
        start = checker_module._snap_anchor_start(graphemes, at)
        self.assertEqual(graphemes[start], "บั")

    def test_the_start_never_lands_on_a_letter_that_cannot_begin_a_word(self):
        graphemes = _display_graphemes("ศึกษาตามหลักสูตรบัณฑิตวิทยาลัย")
        at = graphemes.index("า", 4)                       # า ตัวที่สอง: ตามหลัง ต
        start = checker_module._snap_anchor_start(graphemes, at)
        self.assertNotEqual(graphemes[start], "า")


QUOTED = re.compile(r'"([^"]*)"')


def apply_step(text, step):
    """ทำตามวิธีแก้หนึ่งข้อบนข้อความที่พิมพ์ในเล่ม "ตามตัวอักษร" เหมือนนักศึกษากด Find/Replace ใน Word

    ข้อความที่ข้อนั้นยกมาอ้างต้องเจอเป๊ะ ๆ หนึ่งที่ (นับที่ซ้อนทับกันด้วย) — เจอไม่ครั้งเดียวคืน None
    ช่องว่างที่คั่นระหว่างตัวอ้างอิงกับสิ่งที่แก้ ("บัณฑิต" + " " + "สาขา") ยอมให้มีได้เพราะ "ที่อยู่หลัง" ในภาษาคน
    ไม่ได้แปลว่าต้องติดกันทุกตัวอักษร
    """
    raw = QUOTED.findall(step)
    esc = [re.escape(v) for v in raw]
    if step.startswith("ให้ลบช่องว่างระหว่าง"):
        pattern, build = rf"({esc[0]})\s+({esc[1]})", lambda m: m.group(1) + m.group(2)
    elif step.startswith("ให้ลบ ") and "ที่อยู่หลัง" in step:
        pattern, build = rf"({esc[1]})(\s*){esc[0]}", lambda m: m.group(1) + m.group(2)
    elif step.startswith("ให้ลบ ") and "ที่อยู่หน้า" in step:
        pattern, build = rf"{esc[0]}(\s*)({esc[1]})", lambda m: m.group(1) + m.group(2)
    elif step.startswith("ให้ลบ "):
        pattern, build = esc[0], lambda m: ""
    elif step.startswith("ให้เติม ") and " หลัง " in step:
        pattern, build = f"({esc[1]})", lambda m: m.group(1) + raw[0]
    elif step.startswith("ให้เติม ") and " หน้า " in step:
        pattern, build = f"({esc[1]})", lambda m: raw[0] + m.group(1)
    elif step.startswith("ให้แก้ ") and "ที่อยู่หลัง" in step:
        pattern, build = rf"({esc[1]})(\s*){esc[0]}", lambda m: m.group(1) + m.group(2) + raw[2]
    elif step.startswith("ให้แก้ ") and "ที่อยู่หน้า" in step:
        pattern, build = rf"{esc[0]}(\s*)({esc[1]})", lambda m: raw[2] + m.group(1) + m.group(2)
    elif step.startswith("ให้แก้ "):
        pattern, build = esc[0], lambda m: raw[1]
    else:
        raise AssertionError(f"ไม่รู้จักรูปแบบของวิธีแก้: {step}")
    if len(re.findall(f"(?=(?:{pattern}))", text)) != 1:
        return None
    return re.sub(pattern, build, text, count=1)


def apply_all(found, steps, reverse=False):
    text = found
    for step in (reversed(steps) if reverse else steps):
        text = apply_step(text, step)
        if text is None:
            return None
    return text


def same_letters(a, b):
    """ทำตามวิธีแก้แล้วต้องได้ข้อความที่ถูก "ทุกตัวอักษร" รวมวรรณยุกต์/สระ/ำ (ไม่ใช่แค่ตรงแบบหยาบของ norm)"""
    return checker_module.same_spelling(a, b)


class TheStepsCanBeFollowedOneByOne(unittest.TestCase):
    """วิธีแก้ที่บอกต้องทำตามได้จริง ไม่ว่าจะทำเรียงลำดับหรือสลับลำดับ

    ของเดิม (ก่อนรวมจุดที่อยู่ใกล้กัน) ข้อที่สองอ้างข้อความที่มีคำผิดของข้อแรกอยู่ด้วย ทำข้อแรกเสร็จแล้ว
    ข้อสองก็หาข้อความที่อ้างไว้ไม่เจอในเล่มอีก (เล่มชื่อเรื่องที่มีพิมพ์ผิดสองที่ติดกัน)
    """

    CASES = {
        "พิมพ์ผิดสองที่ติดกัน": (
            "ประสิทธิผลของโปรแกรมพัฒนาความรอบรู้ร่วมกัชแอพลิเคชันไลน์",
            "ประสิทธิผลของโปรแกรมพัฒนาความรอบรู้ร่วมกับแอปพลิเคชันไลน์"),
        "พิมพ์ผิดติดกับคำที่ขาด": (
            "ระในเบียบวิธีเชิงคุณภาพ น้ำหนักและความสำคัญ",
            "ระเบียบวิธีวิจัยเชิงคุณภาพ น้ำหนักและความสำคัญ"),
        "แก้คำผิดแล้วได้คำเดียวกับคำที่ต้องลบ": (
            "ประสิทธิผลนองโปรแกรมพัฒนาความรอบรู้ร่วมกับของแอปพลิเคชันไลน์",
            "ประสิทธิผลของโปรแกรมพัฒนาความรอบรู้ร่วมกับแอปพลิเคชันไลน์"),
        "พยางค์ซ้ำซ้อนทับกัน": (
            "ศิลปศาสตรมหาบัณฑิต สาขาวิชาการจัดจัดจการทรัพยากรจนุษย์และองค์การ",
            "ศิลปศาสตรมหาบัณฑิต สาขาวิชาการจัดการทรัพยากรมนุษย์และองค์การ"),
        "พิมพ์ซ้ำคำไทยที่ขึ้นต้นด้วยสระลอยตัว": (
            # "การ" ซ้ำอยู่หลายที่ในชื่อ และตัวที่เกินติดสระที่เริ่มคำไม่ได้ จึงหาตัวอ้างอิงตำแหน่งที่ใช้ได้ไม่ได้
            "การารศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ: การวิเคราะห์อภิมาน",
            "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ: การวิเคราะห์อภิมาน"),
        "พิมพ์ผิดแบบเดียวกันสองที่ในข้อความที่ซ้ำกัน": (
            "การประเมินผลขระทบด้านสุขภาพและการประเมินผลขระทบด้านสิ่งแวดล้อม",
            "การประเมินผลกระทบด้านสุขภาพและการประเมินผลกระทบด้านสิ่งแวดล้อม"),
        "อังกฤษในชื่อไทยพิมพ์เกินติดกับพิมพ์ผิด": (
            "การศึกษา COVID-19 mRNA vaccie booster er ในผู้สูงอายุ",
            "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ"),
        "อังกฤษในชื่อไทยพิมพ์ผิดและมีคำเกินที่ซ้ำท้ายคำ": (
            "การศึกษา COVID-19 mRNA vaxcine ne booster ในผู้สูงอายุ",
            "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ"),
        "เคาะวรรคเกินติดกับคำที่ขาด": (
            "ศิลปศาสตรมหาบัณฑิต สาขาวิ ชาจัดการทรัพยากรมนุษย์และองค์การ",
            "ศิลปศาสตรมหาบัณฑิต สาขาวิชาการจัดการทรัพยากรมนุษย์และองค์การ"),
        "เคาะวรรคเกินสองที่ติดกัน": (
            "ระเบียบวิธี วิ จัยเชิงคุณภาพ น้ำหนักแลความสำคัญ",
            "ระเบียบวิธีวิจัยเชิงคุณภาพ น้ำหนักและความสำคัญ"),
        "สามจุดในก้อนเดียว": (
            "แนวทางการพัฒาสิ่งITอำ นวยความสะดวกสนามกีฬากรีฑา",
            "แนวทางการพัฒนาสิ่งอำนวยความสะดวกสนามกีฬากรีฑา"),
        "เล่มจริงที่ตัดบรรทัดกลางคำ": (BOOK_TITLE, APPROVED_TITLE),
    }

    def test_every_case_can_be_followed_in_either_order(self):
        for name, (found, expected) in self.CASES.items():
            with self.subTest(name):
                analysis = analyze_diff(found, expected, spaces=True)
                self.assertTrue(analysis.steps, f"ไม่มีวิธีแก้: {analysis.text}")
                for reverse in (False, True):
                    fixed = apply_all(found, analysis.steps, reverse)
                    self.assertIsNotNone(fixed, f"ทำตามไม่ได้ (reverse={reverse}): {analysis.steps}")
                    self.assertTrue(same_letters(fixed, expected), f"{fixed!r} ไม่ตรง {expected!r}")

    def test_two_typos_next_to_each_other_are_one_step(self):
        found, expected = self.CASES["พิมพ์ผิดสองที่ติดกัน"]
        analysis = analyze_diff(found, expected)
        # คำพูดบนการ์ดยังบอกแยกทีละจุด แต่วิธีแก้เป็นข้อเดียวที่ครอบทั้งสองจุด
        self.assertEqual(analysis.text, 'ต่างที่ "ช" ต้องเป็น "บ" และ ขาด "ป"')
        self.assertEqual(analysis.steps, ('ให้แก้ "ร่วมกัชแอพลิเคชัน" เป็น "ร่วมกับแอปพลิเคชัน"',))

    def test_a_typo_beside_a_missing_word_is_one_step(self):
        found, expected = self.CASES["พิมพ์ผิดติดกับคำที่ขาด"]
        self.assertEqual(analyze_diff(found, expected).steps,
                         ('ให้แก้ "ระในเบียบวิธีเชิงคุณ" เป็น "ระเบียบวิธีวิจัยเชิงคุณ"',))

    def test_edits_far_apart_stay_separate_steps(self):
        """ควบคุมเชิงลบ — ไม่รวมจุดที่ห่างกันพอ (ข้อความอ้างอิงไม่ทับกัน)"""
        found = "การนำเทคโนโลยีการวิเคราะห์ภาพมาใช้ในระบบลูกค้าสัมพันธ์ขององค์กรตัวอย่าง"
        expected = "การนำเทคโนโลยีวิเคราะห์ภาพมาใช้ในระบบลูกค้าสัมพันธ์ขององค์กรตัวอย่างวิจัย"
        analysis = analyze_diff(found, expected)
        self.assertEqual(len(analysis.steps), 2, analysis.steps)

    def test_a_stray_space_beside_a_typo_is_one_step_that_removes_it(self):
        found, expected = self.CASES["เคาะวรรคเกินติดกับคำที่ขาด"]
        analysis = analyze_diff(found, expected, spaces=True)
        self.assertEqual(analysis.steps, ('ให้แก้ "สาขาวิ ชาจัดกา" เป็น "สาขาวิชาการจัดกา"',))

    def test_the_words_beside_a_removed_space_never_contain_another_space(self):
        """ลบช่องว่างแรกไปแล้ว ข้อความที่อีกข้อยกมาอ้างซึ่งมีช่องว่างนั้นอยู่ต้องไม่หายไปด้วย"""
        found, expected = self.CASES["เคาะวรรคเกินสองที่ติดกัน"]
        steps = analyze_diff(found, expected, spaces=True).steps
        space_steps = [step for step in steps if step.startswith("ให้ลบช่องว่างระหว่าง")]
        self.assertEqual(len(space_steps), 2, steps)
        for step in space_steps:
            for value in QUOTED.findall(step):
                self.assertNotIn(" ", value)

    def test_english_words_inside_a_thai_title_are_not_cut_in_the_middle(self):
        analysis = analyze_diff("การศึกษา COVID-19 mRNA vaccine bงooster ในผู้สูงอายุ",
                                "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ")
        self.assertEqual(analysis.steps, ('ให้แก้ "vaccine bงooster" เป็น "vaccine booster"',))

    def test_a_repeated_chunk_is_counted_wherever_it_overlaps(self):
        """ "จัดจ" ใน "จัดจัดจ" มีสองที่ ไม่ใช่ที่เดียว — ตัดที่แรกจะได้ "ัดจ" ค้างอยู่"""
        self.assertEqual(checker_module._count_overlapping("จัดจัดจ", "จัดจ"), 2)
        self.assertEqual(checker_module._count_overlapping("abc", ""), 0)
        found, expected = self.CASES["พยางค์ซ้ำซ้อนทับกัน"]
        steps = analyze_diff(found, expected).steps
        self.assertNotIn('ให้ลบ "จัดจ" ออก', steps)

    def test_an_edit_crossing_a_neighbour_is_detected(self):
        make = checker_module._Edit
        replace = make("replace", "ก", "ข", 10, 11, 10)
        insert = make("insert", "", "ค", 20, 20, 20)
        self.assertTrue(checker_module._crosses(8, 12, replace))      # ทับกัน
        self.assertFalse(checker_module._crosses(3, 10, replace))     # ชิดขอบแต่ไม่ทับ
        self.assertFalse(checker_module._crosses(11, 15, replace))
        self.assertTrue(checker_module._crosses(18, 22, insert))      # คร่อมจุดที่เติม
        self.assertFalse(checker_module._crosses(15, 20, insert))     # จบที่จุดเติมพอดี = ไม่คร่อม
        self.assertFalse(checker_module._crosses(20, 25, insert))

    def test_an_insertion_with_no_usable_anchor_shows_the_words_around_it(self):
        """ไม่มีตัวอ้างอิงที่ชี้ตำแหน่งได้ (ตัวถัดไปเป็นสระที่เริ่มคำไม่ได้) — ยกคำรอบจุดนั้นมาแทนการเติมลอย ๆ"""
        fg = _display_graphemes("าบริหารธุรกิจ")
        insert = checker_module._Edit("insert", "", "การ", 0, 0, 0)
        steps, clash = checker_module._steps_once(fg, [insert])
        self.assertIsNone(clash)
        self.assertEqual(steps, ['ให้แก้ "าบริห" เป็น "การาบริห"'])

    def test_edits_whose_ranges_overlap_give_no_steps(self):
        """ไม่ควรเกิด แต่ถ้าเกิด รวมกันแล้วได้ข้อความผิด — ไม่ตอบดีกว่าตอบผิด"""
        make = checker_module._Edit
        fg = list("กขคงจฉชซ")
        overlapping = [make("replace", "กขคง", "ก", 0, 4, 0), make("delete", "ค", "", 2, 3, 2)]
        self.assertEqual(checker_module._edit_steps(fg, overlapping), [])
        # ควบคุมเชิงลบ: ชิดกันพอดีไม่ใช่ซ้อนทับ ยังตอบตามปกติ
        touching = [make("replace", "กข", "ก", 0, 2, 0), make("delete", "ค", "", 2, 3, 2)]
        self.assertTrue(checker_module._edit_steps(fg, touching))

    def test_a_merge_longer_than_a_person_can_follow_gives_no_steps(self):
        """ต่างกันห่างกันไม่มากแต่รวมแล้วยาวเกิน ให้ดูข้อความที่ถูกต้องแทน"""
        make = checker_module._Edit
        fg = list("ก" * 60)
        self.assertIsNone(checker_module._merge_edits(
            fg, make("replace", "ก", "ข", 0, 1, 0), make("replace", "ก", "ข", 59, 60, 59)))
        merged = checker_module._merge_edits(
            fg, make("replace", "ก", "ข", 0, 1, 0), make("replace", "ก", "ข", 4, 5, 4))
        self.assertEqual((merged.kind, merged.got, merged.want), ("chunk", "กกกกก", "ขกกกข"))

    def test_random_mistakes_in_random_titles_can_always_be_followed(self):
        """สุ่มพิมพ์ผิดหนึ่งถึงสามจุด (+ เคาะวรรคเกิน) ในชื่อเรื่องสมมติ แล้วทำตามวิธีแก้ทุกข้อให้ได้ข้อความที่ถูก"""
        pool = [expected for _found, expected in self.CASES.values()] + [
            "ประสิทธิผลของโปรแกรมพัฒนาความรอบรู้ในการดูแลแบบประคับประคองที่บ้าน",
            "ปัจจัยที่มีผลต่อการตัดสินใจใช้บริการสุขภาพทางไกลของผู้สูงอายุในเขตกรุงเทพมหานคร",
        ]
        rng = random.Random(20261006)
        letters = "กขคงจชดตทนบปพมยรลวสหอ"
        chunks = ["การ", "ความ", "ใน", "ของ", "ที่", "ระบบ", "น้ำ", "คำ", "THE", "DATA"]
        checked = 0
        for _ in range(1500):
            expected = rng.choice(pool)
            found = expected
            for _edit in range(rng.choice([1, 1, 2, 3])):
                at = rng.randrange(1, len(found) - 1)
                kind = rng.choice(["drop", "add", "swap", "chunk", "repeat", "space"])
                if kind == "drop":
                    found = found[:at] + found[at + 1:]
                elif kind == "add":
                    found = found[:at] + rng.choice(letters) + found[at:]
                elif kind == "swap":
                    found = found[:at] + rng.choice(letters) + found[at + 1:]
                elif kind == "chunk":
                    found = found[:at] + rng.choice(chunks) + found[at:]
                elif kind == "repeat":
                    found = found[:at + 3] + found[at:at + 3] + found[at + 3:]
                else:
                    found = found[:at] + " " + found[at:]
            # ระบบเทียบข้อความที่ยุบช่องว่างซ้อนเป็นช่องเดียวแล้วเสมอ (soft) การ์ดก็แสดงแบบนั้น
            found = re.sub(r"\s+", " ", found)
            # ข้อความที่ระบบอ่านจากเล่มเรียงตัวเล็กบน/ล่างลำดับมาตรฐานเสมอ (_attach_thai_marks) ส่วนตัวสุ่มข้างบน
            # แทรกตัวอักษรกลางพยางค์จนลำดับเพี้ยนแบบที่เล่มจริงไม่มีทางได้ จึงเรียงให้ก่อนเหมือนตัวอ่าน
            found = checker_module._canonical_marks(found)
            analysis = analyze_diff(found, expected, spaces=True)
            if not analysis.text:
                continue
            self.assertTrue(analysis.steps, (found, expected))
            for reverse in (False, True):
                fixed = apply_all(found, analysis.steps, reverse)
                self.assertIsNotNone(fixed, (found, expected, analysis.steps, reverse))
                self.assertTrue(same_letters(fixed, expected), (found, expected, analysis.steps, fixed))
            checked += 1
        self.assertGreater(checked, 1000)


class TheAlternateTitleCardNamesItsLanguage(unittest.TestCase):
    """เจ้าหน้าที่ (ต.ค. 2569): ข้อความต้องบอกภาษา "ชื่อเรื่องภาษาไทยในรูปเล่ม ไม่ตรงกับข้อมูลในระบบ"
    ไม่ใช่ "ชื่อเรื่องอีกภาษา..." ที่นักศึกษาต้องเดาเองว่าหมายถึงชื่อไหน"""

    def test_run_check_names_the_language_not_the_other_one(self):
        import inspect
        import checker as checker_module
        source = inspect.getsource(checker_module.run_check)
        self.assertIn('"ชื่อเรื่องภาษาอังกฤษในรูปเล่ม " if thai_book', source)
        self.assertIn('else "ชื่อเรื่องภาษาไทยในรูปเล่ม ")', source)
        self.assertIn("title_mismatch_detail(alt_word, compared, alt_title)", source)
        self.assertNotIn('title_mismatch_detail("ชื่อเรื่องอีกภาษา"', source)

    def test_both_wordings_are_translated_as_whole_sentences(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        compared = compare_reference_text(ABSTRACT_PAGE, APPROVED_TITLE, "title")
        for word in ("ชื่อเรื่องภาษาไทยในรูปเล่ม ", "ชื่อเรื่องภาษาอังกฤษในรูปเล่ม "):
            line = str(title_mismatch_detail(word, compared, APPROVED_TITLE))
            self.assertTrue(line.startswith(word + "ไม่ตรงกับข้อมูลในระบบ: "), line)
            en = i18n.tr_en(line, pairs)
            self.assertEqual(i18n.translation_problems(line, en), [], (line, en))
            self.assertIn("title in the document does not match", en)


class TheCardKeepsItsWordsButCarriesTheSteps(unittest.TestCase):
    def test_the_found_text_is_a_plain_string_with_the_difference_at_the_end(self):
        compared = compare_reference_text(ABSTRACT_PAGE, APPROVED_TITLE, "title")
        detail = title_mismatch_detail("ชื่อเรื่องอีกภาษา", compared, APPROVED_TITLE)
        self.assertIsInstance(detail, DetailText)
        self.assertEqual(
            str(detail),
            f'ชื่อเรื่องอีกภาษาไม่ตรงกับข้อมูลในระบบ: "{compared["actual"]}" มี "การ" เกินมา')

    def test_report_add_keeps_the_steps_and_stores_a_plain_string(self):
        compared = compare_reference_text(ABSTRACT_PAGE, APPROVED_TITLE, "title")
        rep = Report()
        rep.add("RED", "front_matter", "บทคัดย่อภาษาไทย (หน้า vi)",
                title_mismatch_detail("ชื่อเรื่องอีกภาษา", compared, APPROVED_TITLE),
                f'ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: "{APPROVED_TITLE}"',
                "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")
        issue = rep.zones["RED"][0]
        self.assertIs(type(issue["found"]), str)
        self.assertEqual(issue["diff_tail"], 'มี "การ" เกินมา')
        self.assertEqual(issue["fix_steps"], ['ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก'])

    def test_the_plain_mismatch_detail_carries_the_steps_too(self):
        """ชื่อบท/ชื่อนักศึกษา/ชื่อปริญญา ฯลฯ ผ่าน mismatch_detail ไม่ใช่ title_mismatch_detail"""
        compared = checker_module.compare_values("RESEARCH METHODLOGY", "RESEARCH METHODOLOGY", "title")
        detail = checker_module.mismatch_detail("ชื่อบท", compared, "RESEARCH METHODOLOGY")
        self.assertIsInstance(detail, DetailText)
        self.assertEqual(
            str(detail),
            'ชื่อบทในเล่มเขียนว่า "RESEARCH METHODLOGY" ต่างที่ "METHODLOGY" ต้องเป็น "METHODOLOGY"')
        self.assertEqual(detail.fix_steps, ('ให้แก้ "METHODLOGY" เป็น "METHODOLOGY"',))

    def test_a_card_built_by_a_direct_call_site_carries_the_steps_too(self):
        """ข้อความ template ใต้ชื่อหัวข้อ (เรียกเครื่องชี้จุดต่างตรง ๆ ไม่ผ่าน mismatch_detail)"""
        degree = "วิทยาศาสตรมหาบัณฑิต (โรคติดเชื้อและวิทยาการระบาดทางการสาธารณสุข)"
        dots = "…………………………………………………………………………...…………"
        page = "\n".join([
            "ก", "วิทยานิพนธ์", "เรื่อง", "ชื่อเรื่องตัวอย่างของวิทยานิพนธ์",
            "นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร", "ปริญญา" + degree, "วันที่ 18 กันยายน 2569",
            "คณะกรรมการที่ปรึกษาวิทยานิพนธ์", "อาจารย์ที่ปรึกษาหลัก", dots,
            "ชื่อ นามสกุล, ผู้ช่วยศาสตราจารย์ ตัวอย่าง", dots, "คณบดี", "คณบดี",
            "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล", "คณะสาธารณสุขศาสตร์ มหาวิทยาลัยมหิดล"])
        rep = Report()
        checker_module._report_signature_template(
            rep, "หน้าลงนาม 1 (หน้า ก)", page, checker_module.SIGNATURE_TEMPLATE_TH, degree, True)
        issue = rep.zones["RED"][0]
        self.assertEqual(issue["diff_tail"], 'ขาด "ได้รับการพิจารณาให้"')
        self.assertEqual(issue["fix_steps"], ['ให้เติม "ได้รับการพิจารณาให้" หน้า "นับเป็น"'])

    def test_an_issue_that_was_not_compared_has_no_steps(self):
        rep = Report()
        rep.add("RED", "front_matter", "หน้าปก", "ไม่พบชื่อเรื่องบนหน้านี้", "ต้องมีชื่อเรื่อง",
                "", "FORM.APPROVED_MATCH")
        issue = rep.zones["RED"][0]
        self.assertEqual(issue["diff_tail"], "")
        self.assertEqual(issue["fix_steps"], [])


def _summary_of(*issues):
    rep = Report()
    for args in issues:
        rep.add(*args)
    return plain_summary(check_result(rep, {"front_label_style": "roman"}))


class TheSummaryLayout(unittest.TestCase):
    """หนึ่งจุด = ตำแหน่ง / สิ่งที่พบ / วิธีแก้ทีละคำสั่ง / ต้องแก้เป็น"""

    def _title_issue(self):
        compared = compare_reference_text(ABSTRACT_PAGE, APPROVED_TITLE, "title")
        compared = checker_module._title_as_printed(compared, ABSTRACT_PAGE, APPROVED_TITLE,
                                                    "สมหญิง ตัวอย่าง")
        return ("RED", "front_matter", "บทคัดย่อภาษาไทย (หน้า vi)",
                title_mismatch_detail("ชื่อเรื่องอีกภาษา", compared, APPROVED_TITLE),
                f'ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: "{APPROVED_TITLE}"',
                "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")

    def test_the_summary_says_how_to_fix_between_what_was_found_and_the_answer(self):
        lines = _summary_of(self._title_issue()).splitlines()
        at = next(i for i, line in enumerate(lines) if line.startswith("1. "))
        self.assertEqual(lines[at], "1. บทคัดย่อภาษาไทย (หน้า vi)")
        self.assertTrue(lines[at + 1].startswith('   ชื่อเรื่องอีกภาษาไม่ตรงกับข้อมูลในระบบ: "'), lines[at + 1])
        self.assertTrue(lines[at + 1].endswith('ขององค์กรตัวอย่าง"'), lines[at + 1])
        self.assertEqual(lines[at + 2], '   ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก')
        self.assertEqual(lines[at + 3], f'   ต้องแก้เป็น "{APPROVED_TITLE}"')

    def test_the_old_unhelpful_wording_is_gone(self):
        text = _summary_of(self._title_issue())
        self.assertNotIn("ต่างที่", text)
        self.assertNotIn("เกินมา", text)
        self.assertNotIn("ระบบ บริหาร", text)

    def test_an_issue_without_a_comparison_is_laid_out_as_before(self):
        text = _summary_of(("RED", "body", "บทที่ 4 ในสารบัญ (หน้า ix) และในเนื้อหา (หน้า 30)",
                            'ชื่อบทในเล่มเขียนว่า "RESEARCH RESULTS"',
                            'ตามประกาศ 2569 ควรเป็น "RESULTS"', "", "BODY.OPTION1"))
        self.assertIn('   ชื่อบทในเล่มเขียนว่า "RESEARCH RESULTS"\n   ต้องแก้เป็น "RESULTS"', text)

    def test_two_steps_are_two_lines(self):
        compared = {"status": "typo", "score": 0.97, "actual": ARealExtraSpaceIsNamed.TYPED}
        issue = ("RED", "front_matter", "บทคัดย่อภาษาไทย (หน้า vi)",
                 title_mismatch_detail("ชื่อเรื่องอีกภาษา", compared, APPROVED_TITLE),
                 f'ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: "{APPROVED_TITLE}"',
                 "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")
        text = _summary_of(issue)
        self.assertIn('   ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก\n'
                      '   ให้ลบช่องว่างระหว่าง "ในระบบ" กับ "บริหาร"\n'
                      '   ต้องแก้เป็น', text)


class TheEnglishSummaryHasNoThaiLeftOver(unittest.TestCase):
    """วิธีแก้ทุกแบบต้องแปลอังกฤษได้ครบ — ข้อความสรุปแปลทีละบรรทัดด้วยกฎเต็มประโยค"""

    @classmethod
    def setUpClass(cls):
        tools = Path(__file__).resolve().parents[1] / "tools"
        sys.path.insert(0, str(tools))
        import check_i18n
        cls.i18n = check_i18n
        _block, cls.pairs = check_i18n.load_tr()

    def _english(self, thai):
        return self.i18n.tr_en(thai, self.pairs)

    def _thai_left(self, thai):
        import re
        stripped = re.sub(r'"[^"]*"', "", self._english(thai))
        return [w for w in re.findall(r"[ก-๙]+", stripped)
                if w not in self.i18n.THAI_PAGE_LETTERS and w not in self.i18n.KEEP_THAI]

    def test_every_kind_of_step_has_a_whole_sentence_rule(self):
        for thai, english in (
            ('ให้ลบ "การ" ออก', 'Delete "การ"'),
            ('ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก', 'Delete "การ" that follows "เทคโนโลยี"'),
            ('ให้ลบ "การ" ที่อยู่หน้า "จดจำ" ออก', 'Delete "การ" that comes before "จดจำ"'),
            ('ให้เติม "กีฬา" หลัง "สนาม"', 'Add "กีฬา" after "สนาม"'),
            ('ให้เติม "นับ" หน้า "เป็น"', 'Add "นับ" before "เป็น"'),
            ('ให้เติม "OF"', 'Add "OF"'),
            ('ให้แก้ "ทิ" เป็น "ฑิ"', 'Change "ทิ" to "ฑิ"'),
            ('ให้แก้ "DATA" ที่อยู่หลัง "ON THE" เป็น "DATUM"',
             'Change "DATA" that follows "ON THE" to "DATUM"'),
            ('ให้แก้ "DATA" ที่อยู่หน้า "SET" เป็น "DATUM"',
             'Change "DATA" that comes before "SET" to "DATUM"'),
            ('ให้แก้การเว้นวรรค "OFTHE" เป็น "OF THE"', 'Fix the spacing: change "OFTHE" to "OF THE"'),
            ('ให้แก้ตัวพิมพ์เล็ก-ใหญ่ "CAMELINA" เป็น "Camelina"',
             'Fix the letter case: change "CAMELINA" to "Camelina"'),
            ('ให้ลบช่องว่างระหว่าง "ในระบบ" กับ "บริหาร"',
             'Remove the space between "ในระบบ" and "บริหาร"'),
        ):
            with self.subTest(thai=thai):
                self.assertEqual(self._english(thai), english)
                self.assertIsNotNone(self.i18n.whole_rule(thai, self.pairs),
                                     "ต้องเป็นกฎเต็มประโยค ไม่พึ่งเศษคำ")

    def test_the_card_phrase_for_an_extra_space_is_translated(self):
        thai = 'มี "การ" เกินมา และ มีช่องว่างเกินระหว่าง "ในระบบ" กับ "บริหาร"'
        self.assertEqual(self._thai_left(thai), [])
        self.assertIn('there is an extra space between "ในระบบ" and "บริหาร"', self._english(thai))

    def test_the_base_lines_left_after_the_difference_is_removed_are_whole_sentences(self):
        """ท่อนจุดต่างถูกถอดไปเป็นบรรทัดแยก เหลือบรรทัดที่พบสั้น ๆ ซึ่งต้องมีกฎเต็มประโยคด้วย"""
        for thai in (
            'ชื่อเรื่องอีกภาษาไม่ตรงกับข้อมูลในระบบ: "A B"',
            'ชื่อเรื่องไม่ตรงกับข้อมูลในระบบ: "A B"',
            'ชื่อบทในเล่มเขียนว่า "A B"',
            'ข้อความ template ใต้ชื่อหัวข้อพิมพ์ว่า "A B"',
            'หน้าปกพิมพ์ "A B" ไม่ตรงข้อความบังคับ (ข้อความประเภทงาน)',
            'สารบัญสะกดหัวข้อนี้ผิด เขียนว่า "A B"',
            'ช่องประธานหลักสูตร (มุมล่างขวา) สะกดชื่อสาขาผิด เขียนว่า "A B"',
            'ช่องคณบดีคณะ (มุมล่างขวา) สะกดชื่อคณะผิด เขียนว่า "A B"',
            'ช่องประธานหลักสูตร (มุมล่างขวา) สะกดชื่อปริญญาผิด เขียนว่า "A B"',
            'หัวข้อรายชื่อคณะกรรมการที่ปรึกษาเขียนว่า "A B"',
            'บรรทัดชื่อนักศึกษาพิมพ์รหัสว่า "A B"',
        ):
            with self.subTest(thai=thai):
                self.assertEqual(self._thai_left(thai), [], self._english(thai))
                self.assertIsNotNone(self.i18n.whole_rule(thai, self.pairs),
                                     f"ไม่มีกฎเต็มประโยค: {thai}")


if __name__ == "__main__":
    unittest.main()
