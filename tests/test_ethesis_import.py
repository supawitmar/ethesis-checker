import unittest

from ethesis_import import (
    _committee_member,
    _degree_abbr,
    _degree_abbr_th,
    _degree_name,
    _exam_date,
    _find,
    _fix_thai_pua,
    _looks_like_a_date,
    _spaced_degree,
    _student_id,
    parse_committees,
)


class ThaiPuaGlyphs(unittest.TestCase):
    """PUA ที่ไม่ได้แมป จะถูก _PUA_LEFTOVER ลบทิ้งเงียบ ๆ ชื่อกรรมการจึงขาดสระ

    เจอจริงกับ eThesis ของ 6236350: "ปัญจมา" ถูกอ่านเป็น "ปญจมา" แล้วรายงานเอา
    ชื่อที่ผิดไปโชว์ให้เจ้าหน้าที่เทียบกับเล่ม
    """

    def test_raised_mai_han_akat_is_mapped(self):
        self.assertEqual(_fix_thai_pua("ปญจมา"), "ปัญจมา")

    def test_raised_mai_taikhu_is_mapped(self):
        self.assertEqual(_fix_thai_pua("เปน"), "เป็น")

    def test_known_pua_block_still_works(self):
        self.assertEqual(_fix_thai_pua("ทวีศักดิ"), "ทวีศักดิ์")


class EnglishDegreeAbbreviation(unittest.TestCase):
    """ตัวย่อปริญญาอังกฤษเดาจากตัวอักษรแรกไม่ได้ (Ph.D. ไม่ใช่ D.O.P.) ต้องเปิดตาราง

    ปริญญาที่ไม่มีในตารางทำให้ช่องฟอร์มว่าง แล้วเคยฟ้องแดงใส่เล่มที่ถูกต้อง
    """

    def test_doctor_of_nursing_science(self):
        self.assertEqual(_degree_abbr("DOCTOR OF NURSING SCIENCE"), "D.N.S.")

    def test_subject_in_brackets_is_kept(self):
        self.assertEqual(_degree_abbr("DOCTOR OF NURSING SCIENCE(NURSING)"),
                         "D.N.S. (NURSING)")

    def test_unknown_degree_is_left_blank_not_guessed(self):
        self.assertEqual(_degree_abbr("DOCTOR OF SOMETHING UNLISTED"), "")

    def test_master_of_clinical_tropical_medicine(self):
        """เจ้าหน้าที่พบในแบบฟอร์ม (ก.ย. 2569) ว่าย่อเป็น M.C.T.M. — เดาจากอักษรแรกได้ตรงก็จริง
        แต่ระบบไม่เดา จึงต้องอยู่ในตาราง ไม่งั้นช่องฟอร์มว่างให้เจ้าหน้าที่กรอกเองทุกครั้ง"""
        self.assertEqual(_degree_abbr("MASTER OF CLINICAL TROPICAL MEDICINE"), "M.C.T.M.")
        self.assertEqual(_degree_abbr("Master of Clinical Tropical Medicine"), "M.C.T.M.")


class TheAbbreviationTableLivesInOnePlace(unittest.TestCase):
    """ตารางตัวย่อปริญญามีที่เดียว (ethesis_import.py) — หน้าเว็บไม่มีตารางของตัวเองแล้ว

    เดิมทางวางข้อความในหน้าเว็บมีตารางซ้ำ และหลุดกันจริง (ขาด D.N.S. / D.P.A. / Dr. P.H. /
    M.P.A.) เจ้าหน้าที่สั่งถอดทางวางข้อความออก (ก.ย. 2569) เหลือทางแนบไฟล์ที่ใช้ตารางนี้
    """

    def test_the_web_form_has_no_copy_of_the_table(self):
        from pathlib import Path
        from ethesis_import import DEGREE_ABBR
        html = (Path(__file__).resolve().parents[1] / "templates" / "index.html"
                ).read_text(encoding="utf-8")
        for full_name in DEGREE_ABBR:
            self.assertNotIn(f"'{full_name}'", html, full_name)


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


class ExamDateMustLookLikeADate(unittest.TestCase):
    """ช่อง "วันที่สอบผ่าน" ที่ว่างในหน้า eThesis ต้องไม่ถูกเติมด้วยหัวข้อถัดไป

    เจอกับเล่มจริง (ก.ย. 2569): หน้า eThesis เว้นช่องนี้ว่าง ตัวอ่านจึงหยิบบรรทัดถัดไป
    ซึ่งเป็นหัวข้อ "การกำหนดรูปแบบรูปเล่ม" มาเป็นวันที่ ฟอร์มเติมค่านั้นให้อัตโนมัติ
    ถ้าเจ้าหน้าที่ไม่ทันแก้ รายงานจะฟ้องนักศึกษาว่า

        พบวันที่สอบผ่านไม่ตรงกันกับในระบบ: "07 July 2026"
        ต้องแก้เป็น "การกำหนดรูปแบบรูปเล่ม"

    ปล่อยว่างให้เจ้าหน้าที่กรอกเอง ดีกว่าเติมค่าที่ไม่ใช่วันที่
    """

    def test_real_dates_are_accepted(self):
        for value in ("25 June 2026", "5 May 2026", "11 พฤษภาคม 2569",
                      "07/07/2569", "7 July 2026", "1-2-2569"):
            self.assertTrue(_looks_like_a_date(value), value)

    def test_headings_and_blanks_are_rejected(self):
        for value in ("การกำหนดรูปแบบรูปเล่ม", "อาจารย์ที่ปรึกษา", "รูปแบบที่ 1",
                      "ผ่านแบบมีเงื่อนไข", "", None):
            self.assertFalse(_looks_like_a_date(value), value)

    def test_a_bare_number_is_not_a_year(self):
        """เลขลอย ๆ ที่ไม่ใช่ปี ต้องไม่ผ่าน ไม่งั้นหัวข้อที่มีเลขจะกลายเป็นวันที่"""
        for value in ("12345", "202", "20269"):
            self.assertFalse(_looks_like_a_date(value), value)


class TheExamDateFallsBackToTheExamField(unittest.TestCase):
    """ช่อง "วันที่สอบผ่าน" ว่าง ให้ถอยไปใช้ช่อง "วันที่สอบ" (เจ้าหน้าที่สั่ง ก.ย. 2569)

    หน้า eThesis ของเล่มจริงเว้นช่อง "วันที่สอบผ่าน" ไว้ แต่มี "วันที่สอบ" อยู่ในตาราง
    เดียวกัน เดิมเจ้าหน้าที่ต้องพิมพ์วันที่เองทุกครั้ง
    """

    LINES = ["วันที่สอบ", "July 7, 2026", "เวลาสอบ", "9:00 a.m.",
             "วันที่สอบผ่าน", "การกำหนดรูปแบบรูปเล่ม"]

    def test_the_two_labels_do_not_collide(self):
        """ป้ายหนึ่งเป็นคำขึ้นต้นของอีกป้าย ถ้าชนกันจะหยิบค่าผิดช่อง"""
        self.assertEqual(_find(self.LINES, "วันที่สอบผ่าน")[0], "การกำหนดรูปแบบรูปเล่ม")
        self.assertEqual(_find(self.LINES, "วันที่สอบ")[0], "July 7, 2026")

    def test_the_fallback_is_wired_into_the_parser(self):
        import inspect
        import ethesis_import
        source = inspect.getsource(ethesis_import.parse_ethesis_pdf)
        self.assertIn("_find(lines, 'วันที่สอบ')[0]", source)
        self.assertIn("exam_value = ''", source)


class TheExamDateIsWrittenDayFirst(unittest.TestCase):
    """ช่อง "วันที่สอบ" เขียนแบบอเมริกัน แต่เล่มพิมพ์วันขึ้นก่อน

    ถ้าไม่เรียงใหม่ ระบบจะฟ้องว่าวันที่ไม่ตรงทั้งที่เป็นวันเดียวกัน
        พบวันที่สอบผ่านไม่ตรงกันกับในระบบ: "07 July 2026"
        ที่ถูกต้องตามระบบคือ "July 7, 2026"
    """

    def test_month_first_is_reordered(self):
        self.assertEqual(_exam_date("July 7, 2026", True), "7 July 2026")
        self.assertEqual(_exam_date("July 07, 2026", True), "7 July 2026")
        self.assertEqual(_exam_date("May 5 2026", True), "5 May 2026")

    def test_day_first_is_left_alone(self):
        self.assertEqual(_exam_date("25 June 2026", True), "25 June 2026")
        self.assertEqual(_exam_date("5 May 2026", True), "5 May 2026")

    def test_a_thai_date_is_untouched(self):
        self.assertEqual(_exam_date("11 พฤษภาคม 2569", False), "11 พฤษภาคม 2569")

    def test_something_that_is_not_a_date_is_left_as_is(self):
        """ฟังก์ชันนี้ไม่ใช่ตัวกรอง หน้าที่กรองเป็นของ _looks_like_a_date"""
        self.assertEqual(_exam_date("การกำหนดรูปแบบรูปเล่ม", True),
                         "การกำหนดรูปแบบรูปเล่ม")


class Update116MisProfileText(unittest.TestCase):
    """ข้อความที่คัดลอกจากหน้าประวัตินักศึกษา (MIS) — ข้อมูลสมมติทั้งหมด ตามโครงหน้าจริง (อัปเดต 1.16 เจ้าของ 26 พ.ย. 2569)"""

    PAGE = "\n".join([
        "ประวัตินักศึกษา",
        "หน้าหลัก",
        "|||| Student Profile",
        "รหัสนักศึกษา : 6512345 GRXX/M",
        "Menu",
        "ชื่อ - นามสกุล : นางสาวสมหญิง ทดสอบดี",
        "ชื่อภาษาอังกฤษ : Miss SOMYING THODSOBDEE",
        "ระดับปริญญา : ปริญญาโท",
        "Menu ประเภท : ภาคพิเศษ",
        "แผนการศึกษา : วิทยานิพนธ์",
        "รูปแบบการศึกษา : ในเวลาราชการ",
        "Grade report : -",
        "รหัสหลักสูตร : 1234M01",
        "หลักสูตร(TH) : ศิลปศาสตรมหาบัณฑิต สาขาวิชาการจัดการสมมติ",
        "หลักสูตร (EN) : Master of Arts Program in Made-up Management",
        "Soft Skills ปริญญาที่ได้รับ(TH) : ศิลปศาสตรมหาบัณฑิต (การจัดการสมมติ)",
        "ปริญญาที่ได้รับ(EN) : MASTER OF ARTS(MADE-UP MANAGEMENT)",
        "AUN-QA วันที่สอบโครงร่าง : 1 มิถุนายน 2569",
        "วันที่สอบวิทยานิพนธ์ : 3 พฤศจิกายน 2569",
        "วันที่สอบวิทยานิพนธ์ผ่าน : 7 พฤศจิกายน 2569",
        "ชื่อวิทยานิพนธ์ (อังกฤษ) : A MADE-UP STUDY OF SOMETHING",
        "VERY LONG",
        "ชื่อวิทยานิพนธ์ (ไทย) : การศึกษาสมมติของบางสิ่ง",
        "ที่ยาวมาก",
        "อาจารย์ที่ปรึกษาหลัก : รองศาสตราจารย์ ดร. สมชาย ใจดี",
        "อาจารย์ที่ปรึกษาร่วม : ผศ.ดร. สมศรี มีสุข",
        "ส่งรูปเล่มออนไลน์ : -",
    ])

    def _parse(self, text):
        from ethesis_import import parse_mis_profile
        return parse_mis_profile(text)

    def test_every_field_it_can_fill(self):
        data = self._parse(self.PAGE)
        self.assertEqual(data["student_id"], "6512345 GRXX/M")
        self.assertEqual(data["student_name"], "SOMYING THODSOBDEE")
        self.assertEqual(data["student_name_th"], "สมหญิง ทดสอบดี")
        self.assertEqual(data["title_en"], "A MADE-UP STUDY OF SOMETHING VERY LONG")
        self.assertEqual(data["title_th"], "การศึกษาสมมติของบางสิ่งที่ยาวมาก")       # ไทยต่อชิด
        self.assertEqual(data["program"], "ศิลปศาสตรมหาบัณฑิต สาขาวิชาการจัดการสมมติ")
        self.assertEqual(data["degree_cover_en"], "MASTER OF ARTS (MADE-UP MANAGEMENT)")    # เติมวรรคก่อนวงเล็บ
        self.assertEqual(data["degree_sig_en"], "Master of Arts (Made-Up Management)")
        self.assertEqual(data["degree_abbr_en"], "M.A. (MADE-UP MANAGEMENT)")
        self.assertEqual(data["degree_cover_th"], "ศิลปศาสตรมหาบัณฑิต (การจัดการสมมติ)")
        self.assertEqual(data["degree_abbr_th"], "ศศ.ม. (การจัดการสมมติ)")
        # วันที่สอบผ่าน ไม่ใช่วันที่สอบ / วันที่สอบโครงร่าง · ไม่รู้ภาษาหลักสูตร = คงรูปไทยตามหน้าเว็บ
        self.assertEqual((data["exam_date"], data["year"]), ("7 พฤศจิกายน 2569", "2569"))
        self.assertEqual(data["committees"], {"advisory": [
            {"name": "สมชาย ใจดี", "role": "อาจารย์ที่ปรึกษาหลัก"},
            {"name": "สมศรี มีสุข", "role": "อาจารย์ที่ปรึกษาร่วม"}], "exam": []})

    def test_what_it_never_fills(self):
        """ไม่เดาประเภทเล่มจาก "แผนการศึกษา" · ไม่มีรูปแบบเล่ม คณะ กรรมการสอบ · ไม่ใช่นานาชาติ = ไม่เติมภาษาหลักสูตร"""
        data = self._parse(self.PAGE)
        for key in ("doc_type", "format", "faculty", "program_language"):
            self.assertNotIn(key, data, key)

    def test_an_international_programme(self):
        page = self.PAGE.replace("Made-up Management", "Made-up Management (International Program)")
        data = self._parse(page)
        self.assertEqual(data["program_language"], "international")
        self.assertEqual((data["exam_date"], data["year"]), ("7 November 2026", "2026"))
        page = self.PAGE.replace("สาขาวิชาการจัดการสมมติ", "สาขาวิชาการจัดการสมมติ (หลักสูตรนานาชาติ)")
        self.assertEqual(self._parse(page)["program_language"], "international")

    def test_labels_and_values_on_separate_lines_with_tabs(self):
        page = "\n".join([
            "รหัสนักศึกษา", "6512345 GRXX/M",
            "ชื่อ - นามสกุล\tนายสมมติ ตัวอย่าง",
            "ชื่อภาษาอังกฤษ:Mr. SOMMOT TUAYANG",
            "ปริญญาที่ได้รับ(EN)", "\tMASTER OF SCIENCE(MADE-UP SCIENCE)",
            "ชื่อวิทยานิพนธ์ (อังกฤษ)", "A SHORT MADE-UP TITLE",
            "อาจารย์ที่ปรึกษาหลัก :", "อาจารย์ สมชาย ใจดี",
            "อาจารย์ที่ปรึกษาร่วม : -",
        ])
        data = self._parse(page)
        self.assertEqual(data["student_id"], "6512345 GRXX/M")
        self.assertEqual(data["student_name_th"], "สมมติ ตัวอย่าง")
        self.assertEqual(data["student_name"], "SOMMOT TUAYANG")
        self.assertEqual(data["degree_abbr_en"], "M.Sc. (MADE-UP SCIENCE)")
        self.assertEqual(data["title_en"], "A SHORT MADE-UP TITLE")
        # ไม่มีที่ปรึกษาร่วม ("-") = ที่ปรึกษาหลักคนเดียว
        self.assertEqual(data["committees"]["advisory"], [{"name": "สมชาย ใจดี", "role": "อาจารย์ที่ปรึกษาหลัก"}])

    def test_several_co_advisors(self):
        """ที่ปรึกษาร่วมหลายคน: คั่นจุลภาค · ขึ้นบรรทัดใหม่ (ขึ้นต้นด้วยตำแหน่งวิชาการ) · ป้ายซ้ำ"""
        base = self.PAGE.split("อาจารย์ที่ปรึกษาร่วม")[0]
        for tail in ("อาจารย์ที่ปรึกษาร่วม : ผศ.ดร. สมศรี มีสุข, อาจารย์ สมปอง รักดี",
                     "อาจารย์ที่ปรึกษาร่วม : ผศ.ดร. สมศรี มีสุข\nอาจารย์ สมปอง รักดี",
                     "อาจารย์ที่ปรึกษาร่วม : ผศ.ดร. สมศรี มีสุข\nอาจารย์ที่ปรึกษาร่วม : อาจารย์ สมปอง รักดี"):
            data = self._parse(base + tail + "\nส่งรูปเล่มออนไลน์ : -")
            self.assertEqual([m["name"] for m in data["committees"]["advisory"]],
                             ["สมชาย ใจดี", "สมศรี มีสุข", "สมปอง รักดี"], tail)
        # บรรทัดต่อที่ไม่ขึ้นต้นด้วยตำแหน่งวิชาการ (เมนู ข้อความอื่น) ไม่นับเป็นอาจารย์
        data = self._parse(base + "อาจารย์ที่ปรึกษาร่วม : ผศ.ดร. สมศรี มีสุข\nติดต่อเรา\nเว็บบอร์ด")
        self.assertEqual(len(data["committees"]["advisory"]), 2)

    def test_a_foreign_student_without_a_thai_name(self):
        page = self.PAGE.replace("ชื่อ - นามสกุล : นางสาวสมหญิง ทดสอบดี", "ชื่อ - นามสกุล : -")
        self.assertEqual(self._parse(page)["student_name_th"], "SOMYING THODSOBDEE")

    def test_an_exam_date_falls_back_to_the_exam_day(self):
        page = self.PAGE.replace("วันที่สอบวิทยานิพนธ์ผ่าน : 7 พฤศจิกายน 2569", "วันที่สอบวิทยานิพนธ์ผ่าน : -")
        self.assertEqual(self._parse(page)["exam_date"], "3 พฤศจิกายน 2569")
        page = page.replace("วันที่สอบวิทยานิพนธ์ : 3 พฤศจิกายน 2569", "วันที่สอบวิทยานิพนธ์ :")
        self.assertNotIn("exam_date", self._parse(page))

    def test_an_unknown_degree_has_no_abbreviation(self):
        page = self.PAGE.replace("MASTER OF ARTS(MADE-UP MANAGEMENT)", "MASTER OF MADE-UP ARTS(SOMETHING)")
        data = self._parse(page)
        self.assertEqual(data["degree_cover_en"], "MASTER OF MADE-UP ARTS (SOMETHING)")
        self.assertNotIn("degree_abbr_en", data)

    def test_text_that_is_not_the_profile_page(self):
        for text in ("", "hello world", "รหัสนักศึกษา : 6512345 GRXX/M",            # ป้ายเดียว ไม่พอ
                     "ข้อความทั่วไป\nชื่อวิทยานิพนธ์ (อังกฤษ) : A TITLE\nหลักสูตร(TH) : ก"):   # ไม่มีรหัสนักศึกษา
            self.assertEqual(self._parse(text), {}, text)

    def test_missing_labels_are_skipped(self):
        page = "\n".join(line for line in self.PAGE.splitlines()
                         if not line.startswith(("ปริญญาที่ได้รับ", "Soft Skills", "อาจารย์")))
        data = self._parse(page)
        self.assertEqual(data["student_id"], "6512345 GRXX/M")
        for key in ("degree_cover_en", "degree_cover_th", "committees"):
            self.assertNotIn(key, data)

    def test_both_date_forms_come_back(self):
        """หน้าแบบฟอร์มเลือกรูปตามภาษาหลักสูตร — เซิร์ฟเวอร์ส่งทั้งสองรูป (หัวหน้าทีม QA 1.16)"""
        variants = self._parse(self.PAGE)["exam_date_variants"]
        self.assertEqual(variants, {"thai": {"exam_date": "7 พฤศจิกายน 2569", "year": "2569"},
                                    "english": {"exam_date": "7 November 2026", "year": "2026"}})
        # หน้าเว็บพิมพ์วันที่เป็นอังกฤษ ก็ได้รูปไทย พ.ศ. ด้วย
        page = self.PAGE.replace("วันที่สอบวิทยานิพนธ์ผ่าน : 7 พฤศจิกายน 2569", "วันที่สอบวิทยานิพนธ์ผ่าน : November 7, 2026")
        data = self._parse(page)
        self.assertEqual(data["exam_date_variants"]["thai"], {"exam_date": "7 พฤศจิกายน 2569", "year": "2569"})
        self.assertEqual((data["exam_date"], data["year"]), ("7 พฤศจิกายน 2569", "2569"))   # ยังไม่รู้ภาษา = รูปไทย
        # ไม่มีวันที่ = ไม่มีสองรูป
        page = self.PAGE.replace(": 7 พฤศจิกายน 2569", ": -").replace(": 3 พฤศจิกายน 2569", ": -")
        self.assertNotIn("exam_date_variants", self._parse(page))

    def test_the_ethesis_file_keeps_one_date(self):
        """ควบคุมเชิงลบ — ไฟล์ eThesis รู้ภาษาจากไฟล์อยู่แล้ว ไม่มีสองรูป พฤติกรรมเดิม"""
        import inspect
        import ethesis_import
        self.assertNotIn("exam_date_variants", inspect.getsource(ethesis_import.parse_ethesis_pdf))

    def test_the_degree_fields_are_shared_with_the_ethesis_file(self):
        import inspect
        import ethesis_import
        self.assertIn("data.update(_degree_fields(english, thai))", inspect.getsource(ethesis_import.parse_ethesis_pdf))
        self.assertIn("_degree_fields(value('degree_en'), value('degree_th'))",
                      inspect.getsource(ethesis_import.parse_mis_profile))


if __name__ == "__main__":
    unittest.main()
