import unittest

from ethesis_import import (
    _committee_member,
    _degree_abbr,
    _degree_abbr_th,
    _degree_name,
    _fix_thai_pua,
    _spaced_degree,
    _student_id,
    parse_committees,
)


class CommitteeParsingTests(unittest.TestCase):
    """ดึงชื่อ-สกุลกรรมการจากหน้า eThesis (ตัดคำนำหน้าวิชาการ + บทบาทท้ายบรรทัด)"""

    def test_committee_member_strips_title_and_role(self):
        m = _committee_member("1 รองศาสตราจารย์ดร. นริศรา จันทราทิตย์ อาจารย์ที่ปรึกษาหลัก")
        self.assertEqual(m["name"], "นริศรา จันทราทิตย์")
        self.assertEqual(m["role"], "อาจารย์ที่ปรึกษาหลัก")

    def test_committee_member_external_expert_suffix(self):
        m = _committee_member("1 ผู้ช่วยศาสตราจารย์ ดร. อัญชลี ฐานวิสัย ประธานสอบ (ผู้ทรงคุณวุฒิภายนอก)")
        self.assertEqual(m["name"], "อัญชลี ฐานวิสัย")
        self.assertIn("ประธานสอบ", m["role"])

    def test_committee_member_non_numbered_line_returns_none(self):
        self.assertIsNone(_committee_member("คณะกรรมการสอบวิทยานิพนธ์/สารนิพนธ์"))

    def test_thai_name_starting_with_title_letter_is_kept(self):
        # ตัวย่อ ศ. ต้องมีจุด ไม่งั้นจะกินอักษรตัวแรกของชื่อจริงที่ขึ้นต้นด้วย ศ
        self.assertEqual(_committee_member("1 ศศิธร วงศ์ไทย ประธานสอบ")["name"],
                         "ศศิธร วงศ์ไทย")
        self.assertEqual(_committee_member("2 ศิริพร ใจดี กรรมการสอบ")["name"],
                         "ศิริพร ใจดี")

    def test_abbreviated_title_with_dot_is_still_stripped(self):
        self.assertEqual(_committee_member("3 ผศ. ธเนศ เกษศิลป์ กรรมการสอบ")["name"],
                         "ธเนศ เกษศิลป์")
        self.assertEqual(_committee_member("4 รศ.ดร. สมชาย มานะ กรรมการสอบ")["name"],
                         "สมชาย มานะ")

    def test_numbered_line_that_is_not_a_person_is_rejected(self):
        # ชื่อคนไม่มีตัวเลข — กันบรรทัดวันที่ถูกนับเป็นกรรมการ
        self.assertIsNone(_committee_member("1 มกราคม 2569"))

    def test_wrapped_expert_suffix_is_joined(self):
        # PDF ตัดบรรทัดกลางวงเล็บ — ต้องต่อกลับ ไม่ใช่จบลิสต์ทิ้งคนที่เหลือ
        lines = [
            "คณะกรรมการสอบวิทยานิพนธ์/สารนิพนธ์",
            "1 ศาสตราจารย์ ดร. ประสาท กิตตะคุปต์ กรรมการสอบ (ผู้ทรงคุณ",
            "วุฒิภายนอก)",
            "2 อาจารย์ ดร. เตชิษฏ์ ถาวรศักดิ์ กรรมการสอบ",
        ]
        exam = parse_committees(lines)["exam"]
        self.assertEqual([m["name"] for m in exam],
                         ["ประสาท กิตตะคุปต์", "เตชิษฏ์ ถาวรศักดิ์"])

    def test_wrapped_name_line_is_joined(self):
        lines = [
            "คณะกรรมการที่ปรึกษาวิทยานิพนธ์/สารนิพนธ์",
            "1 รองศาสตราจารย์ ดร. คนางค์",
            "คันธมธุรพจน์ อาจารย์ที่ปรึกษาหลัก",
        ]
        advisory = parse_committees(lines)["advisory"]
        self.assertEqual([m["name"] for m in advisory], ["คนางค์ คันธมธุรพจน์"])

    def test_next_section_heading_is_not_treated_as_continuation(self):
        lines = [
            "คณะกรรมการที่ปรึกษาวิทยานิพนธ์/สารนิพนธ์",
            "1 รองศาสตราจารย์ ดร. คนางค์ คันธมธุรพจน์ อาจารย์ที่ปรึกษาหลัก",
            "กำหนดสอบวิทยานิพนธ์/สารนิพนธ์และคณะกรรมการสอบวิทยานิพนธ์/สารนิพนธ์",
        ]
        advisory = parse_committees(lines)["advisory"]
        self.assertEqual([m["name"] for m in advisory], ["คนางค์ คันธมธุรพจน์"])

    def test_parse_committees_two_lists_in_order(self):
        lines = [
            "คณะกรรมการที่ปรึกษาวิทยานิพนธ์/สารนิพนธ์",
            "1 รองศาสตราจารย์ ดร. คนางค์ คันธมธุรพจน์ อาจารย์ที่ปรึกษาหลัก",
            "2 ผู้ช่วยศาสตราจารย์ ดร. สุภาภรณ์ สงค์ประชา อาจารย์ที่ปรึกษาร่วม",
            "อื่น ๆ",
            "คณะกรรมการสอบวิทยานิพนธ์/สารนิพนธ์",
            "1 ผู้ช่วยศาสตราจารย์ ดร. สวรรยา ธรรมอภิพล ประธานสอบ",
            "2 รองศาสตราจารย์ ดร. คนางค์ คันธมธุรพจน์ กรรมการสอบ",
        ]
        result = parse_committees(lines)
        self.assertEqual([m["name"] for m in result["advisory"]],
                         ["คนางค์ คันธมธุรพจน์", "สุภาภรณ์ สงค์ประชา"])
        self.assertEqual([m["name"] for m in result["exam"]],
                         ["สวรรยา ธรรมอภิพล", "คนางค์ คันธมธุรพจน์"])


class StudentIdTests(unittest.TestCase):
    """รหัสนักศึกษาต้องเก็บทั้งเลข 7 หลักและรหัสหลักสูตร (ใช้เทียบในบทคัดย่อ)"""

    def test_digits_and_program_code_are_kept_together(self):
        self.assertEqual(_student_id("รหัสนักศึกษา 6838141 SHSS/M"), "6838141 SHSS/M")
        self.assertEqual(_student_id("6136017 TMTM/D"), "6136017 TMTM/D")

    def test_code_glued_to_digits_is_still_split_correctly(self):
        # บาง PDF ดึงมาติดกัน — เดิม \b\d{7}\b พังเพราะไม่มี word boundary
        self.assertEqual(_student_id("รหัสนักศึกษา6738050PHIE/M"), "6738050 PHIE/M")

    def test_falls_back_to_seven_digits_when_no_program_code(self):
        self.assertEqual(_student_id("รหัสนักศึกษา 6537730"), "6537730")

    def test_ignores_longer_number_runs(self):
        self.assertEqual(_student_id("เลขที่ 123456789012"), "")

    def test_returns_empty_when_nothing_found(self):
        self.assertEqual(_student_id(""), "")
        self.assertEqual(_student_id("ไม่มีรหัสในบรรทัดนี้"), "")


class DegreeConversionTests(unittest.TestCase):
    """ชื่อปริญญาแยกตามตำแหน่งตรวจ: ปก = ต้นฉบับ, ลงนาม = Sentence case, บทคัดย่อ = ตัวย่อ"""

    def test_signature_form_is_sentence_case_with_minor_words_lowered(self):
        self.assertEqual(
            _degree_name("MASTER OF SCIENCE (INFORMATION TECHNOLOGY MANAGEMENT)"),
            "Master of Science (Information Technology Management)")

    def test_english_abbreviation_keeps_field_uppercase(self):
        self.assertEqual(
            _degree_abbr("MASTER OF ARTS (ENVIRONMENTAL SOCIAL SCIENCES)"),
            "M.A. (ENVIRONMENTAL SOCIAL SCIENCES)")

    def test_unknown_english_degree_is_not_guessed(self):
        # เดาไม่ได้ต้องคืนค่าว่างให้เจ้าหน้าที่กรอกเอง ห้ามเดาแล้วเอาไปตัดสิน
        self.assertEqual(_degree_abbr("MASTER OF SOMETHING NEW (X)"), "")

    def test_thai_abbreviation_is_derived_from_thai_degree(self):
        self.assertEqual(
            _degree_abbr_th("ศิลปศาสตรมหาบัณฑิต(สังคมศาสตร์สิ่งแวดล้อม)"),
            "ศศ.ม. (สังคมศาสตร์สิ่งแวดล้อม)")
        self.assertEqual(
            _degree_abbr_th("ปรัชญาดุษฎีบัณฑิต(อายุรศาสตร์เขตร้อน)"),
            "ปร.ด. (อายุรศาสตร์เขตร้อน)")
        self.assertEqual(
            _degree_abbr_th("วิทยาศาสตรมหาบัณฑิต(การจัดการเทคโนโลยีสารสนเทศ)"),
            "วท.ม. (การจัดการเทคโนโลยีสารสนเทศ)")

    def test_unknown_thai_degree_is_not_guessed(self):
        self.assertEqual(_degree_abbr_th("สาขาที่ไม่มีในตารางมหาบัณฑิต(อะไรสักอย่าง)"), "")

    def test_degree_always_has_one_space_before_the_field(self):
        # eThesis พิมพ์ติดวงเล็บ — ค่าที่เติมในฟอร์มต้องเว้นวรรค 1 เคาะเสมอ
        self.assertEqual(
            _spaced_degree("MASTER OF SCIENCE(INFORMATION TECHNOLOGY MANAGEMENT)"),
            "MASTER OF SCIENCE (INFORMATION TECHNOLOGY MANAGEMENT)")
        self.assertEqual(
            _spaced_degree("ปรัชญาดุษฎีบัณฑิต(อายุรศาสตร์เขตร้อน)"),
            "ปรัชญาดุษฎีบัณฑิต (อายุรศาสตร์เขตร้อน)")

    def test_degree_spacing_collapses_extra_whitespace(self):
        self.assertEqual(
            _spaced_degree("  MASTER OF ARTS   (  ENVIRONMENTAL  ) "),
            "MASTER OF ARTS (ENVIRONMENTAL)")


class ThaiPuaTests(unittest.TestCase):
    """ฟอนต์ไทยใน eThesis PDF เก็บสระ/วรรณยุกต์ไว้ใน Private Use Area"""

    def test_thanthakhat_is_mapped_back(self):
        # U+F70E = การันต์
        self.assertEqual(_fix_thai_pua("วชิรนันท" + chr(0xF70E)), "วชิรนันท์")

    def test_raised_vowel_is_mapped_back(self):
        # U+F701 = สระอิ ตำแหน่งยกสูง — เดิมถูก strip ทิ้งทำให้ชื่อตกสระ
        self.assertEqual(_fix_thai_pua("ป" + chr(0xF701) + "ยอร"), "ปิยอร")

    def test_sara_am_is_recombined(self):
        self.assertEqual(_fix_thai_pua("ก" + chr(0x0E4D) + chr(0x0E32)), "กำ")


if __name__ == "__main__":
    unittest.main()
