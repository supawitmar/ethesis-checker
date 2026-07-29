# -*- coding: utf-8 -*-
"""unittest ของจุดที่เคยฟ้องผิดกับเล่มจริง — ครอบทุกเงื่อนไข/รูปแบบที่พบจริง

ทุกเคสในไฟล์นี้มาจากเล่มจริงที่เจ้าหน้าที่ส่งมา ไม่ใช่เคสสมมติ:
  * เล่มพยาบาล  — บทคัดย่อขาดจุลภาค + ชื่อสาขาห่อบรรทัดในช่องประธานหลักสูตร
  * เล่มวิศวะ   — ช่องประธานหลักสูตรยาวสองบรรทัดทั้งซ้ายและขวา
  * เล่มพยาบาล 2 — ฟอนต์ map นิคหิตของ ำ เป็นตัวเว้นวรรค
"""
import unittest

from checker import (
    _compose_thai_line,
    _strip_committee_title,
    _thai_chars,
    abstract_committee_missing_commas,
    issues_to_fix,
    norm,
    signature_committee_slots,
    split_abstract_committee,
)


def ch(text, x0, top=100.0, width=6.0):
    """สร้าง char ปลอมให้เหมือนที่ pdfplumber คืนมา"""
    return {"text": text, "x0": x0, "x1": x0 + width, "top": top}


class SplitAbstractCommittee(unittest.TestCase):
    """แยก 'ชื่อ, คุณวุฒิ' บนหน้าบทคัดย่อ — ครอบทุกรูปแบบคุณวุฒิที่เจอในเล่มจริง"""

    def test_english_standard(self):
        names, degrees = split_abstract_committee(
            "NARISARA CHANTRATITA, Ph.D., NITAYA INDRAWATTANA, Ph.D.")
        self.assertEqual(names, ["NARISARA CHANTRATITA", "NITAYA INDRAWATTANA"])
        self.assertEqual(degrees, ["Ph.D.", "Ph.D."])

    def test_thai_standard(self):
        names, degrees = split_abstract_committee(
            "ยอด สุขะมงคล, ปร.ด., ทวีศักดิ์ สมานชื่น, ปร.ด.")
        self.assertEqual(names, ["ยอด สุขะมงคล", "ทวีศักดิ์ สมานชื่น"])
        self.assertEqual(degrees, ["ปร.ด.", "ปร.ด."])

    def test_mixed_thai_degree_abbreviations(self):
        # เล่มจริงใช้ ศษ.ด. / วศ.ด. / พย.ด. ปนกัน
        names, _ = split_abstract_committee(
            "คนางค์ คันธมธุรพจน์, PhD., สุภาภรณ์ สงค์ประชา, ศษ.ด., ธเนศ เกษศิลป์, ปร.ด.")
        self.assertEqual(names,
                         ["คนางค์ คันธมธุรพจน์", "สุภาภรณ์ สงค์ประชา", "ธเนศ เกษศิลป์"])

    def test_malformed_english_degrees_still_split(self):
        # เล่มจริงพิมพ์ผิดเป็น "Ph.D" / "P.hD." — ต้องยังแยกชื่อได้
        names, _ = split_abstract_committee(
            "KANANG KANTAMATURAPOJ, Ph.D, SUPAPORN SONGPRACHA, Ed.D., TANET KETSIL, P.hD.")
        self.assertEqual(names,
                         ["KANANG KANTAMATURAPOJ", "SUPAPORN SONGPRACHA", "TANET KETSIL"])

    def test_degree_with_subject_in_parentheses(self):
        names, degrees = split_abstract_committee(
            "SARUNYA KOOSITAMONGKOL, Ph.D. (NURSING)., USAVADEE ASDORNWISED, Ph.D. (NURSING)")
        self.assertEqual(names, ["SARUNYA KOOSITAMONGKOL", "USAVADEE ASDORNWISED"])
        self.assertEqual(degrees, ["Ph.D. (NURSING).", "Ph.D. (NURSING)"])

    def test_missing_comma_between_degree_and_next_name(self):
        """เคสที่เคยฟ้องแดงผิดว่า 'ไม่พบกรรมการ อุษาวดี อัศดรวิเศษ'

        เล่มลืมจุลภาคหลัง "ปร.ด.(การพยาบาล)" ตัวแยกเดิมอ่านชื่อคนที่ 2
        เป็น "Ph.D. (NURSING)" แล้วสรุปว่ากรรมการหายไป
        """
        block = "ศรัณยา โฆสิตะมงคล, ปร.ด.(การพยาบาล) อุษาวดี อัศดรวิเศษ, Ph.D. (NURSING)"
        names, degrees = split_abstract_committee(block)
        self.assertEqual(names, ["ศรัณยา โฆสิตะมงคล", "อุษาวดี อัศดรวิเศษ"])
        self.assertEqual(degrees, ["ปร.ด.(การพยาบาล)", "Ph.D. (NURSING)"])

    def test_missing_comma_is_reported_so_staff_knows_what_to_fix(self):
        block = "ศรัณยา โฆสิตะมงคล, ปร.ด.(การพยาบาล) อุษาวดี อัศดรวิเศษ, Ph.D. (NURSING)"
        self.assertEqual(abstract_committee_missing_commas(block), ["อุษาวดี อัศดรวิเศษ"])

    def test_no_missing_comma_reported_when_format_is_correct(self):
        self.assertEqual(
            abstract_committee_missing_commas("ยอด สุขะมงคล, ปร.ด., ทวีศักดิ์ สมานชื่น, ปร.ด."),
            [])

    def test_trailing_comma_and_extra_spaces(self):
        names, _ = split_abstract_committee("ยุพา จิ๋วพัฒนกุล, ปร.ด., รักชนก คชไกร , ปร.ด.,")
        self.assertEqual(names, ["ยุพา จิ๋วพัฒนกุล", "รักชนก คชไกร"])

    def test_empty_block(self):
        self.assertEqual(split_abstract_committee(""), ([], []))
        self.assertEqual(split_abstract_committee(None), ([], []))


class AbstractNamesHaveNoAcademicTitle(unittest.TestCase):
    """ชื่อกรรมการในบทคัดย่อต้องเป็นชื่อ-สกุลล้วน ห้ามมีตำแหน่งทางวิชาการนำหน้า

    ต้องเทสคู่กับตัวแยกชื่อเสมอ เพราะถ้าแยกชื่อพลาด กฎนี้จะไปตรวจสตริงผิดตัว
    (ก่อนแก้บั๊กจุลภาค ชื่อคนที่ 2 ถูกอ่านเป็น "Ph.D. (NURSING)" กฎนี้จึงไม่เคยได้ตรวจ)
    """

    def _titles_flagged(self, block):
        names, _ = split_abstract_committee(block)
        return [nm for nm in names if _strip_committee_title(nm) != nm]

    def test_thai_full_titles_are_flagged(self):
        self.assertEqual(
            self._titles_flagged(
                "ศาสตราจารย์ ศรัณยา โฆสิตะมงคล, ปร.ด., "
                "รองศาสตราจารย์ อุษาวดี อัศดรวิเศษ, Ph.D."),
            ["ศาสตราจารย์ ศรัณยา โฆสิตะมงคล", "รองศาสตราจารย์ อุษาวดี อัศดรวิเศษ"])

    def test_thai_abbreviated_titles_are_flagged(self):
        self.assertEqual(
            self._titles_flagged("ผศ. ดร. ยอด สุขะมงคล, ปร.ด., ดร.ทวีศักดิ์ สมานชื่น, ปร.ด."),
            ["ผศ. ดร. ยอด สุขะมงคล", "ดร.ทวีศักดิ์ สมานชื่น"])

    def test_english_titles_are_flagged(self):
        self.assertEqual(
            self._titles_flagged(
                "Prof. NARISARA CHANTRATITA, Ph.D., Assoc. Prof. NITAYA INDRAWATTANA, Ph.D."),
            ["Prof. NARISARA CHANTRATITA", "Assoc. Prof. NITAYA INDRAWATTANA"])

    def test_clean_names_are_not_flagged(self):
        self.assertEqual(
            self._titles_flagged("ศรัณยา โฆสิตะมงคล, ปร.ด., อุษาวดี อัศดรวิเศษ, Ph.D."), [])

    def test_second_name_is_checked_even_when_a_comma_is_missing(self):
        """เคสที่เคยหลุด: ขาดจุลภาค -> ชื่อคนที่ 2 อ่านผิด กฎคำนำหน้าเลยไม่ได้ตรวจ"""
        flagged = self._titles_flagged(
            "ศรัณยา โฆสิตะมงคล, ปร.ด.(การพยาบาล) ดร.อุษาวดี อัศดรวิเศษ, Ph.D.")
        self.assertEqual(flagged, ["ดร.อุษาวดี อัศดรวิเศษ"])


class ThaiTextRepair(unittest.TestCase):
    """ซ่อมข้อความไทยจาก PDF ให้เจ้าหน้าที่อ่านออกว่าหมายถึงตรงไหนของเล่ม"""

    def test_zero_width_space_becomes_sara_am(self):
        """ฟอนต์ map นิคหิตของ ำ เป็นตัวเว้นวรรคกว้างศูนย์ -> "จำ" ไม่ใช่ "จา " """
        chars = [ch("จ", 10.0), {"text": " ", "x0": 16.4, "x1": 16.4, "top": 100.0},
                 ch("า", 16.0), ch("ล", 22.0), ch("อ", 28.0), ch("ง", 34.0)]
        self.assertEqual(_compose_thai_line(_thai_chars(chars)), "จำลอง")

    def test_real_space_is_kept(self):
        """ช่องว่างจริง (กว้าง >= 0.5) ต้องไม่ถูกแปลงเป็นนิคหิต"""
        chars = [ch("ก", 10.0), {"text": " ", "x0": 16.0, "x1": 20.0, "top": 100.0},
                 ch("ข", 30.0)]
        out = _compose_thai_line(_thai_chars(chars))
        self.assertIn(" ", out)
        self.assertNotIn("ํ", out)

    def test_existing_sara_am_not_doubled(self):
        """ไฟล์ที่มี ำ อยู่แล้วและยังใส่นิคหิตซ้ำ ต้องไม่ได้ "คํำ" """
        chars = [ch("ค", 10.0), {"text": " ", "x0": 16.2, "x1": 16.2, "top": 100.0},
                 ch("ำ", 16.0)]
        self.assertEqual(_compose_thai_line(_thai_chars(chars)), "คำ")

    def test_marks_reattach_to_their_own_base(self):
        """pdfplumber เรียงตาม x ทำให้ ั หลุดไปหลังพยัญชนะถัดไป ("พฒั" -> "พัฒ")"""
        chars = [ch("พ", 10.0), ch("ฒ", 16.0), ch("ั", 14.0, width=0.0), ch("น", 22.0)]
        self.assertEqual(_compose_thai_line(chars), "พัฒน")

    def test_vowel_sorts_before_tone_mark(self):
        """ลำดับไทยคือ พยัญชนะ + สระ + วรรณยุกต์ ("ที่" ไม่ใช่ "ท่ี")"""
        chars = [ch("ท", 10.0), ch("่", 11.0, width=0.0), ch("ี", 12.0, width=0.0)]
        self.assertEqual(_compose_thai_line(chars), "ที่")

    def test_thanthakhat_sorts_last(self):
        chars = [ch("ด", 10.0), ch("์", 11.0, width=0.0), ch("ิ", 12.0, width=0.0)]
        self.assertEqual(_compose_thai_line(chars), "ดิ์")

    def test_empty_input(self):
        self.assertEqual(_compose_thai_line([]), "")


class _FakePage:
    """หน้า PDF ปลอมสำหรับทดสอบการอ่านตารางลายเซ็น"""

    def __init__(self, width, words):
        self.width = width
        self._words = words
        self.chars = []

    def extract_words(self, **_kwargs):
        return self._words


def word(text, x0, top):
    return {"text": text, "x0": x0, "x1": x0 + 8.0 * len(text), "top": top,
            "non_stroking_color": (0, 0, 0)}


class SignatureBottomCell(unittest.TestCase):
    """ช่องสถาบันแถวล่าง — ต้องหาชื่อหลักสูตรเจอทั้งกรณีห่อบรรทัดและสองช่องยาว"""

    def _page(self, institution_lines, width=595.0):
        """สร้างหน้าลงนามตามกริด template: 5 แถวกรรมการ + 1 แถวสถาบัน

        institution_lines = บรรทัดของช่องล่างสุด (คณบดี | ประธานหลักสูตร)
        ซึ่งเป็นส่วนที่ signature_committee_slots คืนมาเป็น bottom_text
        """
        words, top = [], 100.0
        for i in range(5):                       # แถวกรรมการ 1..5
            words.append(word("……………", 60.0, top))
            words.append(word("……………", 320.0, top))
            top += 20.0
            words.append(word(f"Member{i}", 320.0, top))
            top += 20.0
        words.append(word("……………", 60.0, top))   # แถวเส้นประของช่องสถาบัน
        words.append(word("……………", 320.0, top))
        top += 20.0
        for line in institution_lines:
            for text, x0 in line:
                words.append(word(text, x0, top))
            top += 20.0
        return _FakePage(width, words)

    def test_subject_wrapped_across_lines_is_still_found(self):
        """ชื่อสาขายาวจนขึ้นบรรทัดใหม่ และคำท้ายตกไปทางซ้ายของกึ่งกลางหน้า

        เคสนี้เคยฟ้องส้มผิดว่า "ไม่พบชื่อสาขา" ทั้งที่เล่มพิมพ์ถูกต้อง
        """
        page = self._page([
            [("คณบดี", 60.0), ("ประธานหลักสูตร", 250.0)],
            [("บัณฑิตวิทยาลัย", 60.0), ("สาขาวิชาการพยาบาลผู้ใหญ่และ", 250.0)],
            [("ผู้สูงอายุ", 250.0)],          # คำท้ายอยู่ซ้ายของกึ่งกลางหน้า (297.5)
        ])
        _members, _quals, bottom = signature_committee_slots(page)
        self.assertIn(norm("การพยาบาลผู้ใหญ่และผู้สูงอายุ"), norm(bottom))

    def test_subject_in_right_cell_when_both_cells_are_long(self):
        """สองช่องยาวทั้งคู่ — เรียงตามลำดับอ่านล้วน ๆ จะสลับกัน ต้องยังหาเจอ"""
        page = self._page([
            [("Dean", 60.0), ("Program", 300.0), ("Director", 360.0)],
            [("Faculty", 60.0), ("of", 110.0), ("Graduate", 140.0),
             ("Information", 300.0), ("Technology", 380.0)],
            [("Studies", 60.0), ("Management", 300.0)],
        ])
        _members, _quals, bottom = signature_committee_slots(page)
        self.assertIn(norm("Information Technology Management"), norm(bottom))

    def test_empty_page_returns_blank(self):
        members, quals, bottom = signature_committee_slots(_FakePage(595.0, []))
        self.assertEqual((members, quals, bottom), ({}, {}, ""))


class SystemNoteNotSentToStudent(unittest.TestCase):
    """ข้อจำกัดของระบบต้องไม่ไปอยู่ในใบสั่งแก้ของนักศึกษา"""

    def _report(self, *orange):
        return {"issues_by_zone": {"RED": [], "ORANGE": list(orange), "YELLOW": []}}

    def test_system_note_is_excluded(self):
        report = self._report(
            {"found": "ระบบยังไม่ได้เปิดใช้ AI", "system_note": True},
            {"found": "ชื่อเรื่องไม่ตรง", "system_note": False},
        )
        found = [i["found"] for i in issues_to_fix(report)]
        self.assertEqual(found, ["ชื่อเรื่องไม่ตรง"])

    def test_issue_without_the_flag_is_kept(self):
        report = self._report({"found": "ชื่อเรื่องไม่ตรง"})
        self.assertEqual(len(issues_to_fix(report)), 1)

    def test_red_system_note_is_also_excluded(self):
        report = {"issues_by_zone": {
            "RED": [{"found": "ระบบอ่านไฟล์ไม่ได้", "system_note": True}],
            "ORANGE": [], "YELLOW": []}}
        self.assertEqual(issues_to_fix(report), [])


if __name__ == "__main__":
    unittest.main()
