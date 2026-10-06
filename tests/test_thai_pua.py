# -*- coding: utf-8 -*-
"""วรรณยุกต์/สระที่ฟอนต์ไทยเก็บเป็นรหัส PUA (U+F700-F71D) ต้องอ่านกลับได้ครบ (เล่มจริง ต.ค. 2569)

เจ้าหน้าที่แจ้ง: *"การอ่านหัวข้อของเล่มนี้เพี้ยนหนักมาก การอ่านวรรณยุกต์ก็ทำไม่ได้"* ·
*"ส่วนชื่อหัวข้อ ยังไงก็ผิด ในการอ่านคำแน่นอน"* — เล่มพิมพ์ด้วย TH Sarabun New ซึ่งเก็บวรรณยุกต์ตัวต่ำ/เยื้องซ้าย
เป็นรหัส PUA ระบบทิ้งไปทั้งหมด ("ได้รับ" → "ไดรับ") แล้วตัวหาบล็อกชื่อเรื่องบนหน้าลงนามหา "ได้รับการพิจารณา"
ไม่เจอ ชื่อเรื่องจึงกินบรรทัดถัดไปอีก 4 บรรทัด
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import checker as checker_module  # noqa: E402
import ethesis_import  # noqa: E402
import thai_text  # noqa: E402
from thai_text import fix_thai_pua  # noqa: E402

# ชื่อ glyph ที่แต่ละรหัสชี้ไป อ่านจากไฟล์ฟอนต์ TH Sarabun PSK และ TH Sarabun New (ตาราง cmap + post) — ทั้งสองไฟล์ตรงกัน
# "uniXXXX.altN" = ตัวสำรองของอักษร U+XXXX ตารางแปลงจึงต้องให้ผลเป็นอักษรตัวนั้นพอดี
GLYPH_NAMES = {
    0xF700: "uni0E10.alt1", 0xF701: "uni0E34.alt1", 0xF702: "uni0E35.alt1", 0xF703: "uni0E36.alt1",
    0xF704: "uni0E37.alt1", 0xF705: "uni0E48.alt1", 0xF706: "uni0E49.alt1", 0xF707: "uni0E4A.alt1",
    0xF708: "uni0E4B.alt1", 0xF709: "uni0E4C.alt1", 0xF70A: "uni0E48.alt2", 0xF70B: "uni0E49.alt2",
    0xF70C: "uni0E4A.alt2", 0xF70D: "uni0E4B.alt2", 0xF70E: "uni0E4C.alt2", 0xF70F: "uni0E0D.alt1",
    0xF710: "uni0E31.alt1", 0xF711: "uni0E4D.alt1", 0xF712: "uni0E47.alt1", 0xF713: "uni0E48.alt3",
    0xF714: "uni0E49.alt3", 0xF715: "uni0E4A.alt3", 0xF716: "uni0E4B.alt3", 0xF717: "uni0E4C.alt3",
    0xF718: "uni0E38.alt1", 0xF719: "uni0E39.alt1", 0xF71A: "uni0E3A.alt1", 0xF71B: "uni0E0E.alt1",
    0xF71C: "uni0E0F.alt1", 0xF71D: "uni0E2C.alt1",
}
LOW_MAI_EK, LOW_MAI_THO, LOW_THANTHAKHAT = chr(0xF70A), chr(0xF70B), chr(0xF70E)
LEFT_MAI_HAN_AKAT, LEFT_MAITAIKHU = chr(0xF710), chr(0xF712)
YO_YING_DESCLESS, LO_CHULA_ALT = chr(0xF70F), chr(0xF71D)


def _chars(*spec, font="FNTSBS+THSarabunNew"):
    """[(ข้อความ, ความกว้าง)] → chars ต่อกันแบบที่ pdfplumber ส่งมา (ตัวกว้างศูนย์วางที่ขอบขวาของตัวก่อนหน้า)"""
    out, x = [], 10.0
    for text, width in spec:
        out.append({"text": text, "x0": x, "x1": x + width, "top": 100.0, "size": 16.0, "fontname": font})
        x += width
    return out


def _line(*spec):
    return checker_module._compose_thai_line(checker_module._thai_chars(_chars(*spec)))


class _Page:
    def __init__(self, chars):
        self.chars = chars


class ThePuaTableComesFromTheFont(unittest.TestCase):
    def test_every_code_reads_as_the_letter_its_glyph_is_named_after(self):
        for code, name in GLYPH_NAMES.items():
            self.assertEqual(fix_thai_pua(chr(code)), chr(int(name[3:7], 16)), hex(code))

    def test_the_table_covers_exactly_the_glyphs_in_the_font(self):
        self.assertEqual(set(thai_text._PUA_GLYPH_OF), set(GLYPH_NAMES))

    def test_the_two_codes_the_old_import_table_guessed_wrong(self):
        """ตารางเดิมของตัวนำเข้า eThesis เดา F700 เป็น ั และ F70F เป็น ํ — ในฟอนต์คือ ฐ และ ญ ตัดเชิง"""
        self.assertEqual(fix_thai_pua(chr(0xF700)), "ฐ")
        self.assertEqual(fix_thai_pua(YO_YING_DESCLESS), "ญ")

    def test_other_text_is_untouched(self):
        for text in ("ได้รับการพิจารณา", "THESIS", "", chr(0xF71E), chr(0xF6FF)):
            self.assertEqual(fix_thai_pua(text), text)
        self.assertEqual(fix_thai_pua(None), "")


class ThaiWordsReadBackWhole(unittest.TestCase):
    def test_words_from_the_book(self):
        self.assertEqual(fix_thai_pua("ได" + LOW_MAI_THO + "รับ"), "ได้รับ")
        self.assertEqual(fix_thai_pua("มีต" + LOW_MAI_EK + "อ"), "มีต่อ")
        self.assertEqual(fix_thai_pua("วิทยานิพนธ" + LOW_THANTHAKHAT), "วิทยานิพนธ์")
        self.assertEqual(fix_thai_pua("เป" + LEFT_MAITAIKHU + "น"), "เป็น")
        self.assertEqual(fix_thai_pua("ป" + LEFT_MAI_HAN_AKAT + "จจุบัน"), "ปัจจุบัน")
        self.assertEqual(fix_thai_pua("นา" + LO_CHULA_ALT + "ิกา"), "นาฬิกา")
        self.assertEqual(fix_thai_pua("วิญ" + YO_YING_DESCLESS + "ูชน"), "วิญญูชน")


class TheBookReaderKeepsPuaMarks(unittest.TestCase):
    """เดิม _thai_chars ทิ้งอักขระกว้างศูนย์ที่ไม่ใช่สระ/วรรณยุกต์ — วรรณยุกต์รหัส PUA จึงหายทั้งเล่ม"""

    def test_a_low_tone_mark_attaches_to_its_consonant(self):
        self.assertEqual(_line(("ต", 6.0), (LOW_MAI_EK, 0.0), ("อ", 6.0)), "ต่อ")

    def test_a_low_tone_mark_after_a_lower_vowel(self):
        self.assertEqual(_line(("ก", 6.0), ("ล", 6.0), ("ุ", 0.0), (LOW_MAI_EK, 0.0), ("ม", 6.0)), "กลุ่ม")

    def test_left_shifted_marks_on_tall_consonants(self):
        self.assertEqual(_line(("เ", 4.0), ("ป", 6.0), (LEFT_MAITAIKHU, 0.0), ("น", 6.0)), "เป็น")

    def test_a_wide_consonant_variant_is_a_letter(self):
        self.assertEqual(_line(("น", 6.0), ("า", 4.0), (LO_CHULA_ALT, 7.3), ("ิ", 0.0), ("ก", 6.0), ("า", 4.0)),
                         "นาฬิกา")

    def test_the_page_text_has_the_marks(self):
        page = _Page(_chars(("ไ", 4.0), ("ด", 6.0), (LOW_MAI_THO, 0.0), ("ร", 5.0), ("ั", 0.0), ("บ", 6.0)))
        self.assertEqual(checker_module._page_text(page), "ได้รับ")

    def test_a_zero_width_letter_is_still_dropped(self):
        """ฟอนต์ที่ map วรรณยุกต์เป็นตัวอักษรอื่น ("8") ยังทิ้งเหมือนเดิม เพราะเดาไม่ได้ว่าเป็นวรรณยุกต์ตัวไหน"""
        self.assertEqual(_line(("ท", 6.0), ("ี", 0.0), ("8", 0.0), ("ป", 6.0)), "ทีป")

    def test_header_words_are_mapped_not_stripped(self):
        class HeaderPage:
            height = 841.9

            @staticmethod
            def extract_words(*_a, **_k):
                return [{"text": "ได" + LOW_MAI_THO + "รับ", "top": 40.0}, {"text": "12", "top": 40.0}]

        self.assertEqual(checker_module.header_extra_text(HeaderPage()), "ได้รับ")


class PuaAndFormulaGlyphsAreNotFontDamage(unittest.TestCase):
    """ป้าย "ฟอนต์ในไฟล์ทำให้ระบบอ่านข้อความเพี้ยน" ต้องขึ้นเฉพาะเมื่ออ่านข้อความไทยผิดจริง

    เล่มจริงสองเล่ม (ต.ค. 2569) ขึ้น 119 หน้า และ 4 หน้า จากรหัส PUA และจาก x̄ ในตารางสถิติล้วน ๆ —
    เล่มที่ถูกตั้งป้ายนี้ ข้อสะกดผิดระดับวรรณยุกต์ถูกลดเป็นส้มทั้งเล่ม (Report.marks_unreliable)
    """

    def test_pua_tone_marks_are_not_damage(self):
        page = _Page(_chars(("ต", 6.0), (LOW_MAI_EK, 0.0), ("อ", 6.0), ("เ", 4.0), ("ป", 6.0), (LEFT_MAITAIKHU, 0.0)))
        self.assertEqual(checker_module.font_damage_score(page, "ต่อเป็"), 0)

    def test_x_bar_from_a_math_font_is_not_damage(self):
        page = _Page(_chars(("(cid:1876)", 6.0), (chr(0x0305), 0.0), font="FNTSBS+CambriaMath")
                     + _chars(("ก", 6.0)))
        self.assertEqual(checker_module.font_damage_score(page, "(cid:1876)ก"), 0)

    def test_the_same_glyphs_in_a_text_font_still_count(self):
        """ควบคุม: glyph ไม่มีรหัสและอักขระกว้างศูนย์ในฟอนต์ข้อความยังเป็นความเสียหายเหมือนเดิม"""
        page = _Page(_chars(("(cid:12)", 6.0), ("8", 0.0)))
        self.assertEqual(checker_module.font_damage_score(page, "(cid:12)"), 2)

    def test_only_the_formula_glyphs_are_excused(self):
        page = _Page(_chars(("(cid:1876)", 6.0), font="FNTSBS+CambriaMath") + _chars(("(cid:12)", 6.0)))
        self.assertEqual(checker_module.font_damage_score(page, "(cid:1876)(cid:12)"), 1)


class TemplateLinesAreFoundEvenWhenMarksAreLost(unittest.TestCase):
    """ตัวหาขอบบล็อกบนหน้า (ชื่อเรื่อง ชื่อนักศึกษา) หาแบบไม่สนวรรณยุกต์ — ตัดสินตัวสะกดทำที่อื่น"""

    TITLE = ["การศึกษาตัวอย่างระบบ", "ของหน่วยงาน"]
    AFTER = ["ปริญญาวิทยาศาสตรมหาบัณฑิต", "วันที่ 1 มกราคม 2569", "คณะกรรมการที่ปรึกษาวิทยานิพนธ"]

    def _signature_page(self, lead_in, template):
        return "\n".join(["วิทยานิพนธ์", lead_in] + self.TITLE + [template] + self.AFTER)

    def test_the_title_stops_at_a_template_line_without_marks(self):
        page = self._signature_page("เรื่อง", "ไดรับการพิจารณาใหนับเปนสวนหนึ่งของการศึกษาตามหลักสูตร")
        self.assertEqual(checker_module.printed_title(page), "การศึกษาตัวอย่างระบบของหน่วยงาน")

    def test_the_title_starts_after_a_lead_in_without_marks(self):
        page = self._signature_page("เรอง", "ไดรับการพิจารณาใหนับเปนสวนหนึ่งของการศึกษาตามหลักสูตร")
        self.assertEqual(checker_module.printed_title(page), "การศึกษาตัวอย่างระบบของหน่วยงาน")

    def test_a_correctly_read_page_gives_the_same_title(self):
        page = self._signature_page("เรื่อง", "ได้รับการพิจารณาให้นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร")
        self.assertEqual(checker_module.printed_title(page), "การศึกษาตัวอย่างระบบของหน่วยงาน")

    def test_english_pages_are_unchanged(self):
        page = "\n".join(["Thesis", "entitled", "A STUDY OF SAMPLE SYSTEMS", "was submitted to the Graduate"])
        self.assertEqual(checker_module.printed_title(page), "A STUDY OF SAMPLE SYSTEMS")

    def test_the_cover_name_slot_is_found_without_marks(self):
        page = "\n".join(self.TITLE + ["สมหญิง ตัวอย่าง",
                                       "วิทยานิพนธนี้เปนสวนหนึ่งของการศึกษาตามหลักสูตร", "ปริญญาวิทยาศาสตรมหาบัณฑิต"])
        self.assertEqual(checker_module.cover_printed_name(page), "สมหญิง ตัวอย่าง")

    def test_the_signature_name_slot_is_found_without_marks(self):
        page = "\n".join(["คณะกรรมการที่ปรึกษาวิทยานิพนธ", "สมหญิง ตัวอย่าง", "ผูวิจัย"])
        self.assertEqual(checker_module.signature_printed_name(page), "สมหญิง ตัวอย่าง")

    def test_a_title_line_is_not_mistaken_for_a_template_line(self):
        """ควบคุม: ชื่อเรื่องที่มีข้อความ template อยู่กลางบรรทัดต้องไม่ถูกตัด (หาเฉพาะต้นบรรทัด)"""
        page = "\n".join(["เรื่อง", "ปัจจัยที่มีผลต่อการได้รับการพิจารณาเลื่อนตำแหน่ง",
                          "ได้รับการพิจารณาให้นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร"])
        self.assertEqual(checker_module.printed_title(page), "ปัจจัยที่มีผลต่อการได้รับการพิจารณาเลื่อนตำแหน่ง")


class TheEthesisImportUsesTheSameTable(unittest.TestCase):
    def test_no_private_table_left(self):
        self.assertFalse(hasattr(ethesis_import, "_PUA_TONE"))

    def test_a_committee_name_with_a_descless_yo_ying(self):
        """ไฟล์ eThesis ทดสอบมีชื่อกรรมการที่สะกด "ญญู" — ญ ตัวที่สองเคยกลายเป็นนิคหิต"""
        self.assertEqual(ethesis_import._fix_thai_pua("วิญ" + YO_YING_DESCLESS + "ูชน"), "วิญญูชน")

    def test_marks_the_import_already_read_still_read(self):
        self.assertEqual(ethesis_import._fix_thai_pua("เป" + LEFT_MAITAIKHU + "น"), "เป็น")
        self.assertEqual(ethesis_import._fix_thai_pua("ทวีศักดิ" + LOW_THANTHAKHAT), "ทวีศักดิ์")


if __name__ == "__main__":
    unittest.main()
