# -*- coding: utf-8 -*-
"""unittest ของจุดที่เคยฟ้องผิดกับเล่มจริง — ครอบทุกเงื่อนไข/รูปแบบที่พบจริง

ทุกเคสในไฟล์นี้มาจากเล่มจริงที่เจ้าหน้าที่ส่งมา ไม่ใช่เคสสมมติ:
  * เล่มพยาบาล  — บทคัดย่อขาดจุลภาค + ชื่อสาขาห่อบรรทัดในช่องประธานหลักสูตร
  * เล่มวิศวะ   — ช่องประธานหลักสูตรยาวสองบรรทัดทั้งซ้ายและขวา
  * เล่มพยาบาล 2 — ฟอนต์ map นิคหิตของ ำ เป็นตัวเว้นวรรค
"""
import unittest

from checker import (
    Report,
    _compose_thai_line,
    classify,
    _report_committee_name_case,
    _report_student_name_style,
    _strip_student_title,
    _report_thai_committee,
    _rejoin_thai_marks,
    _sig_words,
    _page_count_issue,
    ethesis_matches_book,
    _toc_continuation_pages,
    _toc_page_label,
    _toc_section_kind,
    _strip_toc_page_number,
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

    def test_multi_part_degree_is_not_split_into_a_name(self):
        """คุณวุฒิที่มีหลายท่อนคั่นด้วยช่องว่าง เช่น "Dr. rer. nat." (เยอรมัน)

        เคยฟ้องผิดว่า "ชื่อกรรมการคนที่ 5 ไม่ได้เป็นตัวพิมพ์ใหญ่: rer. nat."
        เพราะตัดคุณวุฒิที่หัวก้อนแล้วเหมาว่าส่วนที่เหลือคือชื่อคนถัดไป
        """
        block = ("PICHITPONG SOONTORNPIPIT, Ph.D., JUTATIP SILLABUTRA, Ph.D.,"
                 "PRATANA SATITVIPAWEE, Ph.D., "
                 "WACHIRAPORN WANICHNOPPARAT, Dr. rer. nat.")
        names, degrees = split_abstract_committee(block)
        self.assertEqual(names, ["PICHITPONG SOONTORNPIPIT", "JUTATIP SILLABUTRA",
                                 "PRATANA SATITVIPAWEE", "WACHIRAPORN WANICHNOPPARAT"])
        self.assertEqual(degrees[-1], "Dr. rer. nat.")
        self.assertEqual(abstract_committee_missing_commas(block), [])

    def test_other_multi_part_degrees(self):
        names, degrees = split_abstract_committee(
            "SOMCHAI JAIDEE, Dr. med., SOMSRI DEEJAI, Dr. phil.")
        self.assertEqual(names, ["SOMCHAI JAIDEE", "SOMSRI DEEJAI"])
        self.assertEqual(degrees, ["Dr. med.", "Dr. phil."])

    def test_two_degrees_per_person(self):
        """คนหนึ่งมีคุณวุฒิหลายตัวคั่นจุลภาค เช่น "..., M.D., Ph.D., ..."

        เคยทำให้การสลับ ชื่อ/คุณวุฒิ เลื่อนทั้งชุด จนได้ชื่อเพี้ยนอย่าง
        "RAJIT BOONSAEN" และ "OL UDOL" แล้วฟ้องว่าไม่พบกรรมการ
        """
        names, degrees = split_abstract_committee(
            "MAYUREE HOMSANIT, M.D., Ph.D., THIRAJIT BOONSAEN, M.D., Ph.D., "
            "KAMOL UDOL, M.D., M.Sc.")
        self.assertEqual(names, ["MAYUREE HOMSANIT", "THIRAJIT BOONSAEN", "KAMOL UDOL"])
        self.assertEqual(degrees, ["M.D.", "Ph.D.", "M.D.", "Ph.D.", "M.D.", "M.Sc."])

    def test_name_starting_with_short_word_is_not_eaten_as_a_degree(self):
        """ตัวย่อคุณวุฒิต้องมีจุด ไม่งั้นคำแรกของชื่อจะถูกกินเป็นคุณวุฒิ (THIRAJIT -> THI)"""
        names, _ = split_abstract_committee("SOM CHAI, Ph.D., THIRAJIT BOONSAEN, Ph.D.")
        self.assertEqual(names, ["SOM CHAI", "THIRAJIT BOONSAEN"])

    def test_trailing_comma_and_extra_spaces(self):
        names, _ = split_abstract_committee("ยุพา จิ๋วพัฒนกุล, ปร.ด., รักชนก คชไกร , ปร.ด.,")
        self.assertEqual(names, ["ยุพา จิ๋วพัฒนกุล", "รักชนก คชไกร"])

    def test_thai_degree_written_with_a_space_after_the_dot(self):
        """เล่มจริงพิมพ์ "พย. ด." / "ปร. ด." (เว้นวรรคหลังจุด) ไม่ใช่ "พย.ด."

        เดิมจับได้แค่ท่อนแรก ท่อนที่เหลือเลื่อนไปเป็นชื่อคน
        """
        names, degrees = split_abstract_committee(
            "อัจฉราพร สี่หิรัญวงศ์, พย. ด., อาภาวรรณ หนูคง, ปร. ด.")
        self.assertEqual(names, ["อัจฉราพร สี่หิรัญวงศ์", "อาภาวรรณ หนูคง"])
        self.assertEqual(degrees, ["พย. ด.", "ปร. ด."])

    def test_degrees_joined_with_and_in_one_field(self):
        """คนเดียวมีหลายวุฒิเขียนต่อกันด้วย "และ" เช่น "พ.บ., ว.ว. และ อ.ว."

        เดิม "ว.ว. และ อ.ว." ถูกอ่านเป็นชื่อคน แล้วทำให้ชื่อ/วุฒิสลับกันทั้งชุด
        จนฟ้องแดงว่าพบชื่อ "ว.ว. และ อ.ว." และ "ปร. ด." ที่ไม่อยู่ในรายชื่ออนุมัติ
        """
        block = ("อัจฉราพร สี่หิรัญวงศ์, พย. ด., อัจฉริยา พ่วงแก้ว, พย. ด., "
                 "อาภาวรรณ หนูคง, ปร. ด., ปัญจมา ปาราจารย์, พ.บ., ว.ว. และ อ.ว., "
                 "ทวีศักดิ์ สมานชื่น, ปร. ด.")
        names, degrees = split_abstract_committee(block)
        self.assertEqual(names, ["อัจฉราพร สี่หิรัญวงศ์", "อัจฉริยา พ่วงแก้ว",
                                 "อาภาวรรณ หนูคง", "ปัญจมา ปาราจารย์",
                                 "ทวีศักดิ์ สมานชื่น"])
        self.assertIn("ว.ว. และ อ.ว.", degrees)
        self.assertEqual(abstract_committee_missing_commas(block), [])

    def test_english_degrees_joined_with_and(self):
        names, degrees = split_abstract_committee(
            "SOMCHAI JAIDEE, M.D., Ph.D. and D.Sc., SOMSRI DEEJAI, Ph.D.")
        self.assertEqual(names, ["SOMCHAI JAIDEE", "SOMSRI DEEJAI"])
        self.assertIn("Ph.D. and D.Sc.", degrees)

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

    def test_mark_never_attaches_to_a_space(self):
        """เล่มที่ 6 อ่าน "ทวีศักดิ์ สมานชื่น" ได้เป็น "ทวีศักดิ ์สมานชื่น"

        การันต์วางห่างจาก ด เล็กน้อยจนขอบขวาของช่องว่างที่ตามมาใกล้กว่า
        ถ้านับช่องว่างเป็นฐานได้ mark จะไปเกาะช่องว่างแล้วชื่อขาดกลาง
        """
        chars = [ch("ด", 10.0), {"text": " ", "x0": 16.0, "x1": 20.0, "top": 100.0},
                 {"text": "์", "x0": 18.0, "x1": 18.0, "top": 100.0},
                 ch("ส", 30.0)]
        self.assertEqual(_compose_thai_line(chars), "ด์ ส")

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
        # ช่องขวาเริ่มตรงกับเส้นประขวา (320) เหมือนเล่มจริง
        page = self._page([
            [("คณบดี", 60.0), ("ประธานหลักสูตร", 320.0)],
            [("บัณฑิตวิทยาลัย", 60.0), ("สาขาวิชาการพยาบาลผู้ใหญ่และ", 320.0)],
            [("ผู้สูงอายุ", 320.0)],
        ])
        _members, _quals, bottom, _raw = signature_committee_slots(page)
        self.assertIn(norm("การพยาบาลผู้ใหญ่และผู้สูงอายุ"), norm(bottom))

    def test_subject_in_right_cell_when_both_cells_are_long(self):
        """สองช่องยาวทั้งคู่ — เรียงตามลำดับอ่านล้วน ๆ จะสลับกัน ต้องยังหาเจอ"""
        # ช่องขวาเริ่มตรงกับเส้นประขวา (320) เหมือนเล่มจริง
        page = self._page([
            [("Dean", 60.0), ("Program", 320.0), ("Director", 380.0)],
            [("Faculty", 60.0), ("of", 110.0), ("Graduate", 140.0),
             ("Information", 320.0), ("Technology", 400.0)],
            [("Studies", 60.0), ("Management", 320.0)],
        ])
        _members, _quals, bottom, _raw = signature_committee_slots(page)
        self.assertIn(norm("Information Technology Management"), norm(bottom))

    def test_name_is_not_split_when_cell_starts_just_left_of_page_centre(self):
        """เส้นแบ่งคอลัมน์ต้องมาจากเส้นประ ไม่ใช่กึ่งกลางหน้า

        เล่มจริงพบช่องขวาเริ่มที่ x0=297.53 ขณะที่กึ่งกลางหน้าคือ 297.66
        ต่างกัน 0.13 pt ทำให้คำแรกของช่องขวาตกไปฝั่งซ้าย ชื่อกรรมการเลยขาดครึ่ง
        ("มยุรี หอมสนิท" เหลือ "หอมสนิท" ส่วน "มยุรี" ไปโผล่เป็นกรรมการอีกคน)
        """
        words, top = [], 100.0
        for i in range(5):                       # แถวกรรมการ 1..5
            words.append(word("……………", 43.0, top))
            words.append(word("……………", 297.53, top))
            top += 20.0
            if i == 0:                           # ชื่อเริ่มชิดเส้นประขวาพอดี
                words.append(word("มยุรี", 297.53, top))
                words.append(word("หอมสนิท", 360.0, top))
            top += 20.0
        words.append(word("……………", 43.0, top))
        words.append(word("……………", 297.53, top))
        page = _FakePage(595.32, words)          # กึ่งกลาง = 297.66 (ขวาของ 297.53)

        members, _quals, _bottom, _raw = signature_committee_slots(page)
        self.assertEqual(members.get(1), "มยุรี หอมสนิท")
        self.assertIsNone(members.get(9))        # ต้องไม่มีชื่อหลุดไปช่องซ้าย

    def test_empty_page_returns_blank(self):
        members, quals, bottom, raw = signature_committee_slots(_FakePage(595.0, []))
        self.assertEqual((members, quals, bottom, raw), ({}, {}, "", {}))


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


class _CharPage:
    """หน้า PDF ปลอมที่มี chars จริง (ใช้ทดสอบการซ่อมนิคหิตบนหน้าลงนาม)"""

    def __init__(self, chars, width=595.0):
        self.chars = chars
        self.width = width

    def extract_words(self, **_kwargs):          # ทางสำรองเมื่ออ่าน chars ไม่ได้
        return []


class SignatureNikhahitRepair(unittest.TestCase):
    """ฟอนต์ map นิคหิตของ ำ เป็น "ช่องว่างกว้างศูนย์" — เจอในเล่มที่ 9

    ถ้าอ่านหน้าลงนามด้วย extract_words ตรง ๆ ตัวนั้นถูกนับเป็นการเว้นวรรค
    "จำเนียร จวงตระกูล" จึงกลายเป็น "จ าเนียร จวงตระกูล" แล้วเทียบกับ บฑ. ไม่ตรง
    ระบบฟ้องแดงผิดสองข้อ: ไม่พบกรรมการคนนี้ + พบชื่อนอกรายชื่ออนุมัติ
    """

    def _chars(self, text, x0=100.0, top=100.0):
        out, x = [], x0
        for c in text:
            width = 0.0 if c == " " else 6.0     # ช่องว่างกว้างศูนย์ = นิคหิต
            out.append({"text": c, "x0": x, "x1": x + width, "top": top,
                        "doctop": top, "bottom": top + 10.0, "upright": True,
                        "size": 10.0, "non_stroking_color": (0, 0, 0),
                        "fontname": "TH"})
            x += width
        return out

    def test_zero_width_space_becomes_sara_am(self):
        words = _sig_words(_CharPage(self._chars("จ าเนียร")))
        self.assertEqual("".join(w["text"] for w in words), "จำเนียร")

    def test_real_space_still_separates_words(self):
        chars = self._chars("จ าเนียร", x0=100.0)
        chars += self._chars("จวงตระกูล", x0=180.0)      # เว้นระยะจริง
        texts = [w["text"] for w in _sig_words(_CharPage(chars))]
        self.assertEqual(texts, ["จำเนียร", "จวงตระกูล"])


class CommitteeNamesCheckedWithoutPosition(unittest.TestCase):
    """นโยบายเจ้าหน้าที่ (ก.ค. 2569): ดูแค่ "ชื่อ" ว่าครบและถูกคน ไม่ดูตำแหน่งช่อง

    "แต่ละเล่มทำมาไม่เหมือนกัน" — เล่มที่จัดตารางต่างจาก template จะอ่านไม่เข้าช่อง
    ถ้าเชื่อช่องอย่างเดียวระบบจะฟ้องผิดว่าไม่พบกรรมการ ทั้งที่ชื่อพิมพ์อยู่บนหน้าครบ
    """

    EXPECTED = [{"name": "จำเนียร จวงตระกูล"}, {"name": "ศิริพร แย้มนิล"}]

    def _reds(self, members, page_text=None):
        rep = Report()
        _report_thai_committee(rep, self.EXPECTED, members, "หน้ากรรมการสอบ",
                               page_text=page_text)
        return [i["found"] for i in rep.zones["RED"]]

    def test_names_read_into_the_wrong_cells_are_not_flagged(self):
        self.assertEqual(self._reds({1: "ศิริพร แย้มนิล", 5: "จำเนียร จวงตระกูล"}), [])

    def test_name_found_on_the_page_counts_even_if_no_cell_matched(self):
        page = ("ศาสตราจารย์พิศิษฐ์ จำเนียร จวงตระกูล\n"
                "รองศาสตราจารย์ ศิริพร แย้มนิล\n")
        self.assertEqual(self._reds({}, page_text=page), [])

    def test_genuinely_missing_name_is_still_red(self):
        page = "รองศาสตราจารย์ ศิริพร แย้มนิล\n"
        reds = self._reds({1: "ศิริพร แย้มนิล"}, page_text=page)
        self.assertEqual(len(reds), 1)
        self.assertIn("จำเนียร จวงตระกูล", reds[0])

    def test_stranger_is_still_red(self):
        page = "ศิริพร แย้มนิล\nจำเนียร จวงตระกูล\nสมชาย ใจดี\n"
        reds = self._reds({1: "ศิริพร แย้มนิล", 2: "จำเนียร จวงตระกูล",
                           3: "สมชาย ใจดี"}, page_text=page)
        self.assertEqual(len(reds), 1)
        self.assertIn("สมชาย ใจดี", reds[0])

    def test_half_a_name_read_into_a_cell_is_not_called_a_stranger(self):
        """ระบบแบ่งช่องคร่อมคำจนได้ "จวงตระกูล" ลอยมาช่องหนึ่ง — ไม่ใช่คนนอก"""
        page = "จำเนียร จวงตระกูล\nศิริพร แย้มนิล\n"
        self.assertEqual(self._reds({1: "ศิริพร แย้มนิล", 2: "จวงตระกูล"},
                                    page_text=page), [])


def _word(text, x0, x1, top=100.0, chars=None):
    """word dict แบบที่ extract_words คืนมา (พร้อม chars สำหรับซ่อม mark)"""
    w = {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 12}
    if chars is not None:
        w["chars"] = chars
    return w


def _char(text, x0, x1, top=100.0):
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 12}


class ThaiMarksDoNotDriftOnSignaturePage(unittest.TestCase):
    """เล่มทดสอบ 3 อ่านชื่อ "สุภาภรณ์ สงค์ประชา" ได้เป็น "สุภาภรณ ์ สงค์ประชา"

    การันต์เป็นอักขระกว้างศูนย์ extract_words จึงตัดออกเป็น "คำ" ของตัวเองกลางชื่อ
    เจ้าหน้าที่อ่านรายงานแล้วนึกว่าระบบอ่านชื่อผิดคน
    """

    def test_floating_mark_is_merged_back_into_the_name(self):
        words = [
            _word("สุภาภรณ", 10, 50, chars=[_char(c, 10 + i * 5, 15 + i * 5)
                                            for i, c in enumerate("สุภาภรณ")]),
            _word("์", 50, 50, chars=[_char("์", 50, 50)]),
            _word("สงค์ประชา", 56, 100,
                  chars=[_char(c, 56 + i * 5, 61 + i * 5)
                         for i, c in enumerate("สงค์ประชา")]),
        ]
        got = [w["text"] for w in _rejoin_thai_marks(words)]
        self.assertEqual(got, ["สุภาภรณ์", "สงค์ประชา"])

    def test_marks_inside_a_word_are_put_back_in_the_right_order(self):
        """เรียงตามพิกัด x เฉย ๆ ได้ "ท่ี" เพราะวรรณยุกต์วางเยื้องซ้ายกว่าสระ"""
        chars = [_char("ท", 10, 16), _char("่", 16, 16), _char("ิ", 16, 16),
                 _char("บ", 16, 22)]
        got = _rejoin_thai_marks([_word("ท่ิบ", 10, 22, chars=chars)])
        self.assertEqual(got[0]["text"], "ทิ่บ")

    def test_pages_without_chars_are_left_alone(self):
        words = [_word("ธเนศ เกษศิลป์", 10, 90)]
        self.assertEqual(_rejoin_thai_marks(words), words)


class NearlyIdenticalNameIsOrangeNotRed(unittest.TestCase):
    """ชื่อที่ต่างกันแค่ตัวสะกด/ระบบอ่านเพี้ยน = คนเดียวกัน ไม่ใช่ "ขาด + คนนอก"

    เดิมพลาดตัวอักษรเดียวได้แดงสองข้อ (ไม่พบคนนี้ + เจอคนแปลกหน้า) ทั้งที่เป็นคนเดียวกัน
    """

    EXPECTED = [{"name": "จำเนียร จวงตระกูล"}, {"name": "ศิริพร แย้มนิล"}]

    def _run(self, members, page_text=None):
        rep = Report()
        _report_thai_committee(rep, self.EXPECTED, members, "หน้ากรรมการสอบ",
                               page_text=page_text)
        return ([i["found"] for i in rep.zones["RED"]],
                [i["found"] for i in rep.zones["ORANGE"]])

    def test_one_letter_off_is_orange(self):
        reds, oranges = self._run({1: "จำเนียร จวงตระกูล", 2: "ศิริพร แย้มนิน"})
        self.assertEqual(reds, [])
        self.assertEqual(len(oranges), 1)
        self.assertIn("ใกล้เคียง", oranges[0])
        self.assertIn("ศิริพร แย้มนิล", oranges[0])

    def test_same_surname_with_a_garbled_first_name_is_orange(self):
        reds, oranges = self._run({1: "จำเนียร จวงตระกูล", 2: "ศริพ แย้มนิล"})
        self.assertEqual(reds, [])
        self.assertEqual(len(oranges), 1)

    def test_a_different_person_is_still_red(self):
        reds, oranges = self._run({1: "จำเนียร จวงตระกูล", 2: "สมชาย ใจดี"})
        self.assertEqual(len(reds), 2)
        self.assertTrue(any("ศิริพร แย้มนิล" in r for r in reds))
        self.assertTrue(any("สมชาย ใจดี" in r for r in reds))


class PageThatMatchesNobodyIsNotJudged(unittest.TestCase):
    """ไม่ตรงสักคน = แยกไม่ออกว่าระบบอ่านไม่ออก หรือเล่มใส่รายชื่อผิดชุด → ส้มข้อเดียว

    ถ้ารายชื่อในเล่มถูกจริง อย่างน้อยหนึ่งคนต้องแมตช์ การไม่แมตช์เลยจึงเป็นสัญญาณของ
    การอ่านพลาดพอ ๆ กับสัญญาณว่าเล่มผิด — ปรับเล่มที่ถูกให้ตกเสียหายกว่า
    """

    EXPECTED = [{"name": "จำเนียร จวงตระกูล"}, {"name": "ศิริพร แย้มนิล"}]

    def _run(self, members):
        rep = Report()
        _report_thai_committee(rep, self.EXPECTED, members, "หน้ากรรมการสอบ")
        return rep

    def test_unreadable_page_is_one_orange_not_a_pile_of_reds(self):
        rep = self._run({})
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(len(rep.zones["ORANGE"]), 1)
        item = rep.zones["ORANGE"][0]
        self.assertIn("อ่านรายชื่อกรรมการบนหน้านี้ไม่ได้", item["found"])
        self.assertIn("จำเนียร จวงตระกูล", item["expected"])
        # ระบบอ่านไม่ออก ไม่ใช่จุดที่นักศึกษาแก้ได้ด้วยการพิมพ์ใหม่
        self.assertTrue(item["system_note"])

    def test_totally_different_list_is_one_orange_naming_what_was_read(self):
        rep = self._run({1: "สมชาย ใจดี", 2: "สมหญิง รักไทย"})
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(len(rep.zones["ORANGE"]), 1)
        found = rep.zones["ORANGE"][0]["found"]
        self.assertIn("สมชาย ใจดี", found)
        self.assertIn("สมหญิง รักไทย", found)
        self.assertFalse(rep.zones["ORANGE"][0]["system_note"])

    def test_one_good_name_still_lets_the_rest_be_flagged(self):
        rep = self._run({1: "จำเนียร จวงตระกูล", 2: "สมชาย ใจดี"})
        self.assertEqual(len(rep.zones["RED"]), 2)


class StudentNameIgnoresTitles(unittest.TestCase):
    """นโยบายเจ้าหน้าที่ (ก.ค. 2569): "ชื่อนักศึกษา ให้ตรวจแบบไม่มีคำนำหน้า ถ้ามีให้เตือนส้ม"

    เล่มที่ 9: บฑ. เขียน "พ.จ.ต. ณัชนพ เพชรสุข" / "CPO 3 NUTCHANOP PETSUK"
    แต่เล่มพิมพ์แค่ชื่อ-สกุล เดิมฟ้องแดง 5 ตำแหน่งจากสาเหตุเดียวกันหมด
    """

    def test_strips_thai_rank_abbreviations(self):
        for raw, want in (
            ("พ.จ.ต. ณัชนพ เพชรสุข", "ณัชนพ เพชรสุข"),
            ("จ.ส.อ. มานะ อดทน", "มานะ อดทน"),
            ("พ.ต.ท. วิชัย ศรีสุข", "วิชัย ศรีสุข"),
            ("ร.ต.อ.หญิง สมหญิง ใจดี", "สมหญิง ใจดี"),
        ):
            self.assertEqual(_strip_student_title(raw), want, raw)

    def test_strips_thai_full_word_titles(self):
        for raw, want in (
            ("นายสมชาย ใจดี", "สมชาย ใจดี"),
            ("นางสาว สุดา ดีงาม", "สุดา ดีงาม"),
            ("ว่าที่ร้อยตรี ก้อง ทองดี", "ก้อง ทองดี"),
            ("จ่าสิบเอก มานะ อดทน", "มานะ อดทน"),
            ("พันเอกหญิง สมหญิง ใจดี", "สมหญิง ใจดี"),
            ("นายแพทย์ สมชาย ใจดี", "สมชาย ใจดี"),   # ต้องไม่ตัดแค่ "นาย" แล้วเหลือ "แพทย์"
        ):
            self.assertEqual(_strip_student_title(raw), want, raw)

    def test_strips_english_rank_with_class_number(self):
        self.assertEqual(_strip_student_title("CPO 3 NUTCHANOP PETSUK"),
                         "NUTCHANOP PETSUK")
        self.assertEqual(_strip_student_title("Lt. Col. Somchai Jaidee"),
                         "Somchai Jaidee")
        self.assertEqual(_strip_student_title("Miss Suda Deengam"), "Suda Deengam")

    def test_plain_names_are_untouched(self):
        for raw in ("ณัชนพ เพชรสุข", "NUTCHANOP PETSUK", "นภา ใจดี",
                    "MISSAKORN SOMCHAI"):     # ห้ามกิน "MISS" ที่เป็นส่วนของชื่อ
            self.assertEqual(_strip_student_title(raw), raw, raw)

    def _run(self, page_text, core, kind="cover"):
        rep = Report()
        _report_student_name_style(rep, page_text, core, "หน้าปก", "ชื่อนักศึกษา",
                                   kind, "FORM.APPROVED_MATCH")
        return rep

    def _oranges(self, page_text, core, kind="cover"):
        return [i["found"] for i in self._run(page_text, core, kind).zones["ORANGE"]]

    def test_prefix_in_the_book_is_orange_not_red(self):
        rep = self._run("นายสมชาย ใจดี\n", "สมชาย ใจดี")
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(len(rep.zones["ORANGE"]), 1)
        self.assertIn('"นาย"', rep.zones["ORANGE"][0]["found"])

    def test_rank_prefix_is_named_in_the_message(self):
        out = self._oranges("พ.จ.ต. ณัชนพ เพชรสุข\n", "ณัชนพ เพชรสุข")
        self.assertEqual(len(out), 1)
        self.assertIn('"พ.จ.ต."', out[0])
        self.assertEqual(self._oranges("CPO 3 NUTCHANOP PETSUK\n",
                                       "NUTCHANOP PETSUK")[0].count('"CPO 3"'), 1)

    def test_clean_name_reports_nothing(self):
        self.assertEqual(self._oranges("ณัชนพ เพชรสุข\n", "ณัชนพ เพชรสุข"), [])
        # บรรทัดที่มีรหัสนักศึกษาต่อท้าย (หน้าบทคัดย่อ) ก็ต้องไม่ฟ้อง
        self.assertEqual(
            self._oranges("ณัชนพ เพชรสุข 6538041 SHPP/D\n", "ณัชนพ เพชรสุข"), [])


class StudentNameLetterCaseByPage(unittest.TestCase):
    """นโยบายเจ้าหน้าที่ (ก.ค. 2569) — ตัวพิมพ์ของชื่อนักศึกษาต่างกันตามหน้า

    "ชื่อนักศึกษาภาษาอังกฤษในหน้าลงนาม ต้องเป็น Capital case และจะมีหรือไม่มี
     คำนำหน้านามก็ได้ ส่วนชื่อในหน้าปก และหน้าบทคัดย่อ ต้องเป็น UPPERCASE
     และไม่มีคำนำหน้านาม"
    """

    CORE = "NAMMONT PROMPIANPONG"

    def _run(self, page_text, kind, core=None):
        rep = Report()
        _report_student_name_style(rep, page_text, core or self.CORE, "ที่นี่",
                                   "ชื่อนักศึกษา", kind, "FORM.APPROVED_MATCH")
        return rep

    def _issues(self, page_text, kind, core=None):
        rep = self._run(page_text, kind, core)
        return [(z, i["found"]) for z in ("RED", "ORANGE") for i in rep.zones[z]]

    def test_cover_and_abstract_accept_uppercase_without_prefix(self):
        self.assertEqual(self._issues("NAMMONT PROMPIANPONG\n", "cover"), [])
        self.assertEqual(
            self._issues("NAMMONT PROMPIANPONG 6637951 EGRS/M\n", "abstract"), [])

    def test_cover_rejects_title_case(self):
        out = self._issues("Nammont Prompianpong\n", "cover")
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0][0], "RED")
        self.assertIn("UPPERCASE", out[0][1])

    def test_signature_accepts_capital_case_with_or_without_prefix(self):
        """เล่มที่ 15 พิมพ์ "Mr. Nammont Prompianpong" บนหน้าลงนาม — ต้องผ่าน"""
        self.assertEqual(
            self._issues("Mr. Nammont Prompianpong, Asst. Prof. X,\n", "signature"), [])
        self.assertEqual(self._issues("Nammont Prompianpong\n", "signature"), [])

    def test_signature_flags_all_caps_and_lowercase_as_orange(self):
        for text in ("NAMMONT PROMPIANPONG\n", "nammont prompianpong\n"):
            out = self._issues(text, "signature")
            self.assertEqual(len(out), 1, text)
            self.assertEqual(out[0][0], "ORANGE", text)
            self.assertIn("Capital Case", out[0][1])

    def test_cover_reports_case_and_prefix_in_one_issue(self):
        """แก้ที่บรรทัดเดียวกัน จึงต้องเป็นข้อเดียว ไม่ใช่สองข้อ"""
        out = self._issues("Mr. Nammont Prompianpong\n", "cover")
        self.assertEqual(len(out), 1)
        self.assertIn("UPPERCASE", out[0][1])
        self.assertIn('"Mr."', out[0][1])

    def test_hyphenated_surname_is_valid_capital_case(self):
        """เล่มจริงเขียนทั้ง "Pan-Ngum" และ "Pan-ngum" — เจ้าตัวเลือกเอง ต้องผ่านทั้งคู่"""
        for printed in ("Wirichada Pan-Ngum\n", "Wirichada Pan-ngum\n"):
            self.assertEqual(self._issues(printed, "signature",
                                          core="WIRICHADA PAN-NGUM"), [], printed)

    def test_thai_name_has_no_letter_case_to_check(self):
        self.assertEqual(self._issues("นํ้ามนต์ พรหมเพียรพงศ์\n", "cover",
                                      core="นํ้ามนต์ พรหมเพียรพงศ์"), [])


class MultiPageTableOfContents(unittest.TestCase):
    """สารบัญยาวหลายหน้า — คำว่า "สารบัญ" พิมพ์เฉพาะหน้าแรก

    เล่มที่ 4 มีสารบัญ 5 หน้า (ซ ฌ ญ ฎ ฏ) เดิมตัดไว้แค่ 4 หน้าตายตัว หน้าสุดท้าย
    จึงหลุด ซึ่งเป็นหน้าที่มี บรรณานุกรม / ภาคผนวก / ประวัติผู้วิจัย พอดี
    ระบบเลยฟ้องแดงผิด 4 ข้อว่า "ไม่พบหัวข้อ ... ในสารบัญ" ทั้งที่พิมพ์ไว้ครบ
    """

    def _toc_page(self, n):
        return "\n".join(f"หัวข้อที่ {i} {i * 10}" for i in range(1, n + 1))

    def test_five_page_toc_is_read_whole(self):
        pages = ["สารบัญ\n" + self._toc_page(5)] + [self._toc_page(5) for _ in range(4)]
        pages.append("ฐ\nสารบัญตาราง\n")
        self.assertEqual(_toc_continuation_pages(pages, 0, 5), [0, 1, 2, 3, 4])

    def test_stops_at_the_next_front_matter_section(self):
        pages = ["สารบัญ\n" + self._toc_page(5), self._toc_page(5),
                 "ฐ\nสารบัญตาราง\n1 หน้า 10"]
        self.assertEqual(_toc_continuation_pages(pages, 0, 2), [0, 1])

    def test_stops_when_the_body_starts(self):
        for head in ("บทที่ 1 บทนำ", "CHAPTER 1", "บทที่ 1"):
            pages = ["สารบัญ\n" + self._toc_page(5), self._toc_page(5),
                     f"1\n{head}\nเนื้อหา 1\nเนื้อหา 2\nเนื้อหา 3"]
            self.assertEqual(_toc_continuation_pages(pages, 0, len(pages)), [0, 1], head)

    def test_toc_page_may_start_with_a_chapter_entry(self):
        """หน้าสารบัญหน้าที่ 2 ขึ้นต้นด้วย "CHAPTER 4 RESULTS 23" ได้ตามปกติ

        เล่มที่ 1 เป็นแบบนี้ ถ้าเหมาว่าขึ้นบทแล้วจะตัดหน้าสารบัญทิ้ง
        แล้วฟ้องผิดว่าไม่พบ REFERENCES / APPENDIX / BIOGRAPHY ในสารบัญ
        """
        pages = ["v\nTABLE OF CONTENTS\nACKNOWLEDGEMENTS iii\nABSTRACT iv\nLIST OF TABLES vii",
                 "vi\nCHAPTER 4 RESULTS 23\nREFERENCES 48\nAPPENDIX 55\nBIOGRAPHY 83",
                 "vii\nLIST OF TABLES"]
        self.assertEqual(_toc_continuation_pages(pages, 0, 2), [0, 1])

    def test_stops_when_the_page_has_no_page_numbers(self):
        pages = ["สารบัญ\n" + self._toc_page(5), "ข้อความธรรมดาไม่มีเลขหน้าท้ายบรรทัด"]
        self.assertEqual(_toc_continuation_pages(pages, 0, len(pages)), [0])

    def test_single_page_toc(self):
        self.assertEqual(_toc_continuation_pages(["สารบัญ\n" + self._toc_page(3)], 0, 1),
                         [0])


class EthesisAndBookMustBeTheSameStudent(unittest.TestCase):
    """ด่านกัน "อัปโหลดไฟล์สลับคน" — เกิดขึ้นจริงหลายครั้งตอนใช้งาน

    ถ้าไม่มีด่านนี้ รายงานจะแดงยาวเป็นสิบข้อโดยไม่มีข้อไหนช่วยอะไร
    (เล่มที่ 4 คู่กับ eThesis คนอื่น: แดง 39 ข้อ)
    """

    APPROVED = {"student_id": "6538041 SHPP/D",
                "student_name": "NUTCHANOP PETSUK",
                "student_name_th": "พ.จ.ต. ณัชนพ เพชรสุข",
                "title_th": "การพัฒนาการบริหารแบบความร่วมมือการคุ้มครองพยาน"}

    def _match(self, *pages):
        return ethesis_matches_book(self.APPROVED, list(pages))[0]

    def test_matching_student_id_alone_is_enough(self):
        self.assertTrue(self._match("บางอย่าง 6538041 SHPP/D"))

    def test_matching_name_alone_is_enough(self):
        self.assertTrue(self._match("ณัชนพ เพชรสุข"))
        self.assertTrue(self._match("NUTCHANOP PETSUK"))

    def test_matching_title_alone_is_enough(self):
        """ชื่อเรื่องพิมพ์ผิดบางคำก็ยังนับว่าเป็นเล่มเดียวกัน"""
        self.assertTrue(self._match(
            "การพัฒนาการบริหารแบบความร่วมมือในการคุ้มครองพยานของหน่วยงาน"))

    def test_completely_different_student_is_caught(self):
        self.assertFalse(self._match(
            "CHING TO CHUNG 6637732 TMBI/M",
            "PREDICTING METASTASIS USING MACHINE LEARNING"))

    def test_no_approved_data_never_triggers(self):
        self.assertTrue(ethesis_matches_book({}, ["อะไรก็ได้"])[0])
        # กรอกมาอย่างเดียวก็ยังไม่พอจะสรุปว่าสลับไฟล์
        self.assertTrue(ethesis_matches_book(
            {"student_id": "6538041 SHPP/D"}, ["เล่มอื่น"])[0])

    def test_reports_which_signals_were_checked(self):
        _ok, checked, found = ethesis_matches_book(self.APPROVED, ["เล่มอื่นสิ้นเชิง"])
        self.assertEqual(checked, ["รหัสนักศึกษา", "ชื่อนักศึกษา", "ชื่อเรื่อง"])
        self.assertEqual(found, [])


class TotalPageCountIsOneIssueForAllAbstractPages(unittest.TestCase):
    """จำนวนหน้ารวมเป็นค่าเดียวของทั้งเล่ม แต่พิมพ์ทั้งบทคัดย่อไทยและอังกฤษ

    เดิมฟ้องหน้าละข้อ = ข้อความเดียวกันสองข้อ · ยุบเป็นข้อเดียวได้
    แต่ต้องบอกให้ครบว่าเป็นหน้าไหนบ้าง (คำสั่งเจ้าหน้าที่ ก.ค. 2569)
    """

    TH, EN = "บทคัดย่อ (หน้า ง)", "บทคัดย่อ (หน้า ฉ)"

    def test_same_number_on_both_pages_is_one_issue_naming_both(self):
        zone, where, found = _page_count_issue([(self.TH, 171), (self.EN, 171)], 154)
        self.assertEqual(zone, "RED")
        self.assertIn(self.TH, where)
        self.assertIn(self.EN, where)
        self.assertIn("171", found)
        self.assertIn("154", found)

    def test_different_numbers_say_which_page_states_what(self):
        _zone, _where, found = _page_count_issue([(self.TH, 171), (self.EN, 170)], 154)
        self.assertIn(f"{self.TH} ระบุ 171", found)
        self.assertIn(f"{self.EN} ระบุ 170", found)
        self.assertIn("154", found)

    def test_small_difference_stays_orange(self):
        zone, _where, _found = _page_count_issue([(self.TH, 155), (self.EN, 155)], 154)
        self.assertEqual(zone, "ORANGE")

    def test_any_large_difference_makes_it_red(self):
        zone, _where, _found = _page_count_issue([(self.TH, 155), (self.EN, 171)], 154)
        self.assertEqual(zone, "RED")

    def test_single_page_keeps_the_plain_message(self):
        _zone, where, found = _page_count_issue([(self.TH, 171)], 154)
        self.assertEqual(where, self.TH)
        self.assertNotIn("ไม่ตรงกัน", found)


class TocEntriesMayCarryAPageRange(unittest.TestCase):
    """หัวข้อที่กินสองหน้าเขียนเลขหน้าเป็นช่วงได้ เช่น "LIST OF TABLES xi-xii"

    เดิมตัดได้แค่เลขหน้าเดี่ยว ช่วงจึงติดมากับชื่อหัวข้อ ระบบจำแนกไม่ออก
    แล้วฟ้องผิดว่า "ไม่พบหัวข้อ LIST OF TABLES ในสารบัญ" ทั้งที่มีอยู่ (เล่มที่ 3)
    """

    def test_kind_is_recognised_with_a_range(self):
        for line, kind in (("LIST OF TABLES xi-xii", "list_tables"),
                           ("สารบัญตาราง ฎ-ฏ", "list_tables"),
                           ("LIST OF FIGURES xiii–xiv", "list_figures"),
                           ("APPENDIX 55-83", "appendix")):
            self.assertEqual(_toc_section_kind(line), kind, line)

    def test_heading_text_drops_the_whole_range(self):
        self.assertEqual(_strip_toc_page_number("LIST OF TABLES xi-xii"),
                         "LIST OF TABLES")
        self.assertEqual(_strip_toc_page_number("สารบัญตาราง ฎ-ฏ"), "สารบัญตาราง")

    def test_page_label_is_the_start_of_the_range(self):
        self.assertEqual(_toc_page_label("LIST OF TABLES xi-xii"), "xi")
        self.assertEqual(_toc_page_label("APPENDIX 55-83"), "55")
        self.assertEqual(_toc_page_label("สารบัญตาราง ฎ-ฏ"), "ฎ")

    def test_single_page_label_still_works(self):
        self.assertEqual(_toc_page_label("REFERENCES 48"), "48")
        self.assertEqual(_strip_toc_page_number("REFERENCES 48"), "REFERENCES")


class CommitteeNamesOnSignaturePageAreCapitalCase(unittest.TestCase):
    """ชื่อกรรมการบนหน้าลงนามใช้กติกาตัวพิมพ์เดียวกับชื่อนักศึกษาบนหน้าเดียวกัน

    "รายชื่ออาจารย์ในหน้าลงนามที่ตรวจก็ควรต้องเป็น Capital Case"
    """

    def _found(self, members):
        rep = Report()
        _report_committee_name_case(rep, members, "หน้าอาจารย์ที่ปรึกษา (หน้า i)")
        self.assertEqual(rep.zones["RED"], [])      # เป็นข้อสังเกต ไม่ตีตกเล่ม
        return [i["found"] for i in rep.zones["ORANGE"]]

    def test_capital_case_names_pass(self):
        self.assertEqual(self._found({1: "Naphat Ketphat",
                                      2: "Phumin Kirawanich"}), [])

    def test_all_caps_and_lowercase_are_flagged(self):
        out = self._found({1: "NAPHAT KETPHAT", 2: "Phumin Kirawanich"})
        self.assertEqual(len(out), 1)
        self.assertIn('"NAPHAT KETPHAT"', out[0])
        self.assertNotIn("Phumin", out[0])          # คนที่ถูกต้องต้องไม่ถูกพาดพิง
        self.assertTrue(self._found({1: "naphat ketphat"}))

    def test_every_offending_name_is_listed_in_one_issue(self):
        out = self._found({1: "NAPHAT KETPHAT", 2: "phumin kirawanich"})
        self.assertEqual(len(out), 1)               # ข้อเดียวต่อหน้า ไม่ฟ้องรายคน
        self.assertIn('"NAPHAT KETPHAT"', out[0])
        self.assertIn('"phumin kirawanich"', out[0])

    def test_thai_committee_names_are_skipped(self):
        self.assertEqual(self._found({1: "มยุรี หอมสนิท", 2: "ถิรจิต บุญแสน"}), [])


class ChapterTitleIssuesAreNotReportedTwice(unittest.TestCase):
    """ชื่อบทผิดจากประกาศ = ปัญหาเดียว ต้องอยู่ข้อเดียวและหมวดเดียว

    เดิมแยกเป็นข้อของสารบัญกับข้อของเนื้อหา และยังตกคนละหมวดอีก
    (ต่างเล็กน้อย -> "สะกดผิดเล็กน้อย (typo)" / ต่างมาก -> "ชื่อบทไม่ตรงประกาศ")
    เจ้าหน้าที่จึงเห็นเป็นสองเรื่องทั้งที่ต้องแก้ครั้งเดียว
    """

    def test_small_and_large_differences_share_one_category(self):
        small = {"found": 'ชื่อบทในเล่มเขียนว่า "X" — พิมพ์ผิดเล็กน้อย',
                 "expected": 'ตามประกาศ 2569 ควรเป็น "Y"', "location": "บทที่ 6"}
        large = {"found": 'ชื่อบทในเล่มเขียนว่า "X"',
                 "expected": 'ตามประกาศ 2569 ควรเป็น "Y"', "location": "บทที่ 2"}
        self.assertEqual(classify(small), "ชื่อบทไม่ตรงประกาศ")
        self.assertEqual(classify(large), "ชื่อบทไม่ตรงประกาศ")

    def test_toc_vs_body_mismatch_is_a_different_category(self):
        """สารบัญ↔เนื้อหาไม่ตรงกัน (ไม่ได้อ้างประกาศ) ยังเป็นคนละหมวดตามเดิม"""
        item = {"found": 'ชื่อบทในเนื้อหาในเล่มเขียนว่า "X"',
                "expected": 'ต้องสะกดตรงกับชื่อบทในสารบัญ: "Y"',
                "location": "บทที่ 3 (หน้า 45)"}
        self.assertEqual(classify(item), "สะกดผิด (typo)")

    def test_other_typos_still_land_in_the_typo_category(self):
        item = {"found": 'ชื่อปริญญาพิมพ์ผิดเล็กน้อย (typo, ความใกล้เคียง 0.95): "X"',
                "expected": "", "location": "หน้าปก"}
        self.assertNotEqual(classify(item), "ชื่อบทไม่ตรงประกาศ")


if __name__ == "__main__":
    unittest.main()
