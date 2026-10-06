# -*- coding: utf-8 -*-
"""สระอำเขียนได้หลายแบบ — ระบบต้องนับทุกแบบเป็นสระอำเหมือนกัน (เจ้าหน้าที่สั่ง 6 ต.ค. 2569)

    ำ        อักขระเดียว            เล่มหลายเล่ม
    ํ + า    นิคหิตกับสระอา        ไฟล์ eThesis ทุกไฟล์ที่ตรวจดู และเล่มบางเล่ม
    ํ + ้ + า เมื่อมีวรรณยุกต์ (นํ้า)  ลำดับที่ระบบอ่านได้จาก PDF จริง (เล่มจริงเล่มหนึ่งมี 261 ที่)
    ํ + ช่องว่าง + า              ระบบอ่านแล้วแทรกช่องว่างเองเมื่อ า วางห่าง (เล่มจริงเล่มหนึ่ง 43 ที่)

ตาเห็นเป็น "น้ำ" เหมือนกันหมด แต่เป็นคนละลำดับอักขระ — ถ้าไม่รวมเป็นแบบเดียว จุดต่างจะอ่านออกมาว่า
'ต่างที่ "นํ้า" ต้องเป็น "น้ำ"' ซึ่งไม่มีใครมองออกว่าต่างตรงไหน
"""
import copy
import unittest
from unittest import mock

import checker as checker_module
import ethesis_import
from checker import _compose_thai_line, _thai_chars, analyze_diff, norm
from thai_text import fold_sara_am, fold_sara_am_in

AM, NIKH, AA, MAITHO = "ำ", "ํ", "า", "้"
WATER = "น" + MAITHO + AM                 # น้ำ  (ลำดับมาตรฐาน)
WATER_NIKHAHIT_FIRST = "น" + NIKH + MAITHO + AA   # นํ้า (ลำดับที่ระบบอ่านจาก PDF)
WATER_TONE_FIRST = "น" + MAITHO + NIKH + AA       # น้ํา
WATER_PLAIN_AA = "น" + MAITHO + AA                # น้า  (สระอา ไม่ใช่สระอำ)


def ch(text, x0, top=100.0, width=6.0):
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top}


class EverySaraAmIsFoldedIntoOne(unittest.TestCase):
    def test_all_the_ways_of_writing_it_become_the_single_character(self):
        for written, folded in (
            ("น" + AM, "นำ"),                       # ำ ตัวเดียวอยู่แล้ว
            ("น" + NIKH + AA, "นำ"),                # ํ + า
            ("น" + NIKH + AM, "นำ"),                # ํ ซ้ำหน้า ำ
            (WATER, WATER),
            (WATER_NIKHAHIT_FIRST, WATER),           # ํ + ้ + า
            (WATER_TONE_FIRST, WATER),               # ้ + ํ + า
            ("น" + NIKH + MAITHO + "่" + AA, "น" + MAITHO + "่" + AM),   # วรรณยุกต์สองตัว
            # ช่องว่างที่ระบบอ่านแทรกเองระหว่างนิคหิตกับ า (า ขึ้นต้นคำไม่ได้ จึงไม่ใช่ช่องว่างจริง)
            ("จ" + NIKH + " " + AA + "เนียร", "จ" + AM + "เนียร"),
            ("น" + NIKH + MAITHO + " " + AA, WATER),
        ):
            with self.subTest(written=written.encode("unicode_escape")):
                self.assertEqual(fold_sara_am(written), folded)

    def test_a_whole_sentence_is_folded_everywhere_in_it(self):
        text = "การนํา" "น" + NIKH + MAITHO + AA + "ที่จํา" "เป็น"
        self.assertEqual(fold_sara_am(text), "การนำ" + WATER + "ที่จำเป็น")

    def test_things_that_are_not_sara_am_are_left_alone(self):
        """ควบคุมเชิงลบ — พับเฉพาะนิคหิต + (วรรณยุกต์) + สระอา"""
        for text in (
            WATER_PLAIN_AA,                  # น้า เป็นสระอา ไม่ใช่สระอำ ห้ามกลายเป็น น้ำ
            "สํสกฤต",                        # นิคหิตที่ไม่ตามด้วยสระอา
            "ตํ",                            # นิคหิตลอย ๆ ท้ายข้อความ
            "การนำ",                         # ำ ตัวเดียวอยู่แล้ว
            "ส" + NIKH + " ก",               # นิคหิต + ช่องว่าง + พยัญชนะ — ช่องว่างจริงระหว่างคำ ห้ามลบ
            "จ " + AA,                       # ช่องว่าง + า โดยไม่มีนิคหิต ไม่ใช่สระอำ
        ):
            with self.subTest(text=text.encode("unicode_escape")):
                self.assertEqual(fold_sara_am(text), text)

    def test_folding_twice_changes_nothing(self):
        for text in (WATER_NIKHAHIT_FIRST, WATER_TONE_FIRST, "จํา", "ทำ"):
            self.assertEqual(fold_sara_am(fold_sara_am(text)), fold_sara_am(text))

    def test_missing_text_becomes_empty(self):
        self.assertEqual(fold_sara_am(None), "")
        self.assertEqual(fold_sara_am(""), "")

    def test_a_whole_data_structure_is_folded_without_touching_the_original(self):
        original = {"title": WATER_NIKHAHIT_FIRST, "members": [WATER_NIKHAHIT_FIRST, ("จํา", 5)],
                    "page": 12, "empty": None}
        before = copy.deepcopy(original)
        folded = fold_sara_am_in(original)
        self.assertEqual(folded, {"title": WATER, "members": [WATER, ("จำ", 5)], "page": 12, "empty": None})
        self.assertEqual(original, before)       # ของเดิมไม่ถูกแก้ ทั้งชั้นนอกและรายการข้างใน


class TheBookTextIsFoldedWhenItIsRead(unittest.TestCase):
    def test_nikhahit_then_tone_mark_then_aa_is_one_water_word(self):
        """PDF จริงให้ "นํ้า" (นิคหิตก่อนวรรณยุกต์) — ต้องอ่านเป็น "น้ำ" ไม่ใช่สี่อักขระ"""
        chars = [ch("น", 10.0), ch(NIKH, 16.0, width=0.0), ch(MAITHO, 16.0, width=0.0), ch(AA, 16.2)]
        self.assertEqual(_compose_thai_line(chars), WATER)

    def test_an_aa_drawn_a_little_apart_does_not_split_the_sara_am(self):
        """เล่มจริงเล่มหนึ่งวางตัว า ห่างจากพยัญชนะ 1.2-3.1 pt (43 ที่) ระบบจึงแทรกช่องว่างเป็น "จํ า"

        ช่องว่างนั้นไม่มีอยู่จริง — า ขึ้นต้นคำไม่ได้ ต้องอ่านเป็น "จำ"
        """
        chars = [ch("จ", 10.0), ch(NIKH, 16.0, width=0.0), ch(AA, 17.5), ch("น", 23.5)]
        self.assertEqual(_compose_thai_line(chars), "จ" + AM + "น")

    def test_a_real_gap_after_a_word_is_kept(self):
        """ควบคุมเชิงลบ — ช่องว่างจริงระหว่างคำ (ไม่ได้ตามด้วย า) ต้องอยู่ครบ"""
        chars = [ch("จ", 10.0), ch(NIKH, 16.0, width=0.0), ch(AA, 16.2), ch("น", 30.0)]
        self.assertEqual(_compose_thai_line(chars), "จ" + AM + " น")

    def test_words_read_for_the_signature_table_are_folded_too(self):
        """หน้าลงนามอ่านเป็น "คำ" (ไม่ใช่บรรทัด) เพื่อแบ่งช่องตาราง — ต้องได้สระอำแบบเดียวกัน"""
        class _Page:
            chars = []

            @staticmethod
            def extract_words(**_kwargs):
                return [{"text": WATER_NIKHAHIT_FIRST, "x0": 10.0, "x1": 30.0, "top": 100.0},
                        {"text": "จ" + NIKH + AA, "x0": 40.0, "x1": 52.0, "top": 100.0}]

        texts = [w["text"] for w in checker_module._sig_words(_Page())]
        self.assertEqual(texts, [WATER, "จ" + AM])

    def test_a_font_that_draws_the_nikhahit_as_a_zero_width_space_gives_the_same_word(self):
        chars = [ch("น", 10.0), {"text": " ", "x0": 16.0, "x1": 16.0, "top": 100.0},
                 ch(MAITHO, 16.0, width=0.0), ch(AA, 16.2)]
        self.assertEqual(_compose_thai_line(_thai_chars(chars)), WATER)

    def test_a_word_whose_tone_mark_sits_after_the_aa_is_untouched(self):
        """ควบคุมเชิงลบ — น้า (สระอา) ห้ามถูกเดาเป็น น้ำ"""
        chars = [ch("น", 10.0), ch(MAITHO, 16.0, width=0.0), ch(AA, 16.2)]
        self.assertEqual(_compose_thai_line(chars), WATER_PLAIN_AA)


class TheApprovedDataIsFoldedWhenItEntersTheSystem(unittest.TestCase):
    def test_the_import_from_an_ethesis_file_folds_every_form(self):
        pua_tone = ""                    # ฟอนต์ที่ map ไม้โทเป็นอักขระส่วนบุคคล → ้
        for raw in (WATER_NIKHAHIT_FIRST, WATER_TONE_FIRST, "น" + NIKH + pua_tone + AA):
            with self.subTest(raw=raw.encode("unicode_escape")):
                self.assertEqual(ethesis_import._fix_thai_pua(raw), WATER)
        self.assertEqual(ethesis_import._fix_thai_pua("จ" + NIKH + AA), "จำ")

    def test_values_typed_or_pasted_in_the_form_are_folded_before_checking(self):
        """ข้อมูลอนุมัติเข้า run_check ทางเดียว ไม่ว่ามาจากไฟล์ eThesis หรือที่เจ้าหน้าที่พิมพ์/วาง"""
        approved = {"thesis_title_th": WATER_NIKHAHIT_FIRST}
        with mock.patch.object(checker_module, "fold_sara_am_in",
                               wraps=checker_module.fold_sara_am_in) as spy:
            checker_module.run_check("not-a-pdf.txt", approved)      # ไม่ใช่ PDF จบทันทีหลังเข้าด่านแรก
        spy.assert_called_once_with(approved)


class TheDifferenceExplainerCountsEveryFormAsSaraAm(unittest.TestCase):
    EXPECTED = "ค่า" + WATER + "ประปา"

    def test_no_phantom_difference_between_the_forms(self):
        """มีคำเกินจริงหนึ่งคำ — ต้องรายงานแค่คำนั้น ไม่มี 'ต่างที่ นํ้า ต้องเป็น น้ำ' ที่มองไม่เห็นความต่าง"""
        for form in (WATER, WATER_NIKHAHIT_FIRST, WATER_TONE_FIRST):
            with self.subTest(form=form.encode("unicode_escape")):
                analysis = analyze_diff("ค่า" + form + "ประปาใหม่", self.EXPECTED)
                self.assertEqual(analysis.text, 'มี "ใหม่" เกินมา')
                self.assertEqual(analysis.steps, ('ให้แก้ "ระปาใหม่" เป็น "ระปา"',))

    def test_the_expected_side_may_be_any_form_too(self):
        analysis = analyze_diff("ค่า" + WATER + "ประปาใหม่", "ค่า" + WATER_NIKHAHIT_FIRST + "ประปา")
        self.assertEqual(analysis.text, 'มี "ใหม่" เกินมา')

    def test_a_plain_aa_is_still_a_different_letter_when_the_title_is_flagged_anyway(self):
        """ควบคุมเชิงลบ — น้า ไม่ใช่ น้ำ: เมื่อชื่อเรื่องถูกฟ้องด้วยเหตุอื่น ต้องชี้ว่า า ควรเป็น ำ"""
        analysis = analyze_diff("ค่า" + WATER_PLAIN_AA + "ประปาใหม่", self.EXPECTED)
        self.assertIn('ต่างที่ "' + WATER_PLAIN_AA + '" ต้องเป็น "' + WATER + '"', analysis.text)


class TheComparisonIsStillLenientAboutSaraAmVersusAa(unittest.TestCase):
    def test_norm_treats_the_forms_and_a_plain_aa_as_the_same_key(self):
        """ตั้งใจและยังไม่เปลี่ยน — รอเจ้าหน้าที่ตัดสินว่าจะให้จับ ำ กับ า ต่างกันไหม

        norm() ตัดวรรณยุกต์/สระบนล่างทิ้งและมอง ำ เป็น า เพราะ PDF ทำสระพวกนี้หายบ่อย ถ้าจะจับ ำ/า ตรง ๆ
        ต้องเพิ่มเป็นกฎ (ข้อสีส้ม ระบบอ่านผิดได้) ไม่ใช่แก้ norm — ดู RULES_AND_SOURCES.md
        หัวข้อ "ชี้จุดต่างของข้อความไทย" ย่อหน้าสระอำ เทสต์นี้ล็อกพฤติกรรมไว้ให้การเปลี่ยนเป็นการตัดสินใจ ไม่ใช่เผลอ
        """
        keys = {norm(form) for form in (WATER, WATER_NIKHAHIT_FIRST, WATER_TONE_FIRST, WATER_PLAIN_AA)}
        self.assertEqual(len(keys), 1)


if __name__ == "__main__":
    unittest.main()
