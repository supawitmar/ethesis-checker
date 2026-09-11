import inspect
import re
import sys

import unittest
from pathlib import Path
from unittest import mock

import checker as checker_module
import ethesis_import

NEWLINE = chr(10)

from checker import (
    describe_diff,
    _closest_student_id,
    bold_is_undetectable,
    abstract_committee_missing_degree_commas,
    _drop_separator_dot,
    _graphemes,
    _correctly_spelled_side,
    printed_title,
    _check_student_line_pairs_name_with_id,
    student_id_line,
    _looks_like_degree_line,
    NOT_CHECKED,
    N_APPENDIX,
    Report,
    issue_sort_key,
    summary_section,
    _report_abstract_title_format,
    _report_missing_abstract_language,
    toc_page_mismatch_is_appendix_alt,
    _extract_page_label,
    _is_abstract_heading,
    _is_toc_major_heading,
    _is_blank_page_text,
    _toc_page_label,
    _toc_section_kind,
    _toc_chapter_title,
    _strip_toc_page_number,
    canonical_title_status,
    closest_degree_line,
    closest_text_line,
    find_signature_date,
    header_extra_text,
    reference_terms,
    signature_committee_slots,
    _committee_page_kind,
    signature_page_kind,
    _is_white_fill,
    _report_sig_placeholders,
    _sig_clean_name,
    _strip_committee_title,
    sig_visible_placeholders,
    _check_abstract_committees,
    _check_cover_year,
    _check_exam_date,
    _check_front_page_numbers,
    _check_signature_institution,
    _expected_front_label_style,
    _page_label_order,
    abstract_committee_block,
    split_abstract_committee,
    _degree_subject,
    compare_reference_text,
    mismatch_detail,
    title_mismatch_detail,
    compare_canonical_title,
    compare_values,
    cover_required_items,
    exact_reference_status,
    norm,
    plain_summary,
    person_name_sentence_case,
    resolve_option,
    strip_name_prefix,
)
from ethesis_rules import (
    BODY_RULES,
    CANONICAL_ENFORCED_COUNT,
    CANONICAL_OPTION_1,
    CANONICAL_OPTION_2,
    FORM_FIELD_LABELS,
    FRONT_MATTER_RULES,
    MATCH_RULES,
    RULE_CATALOG,
    SIGNATURE_TEMPLATE_EN,
    SIGNATURE_TEMPLATE_TH,
    SOURCE_PRECEDENCE,
    rule_zone,
)


class NormalizationTests(unittest.TestCase):
    def test_thai_combining_mark_reordering_is_normalized(self):
        self.assertEqual(norm("บทคัดย่อ"), norm("บทคดัยอ่"))


class ExactReferenceTests(unittest.TestCase):
    def test_approved_english_title_is_case_sensitive(self):
        self.assertEqual(
            exact_reference_status(
                "BLAST PROTECTION PERFORMANCE OF UHPC BUNKERS",
                "Blast Protection Performance of UHPC Bunkers",
            ),
            (False, "case"),
        )

    def test_student_honorific_is_not_part_of_name(self):
        self.assertEqual(strip_name_prefix("Mr. WISIT KAWAYAPANIK"), "WISIT KAWAYAPANIK")

    def test_author_name_is_converted_for_acknowledgements(self):
        self.assertEqual(
            person_name_sentence_case("Mr. WISIT KAWAYAPANIK"),
            "Wisit Kawayapanik",
        )

    def test_cover_required_text_depends_on_type_and_language(self):
        english = dict(cover_required_items("INDEPENDENT STUDY", "international"))
        thai = dict(cover_required_items("THESIS", "thai"))
        self.assertTrue(english["ข้อความประเภทงาน"].startswith("AN INDEPENDENT STUDY"))
        self.assertIn("ลิขสิทธิ์", thai["ข้อความลิขสิทธิ์"])

    def test_toc_major_headings_are_recognized(self):
        self.assertTrue(_is_toc_major_heading("CHAPTER 6 CONCLUSION AND RECOMMENDATIONS 41"))
        self.assertTrue(_is_toc_major_heading("REFERENCE 44"))
        self.assertTrue(_is_toc_major_heading("BIOGRAPHY 48"))
        self.assertFalse(_is_toc_major_heading("6.1 Conclusion 41"))

    def test_small_spelling_error_is_reported_as_typo(self):
        compared = compare_values("LIST OF FIGTURES", "LIST OF FIGURES", "toc_heading")
        self.assertEqual(compared["status"], "typo")
        self.assertGreaterEqual(compared["score"], MATCH_RULES["toc_heading"]["typo_threshold"])

    def test_body_scope_is_limited_to_structure_and_page_numbers(self):
        self.assertTrue(BODY_RULES["check_page_sequence"])
        self.assertTrue(BODY_RULES["check_toc_page_numbers"])
        self.assertTrue(BODY_RULES["check_body_chapter_count"])
        self.assertTrue(BODY_RULES["check_toc_title_against_body"])
        self.assertTrue(BODY_RULES["check_body_title_against_canonical"])

    def test_toc_title_comparison_ignores_chapter_and_page_numbers(self):
        self.assertEqual(
            _toc_chapter_title("CHAPTER 6 CONCLUSION AND RECOMMENDATIONS 41"),
            "CONCLUSION AND RECOMMENDATIONS",
        )

    def test_dot_leaders_are_stripped_from_toc_entries(self):
        """จุดไข่ปลา (dot leader) ที่ลากไปเลขหน้า ต้องไม่ถูกนับเป็นตัวสะกด

        rule toc_heading เป็น case_sensitive จึงข้ามการเทียบแบบ norm() — ถ้าไม่ตัด
        จุดออกก่อน compare_values จะมองว่าหัวข้อทุกบรรทัดสะกดผิด (regression จริง
        จากเล่มที่หัวข้อสารบัญตามด้วยจุดยาว)
        """
        # จุด '.' ยาวปกติ + เลขหน้าโรมัน/อารบิก
        self.assertEqual(
            _strip_toc_page_number("LIST OF TABLES " + "." * 60 + " viii"),
            "LIST OF TABLES",
        )
        self.assertEqual(
            _toc_chapter_title("CHAPTER 1 INTRODUCTION " + "." * 40 + " 1"),
            "INTRODUCTION",
        )
        # ellipsis ยูนิโค้ด (…) ผสมจุดเดี่ยว อย่างที่ pdfplumber ดึงบรรทัด ABSTRACT
        self.assertEqual(
            _strip_toc_page_number("ABSTRACT " + "…" * 20 + " . iv"),
            "ABSTRACT",
        )
        # หัวข้อที่ไม่มีจุด/เลขหน้า ต้องไม่ถูกแตะ
        self.assertEqual(_strip_toc_page_number("REFERENCES"), "REFERENCES")
        # เมื่อมีจุดคั่น ต้องได้ exact ไม่ใช่ typo
        stripped = _strip_toc_page_number("RESEARCH METHODOLOGY" + "." * 30 + " 17")
        self.assertEqual(compare_values(stripped, "RESEARCH METHODOLOGY",
                                        "toc_heading")["status"], "exact")

    def test_toc_major_sections_and_printed_labels_are_classified(self):
        self.assertEqual(_toc_section_kind("ACKNOWLEDGEMENTS iii"), "ack")
        self.assertEqual(_toc_section_kind("ABSTRACT (THAI) v"), "abstract_th")
        self.assertEqual(_toc_section_kind("REFERENCES 45"), "references")
        self.assertEqual(_toc_page_label("ACKNOWLEDGEMENTS iii"), "iii")
        self.assertEqual(_toc_page_label("กิตติกรรมประกาศ ค"), "ค")

    def test_blank_page_may_contain_only_its_page_number(self):
        self.assertTrue(_is_blank_page_text("12\n"))
        self.assertTrue(_is_blank_page_text("iv\n"))
        self.assertFalse(_is_blank_page_text("12\nCHAPTER 2"))

    def test_printed_page_label_is_read_from_document_edge(self):
        self.assertEqual(_extract_page_label("iv\nABSTRACT"), "iv")
        self.assertEqual(_extract_page_label("CHAPTER 4\n41"), "41")
        self.assertEqual(_extract_page_label("ข\nข้อความ"), "ข")
        self.assertEqual(_extract_page_label("CHAPTER 4\nMETHODS"), "")

    def test_front_matter_is_strict_and_missing_values_are_red(self):
        self.assertTrue(FRONT_MATTER_RULES["strict"])
        self.assertEqual(FRONT_MATTER_RULES["failure_zone"], "RED")
        international = FRONT_MATTER_RULES["required_form_fields"]["international"]
        self.assertIn("degree_cover_en", international)
        self.assertIn("degree_sig_en", international)
        self.assertIn("degree_abbr_en", international)
        self.assertIn("student_name_th", FRONT_MATTER_RULES["required_form_fields"]["thai"])
        # เล่มไทยล้วนใช้ชุดภาษาไทยตรวจปก/หน้าลงนาม
        thai = FRONT_MATTER_RULES["required_form_fields"]["thai"]
        self.assertIn("degree_cover_th", thai)
        self.assertIn("degree_sig_th", thai)
        self.assertIn("ใช้ตรวจหน้าปก", FORM_FIELD_LABELS["degree_cover_en"])
        self.assertIn("หน้าลงนาม", FORM_FIELD_LABELS["degree_sig_en"])

    def test_official_announcement_has_highest_source_precedence(self):
        self.assertEqual(SOURCE_PRECEDENCE[0], "announcement_2569")
        self.assertIn("BODY.OPTION1", RULE_CATALOG)
        self.assertIn("ประกาศฯ", RULE_CATALOG["BODY.OPTION1"]["references"][0])
        self.assertIn("FRONT.COVER_REQUIRED", RULE_CATALOG)
        self.assertIn("FRONT.ACK_AUTHOR", RULE_CATALOG)
        self.assertIn("FRONT.ORDER", RULE_CATALOG)
        self.assertIn("FRONT.TOC_CONTENT", RULE_CATALOG)

    def test_bold_format_issue_is_orange(self):
        self.assertEqual(rule_zone("FORMAT.BOLD"), "ORANGE")

    def test_abstract_bold_and_verified_blank_page_are_yellow(self):
        self.assertEqual(rule_zone("FORMAT.ABSTRACT_BOLD"), "YELLOW")
        self.assertEqual(rule_zone("PAGE.BLANK"), "YELLOW")
        self.assertEqual(rule_zone("UNCERTAIN.REVIEW"), "ORANGE")


class ReportTests(unittest.TestCase):
    def test_verification_entries_are_grouped_by_topic(self):
        report = Report()
        report.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", "หน้าปก", "pass")
        report.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", "หน้าลงนาม 1 (หน้า i)", "fail", "TITLE X")
        report.add_verification("รหัสนักศึกษา", "บทคัดย่อ", "pending", "หาหน้าไม่เจอ")
        self.assertEqual(len(report.verification), 2)
        title_group = report.verification[0]
        self.assertEqual(title_group["topic"], "ชื่อเรื่อง (ตาม บฑ.1)")
        self.assertEqual([c["status"] for c in title_group["checks"]], ["pass", "fail"])
        self.assertEqual(report.verification[1]["checks"][0]["status"], "pending")

    def test_red_takes_precedence(self):
        report = Report()
        report.add("ORANGE", "-", "x", "x", "x")
        report.add("RED", "-", "x", "x", "x")
        self.assertEqual(report.verdict(), "ไม่ผ่าน")

    def test_report_item_contains_rule_provenance(self):
        report = Report()
        report.add("RED", "front_matter", "หน้าปก", "ผิด", "ถูก", rule_id="FRONT.COVER")
        item = report.zones["RED"][0]
        self.assertEqual(item["rule_id"], "FRONT.COVER")
        self.assertTrue(item["rule_references"])

    def test_report_item_does_not_repeat_expected_as_a_fake_fix(self):
        """ข้อที่ไม่ได้ส่งวิธีแก้มา ต้องไม่ถูกเติมประโยคที่พูดซ้ำ "ควรเป็น" คำต่อคำ

        เจ้าหน้าที่สั่งว่าคำอธิบายไม่ต้องเวิ่นเยอะ — บรรทัด "แนะนำการแก้ไข" ที่เขียนว่า
        "แก้ไขให้เป็นไปตามข้อกำหนด: <ควรเป็น>" ไม่ได้บอกอะไรใหม่เลย
        """
        report = Report()
        report.add("RED", "body", "หน้า 12", "พบข้อผิดพลาด", "ข้อความที่ถูกต้อง", "")
        self.assertEqual(report.zones["RED"][0]["fix"], "")
        # ข้อที่ส่งวิธีแก้จริงมา ต้องเก็บไว้ตามเดิม
        report.add("RED", "body", "หน้า 13", "พบข้อผิดพลาด", "ข้อความที่ถูกต้อง",
                   "แก้เลขหน้าให้ต่อเนื่อง")
        self.assertEqual(report.zones["RED"][1]["fix"], "แก้เลขหน้าให้ต่อเนื่อง")

    def test_orange_means_pending(self):
        report = Report()
        report.add("ORANGE", "-", "x", "x", "x")
        self.assertEqual(report.verdict(), "รอยืนยัน")

    def test_yellow_can_pass(self):
        report = Report()
        report.add("YELLOW", "-", "x", "x", "x")
        self.assertEqual(report.verdict(), "ผ่าน")

    def test_out_of_scope_items_are_declared(self):
        self.assertGreaterEqual(len(NOT_CHECKED), 4)


class OptionResolutionTests(unittest.TestCase):
    def test_free_mode_uses_selected_option(self):
        body = [(1, "Custom chapter title", 0, 1)]
        self.assertEqual(resolve_option(body, {"format": "2"}, "free"), 2)

    def test_strict_mode_infers_published_option(self):
        body = [(1, "SUMMARY", 0, 1)]
        self.assertEqual(resolve_option(body, {"format": "1"}, "strict"), 2)

    def test_strict_mode_defaults_to_traditional_option(self):
        body = [(1, "INTRODUCTION", 0, 1)]
        self.assertEqual(resolve_option(body, {"format": "2"}, "strict"), 1)


class ThaiBookRegressionTests(unittest.TestCase):
    """กันบั๊กชุดที่พบจากการตรวจเล่มภาษาไทยจริง (report ก.ค. 2569)"""

    def test_thai_chapter_title_is_compared_against_thai_canonical(self):
        compared, expected = compare_canonical_title("บทนำ", ("บทนำ", "INTRODUCTION"))
        self.assertEqual(compared["status"], "exact")

    def test_wrong_thai_title_reports_thai_expected_not_english(self):
        compared, expected = compare_canonical_title(
            "ทบทวนวรรณกรรม", ("วรรณกรรมและงานวิจัยที่เกี่ยวข้อง", "LITERATURE REVIEW"))
        self.assertNotEqual(compared["status"], "exact")
        self.assertEqual(expected, "วรรณกรรมและงานวิจัยที่เกี่ยวข้อง")

    def test_english_chapter_title_still_matches_english_canonical(self):
        compared, expected = compare_canonical_title(
            "INTRODUCTION", ("บทนำ", "INTRODUCTION"))
        self.assertEqual(compared["status"], "exact")
        self.assertEqual(expected, "INTRODUCTION")

    def test_thai_toc_lists_english_abstract_in_thai_wording(self):
        # template เล่มไทยใช้หัวข้อ "บทคัดย่อภาษาไทย" / "บทคัดย่อภาษาอังกฤษ"
        self.assertEqual(_toc_section_kind("บทคัดย่อภาษาไทย ง"), "abstract_th")
        self.assertEqual(_toc_section_kind("บทคัดย่อภาษาอังกฤษ จ"), "abstract_en")
        self.assertEqual(_toc_section_kind("สารบัญรูปภาพ ซ"), "list_figures")

    def test_thai_abstract_entries_are_major_headings(self):
        self.assertTrue(_is_toc_major_heading("บทคัดย่อภาษาไทย ง"))
        self.assertTrue(_is_toc_major_heading("บทคัดย่อภาษาอังกฤษ จ"))

    def test_abstract_heading_bold_is_expected_by_template(self):
        self.assertTrue(_is_abstract_heading("บทคัดย่อ"))
        self.assertTrue(_is_abstract_heading("ABSTRACT"))
        self.assertTrue(_is_abstract_heading("ABSTRACT (ENGLISH)"))
        self.assertFalse(_is_abstract_heading("Keywords: Resilience, Aging"))
        self.assertFalse(_is_abstract_heading("FACTORS RELATED TO RESILIENCE"))

    def test_image_only_page_counts_as_unextractable(self):
        self.assertTrue(_is_blank_page_text(""))
        self.assertTrue(_is_blank_page_text("   \n  "))

    def test_thai_final_summary_chapter_does_not_flip_option_to_published(self):
        # เล่มดั้งเดิม 6 บทจบด้วย "บทสรุปและข้อเสนอแนะ" ต้องยังเป็นรูปแบบ 1
        body = [(1, "บทนำ", 7, 1), (2, "วรรณกรรมและงานวิจัยที่เกี่ยวข้อง", 8, 2),
                (3, "วิธีการดำเนินการวิจัย", 9, 3), (4, "ผลการวิจัย", 10, 4),
                (5, "การอภิปรายผล", 11, 5), (6, "บทสรุปและข้อเสนอแนะ", 12, 6)]
        self.assertEqual(resolve_option(body, {"format": "1"}, "strict"), 1)

    def test_thai_published_option_is_inferred_from_first_chapter(self):
        body = [(1, "บทสรุป", 7, 1), (2, "ผลงานตีพิมพ์", 8, 2)]
        self.assertEqual(resolve_option(body, {"format": "2"}, "strict"), 2)

    def test_scrambled_thai_chapter_prefix_is_stripped(self):
        # PDF ไทยดึง "บทที่ 1" เป็น "บทท ี่ 1" — ต้องตัด prefix ได้และชื่อบทเทียบตรง
        self.assertEqual(_toc_chapter_title("บทท ี่ 1 บทน า 1"), "บทน า")
        compared, _ = compare_canonical_title(
            _toc_chapter_title("บทท ี่ 1 บทน า 1"), ("บทนำ", "INTRODUCTION"))
        self.assertEqual(compared["status"], "exact")

    def test_symbol_abbreviation_list_heading_is_recognized(self):
        self.assertEqual(_toc_section_kind("คำอธิบายสัญลักษณ์/คำย่อ ฎ"), "list_abbreviations")

    def test_chapter_title_policy_exact_vs_wrong(self):
        # ยึดประกาศเป็นหลัก: ตรงประกาศ = exact ต่างแม้แต่ตัวเดียว = wrong (แดง)
        kind, _, _ = canonical_title_status("LITERATURE REVIEW", 2, 1)
        self.assertEqual(kind, "exact")
        # บทที่ 2 เกิน S (REVIEW -> REVIEWS) = พิมพ์ผิดเล็กน้อย เหมือนบทอื่น ไม่ใช่ variant
        kind, compared, expected = canonical_title_status("LITERATURE REVIEWS", 2, 1)
        self.assertEqual(kind, "wrong")
        self.assertEqual(compared["status"], "typo")
        self.assertEqual(expected, "LITERATURE REVIEW")
        kind, _, expected = canonical_title_status("RESEARCH METHODLOGY", 3, 1)
        self.assertEqual(kind, "wrong")
        self.assertEqual(expected, "RESEARCH METHODOLOGY")
        kind, _, _ = canonical_title_status("CONCLUSION AND RECOMMENDATONS", 6, 1)
        self.assertEqual(kind, "wrong")


class TheTwoStaffSideCardsAreGone(unittest.TestCase):
    """เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าสองเรื่องนี้ "ไม่จำเป็นต้องมีการ์ดขึ้นมา"

    - ไม่ได้กรอกข้อมูลอนุมัติบางช่อง — "ระบบไม่ควรให้กดตรวจ" หน้าเว็บบังคับกรอก และ
      /check ปฏิเสธก่อนสร้างงานอยู่แล้ว (รายการเดียวกัน) การ์ดนี้จึงมาไม่ถึงรายงานจริง
    - เลือกไฟล์ eThesis ผิดคน — "ก็จะตรวจสอบผิด ให้ตรวจใหม่เอง"
    """

    def test_the_missing_field_card_is_gone(self):
        self.assertFalse(hasattr(checker_module, "_report_missing_form_fields"))
        self.assertNotIn("ระบบจึงข้ามการเทียบข้อมูลนี้", inspect.getsource(checker_module))

    def test_the_wrong_student_card_is_gone(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertNotIn("น่าจะเป็นคนละคนกัน", source)
        self.assertNotIn("ไฟล์ eThesis กับไฟล์เล่มต้องเป็นของนักศึกษาคนเดียวกัน", source)

    def test_a_wrong_file_is_still_compared_not_skipped(self):
        """ห้ามข้ามการเทียบข้อมูลอนุมัติเงียบ ๆ เมื่อไม่มีการ์ดบอกแล้ว

        วัดกับเล่มจริง: เล่ม 1 คู่กับไฟล์ eThesis ของเล่ม 3 ถ้าข้ามโดยไม่มีการ์ด จะได้
        "ผ่าน" ทั้งที่ไม่ได้เทียบอะไรเลย ต้องเทียบตามปกติ ชื่อ/รหัสที่ไม่ตรงจึงขึ้นแดงให้เห็น
        """
        source = inspect.getsource(checker_module.run_check)
        self.assertNotIn("if approved and not same_student", source)
        self.assertIn("    if approved:\n        A = approved", source)

    def test_the_language_gate_still_ignores_someone_elses_file(self):
        """ควบคุมเชิงลบ — ด่านภาษาของเล่มหยุดตรวจทั้งเล่ม ห้ามตัดสินจากไฟล์ของคนอื่น

        ไม่งั้นเลือกไฟล์ผิดคนที่เป็นเล่มคนละภาษา จะได้ข้อเดียวว่า "เล่มผิดภาษา" แล้วหยุด
        ซึ่งทั้งปิดบังว่าเลือกไฟล์ผิด และเสี่ยงที่จะส่งเล่มที่ถูกกลับให้นักศึกษาทำใหม่
        """
        source = inspect.getsource(checker_module.run_check)
        self.assertRegex(source, r"approved and same_student and not skip_identity_check")

    def test_the_web_form_and_the_server_require_the_same_fields(self):
        """การ์ดหายไปได้เพราะมีสองด่านกันไว้ ต้องไม่หลุดจากกันเอง"""
        import re
        from pathlib import Path
        html = (Path(checker_module.__file__).parent / "templates" / "index.html"
                ).read_text(encoding="utf-8")
        always = set(re.findall(r'name="(\w+)"[^>]*\brequired\b', html))
        toggled = dict(re.findall(
            r"querySelector\('\[name=\"(\w+)\"\]'\)\.required = (\w+|!\w+|true)", html))
        from ethesis_rules import FRONT_MATTER_RULES
        rules = FRONT_MATTER_RULES["required_form_fields"]
        for program, want in rules.items():
            flags = {"requiresThai": program in ("thai", "thai_english"),
                     "thaiOnly": program == "thai", "!thaiOnly": program != "thai",
                     "true": True}
            page = {n for n in always if n not in toggled} | \
                   {n for n, flag in toggled.items() if flags[flag]}
            self.assertEqual(page & set(_FIELDS), set(want), program)


_FIELDS = ("title_en", "title_th", "student_name", "student_name_th", "student_id",
           "degree_cover_en", "degree_cover_th", "degree_sig_en", "degree_sig_th",
           "degree_abbr_en", "degree_abbr_th", "exam_date", "year")


class DegreeFieldsByLocationTests(unittest.TestCase):
    """ชื่อปริญญาแยก 3 คู่ ผูกกับตำแหน่งที่ใช้ตรวจ (ปก / หน้าลงนาม / บทคัดย่อ)"""

    def test_international_uses_english_set_only(self):
        fields = FRONT_MATTER_RULES["required_form_fields"]["international"]
        self.assertIn("degree_cover_en", fields)
        self.assertIn("degree_sig_en", fields)
        self.assertIn("degree_abbr_en", fields)
        # นานาชาติไม่มีบทคัดย่อไทยและปก/ลงนามไม่ใช่ภาษาไทย
        self.assertNotIn("degree_cover_th", fields)
        self.assertNotIn("degree_abbr_th", fields)

    def test_thai_book_uses_thai_set_for_cover_and_signature(self):
        fields = FRONT_MATTER_RULES["required_form_fields"]["thai"]
        self.assertIn("degree_cover_th", fields)
        self.assertIn("degree_sig_th", fields)
        # เล่มไทยยังมีบทคัดย่ออังกฤษด้วย จึงต้องมีตัวย่อทั้งสองภาษา
        self.assertIn("degree_abbr_en", fields)
        self.assertIn("degree_abbr_th", fields)

    def test_thai_english_book_uses_english_cover_but_needs_thai_abstract(self):
        fields = FRONT_MATTER_RULES["required_form_fields"]["thai_english"]
        self.assertIn("degree_cover_en", fields)
        self.assertIn("degree_sig_en", fields)
        self.assertIn("degree_abbr_th", fields)
        self.assertNotIn("degree_cover_th", fields)

    def test_labels_name_the_location_each_field_checks(self):
        self.assertIn("หน้าปก", FORM_FIELD_LABELS["degree_cover_en"])
        self.assertIn("หน้าปก", FORM_FIELD_LABELS["degree_cover_th"])
        self.assertIn("หน้าลงนาม", FORM_FIELD_LABELS["degree_sig_en"])
        self.assertIn("บทคัดย่อ", FORM_FIELD_LABELS["degree_abbr_en"])


class DegreeMismatchSeverityTests(unittest.TestCase):
    """นโยบาย: ต่างเฉพาะวรรคตอน/ช่องว่าง = ส้ม, สะกดผิด = แดง

    ตัวตัดสินคือ norm(ข้อมูลอนุมัติ) ยังอยู่ในหน้านั้นหรือไม่ (ตัวอักษรครบ = ส้ม)
    """

    APPROVED = "M.Sc. (INFORMATION TECHNOLOGY MANAGEMENT)"

    def test_punctuation_only_difference_keeps_every_letter(self):
        page = "M.Sc (INFORMATION TECHNOLOGY MANAGEMENT)"   # ตกจุดท้าย Sc
        self.assertFalse(exact_reference_status(page, self.APPROVED)[0])
        self.assertIn(norm(self.APPROVED), norm(page))       # -> เข้าเงื่อนไขสีส้ม

    def test_misspelled_degree_loses_letters(self):
        page = "M.Sd. (INFORMATION TECHNOLOGY MANAGEMENT)"   # สะกดผิด c -> d
        self.assertFalse(exact_reference_status(page, self.APPROVED)[0])
        self.assertNotIn(norm(self.APPROVED), norm(page))     # -> เข้าเงื่อนไขสีแดง

    def test_line_wrap_only_still_counts_as_exact(self):
        # ต่างเฉพาะการตัดบรรทัด ไม่ถือว่าผิด
        page = "M.Sc.\n(INFORMATION TECHNOLOGY MANAGEMENT)"
        self.assertIn(norm(self.APPROVED), norm(page))


class CoverDegreeLineTests(unittest.TestCase):
    """ชื่อปริญญาบนหน้าปกมักถูกตัดหลายบรรทัด ต้องรวมก่อนเทียบ"""

    def test_degree_split_at_parenthesis_is_joined(self):
        page = ("A THESIS SUBMITTED IN PARTIAL FULFILLMENT\n"
                "MASTER OF SCIENCE\n"
                "(INFORMATION TECHNOLOGY MANAGEMENT)\n"
                "FACULTY OF GRADUATE STUDIES")
        line = closest_degree_line(page, "MASTER OF SCIENCE(INFORMATION TECHNOLOGY MANAGEMENT)")
        self.assertIn("MASTER OF SCIENCE", line)
        self.assertIn("INFORMATION TECHNOLOGY MANAGEMENT", line)

    def test_degree_split_mid_parenthesis_is_joined(self):
        # วงเล็บเปิดค้างท้ายบรรทัด — เคสที่ตรรกะเดิมพลาด
        page = ("COVER\n"
                "MASTER OF SCIENCE (WELL-BEING AND\n"
                "SUSTAINABILITY)\n"
                "MAHIDOL UNIVERSITY")
        line = closest_degree_line(page, "MASTER OF SCIENCE (WELL-BEING AND SUSTAINABILITY)")
        self.assertIn("WELL-BEING", line)
        self.assertIn("SUSTAINABILITY)", line)

    def test_thai_cover_degree_is_found_and_joined(self):
        # เดิมไม่มีคำบ่งชี้ภาษาไทยเลย เล่มไทยจึงรวมบรรทัดไม่ได้
        page = ("ชื่อเรื่องภาษาไทย\n"
                "ปริญญาศิลปศาสตรมหาบัณฑิต\n"
                "(สังคมศาสตร์สิ่งแวดล้อม)\n"
                "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล")
        line = closest_degree_line(page, "ศิลปศาสตรมหาบัณฑิต(สังคมศาสตร์สิ่งแวดล้อม)")
        self.assertIn("ศิลปศาสตรมหาบัณฑิต", line)
        self.assertIn("สังคมศาสตร์สิ่งแวดล้อม", line)


class TocSectionPageTests(unittest.TestCase):
    """เลขหน้าหัวข้อหลักในสารบัญไม่ตรงหน้าจริง = ส้มทุกกรณี (นโยบายใหม่)

    helper คืนแค่ว่าเป็นกรณีภาคผนวกหลายชุดหรือไม่ ใช้เลือกข้อความอธิบาย ไม่ใช่สี
    """

    APPENDIX_PAGES = {"85", "87", "88", "90"}

    def test_generic_main_section_mismatch_is_not_appendix_alt(self):
        # หัวข้อหลักทั่วไปที่เลขไม่ตรง ไม่ใช่กรณีภาคผนวกหลายชุด → ข้อความ mismatch ปกติ
        for kind in ("references", "biography", "abstract_en", "list_tables"):
            self.assertFalse(
                toc_page_mismatch_is_appendix_alt(kind, "79", self.APPENDIX_PAGES))

    def test_appendix_pointing_at_another_appendix_uses_alt_message(self):
        # สารบัญเขียน "APPENDIX 87" แต่ภาคผนวกชุดแรกอยู่หน้า 85 — 87 เป็นหน้าเริ่ม
        # ของ APPENDIX B ที่มีจริง จึงใช้ข้อความอธิบายแบบภาคผนวกหลายชุด (ยังเป็นเหลือง)
        self.assertTrue(
            toc_page_mismatch_is_appendix_alt("appendix", "87", self.APPENDIX_PAGES))

    def test_appendix_pointing_at_a_page_with_no_appendix_is_generic(self):
        # ชี้ไปหน้าที่ไม่มีภาคผนวกเลย = mismatch ธรรมดา (ไม่ใช่ alt) แต่ก็ยังเป็นเหลือง
        self.assertFalse(
            toc_page_mismatch_is_appendix_alt("appendix", "999", self.APPENDIX_PAGES))

    def test_toc_entry_line_would_match_the_appendix_heading_rule(self):
        # เหตุผลที่ต้องกันไม่ให้สแกนหน้าสารบัญเป็นส่วนท้ายเล่ม: บรรทัดในสารบัญ
        # อย่าง "APPENDIX D 90" เข้าเงื่อนไขหัวบทภาคผนวก (startswith) ได้
        self.assertTrue(any(norm("APPENDIX D 90").startswith(w) for w in N_APPENDIX))


class ChapterScopeByFormatTests(unittest.TestCase):
    """รูปแบบ 1 บังคับชื่อครบ 6 บท, รูปแบบ 2 บังคับเฉพาะบท 1-2"""

    def test_format1_enforces_every_chapter(self):
        self.assertEqual(CANONICAL_ENFORCED_COUNT[1], len(CANONICAL_OPTION_1))

    def test_format2_enforces_only_summary_and_publication(self):
        self.assertEqual(CANONICAL_ENFORCED_COUNT[2], 2)
        self.assertEqual(CANONICAL_OPTION_2[0][1], "SUMMARY")
        self.assertEqual(CANONICAL_OPTION_2[1][1], "PUBLICATION")

    def test_format2_third_chapter_is_optional_and_unnamed(self):
        # บทที่ 3 มีชื่อในทะเบียนไว้อ้างอิง แต่อยู่นอกช่วงที่บังคับ
        self.assertGreater(len(CANONICAL_OPTION_2), CANONICAL_ENFORCED_COUNT[2])

    def test_chapter_titles_are_cross_checked_three_ways(self):
        # ประกาศ ↔ สารบัญ ↔ เนื้อหา ต้องเปิดตรวจครบทั้งสามด้าน
        self.assertTrue(BODY_RULES["check_toc_title_against_body"])
        self.assertTrue(BODY_RULES["check_body_title_against_canonical"])
        self.assertTrue(BODY_RULES["check_toc_chapter_presence"])


class SignatureTemplateSentenceTests(unittest.TestCase):
    """หน้าลงนามต้องมีประโยคตายตัวของ template ไม่ใช่แค่ชื่อปริญญาถูก

    เทียบด้วย norm() เหมือนในตัวตรวจจริง (ตัดเว้นวรรค/คอมมา/ตัวพิมพ์)
    """

    # ข้อความจริงที่ดึงได้จากเล่มตัวอย่าง (หน้าอาจารย์ที่ปรึกษา/หน้ากรรมการสอบ)
    EN_PAGE = ("was submitted to the Faculty of Graduate Studies, Mahidol University\n"
               "for the degree of Doctor of Philosophy (Tropical Medicine)\n"
               "on 25 June 2026")
    TH_ADVISORY = ("นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร\n"
                   "ปริญญาศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)")
    TH_EXAM = ("ได้รับการพิจารณาให้นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร\n"
               "ปริญญาศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)")

    def test_english_template_found_across_line_break(self):
        # template ขึ้นบรรทัดใหม่กลางประโยค — norm() ตัดช่องว่างจึงยังเจอ
        self.assertIn(norm(SIGNATURE_TEMPLATE_EN), norm(self.EN_PAGE))

    def test_english_template_is_case_and_comma_insensitive(self):
        self.assertIn(norm(SIGNATURE_TEMPLATE_EN),
                      norm("WAS SUBMITTED TO THE FACULTY OF GRADUATE STUDIES "
                           "MAHIDOL UNIVERSITY FOR THE DEGREE OF"))

    def test_thai_template_covers_both_signature_pages(self):
        # ท่อนที่เก็บไว้ต้องอยู่ในทั้งหน้าที่ปรึกษาและหน้ากรรมการสอบ
        self.assertIn(norm(SIGNATURE_TEMPLATE_TH), norm(self.TH_ADVISORY))
        self.assertIn(norm(SIGNATURE_TEMPLATE_TH), norm(self.TH_EXAM))

    def test_missing_template_sentence_is_detected(self):
        # เล่มที่มีชื่อปริญญาถูกแต่ตัดประโยค template ออก ต้องไม่ผ่าน
        self.assertNotIn(norm(SIGNATURE_TEMPLATE_EN),
                         norm("Doctor of Philosophy (Tropical Medicine)\non 25 June 2026"))
        self.assertNotIn(norm(SIGNATURE_TEMPLATE_TH),
                         norm("ปริญญาศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)"))


class MultiLineTitleTests(unittest.TestCase):
    """ชื่อเรื่องบนหน้าลงนามที่ตัดขึ้นหลายบรรทัด ต้องดึงมาครบ ไม่ฟ้อง "ขาด" ผิด ๆ"""

    SIG_PAGE = (
        "Thematic paper\nentitled\n"
        "An evaluation of officer identification card issuance and management system using\n"
        "ISO/IEC 25010 software quality model\n"
        "was submitted to the Faculty of Graduate Studies, Mahidol University for the\n"
        "degree of Master of Science (Biomedical and Health Informatics)\non 26 June 2026"
    )
    APPROVED = ("AN EVALUATION OF OFFICER IDENTIFICATION CARD ISSUANCE AND MANAGEMENT "
                "SYSTEM USING ISO/IEC 25010 SOFTWARE QUALITY MODEL")

    def test_wrapped_title_is_extracted_in_full(self):
        found = closest_text_line(self.SIG_PAGE, self.APPROVED)
        self.assertIn("system using", found)
        self.assertIn("ISO/IEC 25010 software quality model", found)

    def test_wrapped_title_reported_as_not_matching_system_not_missing(self):
        # เล่มใช้ Sentence case, อนุมัติเป็นตัวใหญ่ — บอกกลาง ๆ ว่า "ไม่ตรงกับข้อมูลในระบบ"
        # ไม่ใช่ "ตัวพิมพ์เล็ก-ใหญ่ไม่ตรง" และต้องไม่ฟ้องว่าข้อความหาย
        compared = compare_reference_text(self.SIG_PAGE, self.APPROVED, "title")
        self.assertEqual(compared["status"], "case")
        detail = title_mismatch_detail("ชื่อเรื่อง", compared, self.APPROVED)
        self.assertIn("ไม่ตรงกับข้อมูลในระบบ", detail)
        self.assertNotIn("ตัวพิมพ์เล็ก-ใหญ่", detail)
        self.assertNotIn("ขาด", detail)
        self.assertIn("ISO/IEC 25010", compared["actual"])

    def test_exact_full_title_still_matches(self):
        page = "entitled\n" + self.APPROVED + "\nwas submitted"
        compared = compare_reference_text(page, self.APPROVED, "title")
        self.assertEqual(compared["status"], "exact")


class ReferenceHeadingTests(unittest.TestCase):
    """สารบัญส่วนอ้างอิงต้องเลือกคำเดียว และตรงกับหัวข้อในหน้าจริง"""

    def test_single_term_recognized(self):
        self.assertEqual(reference_terms("REFERENCES ............ 118"), ["REFERENCES"])
        self.assertEqual(reference_terms("BIBLIOGRAPHY 118"), ["BIBLIOGRAPHY"])
        self.assertEqual(reference_terms("บรรณานุกรม ๑๑๘"), ["บรรณานุกรม"])
        # REFERENCE (เอกพจน์) นับเป็น REFERENCES กลุ่มเดียวกัน
        self.assertEqual(reference_terms("REFERENCE 5"), ["REFERENCES"])

    def test_multiple_terms_flagged(self):
        self.assertEqual(len(reference_terms("REFERENCES/BIBLIOGRAPHY 118")), 2)

    def test_toc_term_vs_page_term_mismatch_detectable(self):
        # สารบัญใช้ BIBLIOGRAPHY แต่หน้าจริงใช้ REFERENCES = ไม่ตรงกัน
        toc = reference_terms("BIBLIOGRAPHY 118")
        page = reference_terms("REFERENCES")
        self.assertNotEqual(set(toc), set(page))


class SignatureCommitteeTests(unittest.TestCase):
    """อ่านตารางลายเซ็นตามกริดตายตัว: กรรมการเติมขวาบน→ล่าง(1–5) แล้วซ้ายล่าง→บน(6–9)"""

    class _Page:
        def __init__(self, height, width, words):
            self.height = height
            self.width = width
            self._words = words

        def extract_words(self, *a, **k):
            return self._words

    def _dot_then_names(self, rows):
        """สร้าง words: แต่ละ row = เส้นประ + ชื่อ [+ คุณวุฒิ] (left, right[, qleft, qright])"""
        words = []
        top = 100
        for row in rows:
            left, right = row[0], row[1]
            qleft = row[2] if len(row) > 2 else None
            qright = row[3] if len(row) > 3 else None
            words.append({"text": "………………", "top": top, "x0": 60})
            words.append({"text": "………………", "top": top, "x0": 320})
            for tok in left.split():
                words.append({"text": tok, "top": top + 13, "x0": 60})
            for tok in right.split():
                words.append({"text": tok, "top": top + 13, "x0": 320})
            if qleft is not None or qright is not None:
                for tok in (qleft or "").split():
                    words.append({"text": tok, "top": top + 26, "x0": 60})
                for tok in (qright or "").split():
                    words.append({"text": tok, "top": top + 26, "x0": 320})
            top += 60
        return words

    def test_grid_maps_right_then_left(self):
        rows = [
            ("Candidate,", "Prof. A One,"),                 # r2: student | member1
            ("ตำแหน่งทางวิชาการและชื่อ นามสกุล,", "Prof. B Two,"),   # r3: (m9 placeholder) | member2
            ("Academic rank First Name Last name,", "Prof. C Three,"),  # r4 | member3
            ("Academic rank First Name Last name,", "Academic rank First Name Last name,"),  # r5
            ("Academic rank First Name Last name,", "Academic rank First Name Last name,"),  # r6
            ("Dean", "Program Director"),                     # r7: dean | director
        ]
        page = self._Page(842, 595, self._dot_then_names(rows))
        members, quals, bottom, _raw = signature_committee_slots(page)
        self.assertEqual(members.get(1), "A One")
        self.assertEqual(members.get(2), "B Two")
        self.assertEqual(members.get(3), "C Three")
        self.assertIsNone(members.get(4))       # ช่องว่าง/placeholder
        self.assertIsNone(members.get(9))       # placeholder ซ้าย
        self.assertIn("Program Director", bottom)

    def test_last_dotted_row_is_never_a_member(self):
        # แถวเส้นประสุดท้าย = ช่องสถาบัน แม้จะอ่านเส้นประเจอไม่ครบ 6 แถวก็ต้องไม่ถูกนับ
        rows = [
            ("Candidate,", "A One,"),
            ("B Nine,", "B Two,"),
            ("ศาสตราจารย์ ฉัตรเฉลิม อิศรางกูร ณ อยุธยา,", "พรรณชฎา ศิริวรรณบุศย์,"),  # แถวคณบดี
        ]
        page = self._Page(842, 595, self._dot_then_names(rows))
        members, _quals, bottom, _raw = signature_committee_slots(page)
        self.assertEqual(members.get(1), "A One")
        self.assertEqual(members.get(2), "B Two")
        found = [v for v in members.values() if v]
        self.assertNotIn("ฉัตรเฉลิม อิศรางกูร ณ อยุธยา", found)
        self.assertNotIn("พรรณชฎา ศิริวรรณบุศย์", found)

    def test_white_filled_text_is_not_read_as_a_member(self):
        # เล่มจริงพบชั้นข้อความเก่าถมขาวทับกัน ถ้าอ่านรวมจะได้ชื่อกรรมการซ้ำ/ผิดช่อง
        words = self._dot_then_names([
            ("Candidate,", "A One,"),
            ("", "B Two,"),
            ("Dean", "Program Director"),
        ])
        for w in words:
            w.setdefault("non_stroking_color", (0, 0, 0))
        words += [{"text": "Ghost", "top": 173, "x0": 60, "non_stroking_color": (1, 1, 1)},
                  {"text": "Member,", "top": 173, "x0": 90, "non_stroking_color": (1, 1, 1)}]
        page = self._Page(842, 595, words)
        members, _quals, _bottom, _raw = signature_committee_slots(page)
        self.assertNotIn("Ghost Member", [v for v in members.values() if v])

    def test_qualification_presence_detected_per_member(self):
        rows = [
            ("Candidate,", "A One,", "", "Ph.D."),                 # m1 มีคุณวุฒิ
            ("Academic rank First Name Last name,", "B Two,", "", "Degree (Subject)"),  # m2 placeholder=ไม่มี
            ("Academic rank First Name Last name,", "C Three,"),   # m3 ไม่มีบรรทัดคุณวุฒิ
            ("Dean", "Program Director"),
        ]
        page = self._Page(842, 595, self._dot_then_names(rows))
        members, quals, bottom, _raw = signature_committee_slots(page)
        self.assertEqual(members.get(1), "A One")   # ชื่อกรรมการคนที่ 1
        self.assertTrue(quals.get(1))               # m1 มีคุณวุฒิ
        self.assertFalse(quals.get(2))              # m2 เป็น placeholder = ไม่มี
        self.assertFalse(quals.get(3))              # m3 ไม่มีบรรทัดคุณวุฒิ

    def test_page_kind_detection(self):
        self.assertEqual(_committee_page_kind("Thesis Advisory Committees\nMajor Advisor"), "advisory")
        self.assertEqual(_committee_page_kind("Thesis Examination Committees\nChair"), "exam")
        self.assertEqual(_committee_page_kind("คณะกรรมการสอบวิทยานิพนธ์"), "exam")

    def test_degree_subject_extracted(self):
        self.assertEqual(_degree_subject("Doctor of Philosophy (Tropical Medicine)"), "Tropical Medicine")
        self.assertEqual(_degree_subject("ปรัชญาดุษฎีบัณฑิต (อายุรศาสตร์เขตร้อน)"), "อายุรศาสตร์เขตร้อน")
        self.assertEqual(_degree_subject("No Parens Here"), "")


class AbstractTitleMustBeLeftAligned(unittest.TestCase):
    """ชื่อเรื่องบนหน้าบทคัดย่อต้องชิดซ้าย ไม่ใช่กึ่งกลาง/ชิดขวา

    ระบบตรวจ PDF จึงไม่มีค่า "การจัดย่อหน้า" ให้อ่าน ต้องเทียบขอบซ้ายของบรรทัด
    ชื่อเรื่องกับขอบซ้ายของเนื้อความในหน้าเดียวกัน (พิกัดจริงจากเล่มที่ 9)
    """

    BODY = {"text": "B" * 90, "x0": 70.9, "bold_ratio": 0.0}
    STUDENT = {"text": "CHRISTI KUSUMA WARDANI 6238285 SHHS/D", "x0": 70.9,
               "bold_ratio": 0.0}

    def _run(self, *title_lines):
        rep = Report()
        lines = list(title_lines) + [self.STUDENT, self.BODY]
        _report_abstract_title_format(rep, lines, "บทคัดย่อ (หน้า iv)")
        return [i["found"] for i in rep.zones["ORANGE"]]

    def test_left_aligned_title_passes(self):
        self.assertEqual(
            self._run({"text": "WOMEN'S AUTONOMY IN THE PROCESS OF SEEKING CARE FOR",
                       "x0": 70.9, "bold_ratio": 0.0},
                      {"text": "OBSTETRIC COMPLICATIONS", "x0": 70.9,
                       "bold_ratio": 0.0}),
            [])

    def test_centred_line_is_reported(self):
        found = self._run(
            {"text": "WOMEN'S AUTONOMY IN THE PROCESS OF SEEKING CARE FOR",
             "x0": 75.5, "bold_ratio": 1.0},
            {"text": "OBSTETRIC COMPLICATIONS", "x0": 194.4, "bold_ratio": 1.0})
        self.assertEqual(len(found), 1)
        self.assertIn("OBSTETRIC COMPLICATIONS", found[0])
        self.assertNotIn("ตัวหนา", found[0])      # ตัวหนามีกฎเหลืองของตัวเองแล้ว

    def test_running_head_is_not_mistaken_for_the_title(self):
        """เล่มที่ 4 มีหัวกระดาษ "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล ..." เหนือชื่อเรื่อง"""
        self.assertEqual(
            self._run({"text": "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล วิทยานิพนธ์ / ง",
                       "x0": 300.0, "bold_ratio": 0.0},
                      {"text": "ชื่อเรื่องภาษาไทยของวิทยานิพนธ์เล่มนี้", "x0": 70.9,
                       "bold_ratio": 0.0}),
            [])

    def test_page_without_a_student_line_is_skipped(self):
        rep = Report()
        _report_abstract_title_format(rep, [self.BODY], "บทคัดย่อ (หน้า iv)")
        self.assertEqual(rep.zones["ORANGE"], [])


class SummaryGroupsByWhereToFixIt(unittest.TestCase):
    """ข้อของหน้าลงนามต้องอยู่หมวด "หน้าลงนาม" ไม่ใช่ "อื่น ๆ"

    ระบบตั้งชื่อตำแหน่งตามบทบาทของหน้า (หน้าอาจารย์ที่ปรึกษา / หน้ากรรมการสอบ)
    ไม่ได้เขียนคำว่า "หน้าลงนาม" ตรง ๆ เสมอ เจ้าหน้าที่จึงเห็นข้อของหน้าลงนาม
    ไปโผล่ใต้หัวข้อ "อื่น ๆ" ทั้งที่ต้องไปแก้ที่หน้าลงนามเหมือนกัน
    """

    def _sec(self, loc):
        return summary_section({"location": loc, "part": "front_matter"})

    def test_signature_pages_are_grouped_together(self):
        for loc in ("หน้าลงนาม 1 (หน้า i)",
                    "หน้าอาจารย์ที่ปรึกษา (หน้า i)",
                    "หน้ากรรมการสอบ (หน้า ii)",
                    "หน้าอาจารย์ที่ปรึกษา — ประธานหลักสูตร (หน้า ก)",
                    "หน้ากรรมการสอบ — คณบดีคณะ (หน้า ข)",
                    "ข้อความ template หน้าลงนาม"):
            self.assertEqual(self._sec(loc), "หน้าลงนาม", loc)

    def test_abstract_committee_stays_under_the_abstract(self):
        """"คณะกรรมการที่ปรึกษา" บนหน้าบทคัดย่อ ต้องไม่ถูกดึงไปหมวดหน้าลงนาม"""
        self.assertEqual(self._sec("บทคัดย่อ (หน้า ง) — คณะกรรมการที่ปรึกษา"), "บทคัดย่อ")

    def test_other_sections_are_unchanged(self):
        self.assertEqual(self._sec("หน้าปก"), "หน้าปก")
        self.assertEqual(self._sec("สารบัญ (หน้า ช)"), "สารบัญ")
        self.assertEqual(self._sec("บทที่ 3 (หน้า 45)"), "เนื้อหา (บท)")

    def test_chapter_structure_belongs_to_the_body(self):
        """"โครงบท" (เล่มใช้รูปแบบไม่ตรงที่อนุมัติ) เคยตกไปอยู่ "อื่น ๆ" """
        self.assertEqual(summary_section({"location": "โครงบท", "part": "body"}),
                         "เนื้อหา (บท)")

    def test_location_without_a_named_section_falls_back_to_the_part(self):
        """ตำแหน่งที่ไม่ได้เอ่ยชื่อส่วนไหน ยังรู้จาก part ว่าอยู่ช่วงไหนของเล่ม"""
        self.assertEqual(summary_section({"location": "ทั้งเล่ม", "part": "end_matter"}),
                         "เนื้อหา (บท)")   # "ทั้งเล่ม" อยู่ในรายการคำของหมวดเนื้อหา
        self.assertEqual(summary_section({"location": "", "part": "end_matter"}),
                         "ส่วนท้ายเล่ม")
        self.assertEqual(summary_section({"location": "ส่วนนำ", "part": "front_matter"}),
                         "ส่วนนำ")

    def test_bare_arabic_page_is_body_content(self):
        """หน้าว่างที่อ่านไม่ออก ตำแหน่งมีแค่ "หน้า 40" — เลขอารบิกแปลว่าอยู่ในเนื้อหา"""
        self.assertEqual(summary_section({"location": "หน้า 40", "part": "-"}),
                         "เนื้อหา (บท)")
        self.assertEqual(summary_section({"location": "ไฟล์แนบ.zip", "part": "-"}), "อื่น ๆ")


class IssuesAreOrderedTheWayStaffReadTheBook(unittest.TestCase):
    """ข้อในรายงานต้องเรียงตามส่วนประกอบของเล่ม แล้วตามเลขหน้าในส่วนนั้น

    เจ้าหน้าที่ไล่แก้เล่มจากหน้าแรกไปหน้าสุดท้าย ถ้าข้อสลับไปมาต้องเปิดกลับไปกลับมา
    """

    def _order(self, locations):
        items = [{"location": loc, "part": "front_matter"} for loc in locations]
        return [it["location"] for it in sorted(items, key=issue_sort_key)]

    def test_sections_come_in_book_order(self):
        self.assertEqual(
            self._order(["บทที่ 3 (หน้า 45)", "สารบัญ (หน้า ฉ)", "หน้าปก",
                         "หน้าลงนามหน้า 1 (หน้า ก)", "บทคัดย่อ (หน้า ง)"]),
            ["หน้าปก", "หน้าลงนามหน้า 1 (หน้า ก)", "บทคัดย่อ (หน้า ง)",
             "สารบัญ (หน้า ฉ)", "บทที่ 3 (หน้า 45)"])

    def test_pages_inside_a_section_run_ascending(self):
        self.assertEqual(
            self._order(["บทที่ 6 (หน้า 88)", "บทที่ 3 (หน้า 45)", "บทที่ 10 (หน้า 120)"]),
            ["บทที่ 3 (หน้า 45)", "บทที่ 6 (หน้า 88)", "บทที่ 10 (หน้า 120)"])

    def test_thai_front_pages_sort_by_alphabet_not_by_code_point(self):
        self.assertEqual(self._order(["บทคัดย่อ (หน้า ฉ)", "บทคัดย่อ (หน้า ง)"]),
                         ["บทคัดย่อ (หน้า ง)", "บทคัดย่อ (หน้า ฉ)"])

    def test_page_in_a_compound_word_is_not_read_as_the_page_number(self):
        """"หน้าลงนามหน้า 1 (หน้า ค)" ต้องอ่านได้ ค ไม่ใช่ 1 (เลขอารบิก = เนื้อหา)"""
        self.assertEqual(
            self._order(["หน้าลงนามหน้า 2 (หน้า ง)", "หน้าลงนามหน้า 1 (หน้า ค)"]),
            ["หน้าลงนามหน้า 1 (หน้า ค)", "หน้าลงนามหน้า 2 (หน้า ง)"])

    def test_items_without_a_page_go_last_in_their_section(self):
        self.assertEqual(
            self._order(["ส่วนนำ", "บทคัดย่อ (หน้า ง)"]),
            ["บทคัดย่อ (หน้า ง)", "ส่วนนำ"])


class ThaiProgramNeedsBothAbstracts(unittest.TestCase):
    """เล่มหลักสูตรไทยต้องมีบทคัดย่อทั้งไทยและอังกฤษ — ขาดภาษาไหนต้องบอกให้ชัด

    เดิมพิมพ์สภาพภายในระบบดิบ ๆ ว่า "พบบทคัดย่อ: EN=False, TH=True"
    เจ้าหน้าที่ต้องแปลเองว่าขาดอะไร
    """

    def _run(self, has_en, has_th, en_loc="", th_loc=""):
        rep = Report()
        _report_missing_abstract_language(rep, has_en, has_th, en_loc, th_loc)
        return rep.zones["RED"]

    def test_both_present_is_not_reported(self):
        self.assertEqual(self._run(True, True, "หน้า ฉ", "หน้า ง"), [])

    def test_missing_english_says_which_one_and_where_the_other_is(self):
        red = self._run(False, True, "", "หน้า ง")
        self.assertEqual(len(red), 1)
        self.assertIn("ขาดบทคัดย่อภาษาอังกฤษ", red[0]["found"])
        self.assertIn("หน้า ง", red[0]["found"])          # บอกด้วยว่าอันที่มีอยู่หน้าไหน
        self.assertEqual(red[0]["fix"], "เพิ่มบทคัดย่อภาษาอังกฤษ")

    def test_missing_thai(self):
        red = self._run(True, False, "หน้า ฉ", "")
        self.assertIn("ขาดบทคัดย่อภาษาไทย", red[0]["found"])
        self.assertNotIn("ภาษาอังกฤษ", red[0]["fix"])

    def test_missing_both_lists_both(self):
        red = self._run(False, False)
        self.assertIn("ขาดบทคัดย่อภาษาไทยและภาษาอังกฤษ", red[0]["found"])
        self.assertEqual(red[0]["fix"], "เพิ่มบทคัดย่อภาษาไทยและภาษาอังกฤษ")
        self.assertNotIn("พบเฉพาะ", red[0]["found"])      # ไม่มีอะไรให้บอกว่าพบ


class AbstractCommitteeTests(unittest.TestCase):
    """หน้าบทคัดย่อ: รายชื่อคณะกรรมการที่ปรึกษา + รูปแบบ (ตัวพิมพ์ใหญ่/วงเล็บ/ตำแหน่ง)"""

    def test_block_parse_english_multiline_wrap(self):
        text = ("THESIS ADVISORY COMMITTEE: NARISARA CHANTRATITA, Ph.D., NITAYA\n"
                "INDRAWATTANA, Ph.D., AMORNRAT AROONNUAL, Ph.D.\nABSTRACT\nxxx")
        is_en, block = abstract_committee_block(text)
        self.assertTrue(is_en)
        names, degrees = split_abstract_committee(block)
        self.assertEqual(names, ["NARISARA CHANTRATITA", "NITAYA INDRAWATTANA",
                                 "AMORNRAT AROONNUAL"])
        self.assertEqual(degrees, ["Ph.D.", "Ph.D.", "Ph.D."])

    def test_block_parse_thai(self):
        text = "คณะกรรมการที่ปรึกษาวิทยานิพนธ์: คนางค์ ก, ปร.ด., ธเนศ ข, พย.ด.\nบทคัดย่อ\nxxx"
        is_en, block = abstract_committee_block(text)
        self.assertFalse(is_en)
        names, _ = split_abstract_committee(block)
        self.assertEqual(names, ["คนางค์ ก", "ธเนศ ข"])

    def _run(self, committees, abs_en, abs_th, pages):
        rep = Report()
        _check_abstract_committees(rep, committees, abs_en, abs_th, pages,
                                   lambda i: f"หน้า {i}")
        return rep

    def _reds(self, rep):
        return [i["found"] for i in rep.zones["RED"]]

    def test_english_lowercase_name_flagged(self):
        committees = {"advisory": [{"name": "ก ข", "role": ""}]}
        pages = ["THESIS ADVISORY COMMITTEE: Narisara Chantratita, Ph.D.\nABSTRACT"]
        reds = self._reds(self._run(committees, [0], [], pages))
        self.assertTrue(any("ตัวพิมพ์ใหญ่" in r for r in reds))

    def test_subject_in_parentheses_flagged(self):
        committees = {"advisory": [{"name": "ก ข", "role": ""}]}
        pages = ["THESIS ADVISORY COMMITTEE: NARISARA CHANTRATITA, Ph.D. (Microbiology)\nABSTRACT"]
        reds = self._reds(self._run(committees, [0], [], pages))
        self.assertTrue(any("วงเล็บ" in r for r in reds))

    def test_academic_rank_in_abstract_flagged(self):
        committees = {"advisory": [{"name": "ก ข", "role": ""}]}
        pages = ["THESIS ADVISORY COMMITTEE: Assoc. Prof. NARISARA CHANTRATITA, Ph.D.\nABSTRACT"]
        reds = self._reds(self._run(committees, [0], [], pages))
        self.assertTrue(any("ตำแหน่งทางวิชาการ" in r for r in reds))

    def test_different_names_are_not_flagged_when_the_count_matches(self):
        """ไม่เทียบชื่อทั้งไทยและอังกฤษ (เจ้าหน้าที่สั่ง ส.ค. 2569) — ครบจำนวนก็พอ"""
        committees = {"advisory": [{"name": "คนางค์ ก", "role": ""},
                                    {"name": "ธเนศ ข", "role": ""}]}
        pages = ["คณะกรรมการที่ปรึกษาวิทยานิพนธ์: คนางค์ ก, ปร.ด., สมชาย ใจดี, พย.ด.\nบทคัดย่อ"]
        rep = self._run(committees, [], [0], pages)
        self.assertEqual(self._reds(rep), [])
        self.assertEqual(rep.zones["ORANGE"], [])

    def test_missing_person_is_caught_by_the_count(self):
        committees = {"advisory": [{"name": "คนางค์ ก", "role": ""},
                                    {"name": "ธเนศ ข", "role": ""}]}
        pages = ["คณะกรรมการที่ปรึกษาวิทยานิพนธ์: คนางค์ ก, ปร.ด.\nบทคัดย่อ"]
        rep = self._run(committees, [], [0], pages)
        # จำนวนไม่ตรง = แดง ตั้งแต่ ก.ย. 2569 (เดิมขาดเป็นส้ม)
        found = [i["found"] for i in rep.zones["RED"]]
        self.assertTrue(any("หน้านี้มี 1 ชื่อ แต่อนุมัติไว้ 2 ชื่อ" in f for f in found),
                        found)
        # ต้องบอกด้วยว่าควรมีชื่ออะไรบ้าง (ดึงจากฟอร์มต้นทาง)
        expected = [i["expected"] for i in rep.zones["RED"]]
        self.assertTrue(any("ธเนศ ข" in e for e in expected), expected)

    def test_committee_list_is_printed_for_staff_to_check_by_eye(self):
        committees = {"advisory": [{"name": "คนางค์ ก", "role": ""}]}
        pages = ["คณะกรรมการที่ปรึกษาวิทยานิพนธ์: คนางค์ ก, ปร.ด.\nบทคัดย่อ"]
        rep = self._run(committees, [], [0], pages)
        why = " ".join(h["why"] for h in rep.human_checklist)
        self.assertIn("นับจำนวนอาจารย์เทียบกับ บฑ.1 ให้แล้ว แต่ไม่ได้เทียบชื่อ", why)
        self.assertIn("คนางค์ ก", why)

    def test_an_english_page_is_treated_exactly_like_a_thai_one(self):
        """ไม่เทียบชื่อแล้ว ภาษาของหน้าจึงไม่มีผล — ชื่อไทยกับอังกฤษนับเหมือนกัน"""
        committees = {"advisory": [{"name": "นริศรา จันทราทิตย์", "role": ""}]}
        pages = ["THESIS ADVISORY COMMITTEE: NARISARA CHANTRATITA, Ph.D.\nABSTRACT"]
        rep = self._run(committees, [0], [], pages)
        self.assertEqual(self._reds(rep), [])
        self.assertEqual(rep.zones["ORANGE"], [])
        why = " ".join(h["why"] for h in rep.human_checklist)
        self.assertIn("นับจำนวนอาจารย์เทียบกับ บฑ.1 ให้แล้ว แต่ไม่ได้เทียบชื่อ", why)

    def test_format_rules_run_without_ethesis_data(self):
        # กฎรูปแบบเป็นกฎของ template ล้วน ต้องตรวจได้แม้เจ้าหน้าที่ไม่ได้อัปโหลด eThesis
        pages = ["THESIS ADVISORY COMMITTEE: Assoc. Prof. Narisara Chantratita, "
                 "Ph.D. (Microbiology)\nABSTRACT"]
        reds = self._reds(self._run({}, [0], [], pages))
        self.assertTrue(any("วงเล็บ" in r for r in reds))
        self.assertTrue(any("ตำแหน่งทางวิชาการ" in r for r in reds))
        self.assertTrue(any("ตัวพิมพ์ใหญ่" in r for r in reds))

    def test_name_order_not_compared_without_ethesis_data(self):
        # ไม่มีข้อมูลอนุมัติ = เทียบชื่อ/ลำดับไม่ได้ ต้องไม่เดาว่าขาดหรือเกิน
        pages = ["THESIS ADVISORY COMMITTEE: NARISARA CHANTRATITA, Ph.D.\nABSTRACT"]
        reds = self._reds(self._run({}, [0], [], pages))
        self.assertEqual(reds, [])


class CommitteeTitleAnywhereTests(unittest.TestCase):
    """ตำแหน่งวิชาการจะเขียนหน้าหรือท้ายชื่อก็ได้ ต้องไม่ทำให้เทียบชื่อไม่ตรง"""

    def test_trailing_rank_is_ignored(self):
        # รูปแบบที่พบในเล่มจริง: "ธเนศ เกษศิลป์, ผู้ช่วยศาสตราจารย์"
        self.assertEqual(_strip_committee_title("ธเนศ เกษศิลป์, ผู้ช่วยศาสตราจารย์"),
                         "ธเนศ เกษศิลป์")

    def test_leading_and_trailing_rank_together(self):
        self.assertEqual(
            _strip_committee_title("รองศาสตราจารย์ ดร. คนางค์ คันธมธุรพจน์, ศาสตราจารย์"),
            "คนางค์ คันธมธุรพจน์")

    def test_name_ending_with_title_letter_is_kept(self):
        # "ธเนศ"/"ศศิธร" ต้องไม่ถูกกินเพราะตัวย่อ ศ. บังคับต้องมีจุด
        self.assertEqual(_strip_committee_title("ธเนศ เกษศิลป์"), "ธเนศ เกษศิลป์")
        self.assertEqual(_strip_committee_title("ศศิธร วงศ์ไทย"), "ศศิธร วงศ์ไทย")

    def test_title_only_cell_is_treated_as_empty(self):
        # ช่องที่อ่านได้แต่ตำแหน่ง ไม่มีชื่อคน = ช่องว่าง ไม่ใช่ "คนที่ไม่อยู่ในรายชื่อ"
        self.assertIsNone(_sig_clean_name(", รองศาสตราจารย์"))
        self.assertIsNone(_sig_clean_name("ผู้ช่วยศาสตราจารย์"))


class SignaturePageKindTests(unittest.TestCase):
    """หน้าลงนามหน้าไหนเป็นของใคร — ยึดเลขหน้าก่อน (i/ก = ที่ปรึกษา, ii/ข = กรรมการสอบ)"""

    def test_page_label_decides(self):
        self.assertEqual(signature_page_kind("i", ""), "advisory")
        self.assertEqual(signature_page_kind("ii", ""), "exam")
        self.assertEqual(signature_page_kind("ก", ""), "advisory")
        self.assertEqual(signature_page_kind("ข", ""), "exam")

    def test_label_wins_over_page_text(self):
        # เลขหน้าเป็นตัวตัดสินหลักตามกติกาเจ้าหน้าที่
        self.assertEqual(signature_page_kind("i", "Thesis Examination Committees"), "advisory")

    def test_falls_back_to_heading_when_label_unusable(self):
        self.assertEqual(signature_page_kind("", "Thesis Examination Committees"), "exam")
        self.assertEqual(signature_page_kind("ค", "คณะกรรมการที่ปรึกษาวิทยานิพนธ์"), "advisory")
        self.assertEqual(signature_page_kind("iii", "Thesis Advisory Committees"), "advisory")


class SignatureInstitutionCellTests(unittest.TestCase):
    """ช่องล่างขวาของหน้าลงนามคนละบทบาทกัน — ที่ปรึกษา=ประธานหลักสูตร, สอบ=คณบดีคณะ"""

    APPROVED = {"degree_cover_th": "ศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)",
                "faculty": "คณะสังคมศาสตร์และมนุษยศาสตร์"}

    def _run(self, kind, bottom, english=False):
        rep = Report()
        _check_signature_institution(rep, kind, bottom, self.APPROVED, english)
        return [i["found"] for i in rep.zones["ORANGE"]]

    def test_advisory_page_wants_program_subject(self):
        ok = self._run("advisory",
                       "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล ประธานหลักสูตร "
                       "ศิลปศาสตรมหาบัณฑิต สาขาวิชาสังคมศาสตร์สิ่งแวดล้อม")
        self.assertEqual(ok, [])
        bad = self._run("advisory", "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล ประธานหลักสูตร")
        self.assertTrue(any("ไม่พบชื่อสาขา" in b for b in bad))

    def test_exam_page_wants_faculty_not_subject(self):
        # หน้ากรรมการสอบมีแต่ชื่อคณะ ไม่มีชื่อสาขา — ต้องไม่ฟ้อง (เดิมฟ้องผิดทุกเล่ม)
        self.assertEqual(
            self._run("exam", "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล คณบดี "
                              "คณะสังคมศาสตร์และมนุษยศาสตร์ มหาวิทยาลัยมหิดล"),
            [])
        bad = self._run("exam", "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล คณบดี คณะอื่น")
        self.assertTrue(any("ไม่พบชื่อคณะ" in b for b in bad))

    def test_english_book_skips_faculty_compare(self):
        # ชื่อคณะจาก eThesis เป็นภาษาไทย เทียบกับหน้าลงนามอังกฤษไม่ได้
        self.assertEqual(self._run("exam", "Dean Faculty of Engineering", english=True), [])

    def test_near_miss_says_what_the_document_actually_printed(self):
        """เล่มที่ 1 พิมพ์สาขาตกตัวอักษรไปตัวเดียว

        เดิมฟ้องว่า "ไม่พบชื่อสาขา ..." เจ้าหน้าที่กวาดตาเห็นข้อความอยู่บนหน้ากระดาษ
        เลยนึกว่าระบบอ่านไม่เจอ ทั้งที่ผลตรวจถูก — ต้องบอกว่าเล่มเขียนว่าอะไรและต่างตรงไหน
        """
        bad = self._run("advisory",
                        "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล ประธานหลักสูตร "
                        "ศิลปศาสตรมหาบัณฑิต สาขาวิชาสังคมศาสตร์สิ่งแวดล้ม")
        self.assertEqual(len(bad), 1)
        self.assertIn("เขียนว่า", bad[0])
        self.assertIn("สังคมศาสตร์สิ่งแวดล้ม", bad[0])       # ค่าที่พบจริงในเล่ม
        self.assertNotIn("ไม่พบ", bad[0])

    def test_completely_absent_subject_still_says_not_found(self):
        """ไม่มีอะไรใกล้เคียงเลย ต้องคงข้อความ "ไม่พบ" ไว้ ไม่ใช่เดาสุ่มมาโชว์"""
        bad = self._run("advisory", "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล ประธานหลักสูตร")
        self.assertEqual(len(bad), 1)
        self.assertIn("ไม่พบชื่อสาขา", bad[0])


class SignaturePlaceholderTests(unittest.TestCase):
    """ข้อความตัวอย่างของ template ที่ถมขาวไว้ = ปกติ / ที่ยังมองเห็น = ต้องแจ้ง"""

    class _Page:
        def __init__(self, words):
            self._words = words

        def extract_words(self, *a, **k):
            return self._words

    def test_white_filled_placeholder_is_not_reported(self):
        # เล่มจริงทั้ง 3 เล่มถมขาวไว้แบบนี้ ถ้าฟ้องจะกลายเป็น noise ทุกเล่ม
        page = self._Page([
            {"text": "ตำแหน่งทางวิชาการและชื่อ", "non_stroking_color": (1, 1, 1)},
            {"text": "นามสกุล", "non_stroking_color": (1, 1, 1)},
        ])
        self.assertEqual(sig_visible_placeholders(page), [])

    def test_visible_placeholder_is_reported(self):
        page = self._Page([
            {"text": "ตำแหน่งทางวิชาการและชื่อ", "non_stroking_color": (0, 0, 0)},
        ])
        found = sig_visible_placeholders(page)
        self.assertTrue(found)
        rep = Report()
        _report_sig_placeholders(rep, found, "หน้าลงนาม")
        self.assertEqual(rep.zones["RED"], [])
        self.assertTrue(any("template" in i["found"] for i in rep.zones["ORANGE"]))

    def test_the_fix_says_paint_it_white_not_delete_it(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569) — กรอบของ template ต้องคงไว้ตามที่ set มา
        ช่องที่ไม่มีชื่อให้ถมขาว ของเดิมบอกให้ "ลบข้อความตัวอย่างออกจากไฟล์"
        ซึ่งสวนทางกับถ้อยคำของเจ้าหน้าที่เองใน STAFF_CHECKS
        """
        rep = Report()
        _report_sig_placeholders(rep, ["Academic rank First Name Last name"],
                                 "หน้าลงนาม 1 (หน้า i)")
        issue = rep.zones["ORANGE"][0]
        for line in (issue["expected"], issue["fix"]):
            self.assertIn("สีขาว", line)
        self.assertNotIn("ลบข้อความตัวอย่างออกจากไฟล์", issue["expected"])
        self.assertNotIn("ให้ลบออกจากช่อง", issue["fix"])

    def test_the_line_in_the_summary_stops_at_white(self):
        """เจ้าหน้าที่สั่งว่าแค่นี้พอ (ก.ย. 2569) — เคยต่อท้ายว่า "ไม่ใช่ลบบรรทัดออก"
        แล้วถูกสั่งให้ตัดออก บรรทัดนี้อยู่ในข้อความที่คัดลอกส่งนักศึกษา
        """
        rep = Report()
        _report_sig_placeholders(rep, ["Degree (Subject)"], "หน้าลงนาม 1 (หน้า i)")
        self.assertEqual(rep.zones["ORANGE"][0]["expected"],
                         "ช่องกรรมการที่ไม่ได้ใช้ต้องเปลี่ยนสีตัวอักษรเป็นสีขาว")

    def test_a_thai_book_gets_the_same_wording(self):
        """เล่มไทยเจอข้อความตัวอย่างคนละชุดกับเล่มอังกฤษ แต่วิธีแก้อันเดียวกัน

        เล่มไทย  "ตำแหน่งทางวิชาการและชื่อ นามสกุล" / "คุณวุฒิ (ระบุสาขาวิชา)"
        เล่มอังกฤษ "Academic rank First Name Last name" / "Degree (Subject)"
        """
        pairs = (["ตำแหน่งทางวิชาการและชื่อ นามสกุล", "คุณวุฒิ (ระบุสาขาวิชา)"],
                 ["Academic rank First Name Last name", "Degree (Subject)"])
        seen = []
        for found in pairs:
            rep = Report()
            _report_sig_placeholders(rep, found, "หน้าลงนาม 1")
            issue = rep.zones["ORANGE"][0]
            for label in found:                 # ต้องยกข้อความที่เจอมาให้ครบ
                self.assertIn(label, issue["found"])
            seen.append((issue["expected"], issue["fix"]))
        self.assertEqual(seen[0], seen[1])
        # ข้อความตัวอย่างทั้งสี่แบบต้องอยู่ในทะเบียนเดียวกัน ไม่ใช่รู้จักแค่ฝั่งอังกฤษ
        labels = [label for _key, label in checker_module._SIG_LEFTOVER_PLACEHOLDERS]
        for label in pairs[0] + pairs[1]:
            self.assertIn(label, labels)

    def test_the_fix_matches_the_wording_staff_already_use(self):
        """ควบคุมเชิงลบของข้อบน — ต้องพูดเรื่องเดียวกับถ้อยคำที่เจ้าหน้าที่เขียนไว้เอง
        ในหัวข้อ "โครงสร้างหน้าลงนาม" ไม่ใช่คิดคำใหม่ที่ขัดกันเอง
        """
        staff = checker_module.STAFF_CHOICE_BY_ID["SIGNATURE_LAYOUT_WRONG"][1]["text"]
        self.assertIn("เปลี่ยนสีตัวอักษรเป็นสีขาว", staff)
        rep = Report()
        _report_sig_placeholders(rep, ["Degree (Subject)"], "หน้าลงนาม 2 (หน้า ii)")
        self.assertIn("เปลี่ยนสีตัวอักษรเป็นสีขาว", rep.zones["ORANGE"][0]["expected"])

    def test_white_detection_across_colour_spaces(self):
        self.assertTrue(_is_white_fill((1,)))          # grayscale
        self.assertTrue(_is_white_fill((1, 1, 1)))     # RGB
        self.assertTrue(_is_white_fill((0, 0, 0, 0)))  # CMYK
        self.assertFalse(_is_white_fill((0, 0, 0)))
        self.assertFalse(_is_white_fill(None))


class TheDegreeLineMustNotCarryExtraWords(unittest.TestCase):
    """ชื่อปริญญาต้องตรงเป๊ะ ห้ามมีคำเกิน (เจ้าหน้าที่สั่ง ก.ย. 2569)

    เล่มจริงที่ทำให้ออกกฎนี้
        หน้าปก    "DOCTOR OF PUBLIC HEALTH (INTERNATIONAL PROGRAM)"
        บทคัดย่อ  "Dr.PH (PUBLIC HEALTH)"   ข้อมูลอนุมัติคือ "Dr. P.H."
    ของเดิมผ่านทั้งคู่ เพราะถามแค่ว่า "ข้อความที่อนุมัติอยู่บนหน้านี้ไหม"
    """

    COVER = NEWLINE.join([
        "A THESIS SUBMITTED IN PARTIAL FULFILLMENT",
        "OF THE REQUIREMENTS FOR THE DEGREE OF",
        "DOCTOR OF PUBLIC HEALTH (INTERNATIONAL PROGRAM)",
        "FACULTY OF GRADUATE STUDIES",
    ])

    def test_extra_words_on_the_cover_are_returned(self):
        self.assertEqual(
            checker_module.degree_line_extras(self.COVER, "DOCTOR OF PUBLIC HEALTH"),
            "DOCTOR OF PUBLIC HEALTH (INTERNATIONAL PROGRAM)")

    def test_a_clean_line_returns_nothing(self):
        """ควบคุมเชิงบวก — บรรทัดที่ตรงพอดีต้องเงียบ"""
        page = self.COVER.replace(" (INTERNATIONAL PROGRAM)", "")
        self.assertEqual(
            checker_module.degree_line_extras(page, "DOCTOR OF PUBLIC HEALTH"), "")

    def test_spacing_alone_is_not_extra_text(self):
        """เว้นวรรคต่างกันยอมรับได้ (เจ้าหน้าที่สั่ง) — ยังต้องคืน "" """
        self.assertEqual(
            checker_module.degree_line_extras("Dr.PH", "Dr. P.H."), "")

    def test_spacing_plus_extra_words_is_still_extra(self):
        """เคสของเล่มจริง — ต่างวรรคตอน *และ* มีคำเกิน ต้องจับได้"""
        page = NEWLINE.join(["NGUYEN THI NGA 6637019 PHPH/D",
                             "Dr.PH (PUBLIC HEALTH)",
                             "THESIS ADVISORY COMMITTEE: SUPA PENGPID, Dr.PH,"])
        self.assertEqual(checker_module.degree_line_extras(page, "Dr. P.H."),
                         "Dr.PH (PUBLIC HEALTH)")

    def test_the_shortest_matching_line_wins(self):
        """ควบคุมเชิงลบของข้อบน — บรรทัดรายชื่อกรรมการก็มีชื่อปริญญาอยู่

        ถ้าเลือกบรรทัดแรกที่เจอ จะไปยกบรรทัดกรรมการมาอ้างว่าเป็นบรรทัดชื่อปริญญา
        """
        page = NEWLINE.join(["THESIS ADVISORY COMMITTEE: SUPA PENGPID, Dr.PH, X, Y",
                             "Dr.PH (PUBLIC HEALTH)"])
        self.assertEqual(checker_module.degree_line_extras(page, "Dr. P.H."),
                         "Dr.PH (PUBLIC HEALTH)")

    def test_the_thai_template_prefix_is_not_extra(self):
        """หน้าปกเล่มไทยขึ้นบรรทัดว่า "ปริญญา<ชื่อปริญญา>" ตาม template

        ข้อมูลอนุมัติเก็บไว้แค่ชื่อปริญญา ถ้าไม่ยกเว้นคำนี้ เล่มไทยที่ถูกต้องจะโดนฟ้อง
        ทุกเล่ม (เจอตอนวัดกับเล่มทดสอบ 3)
        """
        page = NEWLINE.join(["วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
                             "ปริญญาศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)",
                             "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล"])
        self.assertEqual(
            checker_module.degree_line_extras(
                page, "ศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)"), "")

    def test_the_thai_prefix_does_not_hide_real_extras(self):
        """ควบคุมเชิงลบ — ยกเว้นแค่คำว่า "ปริญญา" ไม่ใช่ยกเว้นทั้งบรรทัด"""
        page = "ปริญญาศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม) (ภาคพิเศษ)"
        self.assertTrue(checker_module.degree_line_extras(
            page, "ศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)"))

    def test_both_call_sites_actually_use_the_helper(self):
        """ล็อกการต่อสาย ไม่ใช่ล็อกแค่ตัว helper

        เทสต์ชุดนี้รอบแรกเรียก degree_line_extras ตรง ๆ อย่างเดียว พอลองปิดการต่อสาย
        ใน run_check เทสต์ผ่านหมดโดยที่กฎไม่ทำงานเลย
        """
        source = inspect.getsource(checker_module.run_check)
        self.assertIn(
            "extras = degree_line_extras(spot_text, expected_degree) if own_line",
            source)
        self.assertIn("extras = degree_line_extras(abstract_text, abbr)", source)
        for marker in ('บรรทัดชื่อปริญญามีข้อความเกิน',
                       'บรรทัดชื่อปริญญาแบบย่อมีข้อความเกิน'):
            block = source.split(marker, 1)[0][-260:]
            self.assertIn('rep.add("RED", "front_matter"', block, marker)

    def test_the_signature_page_is_left_alone(self):
        """หน้าลงนามวางชื่อปริญญาไว้กลางประโยค template จึงห้ามตรวจคำเกิน"""
        source = inspect.getsource(checker_module.run_check)
        block = source.split("degree_spots = []", 1)[1][:900]
        self.assertIn('("หน้าปก", cover_text, cover_degree, True)', block)
        self.assertIn("sig_degree, False", block)


class ChapterHeadingsMustUseArabicNumerals(unittest.TestCase):
    """หัวบทต้องใช้เลขอารบิก (เจ้าหน้าที่สั่ง ก.ย. 2569) แดง ฟ้องรายบท

    ระบบรู้จักเลขโรมันไว้เพื่อหาบทให้เจออยู่แล้ว แต่ไม่เคยฟ้อง เล่มจริงเล่มหนึ่งพิมพ์
    CHAPTER I, II, III, IV, 5, VI คือปนกันเองด้วยซ้ำ แล้วรายงานเงียบสนิท
    """

    def test_arabic_headings_pass(self):
        for line in ("CHAPTER 2", "บทที่ 2", "CHAPTER 12"):
            self.assertTrue(checker_module.chapter_number_is_arabic(line), line)

    def test_roman_and_thai_numerals_fail(self):
        """เล่มไทยใช้เลขอารบิก ไม่ใช่เลขไทย (เจ้าหน้าที่ยืนยัน ก.ย. 2569)"""
        for line in ("CHAPTER II", "CHAPTER IV", "บทที่ ๒", "บทที่ ๑๐"):
            self.assertFalse(checker_module.chapter_number_is_arabic(line), line)

    def test_the_rule_is_wired_in_per_chapter(self):
        """ฟ้องรายบท ไม่รวมเป็นข้อเดียว — แต่ละบทไปแก้คนละหน้า"""
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("if not chapter_number_is_arabic(l):", source)
        block = source.split("for _cn, _head, _idx in non_arabic_heads:", 1)[1][:420]
        self.assertIn('rep.add("RED", "body", f"บทที่ {_cn} ({page_ref(_idx)})"', block)
        self.assertIn("BODY.CHAPTER_NUMBER", block)

    def test_the_rule_is_in_the_catalog(self):
        self.assertIn("BODY.CHAPTER_NUMBER", RULE_CATALOG)
        self.assertNotIn("failure_zone", RULE_CATALOG["BODY.CHAPTER_NUMBER"])


class TooManyCommitteeNamesIsRed(unittest.TestCase):
    """ขาด = ส้ม · เกิน = แดง (เจ้าหน้าที่สั่ง ก.ย. 2569)

    ขาดเป็นส้มเพราะระบบอาจอ่านบางช่องไม่ออก แต่เกินฟันธงได้ — การอ่านไม่ครบทำให้ได้
    ชื่อน้อยกว่า ไม่มีทางทำให้ได้มากกว่าที่พิมพ์ไว้จริง เล่มจริงพิมพ์กรรมการซ้ำสองคน
    คนละสองครั้ง จึงได้ 7 ชื่อจากที่อนุมัติไว้ 5
    """

    APPROVED = [{"name": "BRIAN EDUARD VAN WYK"}, {"name": "SUPA PENGPID"},
                {"name": "KARL PELTZER"}]

    def _run(self, count):
        rep = checker_module.Report()
        checker_module._report_committee_count(
            rep, self.APPROVED, [f"name {k}" for k in range(count)],
            "หน้าลงนาม 2 (หน้า ii)", "บฑ.2", "FRONT.COMMITTEE")
        return rep

    def test_too_many_names_is_red(self):
        rep = self._run(5)
        self.assertEqual(len(rep.zones["RED"]), 1)
        self.assertIn("รายชื่อเกิน", rep.zones["RED"][0]["found"])
        self.assertIn("ลบรายชื่อที่เกินออก", rep.zones["RED"][0]["fix"])

    def test_too_few_names_is_red_too(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าไม่ครบก็ต้องแจ้งสีแดง — เดิมเป็นส้ม

        ยอมรับความเสี่ยงแล้วว่าถ้าระบบอ่านตารางไม่ครบจะฟ้องแดงทั้งที่เล่มถูก
        เจ้าหน้าที่จึงต้องเปิดหน้านั้นดูก่อนส่งกลับ
        """
        rep = self._run(2)
        self.assertEqual(len(rep.zones["RED"]), 1)
        self.assertEqual(rep.zones["ORANGE"], [])
        self.assertIn("เพิ่มรายชื่อที่ขาด", rep.zones["RED"][0]["fix"])

    def test_the_right_count_says_nothing(self):
        rep = self._run(3)
        self.assertEqual(rep.zones["RED"] + rep.zones["ORANGE"], [])


class ThePageSequenceFindingIsOrange(unittest.TestCase):
    """เลขหน้าส่วนนำที่เรียงไม่ต่อเนื่อง = สีส้ม (เจ้าหน้าที่สั่ง ก.ย. 2569)

    เจ้าหน้าที่ส่งภาพข้อที่ฟ้องว่า กระโดดจาก "v" ไป "vii" มาแล้วสั่งว่าขอเป็นสีส้ม
    ข้ออื่นของเลขหน้ายังเป็นแดงเหมือนเดิม จึงต้องแยกรหัสกฎ ไม่ใช่เปลี่ยนทั้ง
    PAGE.NUMBERING ซึ่งคุมชนิดเลขหน้าและเลขหน้าเนื้อหาอยู่ด้วย
    """

    def test_the_zone_comes_from_the_rule_catalog(self):
        self.assertEqual(RULE_CATALOG["PAGE.NUMBERING_SEQUENCE"]["failure_zone"],
                         "ORANGE")
        self.assertEqual(checker_module.PAGE_SEQUENCE_ZONE, "ORANGE")

    def test_the_finding_uses_that_zone(self):
        source = inspect.getsource(checker_module)
        block = source.split('"เลขหน้าไม่ต่อเนื่อง: " + " และ "', 1)[0][-260:]
        self.assertIn("rep.add(PAGE_SEQUENCE_ZONE", block)
        self.assertNotIn('rep.add("RED", "front_matter", "ส่วนนำ",' + NEWLINE
                         + '                    "เลขหน้าไม่ต่อเนื่อง', source)

    def test_the_body_page_sequence_is_orange_too(self):
        """เจ้าหน้าที่สั่งเพิ่ม (ก.ย. 2569) ว่าเลขหน้าเนื้อหาที่กระโดดก็เป็นสีส้ม
        เหมือนส่วนนำ — เป็นเรื่องเดียวกันคือความต่อเนื่องของเลขหน้า
        """
        source = inspect.getsource(checker_module)
        before = source.split(
            'f"เลขหน้าไม่ต่อเนื่อง หน้าก่อนหน้านี้พิมพ์เลข {a}"', 1)[0][-200:]
        self.assertIn("rep.add(PAGE_SEQUENCE_ZONE", before)

    def test_the_other_page_number_rules_stay_red(self):
        """ควบคุมเชิงลบ — ห้ามลากข้ออื่นของเลขหน้าเป็นสีส้มไปด้วย

        ชนิดเลขหน้าผิด (ก ข ค ปนกับ i ii iii) และเลขหน้าอารบิกที่ไม่เริ่มที่บทที่ 1
        ยังเป็นสีแดง เจ้าหน้าที่สั่งเฉพาะเรื่อง "ความต่อเนื่อง"
        """
        self.assertNotIn("failure_zone", RULE_CATALOG["PAGE.NUMBERING"])
        source = inspect.getsource(checker_module)
        for phrase in ('f"มีเลขหน้าเป็น{found_names} ',
                       '"เลขหน้าอารบิกต้องเริ่มที่ 1 ณ บทที่ 1"'):
            before = source.split(phrase, 1)[0][-220:]
            self.assertIn('rep.add("RED"', before, phrase)


class FrontPageNumberTests(unittest.TestCase):
    """เลขหน้าส่วนนำ: เล่มอังกฤษ=โรมัน เล่มไทย=พยัญชนะ และต้องเรียงต่อเนื่อง"""

    def _run(self, labels, style=None, start=1, stop=None, texts=None):
        rep = Report()
        page_labels = {i: lab for i, lab in enumerate(labels) if lab}
        _check_front_page_numbers(
            rep, page_labels,
            lambda i: (f"หน้า {page_labels[i]}" if page_labels.get(i)
                       else f"หน้าไม่ระบุเลข (แผ่นที่ {i + 1} ของไฟล์)"),
            start, len(labels) if stop is None else stop, style, page_texts=texts)
        return rep

    def test_label_order_by_style(self):
        self.assertEqual(_page_label_order("iii"), ("roman", 3))
        self.assertEqual(_page_label_order("ค"), ("thai", 3))
        self.assertEqual(_page_label_order("7"), ("arabic", 7))
        self.assertEqual(_page_label_order(""), (None, None))

    def test_expected_style_by_program_language(self):
        self.assertEqual(_expected_front_label_style("thai"), "thai")
        self.assertEqual(_expected_front_label_style("international"), "roman")
        # เล่ม thai_english ใช้ปก/หน้าลงนามอังกฤษ จึงเป็นเล่มอังกฤษ (ยืนยันจากเล่มจริง)
        self.assertEqual(_expected_front_label_style("thai_english"), "roman")
        self.assertIsNone(_expected_front_label_style(""))

    def test_english_book_roman_passes(self):
        rep = self._run(["", "i", "ii", "iii", "iv", "v"], style="roman")
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(rep.zones["ORANGE"], [])

    def test_thai_book_thai_letters_pass(self):
        rep = self._run(["", "ก", "ข", "ค", "ง", "จ"], style="thai")
        self.assertEqual(rep.zones["RED"], [])

    def test_thai_book_using_roman_is_flagged(self):
        rep = self._run(["", "i", "ii", "iii"], style="thai")
        reds = [i for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn("เลขโรมัน", reds[0]["found"])
        self.assertIn("เล่มหลักสูตรไทย", reds[0]["expected"])
        self.assertIn("พยัญชนะไทย", reds[0]["expected"])

    def test_english_book_using_thai_letters_is_flagged(self):
        rep = self._run(["", "ก", "ข", "ค"], style="roman")
        reds = [i for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn("พยัญชนะไทย", reds[0]["found"])
        self.assertIn("เล่มภาษาอังกฤษ", reds[0]["expected"])

    def test_duplicate_labels_reported_once(self):
        # เล่มจริง (ไทย) ที่พบ: ค, ค, ค, ง, จ — ต้องรวมเป็นข้อความเดียว ไม่ฟ้องทีละคู่
        rep = self._run(["", "ค", "ค", "ค", "ง", "จ"], style="thai")
        # ข้อความต่อเนื่องของเลขหน้าส่วนนำเป็นสีส้ม (เจ้าหน้าที่สั่ง ก.ย. 2569)
        found = [i["found"] for i in rep.zones[checker_module.PAGE_SEQUENCE_ZONE]]
        self.assertEqual(len(found), 1)
        self.assertIn('ถูกใช้ซ้ำ 3 หน้า', found[0])

    def test_skipped_label_reported(self):
        rep = self._run(["", "i", "ii", "v", "vi"], style="roman")
        found = [i["found"] for i in rep.zones[checker_module.PAGE_SEQUENCE_ZONE]]
        self.assertEqual(len(found), 1)
        self.assertIn('กระโดดจาก "ii" ไป "v"', found[0])

    def test_arabic_in_front_matter_flagged(self):
        rep = self._run(["", "i", "ii", "3", "4"], style="roman")
        reds = [i["found"] for i in rep.zones["RED"]]
        self.assertTrue(any("เลขอารบิก" in r for r in reds))

    def test_arabic_flagged_even_without_program_language(self):
        # ไม่รู้ภาษาเล่ม แต่อารบิกในส่วนนำผิดแน่นอน
        rep = self._run(["", "1", "2", "3"])
        self.assertTrue(any("เลขอารบิก" in i["found"] for i in rep.zones["RED"]))

    def test_mixed_styles_flagged_without_program_language(self):
        rep = self._run(["", "i", "ii", "ค"])
        reds = [i for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn("พยัญชนะไทย", reds[0]["found"])

    def test_unreadable_label_is_orange_not_red(self):
        """หน้าที่ดึงข้อความไม่ได้เลย = หน้าภาพ/สแกน ระบบไม่รู้ว่าพิมพ์เลขไว้ไหม"""
        rep = self._run(["", "i", "ii", "", "iv"], style="roman",
                        texts=["ปก", "หน้า i", "หน้า ii", "", "หน้า iv"])
        self.assertEqual(rep.zones["RED"], [])
        self.assertTrue(any("ระบบอ่านเลขหน้าไม่ได้" in i["found"]
                            for i in rep.zones["ORANGE"]))

    def test_page_with_text_but_no_number_is_a_defect(self):
        """หน้าที่มีข้อความแต่ไม่มีบรรทัดเลขหน้า = เล่มไม่ได้ใส่เลขหน้ามาจริง ฟันธงได้

        เจ้าหน้าที่สั่งว่า "อย่าทำให้การตรวจเพี้ยนเพราะหาเลขหน้าไม่เจอ ให้บอกว่า
        เลขหน้าผิด และดำเนินการตามกฎ"
        """
        rep = self._run(["", "i", "ii", "", "iv"], style="roman",
                        texts=["ปก", "หน้า i", "หน้า ii", "มีเนื้อความแต่ไม่มีเลขหน้า", "หน้า iv"])
        reds = [i["found"] for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn("ไม่ได้พิมพ์เลขหน้าไว้", reds[0])
        # ต้องบอกแผ่นที่ในไฟล์ ไม่งั้นเจ้าหน้าที่เปิดไปดูหน้านั้นไม่ถูก
        self.assertIn("แผ่นที่ 4 ของไฟล์", reds[0])

    def test_unreadable_page_does_not_silence_the_sequence_check(self):
        """หน้าที่คั่นอยู่ยังนับเป็นหนึ่งหน้า จึงยังฟันธงความต่อเนื่องได้

        เดิมเจอหน้าที่อ่านเลขไม่ได้แล้ว "ข้ามไปเลย" ทั้งช่วง เล่มที่เลขหน้าผิดจริง
        เลยรอดไปได้ทั้งที่ควรฟ้อง
        """
        # i, (อ่านไม่ออก), iii -> หน้าที่คั่นคือ ii พอดี ไม่ใช่การกระโดด
        ok = self._run(["", "i", "", "iii", "iv"], style="roman",
                       texts=["ปก", "หน้า i", "", "หน้า iii", "หน้า iv"])
        # กรองเฉพาะข้อความต่อเนื่อง — โซนส้มมีข้อ "อ่านเลขหน้าไม่ได้" ของหน้าที่คั่นอยู่ด้วย
        self.assertEqual([i["found"] for i in ok.zones[checker_module.PAGE_SEQUENCE_ZONE]
                          if "เลขหน้าไม่ต่อเนื่อง" in i["found"]], [])
        # i, (อ่านไม่ออก), v -> ต่อให้หน้าที่คั่นเป็น ii ก็ยังข้ามจาก ii ไป v อยู่ดี
        bad = self._run(["", "i", "", "v", "vi"], style="roman",
                        texts=["ปก", "หน้า i", "", "หน้า v", "หน้า vi"])
        self.assertTrue(any('กระโดดจาก "i" ไป "v"' in i["found"]
                            for i in bad.zones[checker_module.PAGE_SEQUENCE_ZONE]))

    def test_skipped_when_body_start_unknown(self):
        # ไม่รู้ว่าเนื้อหาเริ่มหน้าไหน = ไม่เดาขอบเขตส่วนนำ
        rep = Report()
        _check_front_page_numbers(rep, {1: "i"}, lambda i: "หน้า i", 1, None, "roman")
        self.assertEqual(rep.zones["RED"], [])


class ExamDatePerSignaturePageTests(unittest.TestCase):
    """วันที่สอบต้องตรวจแยกทีละหน้าลงนาม ไม่ใช่รวมข้อความสองหน้าแล้วค้นครั้งเดียว"""

    def _run(self, pages_text, sig_pages=(1, 2), exam_date="5 พฤษภาคม 2569"):
        rep = Report()
        _check_exam_date(rep, exam_date, list(sig_pages), pages_text,
                         lambda i: f"หน้า {i}")
        return rep

    def test_correct_date_on_both_pages_passes(self):
        page = "วันที่ 5 พฤษภาคม พ.ศ. 2569"
        rep = self._run(["ปก", page, page])
        self.assertEqual(rep.zones["RED"], [])
        statuses = [c["status"] for g in rep.verification for c in g["checks"]]
        self.assertEqual(statuses, ["pass", "pass"])

    def test_wrong_date_on_second_page_is_caught(self):
        rep = self._run(["ปก", "วันที่ 5 พฤษภาคม พ.ศ. 2569",
                         "วันที่ 6 พฤษภาคม พ.ศ. 2569"])
        reds = [i for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn("หน้าลงนาม 2", reds[0]["location"])
        self.assertIn("6 พฤษภาคม", reds[0]["found"])

    def test_missing_date_reports_not_found(self):
        rep = self._run(["ปก", "วันที่ 5 พฤษภาคม พ.ศ. 2569", "ไม่มีวันที่บนหน้านี้"])
        self.assertIn("ไม่พบวันที่สอบ", rep.zones["RED"][0]["found"])

    def test_no_signature_page_is_pending_not_red(self):
        rep = self._run(["ปก"], sig_pages=())
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(rep.verification[0]["checks"][0]["status"], "pending")


class CoverYearLineTests(unittest.TestCase):
    """ปีบนหน้าปกต้องอยู่ในบรรทัดปีของตัวเอง ไม่ใช่พบเลขปีที่ไหนก็ได้บนหน้า"""

    def _run(self, cover, year="2569"):
        rep = Report()
        _check_cover_year(rep, year, cover)
        return rep

    def test_standalone_year_line_passes(self):
        rep = self._run("ชื่อเรื่อง\nบัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล\n2569\nลิขสิทธิ์ฯ")
        self.assertEqual(rep.zones["RED"], [])

    def test_buddhist_era_prefix_allowed(self):
        rep = self._run("ชื่อเรื่อง\nพ.ศ. 2569\nลิขสิทธิ์ฯ")
        self.assertEqual(rep.zones["RED"], [])

    def test_year_only_inside_title_is_flagged(self):
        rep = self._run("การประเมินผลกระทบ พ.ศ. 2569 ของโครงการ\nบัณฑิตวิทยาลัย")
        self.assertIn("ไม่ได้อยู่ในบรรทัดปีของตัวเอง", rep.zones["RED"][0]["found"])

    def test_missing_year_is_flagged(self):
        rep = self._run("ชื่อเรื่อง\nบัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล")
        self.assertIn("ไม่พบปี 2569", rep.zones["RED"][0]["found"])

    def test_english_cover_year(self):
        rep = self._run("A THESIS ...\nMAHIDOL UNIVERSITY\n2026\nCOPYRIGHT", year="2026")
        self.assertEqual(rep.zones["RED"], [])


class HeaderOnlyPageNumberTests(unittest.TestCase):
    """หัวกระดาษส่วนเนื้อหา/ส่วนท้าย ต้องมีเพียงเลขหน้า ไม่มี running head/ชื่อบท"""

    class _Page:
        def __init__(self, height, words):
            self.height = height
            self._words = words

        def extract_words(self, *a, **k):
            return self._words

    def test_header_with_only_page_number_is_clean(self):
        page = self._Page(841.9, [
            {"text": "23", "top": 48.7},          # เลขหน้ามุมบนขวา
            {"text": "CHAPTER", "top": 86.9},      # เนื้อความอยู่ต่ำกว่าแถบหัวกระดาษ
        ])
        self.assertEqual(header_extra_text(page), "")

    def test_running_head_in_header_is_flagged(self):
        page = self._Page(841.9, [
            {"text": "Chapter", "top": 49}, {"text": "3", "top": 49},
            {"text": "Methodology", "top": 49}, {"text": "42", "top": 49},
            {"text": "bodytext", "top": 120},
        ])
        extra = header_extra_text(page)
        self.assertIn("Chapter", extra)
        self.assertIn("Methodology", extra)
        self.assertNotIn("42", extra)   # เลขหน้าไม่นับเป็นข้อความเกิน

    def test_body_text_below_header_band_is_ignored(self):
        page = self._Page(841.9, [{"text": "Introduction", "top": 90}])
        self.assertEqual(header_extra_text(page), "")


class SignatureDateTests(unittest.TestCase):
    """วันที่สอบบนหน้าลงนามที่ไม่ตรงระบบ ต้องแยกจาก "ไม่พบวันที่" และบอกวันที่ถูก"""

    def test_extracts_english_date(self):
        self.assertEqual(
            find_signature_date("was submitted ...\non 26 June 2026\nCommittees"),
            "26 June 2026")

    def test_extracts_thai_date_with_buddhist_era(self):
        self.assertEqual(
            find_signature_date("ปริญญา...\nวันที่ 11 พฤษภาคม พ.ศ. 2569\nคณะกรรมการ"),
            "11 พฤษภาคม พ.ศ. 2569")

    def test_returns_empty_when_no_date(self):
        self.assertEqual(find_signature_date("no date printed on this page"), "")


class PlainSummaryProseTests(unittest.TestCase):
    """สรุปคัดลอกได้ต้องเป็นประโยคภาษาคน ไล่เลขทุกจุด ไม่มี '-'/'→' และรวมรายการซ้ำ"""

    def _report(self, red):
        return {"verdict": "ไม่ผ่าน", "issues_by_zone": {"RED": red}}

    def test_summary_is_numbered_prose_without_symbols(self):
        report = self._report([{
            "part": "front_matter", "location": "สารบัญ (หน้า viii) บทที่ 3",
            "found": 'ชื่อบทในสารบัญพิมพ์ผิดเล็กน้อย (typo, ความใกล้เคียง 0.97): '
                     '"RESEARCH METHODLOGY" ต่างที่ "METHODLOGY" ต้องเป็น "METHODOLOGY"',
            "expected": 'ควรเป็น "RESEARCH METHODOLOGY"', "fix": "แก้การสะกด",
        }])
        text = plain_summary(report)
        # หนึ่งจุด = สามบรรทัด: อยู่หน้าไหน / อะไรผิด / ต้องแก้เป็นอะไร
        self.assertIn("1. สารบัญ (หน้า viii) บทที่ 3\n", text)
        self.assertIn('ต่างที่ "METHODLOGY"', text)
        self.assertIn('ต้องแก้เป็น "RESEARCH METHODOLOGY"', text)
        # ค่าที่ถูกต้องต้องบอกครั้งเดียว ไม่ใช่ทั้งในท่อน "พบ" และท่อน "ต้องแก้เป็น"
        self.assertEqual(text.count("RESEARCH METHODOLOGY"), 1)
        self.assertNotIn('ต้องเป็น "METHODOLOGY"', text)
        # ห้ามมีเครื่องหมายนำรายการหรือลูกศร และไม่หลงเหลือ (typo, ...)
        self.assertNotIn("- ", text)
        self.assertNotIn("→", text)
        self.assertNotIn("typo", text)
        self.assertNotIn("[", text)

    def test_summary_does_not_repeat_the_section_name(self):
        """ข้อที่ตำแหน่งเป็นชื่อส่วนเปล่า ๆ ต้องไม่พิมพ์ชื่อส่วนซ้ำกับหัวข้อกลุ่มบรรทัดบน

        เจ้าหน้าที่ทักว่า "คำมันดูซ้ำซ้อนไหม" จากข้อที่ขึ้นว่า
            ส่วนนำ
            6. ส่วนนำ
               เลขหน้าส่วนนำไม่ต่อเนื่อง: ...
               เลขหน้าส่วนนำต้องเรียงต่อเนื่อง...
        คำว่า "ส่วนนำ" โผล่ 4 ครั้งในข้อเดียว
        """
        report = self._report([{
            "part": "front_matter", "location": "ส่วนนำ",
            "found": 'เลขหน้าไม่ต่อเนื่อง: กระโดดจาก "iv" ไป "iii"',
            "expected": "เลขหน้าต้องเรียงต่อเนื่องทีละหน้า ไม่ซ้ำ ไม่ข้าม", "fix": "",
        }])
        text = plain_summary(report)
        self.assertEqual(text.count("ส่วนนำ"), 1, f"ชื่อส่วนซ้ำ:\n{text}")
        self.assertIn('1. เลขหน้าไม่ต่อเนื่อง', text)

    def test_summary_keeps_the_location_when_it_adds_information(self):
        """ตำแหน่งที่บอกหน้าเจาะจง ต้องยังพิมพ์ไว้ ไม่ใช่ตัดทิ้งเพราะอยู่ในกลุ่มเดียวกัน"""
        report = self._report([{
            "part": "front_matter", "location": "สารบัญ (หน้า viii) บทที่ 6",
            "found": 'ชื่อบทในเล่มเขียนว่า "CONCLUSION AND RECOMMENDATONS"',
            "expected": 'ตามประกาศ 2569 ควรเป็น "CONCLUSION AND RECOMMENDATIONS"', "fix": "",
        }])
        self.assertIn("1. สารบัญ (หน้า viii) บทที่ 6\n", plain_summary(report))

    def test_summary_carries_no_decorative_symbols(self):
        """ข้อความสรุปถูกคัดลอกไปวางใน Word/อีเมล — สัญลักษณ์ตกแต่งกลายเป็นตัวประหลาด

        เจ้าหน้าที่เจอ ' · ' ที่เคยใช้คั่นสองท่อน กลายเป็นรูปโทรศัพท์ตอนวางในโปรแกรมอื่น
        (ฟอนต์ไทยไม่มี glyph นั้น จึงหยิบตัวอื่นมาแทน) จึงห้ามมีสัญลักษณ์พวกนี้เลย
        """
        report = self._report([{
            "part": "front_matter", "location": "บทคัดย่อภาษาอังกฤษ (หน้า iv)",
            "found": 'ชื่อปริญญาแบบย่อในเล่มเขียนว่า "DOCTOR OF PHILOSOPHY"',
            "expected": 'ต้องเป็น "Ph.D. (TROPICAL MEDICINE)" ตามรูปแบบชื่อย่อ',
            "fix": "",
        }])
        text = plain_summary(report)
        for symbol in ("·", "—", "–", "→", "↔", "•", "≤", "✆"):
            self.assertNotIn(symbol, text, f"ยังมีสัญลักษณ์ {symbol} ในข้อความสรุป")

    def test_same_fix_reported_twice_is_merged(self):
        # ชื่อบทเดียวกันในเนื้อหา ถูกรายงานทั้งตอนเทียบสารบัญและเทียบประกาศ = จุดเดียว
        dup = [{
            "part": "body", "location": "บทที่ 2 (หน้า 6)",
            "found": 'ชื่อบทในเนื้อหาพิมพ์ผิดเล็กน้อย: "LITTERATURE REVIEW" '
                     '— ต่างที่ "LITTERATURE" → "LITERATURE"',
            "expected": 'ต้องสะกดตรงกับชื่อบทในสารบัญ: "LITERATURE REVIEW"', "fix": "",
        }, {
            "part": "body", "location": "บทที่ 2 (หน้า 6)",
            "found": 'ชื่อบทในเนื้อหาพิมพ์ผิดเล็กน้อย: "LITTERATURE REVIEW" '
                     '— ต่างที่ "LITTERATURE" → "LITERATURE"',
            "expected": 'ตามประกาศ 2569 ควรเป็น "LITERATURE REVIEW"', "fix": "",
        }]
        text = plain_summary(self._report(dup))
        self.assertIn("ทั้งหมด 1 จุด", text)
        self.assertEqual(text.count("บทที่ 2 (หน้า 6)"), 1)

    def test_same_typo_in_toc_and_body_stays_two_points(self):
        # ตำแหน่งต่างกัน (สารบัญ vs เนื้อหา) แม้ค่าที่ต้องแก้เหมือนกัน = สองจุดจริง
        items = [{
            "part": "front_matter", "location": "สารบัญ (หน้า viii) บทที่ 3",
            "found": 'ชื่อบทในสารบัญพิมพ์ผิดเล็กน้อย: "RESEARCH METHODLOGY"',
            "expected": 'ควรเป็น "RESEARCH METHODOLOGY"', "fix": "",
        }, {
            "part": "body", "location": "บทที่ 3 (หน้า 23)",
            "found": 'ชื่อบทในเนื้อหาพิมพ์ผิดเล็กน้อย: "RESEARCH METHODLOGY"',
            "expected": 'ตามประกาศ 2569 ควรเป็น "RESEARCH METHODOLOGY"', "fix": "",
        }]
        text = plain_summary(self._report(items))
        self.assertIn("ทั้งหมด 2 จุด", text)
        self.assertIn("2.", text)

    def test_no_issues_message(self):
        text = plain_summary({"verdict": "ผ่าน", "issues_by_zone": {"RED": []}})
        self.assertIn("ไม่พบจุดที่ต้องแก้ไข", text)

    def test_orange_is_included_by_default(self):
        # สีส้ม (รอยืนยัน) ต้องเข้าสรุปโดยปริยาย นับรวมเป็นจุดที่ต้องแก้
        report = {"verdict": "รอยืนยัน", "issues_by_zone": {"RED": [], "ORANGE": [{
            "part": "front_matter", "location": "สารบัญ (หน้า ฉ) เทียบกับ บทที่ 3 (หน้า 45)",
            "found": "สารบัญระบุหน้า 42 แต่บทอยู่จริงหน้า 45",
            "expected": "เลขหน้าบทในสารบัญควรเป็น 45", "fix": "",
        }], "YELLOW": []}}
        text = plain_summary(report)
        self.assertIn("ทั้งหมด 1 จุด", text)
        self.assertIn("สารบัญระบุหน้า 42 แต่บทอยู่จริงหน้า 45", text)
        # จัดกลุ่มตามส่วนของเล่ม (สารบัญ) ไม่มีหัวข้อแยกระดับความรุนแรง
        self.assertIn("\nสารบัญ\n1.", text)
        self.assertEqual(text.count("รอยืนยัน"), 1)   # โผล่แค่ในบรรทัดผลการตรวจ

    def test_orange_dropped_when_staff_passes_it(self):
        report = {"verdict": "รอยืนยัน", "issues_by_zone": {"RED": [], "ORANGE": [{
            "part": "front_matter", "location": "สารบัญ (หน้า ฉ) เทียบกับ บทที่ 3 (หน้า 45)",
            "found": "สารบัญระบุหน้า 42 แต่บทอยู่จริงหน้า 45",
            "expected": "เลขหน้าบทในสารบัญควรเป็น 45", "fix": "",
        }], "YELLOW": []}}
        text = plain_summary(report, passed=["ORANGE:0"])
        self.assertIn("ไม่พบจุดที่ต้องแก้ไข", text)

    def test_yellow_only_enters_when_staff_fails_it(self):
        report = {"verdict": "ผ่าน", "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": [{
            "part": "body/end", "location": "หน้า 40",
            "found": "พบหน้าที่ระบบดึงข้อความไม่ได้", "expected": "", "fix": "ตรวจด้วยตา",
        }]}}
        self.assertNotIn("กรุณาแก้ไขทั้งหมด", plain_summary(report))
        self.assertIn("ทั้งหมด 1 จุด", plain_summary(report, failed=["YELLOW:0"]))


class DegreeLineIsNotConfusedWithCommitteeQualifications(unittest.TestCase):
    """ชื่อปริญญาต้องไม่ถูกอ่านเป็นคุณวุฒิของอาจารย์

    คุณวุฒิใต้ชื่อกรรมการใช้ตัวย่อชุดเดียวกับชื่อปริญญา (Ph.D. / ปร.ด.) และมักมี
    สาขาในวงเล็บครบ ตัวกรอง "วงเล็บครบ" จึงเคยเลือกบรรทัดของกรรมการแทน โดยเฉพาะ
    เล่มที่บรรทัดปริญญาลืมปิดวงเล็บ ข้อความจากเล่มจริงที่เจ้าหน้าที่ทักมา:
        ชื่อปริญญาแบบย่อในเล่มเขียนว่า "LIANGROKAPART, Ph.D., THANANYA WASUSRI, Ph.D."
    """

    # หน้าลงนาม 2 ของเล่มจริง — บรรทัดปริญญาลืมปิดวงเล็บ ส่วนคุณวุฒิกรรมการวงเล็บครบ
    SIGNATURE_PAGE = "\n".join([
        "ii", "Thesis", "entitled",
        "ANALYZING BARRIERS AND STRATEGIES FOR RAIL FREIGHT DIGITAL",
        "was submitted to the Faculty of Graduate Studies, Mahidol University",
        "for the degree of Doctor of Philosophy (Logistics and Engineering Management",
        "on 20 July 2026",
        "Thesis Examination Committees",
        "Chair",
        "…………………………………………………… ……………………………………………………",
        "Photsawi Sirisaranlak, Assoc. Prof. Walilak Atthirawong,",
        "Candidate Ph.D. (Manufacturing Engineering and Operations",
        "Management)",
    ])

    # หน้าบทคัดย่อของเล่มจริง — ไม่มีบรรทัดชื่อปริญญาแบบย่อเลย
    ABSTRACT_PAGE_WITHOUT_DEGREE = "\n".join([
        "iv",
        "ANALYZING BARRIERS AND STRATEGIES FOR RAIL FREIGHT DIGITAL",
        "TRANSFORMATION IN THAILAND",
        "PHOTSAWI SIRISARANLAK 6637642 EGLE/D",
        "THESIS ADVISORY COMMITTEE: DUANGPUN KRITCHANCHAI, Ph.D., JIRAPAN",
        "LIANGROKAPART, Ph.D., THANANYA WASUSRI, Ph.D.",
        "ABSTRACT",
        "Railways around the world are increasingly adopting digital technologies.",
    ])

    ABSTRACT_PAGE_WITH_DEGREE = ABSTRACT_PAGE_WITHOUT_DEGREE.replace(
        "THESIS ADVISORY COMMITTEE:",
        "Ph.D. (LOGISTICS AND ENGINEERING MANAGEMENT)\nTHESIS ADVISORY COMMITTEE:")

    def test_signature_page_reads_the_degree_line_not_a_member_qualification(self):
        got = closest_degree_line(
            self.SIGNATURE_PAGE,
            "Doctor of Philosophy (Logistics and Engineering Management)")
        self.assertIn("Doctor of Philosophy", got)
        self.assertNotIn("Manufacturing", got)

    def test_abstract_page_does_not_borrow_the_committee_line(self):
        got = closest_degree_line(self.ABSTRACT_PAGE_WITH_DEGREE,
                                  "Ph.D. (LOGISTICS AND ENGINEERING MANAGEMENT)")
        self.assertIn("LOGISTICS AND ENGINEERING MANAGEMENT", got)
        self.assertNotIn("LIANGROKAPART", got)

    def test_missing_degree_line_is_not_quoted_as_a_wrong_one(self):
        """ไม่มีบรรทัดชื่อปริญญา ต้องไม่ยกบรรทัดอื่นมาอ้างว่าเป็นชื่อปริญญา"""
        got = closest_degree_line(self.ABSTRACT_PAGE_WITHOUT_DEGREE,
                                  "Ph.D. (LOGISTICS AND ENGINEERING MANAGEMENT)")
        self.assertNotIn("LIANGROKAPART", got)
        self.assertFalse(_looks_like_degree_line(got),
                         f"ต้องรู้ว่าไม่ใช่บรรทัดชื่อปริญญา แต่ได้ {got!r}")

    def test_degree_line_shapes_are_recognised(self):
        for line in ("Ph.D. (LOGISTICS AND ENGINEERING MANAGEMENT)",
                     "M.Sc. (ORTHODONTICS)", "ปร.ด. (การพยาบาล)",
                     "Ph.D (TROPICAL MEDICINE)", "DEGREE M.Sc. (NURSING)"):
            self.assertTrue(_looks_like_degree_line(line), line)
        for line in ("PHOTSAWI SIRISARANLAK 6637642 EGLE/D", "ABSTRACT",
                     "TRANSFORMATION IN THAILAND", ""):
            self.assertFalse(_looks_like_degree_line(line), line)


class StudentIdMismatchSaysWhatTheBookPrinted(unittest.TestCase):
    """รหัสนักศึกษาผิดตัวเลข ต้องบอกว่าเล่มพิมพ์ว่าอะไร ไม่ใช่ "ไม่พบรหัสนักศึกษา"

    เล่มจริงพิมพ์ 6526627 แต่ข้อมูลอนุมัติเป็น 6536627 ต่างกันหลักเดียว ระบบเดิม
    บอกแค่ "ไม่พบรหัสนักศึกษา" เจ้าหน้าที่อ่านแล้วนึกว่าระบบหาไม่เจอ ทั้งที่รหัสพิมพ์
    อยู่ชัด ๆ แค่ผิดหนึ่งตัว — คนละเรื่องกับเล่มที่ไม่ได้ใส่รหัสมาเลย
    """

    THAI_PAGE = "\n".join([
        "ง",
        "อิทธิพลของความรอบรูในการเลี้ยงลูกดวยนมแม ระยะเวลาที่ลาคลอด",
        "เอมิกา หงสชั้น 6526627 NSMY/M",
        "พย.ม. (การพยาบาลเวชปฏิบัติชุมชน)",
    ])

    def test_finds_the_id_actually_printed(self):
        self.assertEqual(_closest_student_id(self.THAI_PAGE, "6536627 NSMY/M"),
                         "6526627 NSMY/M")

    def test_points_at_the_wrong_digits(self):
        printed = _closest_student_id(self.THAI_PAGE, "6536627 NSMY/M")
        self.assertIn('"6526627" ต้องเป็น "6536627"',
                      describe_diff(printed, "6536627 NSMY/M"))

    def test_page_without_any_id_returns_empty(self):
        """หน้าที่ไม่มีรหัสเลย ต้องคืนค่าว่าง เพื่อให้ยังบอกได้ว่า "ไม่พบ" ตามจริง"""
        self.assertEqual(_closest_student_id("เอมิกา หงสชั้น\nบทคัดยอ", "6536627 NSMY/M"), "")

    def test_picks_the_nearest_id_when_the_page_has_several(self):
        page = "ที่ปรึกษา 6412345 SHSS/D\nเอมิกา หงสชั้น 6526627 NSMY/M"
        self.assertEqual(_closest_student_id(page, "6536627 NSMY/M"), "6526627 NSMY/M")


class TitleIsQuotedAsPrintedNotAsTheClosestFragment(unittest.TestCase):
    """ชื่อเรื่องที่ยกมาต้องเป็นชื่อเรื่องตามที่พิมพ์จริง ไม่ใช่ช่วงที่ใกล้เคียงที่สุด

    ถ้าชื่อในเล่มกับในระบบเป็นคนละเรื่องกันจริง ๆ การหา "ช่วงที่ใกล้เคียงที่สุด"
    จะได้เศษข้อความมั่ว เล่มจริงเคยได้บรรทัดเนื้อความบทคัดย่อมาอ้างว่าเป็นชื่อเรื่อง
    """

    COVER = "\n".join([
        "SELECTION OF SMART WAREHOUSE PROCESSES FOR ROBOTIC",
        "PROCESS AUTOMATION IMPLEMENTATION: A HYBRID MULTI-",
        "CRITERIA DECISION-MAKING BASED ON BOCR FRAMEWORK",
        "CHANATASA MOUNGKHAODAENG",
        "A THESIS SUBMITTED IN PARTIAL FULFILLMENT",
        "OF THE REQUIREMENTS FOR THE DEGREE OF",
    ])
    SIGNATURE = "\n".join([
        "i", "Thesis", "entitled",
        "SELECTION OF SMART WAREHOUSE PROCESSES FOR ROBOTIC",
        "PROCESS AUTOMATION IMPLEMENTATION: A HYBRID MULTI-",
        "CRITERIA DECISION-MAKING BASED ON BOCR FRAMEWORK",
        "was submitted to the Faculty of Graduate Studies, Mahidol University",
    ])
    ABSTRACT = "\n".join([
        "iv",
        "SELECTION OF SMART WAREHOUSE PROCESSES FOR ROBOTIC PROCESS",
        "AUTOMATION IMPLEMENTATION: A HYBRID MULTI-CRITERIA DECISION-",
        "MAKING BASED ON BOCR FRAMEWORK",
        "CHANATASA MOUNGKHAODAENG 6537062 EGIE/M",
        "M.Eng. (LOGISTICS AND SUPPLY CHAIN)",
        "ABSTRACT",
        "This study aims to identify warehouse processes suitable for future",
        "implementation of Robotic Process Automation (RPA), and to establish a",
    ])
    FULL = ("SELECTION OF SMART WAREHOUSE PROCESSES FOR ROBOTIC "
            "PROCESS AUTOMATION IMPLEMENTATION: A HYBRID MULTI- "
            "CRITERIA DECISION-MAKING BASED ON BOCR FRAMEWORK")

    def test_cover_title_stops_at_the_author_name(self):
        self.assertEqual(printed_title(self.COVER, "CHANATASA MOUNGKHAODAENG"),
                         self.FULL)

    def test_signature_title_starts_after_entitled(self):
        got = printed_title(self.SIGNATURE, "CHANATASA MOUNGKHAODAENG")
        self.assertEqual(got, self.FULL)
        self.assertNotIn("Thesis", got)
        self.assertNotIn("was submitted", got)

    def test_abstract_title_does_not_take_a_body_sentence(self):
        got = printed_title(self.ABSTRACT, "CHANATASA MOUNGKHAODAENG")
        self.assertTrue(got.startswith("SELECTION OF SMART WAREHOUSE"), got)
        self.assertNotIn("This study aims", got)
        self.assertNotIn("6537062", got)

    def test_page_without_a_title_returns_empty_so_the_old_value_is_kept(self):
        self.assertEqual(printed_title("iv\nABSTRACT\nBody text here."), "")


class TocHeadingMustBeTableOfContents(unittest.TestCase):
    """หัวข้อหน้าสารบัญที่พิมพ์ผิด ต้องยังหาหน้าสารบัญเจอ แล้วฟ้องแยกว่าให้แก้หัวข้อ

    เล่มจริงพิมพ์ "CONTENT" ระบบจึงหาหน้าสารบัญไม่เจอทั้งชุด แล้วฟ้องผิดพ่วงมาอีก
    ("ไม่พบหน้าสารบัญ", "ภาคผนวกไม่อยู่ในสารบัญ", "บทคัดย่อไทยเกิน 2 หน้า")
    """

    def test_wrong_headings_are_still_recognised_as_a_toc(self):
        for heading in ("CONTENT", "CONTENTS"):
            self.assertIn(checker_module.norm(heading), checker_module.N_TOC, heading)
            self.assertIn(checker_module.norm(heading), checker_module.N_TOC_WRONG)

    def test_canonical_headings_are_not_flagged(self):
        for heading in ("TABLE OF CONTENTS", "สารบัญ"):
            self.assertIn(checker_module.norm(heading), checker_module.N_TOC, heading)
            self.assertNotIn(checker_module.norm(heading), checker_module.N_TOC_WRONG)


class TrArrayMustNotHaveHoles(unittest.TestCase):
    """ช่องว่างในอาร์เรย์ TR ทำให้การแปลพังทั้งก้อนแบบเงียบ ๆ

    JS ยอมให้เขียน [a, , b] ได้ โดยช่องกลางมีค่า undefined — ถ้าลบ entry ทิ้งแต่ลืม
    ลบคอมมา trWhole จะพังตอนอ่าน TR[i][0] ("Cannot read properties of undefined")
    หน้าเว็บไม่ขึ้น error ให้เห็น รู้อีกทีคือกดปุ่ม EN แล้วข้อความสรุปไม่ยอมแปล
    (เกิดขึ้นจริง ส.ค. 2569 ตอนถอดกฎที่เลิกใช้ออก 11 รายการ)

    ด่านเดิมจับไม่ได้ เพราะจำนวน entry กับจำนวน "[/" ลดลงเท่ากัน และวงเล็บยังสมดุล
    """

    GOOD = ("var TR = [\n"
            "  [/aaa/g, 'A'],\n"
            "  [/bbb/g, 'B'],\n"
            "  // คอมเมนต์คั่นระหว่าง entry ได้\n"
            "  [/ccc/g, 'C']\n"
            "];")

    def _problems(self, block):
        import tools.check_i18n as i18n
        return i18n.entry_separator_problems(block)

    def test_a_well_formed_array_has_no_problems(self):
        self.assertEqual(self._problems(self.GOOD), [])

    def test_a_hole_left_by_a_removed_entry_is_caught(self):
        block = self.GOOD.replace("  [/bbb/g, 'B'],", "  ,")
        problems = self._problems(block)
        self.assertEqual(len(problems), 1)
        self.assertIn("ช่องว่างในอาร์เรย์", problems[0][1])

    def test_a_missing_comma_is_caught(self):
        block = self.GOOD.replace("[/aaa/g, 'A'],", "[/aaa/g, 'A']")
        self.assertIn("ไม่มีคอมมาคั่น", self._problems(block)[0][1])

    def test_a_leading_comma_is_caught(self):
        block = self.GOOD.replace("var TR = [", "var TR = [\n  ,")
        self.assertIn("คอมมาก่อน entry แรก", self._problems(block)[0][1])

    def test_the_real_report_template_is_clean(self):
        """ไฟล์จริงต้องไม่มีช่องว่าง — ด่านนี้คือสิ่งที่ควรจับบั๊กเดิมได้"""
        import tools.check_i18n as i18n
        block, _pairs = i18n.load_tr()
        self.assertEqual(i18n.entry_separator_problems(block), [])

    def test_lint_reports_a_failure_count_instead_of_always_passing(self):
        """ของเดิม lint() คืน 0 เสมอ ด่านนี้จึงไม่เคยฟ้องจริง"""
        import tools.check_i18n as i18n
        block, pairs = i18n.load_tr()
        import io as _io
        import contextlib
        with contextlib.redirect_stdout(_io.StringIO()):
            self.assertEqual(i18n.lint(block, pairs), 0)


class DegreeAbbreviationsComeFromAFixedTable(unittest.TestCase):
    """ตัวย่อชื่อปริญญาต้องย่อได้ตอนดึงข้อมูลจาก eThesis เพื่อใช้ตรวจหน้าบทคัดย่อ

    เจ้าหน้าที่แจ้ง ส.ค. 2569 ว่าระบบย่อชื่อปริญญาชุดนี้ไม่ได้ ทำให้ช่อง
    "ชื่อปริญญาแบบย่อ" ว่าง แล้วการตรวจหน้าบทคัดย่อถูกข้ามไปทั้งข้อ
    """

    # (ชื่อเต็มจาก eThesis, ตัวย่อที่ถูกต้อง)
    PAIRS = (
        ("DOCTOR OF PUBLIC ADMINISTRATION", "D.P.A."),
        ("MASTER OF PUBLIC ADMINISTRATION", "M.P.A."),
        ("MASTER OF PUBLIC HEALTH", "M.P.H."),
        ("DOCTOR OF PUBLIC HEALTH", "Dr. P.H."),
        ("MASTER OF NURSING SCIENCE", "M.N.S."),
        ("DOCTOR OF NURSING SCIENCE", "D.N.S."),
    )

    def test_every_degree_the_staff_listed_can_be_abbreviated(self):
        for full, abbr in self.PAIRS:
            self.assertEqual(ethesis_import._degree_abbr(full), abbr, full)

    def test_the_field_in_parentheses_is_kept(self):
        for full, abbr in self.PAIRS:
            for probe in (f"{full}(HEALTH SOCIAL SCIENCE)",
                          f"{full} (HEALTH SOCIAL SCIENCE)"):
                self.assertEqual(ethesis_import._degree_abbr(probe),
                                 f"{abbr} (HEALTH SOCIAL SCIENCE)", probe)

    def test_public_health_doctorate_breaks_the_usual_pattern(self):
        """เหตุผลที่ต้องใช้ตารางตายตัว ไม่ใช่เดาจากอักษรตัวแรกของแต่ละคำ"""
        self.assertEqual(ethesis_import._degree_abbr("DOCTOR OF PUBLIC HEALTH"),
                         "Dr. P.H.")
        self.assertNotEqual(ethesis_import._degree_abbr("DOCTOR OF PUBLIC HEALTH"),
                            "D.P.H.")

    def test_an_abbreviation_with_a_space_still_matches_the_book(self):
        """"Dr. P.H." มีช่องว่างในตัวเอง เล่มพิมพ์ติดกันก็ต้องถือว่าตรง"""
        want = ethesis_import._degree_abbr("DOCTOR OF PUBLIC HEALTH (PUBLIC HEALTH)")
        self.assertEqual(want, "Dr. P.H. (PUBLIC HEALTH)")
        for printed in ("Dr. P.H. (PUBLIC HEALTH)", "Dr.P.H. (PUBLIC HEALTH)"):
            text = ("WISIT KAWAYAPANIK 6236350 NSNS/D\n"
                    f"{printed}\nTHESIS ADVISORY COMMITTEE")
            compared = compare_reference_text(text, want, "degree", degree_line=True)
            self.assertEqual(compared["status"], "exact", printed)

    def test_an_unknown_degree_stays_empty_rather_than_guessing(self):
        """เดาผิดแล้วไปฟ้องเล่มที่ถูก แย่กว่าปล่อยว่างให้เจ้าหน้าที่กรอกเอง"""
        self.assertEqual(ethesis_import._degree_abbr("MASTER OF IMAGINARY STUDIES"), "")

    def test_the_thai_side_covers_the_same_programmes(self):
        for thai, abbr in (("รัฐประศาสนศาสตรดุษฎีบัณฑิต", "รป.ด."),
                           ("รัฐประศาสนศาสตรมหาบัณฑิต", "รป.ม."),
                           ("สาธารณสุขศาสตรมหาบัณฑิต", "ส.ม."),
                           ("สาธารณสุขศาสตรดุษฎีบัณฑิต", "ส.ด."),
                           ("พยาบาลศาสตรมหาบัณฑิต", "พย.ม."),
                           ("พยาบาลศาสตรดุษฎีบัณฑิต", "พย.ด.")):
            self.assertEqual(ethesis_import._degree_abbr_th(thai), abbr, thai)


class AbstractLocationSaysWhichLanguage(unittest.TestCase):
    """เล่มหลักสูตรไทยมีบทคัดย่อสองหน้า ตำแหน่งต้องบอกว่าหน้าไหน

    เจ้าหน้าที่แจ้ง ส.ค. 2569: "เล่มไทย เวลาตรวจ ระบบแจ้งผลการตรวจไม่ได้ระบุว่า
    บทคัดย่อไทยหรืออังกฤษ ระบุแค่ว่าบทคัดย่อ (หน้า ...)"
    """

    def test_the_label_names_the_language(self):
        label = checker_module.abstract_page_label
        self.assertEqual(label(3, [3], [5]), "บทคัดย่ออังกฤษ")
        self.assertEqual(label(5, [3], [5]), "บทคัดย่อไทย")

    def test_an_unknown_page_falls_back_without_crashing(self):
        label = checker_module.abstract_page_label
        self.assertEqual(label(9, [3], [5]), "บทคัดย่อ")
        self.assertEqual(label(9, None, None), "บทคัดย่อ")
        self.assertEqual(label(9, [], []), "บทคัดย่อ")

    def test_the_wording_does_not_collide_with_the_language_category(self):
        """ห้ามใช้ "บทคัดย่อภาษาไทย" — classify() จะจัดเข้าหมวดผิด

        classify() จัดข้อความที่มีคำว่า "ภาษาไทย"/"ภาษาอังกฤษ" เข้าหมวด
        "ภาษาไม่ครบตามหลักสูตร" ซึ่งใช้กับข้อ "เล่มขาดบทคัดย่ออีกภาษา" เท่านั้น
        ถ้าตำแหน่งมีคำนั้น ข้อของหน้าบทคัดย่อทุกข้อจะติดหมวดผิดหมด
        """
        label = checker_module.abstract_page_label
        for lbl in (label(3, [3], [5]), label(5, [3], [5])):
            self.assertNotIn("ภาษาไทย", lbl)
            self.assertNotIn("ภาษาอังกฤษ", lbl)
            issue = {"location": f"{lbl} (หน้า ง)", "found": 'มีข้อความตัวหนา: "x"',
                     "expected": "y", "part": "front_matter"}
            self.assertNotEqual(checker_module.classify(issue),
                                "ภาษาไม่ครบตามหลักสูตร")

    def test_both_languages_still_group_under_one_section(self):
        """ป้ายแยกภาษา แต่การ์ดต้องยังอยู่กลุ่ม "บทคัดย่อ" กลุ่มเดียว"""
        label = checker_module.abstract_page_label
        for lbl in (label(3, [3], [5]), label(5, [3], [5])):
            issue = {"location": f"{lbl} (หน้า ง)", "found": "x",
                     "expected": "y", "part": "front_matter"}
            self.assertEqual(summary_section(issue), "บทคัดย่อ")


class CommitteeFindingsAreNotFiledUnderOther(unittest.TestCase):
    """ข้อของหน้าลงนามต้องไม่ตกหมวด "อื่นๆ"

    classify() มองหาคำว่า "หน้าลงนาม" แต่ตำแหน่งจริงเขียนตามบทบาทของหน้า
    ("หน้าอาจารย์ที่ปรึกษา" / "หน้ากรรมการสอบ") — ปัญหาเดียวกับที่ SUMMARY_SECTIONS
    เคยเจอและแก้ไปแล้ว แต่ classify() ยังไม่ได้แก้ตาม
    """

    FORM3 = [{"name": "ก ก"}, {"name": "ข ข"}, {"name": "ค ค"}]

    def _count_issue(self, loc, form="บฑ.2"):
        rep = Report()
        checker_module._report_committee_count(rep, self.FORM3, ["ก ก", "ข ข"], loc, form)
        return rep.zones["RED"][0]

    def test_the_count_finding_is_filed_as_a_data_mismatch(self):
        for loc in ("หน้ากรรมการสอบ (หน้า ข)", "หน้าอาจารย์ที่ปรึกษา (หน้า ก)",
                    "บทคัดย่อไทย (หน้า ง) รายชื่อคณะกรรมการที่ปรึกษา"):
            issue = self._count_issue(loc)
            self.assertEqual(checker_module.classify(issue), "ไม่ตรงข้อมูลอนุมัติ", loc)

    def test_the_wording_says_sentence_case_and_explains_it(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569): ชื่อกรรมการต้องเป็น Sentence Case และเป็นสีส้ม

        ต้องอธิบายด้วยว่าหมายถึงตัวใหญ่ต้นชื่อและต้นนามสกุล — นักศึกษาที่อ่าน
        Sentence Case ตามตัวอักษรจะพิมพ์นามสกุลเป็นตัวเล็ก แล้วโดนฟ้องซ้ำ
        """
        rep = Report()
        checker_module._report_committee_name_case(
            rep, {1: "MATHUROS TIPAYAMONGKHOLGUL, Ph.D."}, "หน้าลงนาม 2 (หน้า ii)")
        self.assertEqual(rep.zones["RED"], [])
        issue = rep.zones["ORANGE"][0]
        self.assertIn("Sentence Case", issue["found"])
        self.assertIn("Sentence Case", issue["expected"])
        self.assertIn("อักษรแรกของชื่อและนามสกุล", issue["expected"])
        self.assertIn("Sentence Case", issue["fix"])
        for text in (issue["found"], issue["expected"], issue["fix"]):
            self.assertNotIn("Capital Case", text)

    def test_the_committee_wording_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        rep = Report()
        checker_module._report_committee_name_case(
            rep, {1: "MATHUROS TIPAYAMONGKHOLGUL"}, "หน้าลงนาม 2 (หน้า ii)")
        issue = rep.zones["ORANGE"][0]
        for th in (issue["found"], issue["expected"], issue["fix"]):
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}: {en}")
            self.assertIn("Sentence Case", en)

    def test_the_case_rule_is_back_with_the_credential_stripped(self):
        """เจ้าหน้าที่สั่งให้เอากฎกลับมา (ก.ย. 2569) พร้อมตัดคุณวุฒิท้ายชื่อออกก่อน

        กฎนี้เคยถูกถอดออกทั้งกฎ เพราะระบบอ่านชื่อจากตารางลายเซ็นมาทั้งช่อง คุณวุฒิ
        ที่พิมพ์ต่อท้ายจึงติดมาด้วย แล้วชื่อที่ถูกต้องถูกตัดสินว่าผิด
        """
        rep = Report()
        checker_module._report_committee_name_case(
            rep, {1: "Weerawat Limroonreungrat, PT",
                  2: "Assist.Prof. Hoon Kim, ATC"}, "หน้าลงนาม 1 (หน้า i)")
        self.assertEqual(rep.zones["ORANGE"], [], "ชื่อที่ถูกต้องต้องไม่ถูกฟ้อง")

    def test_an_all_uppercase_committee_name_is_reported(self):
        """positive control — อาการที่กฎนี้มีไว้จับ

        นักศึกษาที่คัดรายชื่อจากหน้าบทคัดย่อ (ซึ่งต้องเป็น UPPERCASE) มาวางบน
        หน้าลงนาม จะได้ตัวพิมพ์ใหญ่ทั้งบรรทัด
        """
        rep = Report()
        checker_module._report_committee_name_case(
            rep, {1: "MATHUROS TIPAYAMONGKHOLGUL, Ph.D.",
                  2: "Hathaichon Inchai,"}, "หน้าลงนาม 2 (หน้า ii)")
        self.assertEqual(len(rep.zones["ORANGE"]), 1)
        found = rep.zones["ORANGE"][0]["found"]
        self.assertIn("MATHUROS TIPAYAMONGKHOLGUL", found)
        self.assertNotIn("Hathaichon", found)
        self.assertNotIn("Ph.D.", found)

    def test_thai_committee_names_are_left_alone(self):
        """ภาษาไทยไม่มีตัวพิมพ์ใหญ่-เล็ก"""
        rep = Report()
        checker_module._report_committee_name_case(
            rep, {1: "ฉัตรภา หัตถโกศล, ส.ด."}, "หน้าลงนาม 1 (หน้า ก)")
        self.assertEqual(rep.zones["ORANGE"], [])

    def test_the_credential_is_what_gets_stripped(self):
        for raw, core in (("Weerawat Limroonreungrat, PT", "Weerawat Limroonreungrat"),
                          ("Assoc. Prof. Mathuros Tipayamongkholgul,",
                           "Mathuros Tipayamongkholgul"),
                          ("Prof. Chartchalerm Isarankura-Na-Ayudhya,",
                           "Chartchalerm Isarankura-Na-Ayudhya")):
            self.assertEqual(checker_module.committee_name_for_case(raw), core, raw)

    def test_every_category_used_here_has_an_english_name(self):
        import tools.check_i18n as i18n
        catmap = i18n.load_catmap()
        for loc in ("หน้ากรรมการสอบ (หน้า ข)", "หน้าอาจารย์ที่ปรึกษา (หน้า ก)"):
            issue = self._count_issue(loc)
            self.assertIn(checker_module.classify(issue), catmap)
            self.assertIn(checker_module.summary_section(issue), catmap)


class OrderFindingsAreFiledUnderStructure(unittest.TestCase):
    """กฎลำดับ/ตำแหน่งของส่วนประกอบ ต้องอยู่หมวด "โครงสร้างเล่ม"

    ข้อความของกฎพวกนี้เป็นรายการชื่อส่วนทั้งเล่ม จึงมีคำที่ classify จับได้เต็มไปหมด
    ลำดับส่วนนำของเล่มไทยเคยตกหมวด "ภาษาไม่ครบตามหลักสูตร" เพราะในข้อความมีคำว่า
    "บทคัดย่อภาษาไทย" ส่วนหน้าปกที่ไม่ได้อยู่แผ่นแรกไม่มีคำไหนตรงเลย เลยตกหมวด "อื่นๆ"
    จึงจัดหมวดจากรหัสกฎแทนการเดาจากคำ
    """

    def test_each_order_rule_lands_in_the_structure_bucket(self):
        cases = {
            "FRONT.COVER_FIRST": ("หน้าปกอยู่แผ่นที่ 2 ของไฟล์ ไม่ใช่แผ่นแรก",
                                  "หน้าปกต้องเป็นแผ่นแรกของไฟล์", "หน้าปก"),
            "FRONT.ORDER": ("ลำดับที่พบ: หน้าลงนาม (แผ่นที่ 3 ของไฟล์) แล้ว "
                            "บทคัดย่อภาษาไทย (แผ่นที่ 5 ของไฟล์)",
                            "ลำดับที่ต้องเป็น: หน้าลงนาม แล้ว กิตติกรรมประกาศ", "ส่วนนำ"),
            "END.STRUCTURE": ("ลำดับที่พบ: ภาคผนวก (แผ่นที่ 71 ของไฟล์)",
                              "ลำดับที่ต้องเป็น: รายการอ้างอิง/บรรณานุกรม", "ส่วนท้ายเล่ม"),
        }
        for rule_id, (found, expected, loc) in cases.items():
            got = checker_module.classify(dict(rule_id=rule_id, found=found,
                                               expected=expected, location=loc))
            self.assertEqual(got, "โครงสร้างเล่ม", rule_id)

    def test_the_shortcut_is_what_keeps_them_there(self):
        """ควบคุมเชิงลบ: ถ้าเดาจากคำเหมือนเดิม เล่มไทยจะไปโผล่หมวดภาษา"""
        got = checker_module.classify(dict(
            rule_id="", location="ส่วนนำ",
            found="ลำดับที่พบ: หน้าลงนาม (แผ่นที่ 3 ของไฟล์) แล้ว บทคัดย่อภาษาไทย (แผ่นที่ 5 ของไฟล์)",
            expected="ลำดับที่ต้องเป็น: หน้าลงนาม แล้ว กิตติกรรมประกาศ"))
        self.assertEqual(got, "ภาษาไม่ครบตามหลักสูตร")

    def test_other_cover_rules_keep_their_own_category(self):
        """ทางลัดต้องแคบ ห้ามลากข้ออื่นบนหน้าปกเข้ามาด้วย"""
        got = checker_module.classify(dict(
            rule_id="FRONT.COVER_REQUIRED", location="หน้าปก",
            found="ไม่พบข้อความบังคับ (ข้อความลิขสิทธิ์) บนหน้าปก",
            expected='ข้อความที่ถูกต้อง: "COPYRIGHT OF MAHIDOL UNIVERSITY"'))
        self.assertEqual(got, "ขาดหาย/ไม่พบ")


class PagesAreIdentifiedByContentNotExactLines(unittest.TestCase):
    """ระบุหน้าจาก "สิ่งที่ควรอยู่ในหน้านั้น" ไม่ใช่จากบรรทัดที่ตรงเป๊ะบรรทัดเดียว

    เจ้าหน้าที่ท้วง (ก.ย. 2569) ว่าบางเล่มบรรทัดไม่ตรงกัน การมองบรรทัดเป๊ะทำให้ตรวจ
    คลาดเคลื่อน วัดกับเล่มจริงแล้วยืนยัน: ถ้า PDF ดึงเลขหน้ามาต่อท้ายบรรทัดหัวข้อ
    (เกิดเมื่อหัวกระดาษกับหัวข้ออยู่ระดับเดียวกัน) เล่มที่ผ่านสะอาดกลายเป็นไม่ผ่าน
    4 ข้อรวด คือ ไม่พบกิตติกรรมประกาศ ไม่พบสารบัญ ไม่พบบทคัดย่อ และภาคผนวกไม่อยู่ในสารบัญ
    """

    ACK_EN = """ACKNOWLEDGEMENTS
I would like to express my sincere gratitude to my advisor."""
    ACK_TH = """กิตติกรรมประกาศ
ขอขอบพระคุณอาจารย์ที่ปรึกษาที่กรุณาให้คำแนะนำตลอดมา"""
    TOC_EN = """TABLE OF CONTENTS
Page
ACKNOWLEDGEMENTS iii
ABSTRACT (ENGLISH) iv
ABSTRACT (THAI) v
LIST OF TABLES viii
LIST OF FIGURES ix
CHAPTER I INTRODUCTION 1
REFERENCES 52
BIOGRAPHY 60"""

    def test_clean_headings_are_identified(self):
        kinds = {
            self.ACK_EN: "ack",
            self.ACK_TH: "ack",
            self.TOC_EN: "toc",
            "ABSTRACT@This study aims to": "abstract_en",
            "บทคัดย่อ@การศึกษานี้มีวัตถุประสงค์": "abstract_th",
            "LIST OF TABLES@Table 1.1 Something 5": "list",
        }
        for text, want in kinds.items():
            got = checker_module.front_section_kind(text.replace("@", NEWLINE))[0]
            self.assertEqual(got, want, text[:24])

    def test_a_page_number_stuck_to_the_heading_still_works(self):
        """หัวกระดาษถูกดึงมารวมกับหัวข้อ — เคสที่ทำให้เล่มดี ๆ ตกทั้งเล่ม"""
        cases = {
            "ACKNOWLEDGEMENTS iii@I would like to thank": "ack",
            "กิตติกรรมประกาศ ค@ขอขอบพระคุณอาจารย์": "ack",
            "ABSTRACT iv@This study aims to": "abstract_en",
            "บทคัดย่อ ง@การศึกษานี้มีวัตถุประสงค์": "abstract_th",
            "TABLE OF CONTENTS vi@Page": "toc",
            "สารบัญ ฉ@หน้า": "toc",
        }
        for text, want in cases.items():
            got = checker_module.front_section_kind(text.replace("@", NEWLINE))[0]
            self.assertEqual(got, want, text[:24])

    def test_a_contents_listing_is_not_mistaken_for_the_pages_it_lists(self):
        """บรรทัดในสารบัญมีรูปเดียวกับหัวข้อที่มีเลขหน้าติดท้ายเป๊ะ ๆ ต้องแยกให้ออก"""
        self.assertEqual(checker_module.front_section_kind(self.TOC_EN)[0], "toc")
        self.assertTrue(checker_module.looks_like_contents_page(self.TOC_EN))
        self.assertFalse(checker_module.looks_like_contents_page(self.ACK_EN))

    def test_a_continued_contents_page_is_not_read_as_a_heading_page(self):
        """หน้าสารบัญหน้าที่สองไม่มีหัวข้อของตัวเองให้ยึด การ์ดนี้จึงเป็นตัวเดียวที่กันไว้

        หัวข้อ "TABLE OF CONTENTS (cont.)" ไม่ตรงกับชุดคำหัวข้อ พอไม่มีอะไรแมตช์
        บรรทัดแรกที่เป็นรายการ ("ACKNOWLEDGEMENTS iii") จะกลายเป็นตัวชี้ว่าหน้านี้
        คือหน้ากิตติกรรมประกาศ ทั้งที่เป็นสารบัญ
        """
        cont = NEWLINE.join([
            "TABLE OF CONTENTS (cont.)", "Page",
            "ACKNOWLEDGEMENTS iii", "ABSTRACT (ENGLISH) iv", "ABSTRACT (THAI) v",
            "LIST OF TABLES viii", "LIST OF FIGURES ix", "REFERENCES 52",
        ])
        self.assertTrue(checker_module.looks_like_contents_page(cont))
        self.assertNotEqual(checker_module.front_section_kind(cont)[0], "ack")

    def test_a_short_listing_still_falls_back_to_the_line(self):
        """หน้าที่มีรายการน้อยเกินกว่าจะเป็นสารบัญ ยังใช้บรรทัดตัดสินได้ตามเดิม"""
        short = NEWLINE.join(["ACKNOWLEDGEMENTS iii", "I would like to thank"])
        self.assertFalse(checker_module.looks_like_contents_page(short))
        self.assertEqual(checker_module.front_section_kind(short)[0], "ack")


class CoverIsFoundByWhatBelongsOnIt(unittest.TestCase):
    """หน้าปกหาโดยนับ "สิ่งที่ควรมีบนหน้าปก" ไม่ใช่จับข้อความเดียวแบบเป๊ะ

    เดิมยึดบรรทัดลิขสิทธิ์อย่างเดียว พิมพ์ผิดตัวเดียว (ซึ่งเป็นความผิดที่ระบบมีไว้จับ
    พอดี) ก็หาหน้าปกไม่เจอ แล้วตกกลับไปถือว่าแผ่นแรกคือหน้าปก ทำให้เล่มที่หน้าปก
    ไม่ได้อยู่แผ่นแรกกลับไปฟ้องมั่วเหมือนเดิม
    """

    COVER = """A THESIS SUBMITTED IN PARTIAL FULFILLMENT OF THE REQUIREMENTS FOR THE DEGREE OF
MASTER OF SCIENCE
FACULTY OF GRADUATE STUDIES
MAHIDOL UNIVERSITY
2026
COPYRIGHT OF MAHIDOL UNIVERSITY"""
    SIGNATURE = """THESIS ENTITLED
SOMETHING SOMETHING
FACULTY OF GRADUATE STUDIES
MAHIDOL UNIVERSITY
Advisory Committee"""

    def test_the_cover_outscores_the_signature_page(self):
        self.assertGreater(checker_module.cover_page_score(self.COVER),
                           checker_module.cover_page_score(self.SIGNATURE))

    def test_one_typo_does_not_hide_the_cover(self):
        typo = self.COVER.replace("COPYRIGHT OF MAHIDOL UNIVERSITY",
                                  "COPYRIGHT OF MAHIDOL UNIVERSTY")
        self.assertNotEqual(typo, self.COVER)
        self.assertEqual(checker_module.find_cover_page(["ใบรับรอง", typo, self.SIGNATURE]), 1)

    def test_a_cover_whose_title_contains_a_banned_word_is_still_found(self):
        """หักคะแนนแทนการตัดทิ้ง — ชื่อเรื่องบางเล่มมีคำว่า abstract อยู่จริง"""
        cover = "ABSTRACT REASONING IN CHILDREN" + NEWLINE + self.COVER
        self.assertEqual(checker_module.find_cover_page(["ใบปะหน้า", cover]), 1)


class ChapterTitlesThatAreOnlyTheStartOfTheRule(unittest.TestCase):
    """ชื่อบทที่พิมพ์แค่ต้นของชื่อในประกาศ ต้องถูกฟ้อง ถ้าไม่มีบรรทัดต่อจริง

    เล่มจริงพิมพ์บทที่ 6 ว่า "CONCLUSION" ประกาศ 2569 ให้เป็น
    "CONCLUSION AND RECOMMENDATIONS" แต่ระบบปล่อยผ่าน เพราะข้อผ่อนผัน
    "หัวบทยาวอาจถูกตัดขึ้นบรรทัดใหม่" ยอมรับทุกชื่อที่เป็นต้นของชื่อในประกาศ
    ทั้งที่บรรทัดถัดไปในเล่มคือ "6.1 Conclusions" ซึ่งเป็นหัวข้อย่อย ไม่ใช่ส่วนที่เหลือ

    สำรวจเล่มจริง 11 เล่ม: ข้อผ่อนผันแบบเดิมทำงานครั้งเดียว คือครั้งที่ปล่อยเล่มผิดให้ผ่าน
    ไม่มีเล่มไหนที่หัวบทถูกตัดขึ้นบรรทัดใหม่จริง ๆ เลย
    """

    CH6 = ("บทสรุปและข้อเสนอแนะ", "CONCLUSION AND RECOMMENDATIONS")
    CH2 = ("วรรณกรรมและงานวิจัยที่เกี่ยวข้อง", "LITERATURE REVIEW")

    def test_a_short_title_with_an_unrelated_next_line_is_not_excused(self):
        self.assertFalse(checker_module.canonical_title_wrapped(
            "CONCLUSION", "6.1 Conclusions", self.CH6))

    def test_a_title_with_no_next_line_is_not_excused(self):
        self.assertFalse(checker_module.canonical_title_wrapped(
            "CONCLUSION", "", self.CH6))

    def test_a_really_wrapped_title_is_still_accepted(self):
        """ต้องไม่ปิดข้อผ่อนผันทิ้ง หัวบทที่ห่อบรรทัดจริงต้องยังผ่าน"""
        self.assertTrue(checker_module.canonical_title_wrapped(
            "CONCLUSION", "AND RECOMMENDATIONS", self.CH6))
        self.assertTrue(checker_module.canonical_title_wrapped(
            "CONCLUSION AND", "RECOMMENDATIONS", self.CH6))

    def test_the_toc_page_number_on_the_next_line_is_ignored(self):
        """บรรทัดต่อในสารบัญมีเลขหน้าติดมาด้วย ต้องตัดก่อนเทียบ"""
        self.assertTrue(checker_module.canonical_title_wrapped(
            "CONCLUSION AND", "RECOMMENDATIONS 122", self.CH6))

    def test_a_thai_title_wraps_the_same_way(self):
        self.assertTrue(checker_module.canonical_title_wrapped(
            "วรรณกรรมและงานวิจัย", "ที่เกี่ยวข้อง", self.CH2))
        self.assertFalse(checker_module.canonical_title_wrapped(
            "วรรณกรรมและงานวิจัย", "2.1 แนวคิดที่เกี่ยวข้อง", self.CH2))

    def test_the_rule_still_says_the_short_title_is_wrong(self):
        """ตัวเทียบกับประกาศบอกว่าผิดอยู่แล้ว ปัญหาเดิมอยู่ที่ข้อผ่อนผันล้วน ๆ"""
        kind, _compared, expected = checker_module.canonical_title_status(
            "CONCLUSION", 6, 1)
        self.assertNotEqual(kind, "exact")
        self.assertEqual(expected, "CONCLUSION AND RECOMMENDATIONS")

    def test_the_check_uses_the_next_line_not_a_bare_prefix(self):
        """ควบคุมเชิงลบ: ห้ามกลับไปยอมรับ prefix ลอย ๆ อีก"""
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("canonical_title_wrapped(title, next_line", source)
        self.assertNotIn("norm(cand).startswith(nb)", source)


class StaffDecisionsMoveTheNumbersOnTheReportHead(unittest.TestCase):
    """กด "ไม่ผ่าน" ที่ข้อสีส้ม/เหลือง แล้วตัวเลขสามกล่องบนหัวรายงานต้องขยับตาม

    เจ้าหน้าที่แจ้ง (ก.ย. 2569) ว่าการกดไม่ผ่านคือตัดสินว่าข้อนั้นเป็นจุดที่นักศึกษา
    ต้องแก้ เหมือนสีแดง ของเดิมตัวเลขค้างที่ "สิ่งที่ระบบตรวจพบ" ไม่ว่าจะกดอะไร
    หัวรายงานจึงเขียน "ต้องแก้ 0" อยู่บรรทัดเดียวกับข้อความสรุปที่เขียนว่า
    "กรุณาแก้ไขทั้งหมด 4 จุด"
    """

    @staticmethod
    def _report(red=0, orange=0, yellow=0, notes=0):
        # ตำแหน่งต้องไม่ซ้ำกัน — _dedupe_issues รวมข้อที่ตำแหน่งและค่าที่ต้องแก้
        # เหมือนกันเป็นจุดเดียว ถ้าใช้ข้อความชุดเดียวกันหมด เทสต์จะนับไม่ตรงเอง
        rep = checker_module.Report()
        for i in range(red):
            rep.add("RED", "front_matter", f"หน้าปก {i}", "พบ", f"ควรเป็น {i}")
        for i in range(orange):
            rep.add("ORANGE", "front_matter", f"หน้าลงนาม {i} (หน้า i)",
                    "พบ", f"ควรเป็น {i}")
        for i in range(notes):
            rep.add("ORANGE", "front_matter", f"บทคัดย่อไทย {i} (หน้า vi)",
                    "ระบบอ่านหน้านี้ไม่ออก", "ข้ามการตรวจ", "", None, system_note=True)
        for i in range(yellow):
            rep.add("YELLOW", "body", f"หน้า {10 + i}", "พบ", f"ควรเป็น {i}")
        return checker_module.check_result(rep)

    def _counts(self, report, failed=(), passed=(), staff=()):
        return checker_module.zone_counts(report, failed, passed, staff)

    def test_nothing_pressed_matches_what_the_system_found(self):
        """ควบคุมเชิงบวกของทุกข้อข้างล่าง — ตอนโหลดหน้าต้องเท่าของเดิมเป๊ะ"""
        report = self._report(red=2, orange=3, yellow=4)
        self.assertEqual(self._counts(report), {"RED": 2, "ORANGE": 3, "YELLOW": 4})
        self.assertEqual(checker_module.summary_verdict(report), report["verdict"])

    def test_a_failed_notice_moves_into_the_must_fix_box(self):
        report = self._report(yellow=2)
        self.assertEqual(self._counts(report, failed=["YELLOW:0"]),
                         {"RED": 1, "ORANGE": 0, "YELLOW": 1})

    def test_a_failed_pending_item_moves_into_the_must_fix_box(self):
        report = self._report(orange=2)
        self.assertEqual(self._counts(report, failed=["ORANGE:1"]),
                         {"RED": 1, "ORANGE": 1, "YELLOW": 0})

    def test_an_accepted_item_leaves_every_box(self):
        """กด "ผ่าน" = เจ้าหน้าที่รับได้แล้ว ต้องไม่ไปโผล่กล่องไหนอีก"""
        report = self._report(orange=2, yellow=1)
        self.assertEqual(self._counts(report, passed=["ORANGE:0", "YELLOW:0"]),
                         {"RED": 0, "ORANGE": 1, "YELLOW": 0})

    def test_a_staff_added_finding_counts_as_a_must_fix(self):
        report = self._report()
        self.assertEqual(self._counts(report, staff=["SIGNATURE_LAYOUT_WRONG"])["RED"], 1)

    def test_the_closing_wording_is_not_counted_as_a_must_fix(self):
        """ควบคุมเชิงลบของข้อบน — ถ้อยคำปิดท้าย (ค่าปรับ) ไม่ใช่จุดผิด"""
        report = self._report()
        self.assertEqual(self._counts(report, staff=["PASS_FEE_NONE"])["RED"], 0)

    def test_the_must_fix_number_equals_the_points_in_the_summary(self):
        """ตัวเลขกล่องแรกกับ "กรุณาแก้ไขทั้งหมด N จุด" ต้องเป็นเลขเดียวกัน

        เมื่อข้อรอยืนยันถูกตัดสินครบแล้ว — ข้อที่ยังไม่ตัดสินอยู่ในสรุปโดยปริยาย
        แต่ยังนับเป็น "รอยืนยัน" ไม่ใช่ "ต้องแก้" ซึ่งเป็นคนละคำถามกัน
        """
        report = self._report(red=1, orange=2, yellow=2)
        cases = [(["ORANGE:0", "ORANGE:1"], []),
                 (["ORANGE:0", "ORANGE:1", "YELLOW:0", "YELLOW:1"], []),
                 (["ORANGE:0"], ["ORANGE:1"]),
                 ([], ["ORANGE:0", "ORANGE:1"])]
        for failed, passed in cases:
            items = checker_module._dedupe_issues(
                checker_module.issues_to_fix(report, failed, passed))
            self.assertEqual(self._counts(report, failed, passed)["RED"], len(items),
                             (failed, passed))

    def test_rejecting_a_system_note_makes_it_a_must_fix(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569): "สีส้มกับสีเหลืองถ้ากด ต้องให้เป็นสีแดง = แก้ไข"

        การ์ดสีส้มสองใบที่เป็นปัญหาฝั่งเจ้าหน้าที่ (ไม่ได้กรอกข้อมูลอนุมัติ / ไฟล์
        eThesis คนละคนกับเล่ม) เคยกด ✗ แล้วตัวเลขไม่ขยับ เพราะถูกกันออกจาก "ต้องแก้"
        """
        report = self._report(notes=1)
        self.assertEqual(self._counts(report, failed=["ORANGE:0"]),
                         {"RED": 1, "ORANGE": 0, "YELLOW": 0})
        self.assertEqual(checker_module.summary_verdict(report, failed=["ORANGE:0"]),
                         "ไม่ผ่าน")

    def test_a_rejected_system_note_reaches_the_summary(self):
        """นับเป็นต้องแก้แล้วต้องมีในข้อความสรุปด้วย ไม่งั้นตัวเลขกับรายการขัดกัน"""
        report = self._report(notes=1)
        items = checker_module.issues_to_fix(report, failed=["ORANGE:0"])
        self.assertEqual([i["found"] for i in items], ["ระบบอ่านหน้านี้ไม่ออก"])
        self.assertIn("ระบบอ่านหน้านี้ไม่ออก",
                      checker_module.plain_summary(report, failed=["ORANGE:0"]))

    def test_an_untouched_system_note_stays_out_of_the_summary(self):
        """ควบคุมเชิงลบ — ยังไม่กด ต้องไม่ไปถึงนักศึกษา เพราะเป็นปัญหาฝั่งเจ้าหน้าที่"""
        report = self._report(notes=1)
        self.assertEqual(checker_module.issues_to_fix(report), [])
        self.assertEqual(self._counts(report),
                         {"RED": 0, "ORANGE": 1, "YELLOW": 0})

    def test_accepting_a_system_note_clears_it(self):
        report = self._report(notes=1)
        self.assertEqual(self._counts(report, passed=["ORANGE:0"]),
                         {"RED": 0, "ORANGE": 0, "YELLOW": 0})
        self.assertEqual(checker_module.issues_to_fix(report, passed=["ORANGE:0"]), [])

    def test_the_book_passes_once_every_pending_item_is_accepted(self):
        """เล่มที่ระบบว่า "รอยืนยัน" เคยค้างเป็นรอยืนยันตลอดไป

        เจ้าหน้าที่กดผ่านครบทุกข้อแล้ว นักศึกษายังได้ข้อความว่า "ผลการตรวจ: รอยืนยัน"
        """
        report = self._report(orange=2)
        self.assertEqual(report["verdict"], "รอยืนยัน")
        self.assertEqual(
            checker_module.summary_verdict(report, passed=["ORANGE:0", "ORANGE:1"]),
            "ผ่าน")

    def test_the_book_fails_once_a_pending_item_is_rejected(self):
        report = self._report(orange=2)
        self.assertEqual(checker_module.summary_verdict(report, failed=["ORANGE:0"]),
                         "ไม่ผ่าน")

    def test_a_report_with_no_zones_keeps_the_verdict_it_came_with(self):
        """ควบคุมเชิงลบ — ห้ามยกระดับผลตรวจเอง เมื่อไม่มีข้อรอยืนยันให้ตัดสินสักข้อ

        ทางออกก่อนกำหนดของ run_check และเทสต์อื่นส่ง report ที่โซนว่างมาพร้อม
        verdict ของตัวเอง ถ้าคิดใหม่จากโซนเปล่า ๆ เล่มจะกลายเป็น "ผ่าน" ทันที
        """
        for verdict in ("ผ่าน", "รอยืนยัน", "ไม่ผ่าน"):
            report = {"verdict": verdict,
                      "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []}}
            self.assertEqual(checker_module.summary_verdict(report), verdict)

    def test_an_accepted_notice_leaves_the_summary_alone(self):
        """ข้อสังเกตที่กด "ผ่าน" ไม่มีอะไรหายจากสรุป เพราะไม่เคยอยู่ในสรุปตั้งแต่แรก

        สีเหลืองเข้าสรุปเฉพาะตอนกด "ไม่ผ่าน" การกด "ผ่าน" จึงเป็นการยืนยันค่าตั้งต้น
        ผลที่เห็นได้คือตัวเลขกล่อง "ข้อสังเกต" ลดลง ไม่ใช่ข้อความสรุปเปลี่ยน
        """
        report = self._report(orange=1, yellow=2)
        before = checker_module.plain_summary(report)
        after = checker_module.plain_summary(report, passed=["YELLOW:0", "YELLOW:1"])
        self.assertEqual(before, after)
        self.assertEqual(self._counts(report, passed=["YELLOW:0", "YELLOW:1"]),
                         {"RED": 0, "ORANGE": 1, "YELLOW": 0})

    def test_the_verdict_still_matches_the_first_line_of_the_summary(self):
        """สองค่านี้คำนวณคนละรอบ ถ้าเพี้ยนจากกันเจ้าหน้าที่จะเห็นหัวข้อที่กดแล้วไม่มีผล"""
        report = self._report(orange=2, yellow=1)
        for failed, passed in (([], []), (["YELLOW:0"], []), ([], ["ORANGE:0"]),
                               ([], ["ORANGE:0", "ORANGE:1"]),
                               (["ORANGE:0"], ["ORANGE:1"])):
            text = checker_module.plain_summary(report, failed, passed)
            verdict = checker_module.summary_verdict(report, failed=failed,
                                                     passed=passed)
            self.assertEqual(text.split(NEWLINE)[0], "ผลการตรวจ: " + verdict,
                             (failed, passed))


class AbstractHeadingsWrittenInEnglishAreRecognised(unittest.TestCase):
    """หัวข้อบทคัดย่อที่เขียนว่า ABSTRACT IN THAI ต้องนับเป็นบทคัดย่อภาษาไทย

    template เขียน ABSTRACT (THAI) แต่เล่มจริงเขียน ABSTRACT IN THAI ก็มี
    (สำรวจ 11 เล่ม พบทั้งสองแบบ) ระบบเดิมรู้จักแค่แบบมีวงเล็บ เล่มที่ใช้อีกแบบจึงโดน
    ฟ้องผิดสามทางจากคำเดียวกัน
        สารบัญ    "ไม่พบหัวข้อ บทคัดย่อภาษาไทย ในสารบัญ"  ทั้งที่มีอยู่จริง
        หน้า      หาหน้าบทคัดย่อไทยไม่เจอถ้าหน้านั้นไม่มีคำว่า "บทคัดย่อ" กำกับ
        ตัวหนา    "มีข้อความตัวหนา: ABSTRACT IN THAI" ทั้งที่เป็นหัวข้อตาม template
    """

    def test_the_toc_entry_counts_as_the_thai_abstract(self):
        for line in ("ABSTRACT IN THAI vi", "ABSTRACT (THAI) v", "บทคัดย่อภาษาไทย ง"):
            self.assertEqual(checker_module._toc_section_kind(line), "abstract_th", line)

    def test_the_toc_entry_counts_as_the_english_abstract(self):
        for line in ("ABSTRACT IN ENGLISH iv", "ABSTRACT (ENGLISH) iv", "ABSTRACT"):
            self.assertEqual(checker_module._toc_section_kind(line), "abstract_en", line)

    def test_the_heading_is_not_reported_as_stray_bold_text(self):
        for line in ("ABSTRACT IN THAI", "ABSTRACT IN ENGLISH", "ABSTRACT (THAI)"):
            self.assertTrue(checker_module._is_abstract_heading(line), line)

    def test_the_page_is_found_without_the_thai_word(self):
        """หน้าบทคัดย่อไทยที่หัวข้อเป็นอังกฤษล้วน ต้องยังหาเจอ"""
        page = NEWLINE.join(["vi", "ABSTRACT IN THAI",
                             "ความเป็นมาและความสำคัญของการศึกษา"])
        self.assertEqual(checker_module.front_section_kind(page)[0], "abstract_th")

    def test_other_english_headings_are_not_swept_in(self):
        """ต้องแคบ ห้ามลากหัวข้ออื่นที่มีคำว่า ABSTRACT เข้ามาด้วย"""
        for line in ("ABSTRACT REASONING IN CHILDREN", "LIST OF ABSTRACTS"):
            self.assertNotIn(checker_module._toc_section_kind(line),
                             ("abstract_th", "abstract_en"), line)

    def test_the_two_wordings_are_treated_as_one_heading(self):
        """เจ้าหน้าที่ตัดสิน (ก.ย. 2569) ว่า ABSTRACT IN THAI ถือเป็นเรื่องเดียวกับ
        ABSTRACT (THAI) — ทุกทางที่ระบบอ่านหัวข้อต้องให้ผลเท่ากันทั้งสองถ้อยคำ
        """
        body = "ความเป็นมาและความสำคัญของการศึกษา"
        for book, template in (("ABSTRACT IN THAI", "ABSTRACT (THAI)"),
                               ("ABSTRACT IN ENGLISH", "ABSTRACT (ENGLISH)")):
            self.assertEqual(checker_module._toc_section_kind(book + " vi"),
                             checker_module._toc_section_kind(template + " vi"), book)
            self.assertEqual(checker_module._is_abstract_heading(book),
                             checker_module._is_abstract_heading(template), book)
            kinds = [checker_module.front_section_kind(
                NEWLINE.join(["vi", head, body]))[0] for head in (book, template)]
            self.assertEqual(kinds[0], kinds[1], book)

    def test_the_wording_has_no_fix_it_list_of_its_own(self):
        """ปล่อยผ่านหมายถึงไม่ฟ้องสีไหนเลย

        ต่างจากหัวข้อสารบัญที่เขียน CONTENTS ซึ่งมี N_TOC_WRONG คอยฟ้องให้แก้เป็น
        TABLE OF CONTENTS — ถ้อยคำของหัวข้อบทคัดย่อต้องไม่มีรายการแบบนั้น
        ความต่างนี้ตั้งใจ ดู RULES_AND_SOURCES.md หัวข้อ
        "หัวข้อบทคัดย่อที่เขียนเป็นภาษาอังกฤษ"
        """
        self.assertIn("CONTENTS", checker_module.N_TOC_WRONG)   # ฝั่งที่ยังฟ้อง
        terms = set(checker_module.N_ABSTRACT_TH_EN) | set(checker_module.N_ABSTRACT_EN_EN)
        for name, value in vars(checker_module).items():
            if not name.endswith("_WRONG") or not isinstance(value, (list, tuple, set)):
                continue
            self.assertFalse(terms & {checker_module.norm(str(v)) for v in value}, name)


class EveryDamagedPageIsReported(unittest.TestCase):
    """เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่า "หน้าไหนเพี้ยนควรแจ้ง"

    ของเดิมบอกเฉพาะหน้าบทคัดย่อที่อ่านรหัสนักศึกษาไม่ออก หน้าอื่นเงียบสนิท
    เล่มจริงเล่มหนึ่งฟอนต์เพี้ยน 38 หน้า แต่รายงานพูดถึงหน้าเดียว
    """

    class _Page:
        def __init__(self, chars):
            self.chars = chars

    @staticmethod
    def _char(text, width):
        return {"text": text, "x0": 10.0, "x1": 10.0 + width, "top": 100.0}

    def test_a_clean_page_scores_zero(self):
        """ควบคุมเชิงลบ — เล่มที่ถูกต้อง 4 เล่มได้ 0 ทุกหน้า ห้ามมี false positive"""
        page = self._Page([self._char("ก", 7.0), self._char(" ", 3.5),
                           self._char("ั", 0.0), self._char("่", 0.0)])
        self.assertEqual(checker_module.font_damage_score(page, "กัน ปกติ"), 0)

    def test_a_zero_width_letter_is_a_damaged_font(self):
        """ฟอนต์ map วรรณยุกต์ไปเป็นตัวอักษรอื่น ตัวนั้นจะกว้างศูนย์ทั้งที่ไม่ใช่วรรณยุกต์"""
        page = self._Page([self._char("ก", 7.0), self._char("B", 0.0)])
        self.assertEqual(checker_module.font_damage_score(page, "กB"), 1)

    def test_an_unmapped_glyph_counts_too(self):
        page = self._Page([self._char("ก", 7.0)])
        self.assertEqual(
            checker_module.font_damage_score(page, "ก(cid:127)(cid:128)"), 2)

    def test_the_report_lists_the_pages(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("if font_damaged:", source)
        block = source.split("if font_damaged:", 1)[1][:700]
        self.assertIn("page_ref(i) for i in font_damaged[:12]", block)
        self.assertIn("rep.add_info", block)          # เป็นข้อมูลประกอบ ไม่ใช่จุดผิด
        self.assertNotIn('rep.add("RED"', block)

    def test_the_wording_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        for th in ("ฟอนต์ในไฟล์ทำให้ระบบอ่านข้อความเพี้ยน 9 หน้า",
                   "หน้าที่พบคือ หน้า vi, หน้า vii และอีก 26 หน้า "
                   "หน้ากระดาษแสดงผลถูกต้องตามปกติ "
                   "เสียเฉพาะการดึงข้อความออกจากไฟล์ "
                   "กรุณาเปิดหน้าเหล่านี้ดูด้วยตาอีกครั้ง"):
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}: {en}")


class AShortCommitteeListIsReportedInRed(unittest.TestCase):
    """รายชื่อกรรมการที่ไม่ครบ = แดง และต้องอ้างถึง บฑ.1 / บฑ.2

    เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่า "ถ้าไม่ครบก็ต้องแจ้งสีแดง และอ้างอิงถึง บฑ.1
    หรือ บฑ.2" — ของเดิมขาดเป็นส้ม เพราะจำนวนที่นับได้ขึ้นกับว่าระบบอ่านตารางออกครบไหม
    เจ้าหน้าที่รับความเสี่ยงนั้นแล้ว
    """

    EXPECTED = ["Chatrapa Hudthagosol", "Promluck Sanporkha", "Karl Peltzer"]

    def _run(self, found):
        rep = Report()
        checker_module._report_committee_count(
            rep, self.EXPECTED, found, "หน้าลงนาม 2 (หน้า ii)", "บฑ.2",
            "FRONT.COMMITTEE")
        return rep

    def test_a_short_list_is_red(self):
        rep = self._run(["Chatrapa Hudthagosol", "Promluck Sanporkha"])
        self.assertEqual(len(rep.zones["RED"]), 1)
        self.assertEqual(rep.zones["ORANGE"], [])

    def test_an_over_long_list_is_red_too(self):
        rep = self._run([f"Name {k}" for k in range(1, 6)])
        self.assertEqual(len(rep.zones["RED"]), 1)

    def test_both_directions_cite_the_form(self):
        for found in (["a"], [f"n{k}" for k in range(1, 6)]):
            issue = self._run(found).zones["RED"][0]
            self.assertIn("บฑ.2", issue["found"])
            self.assertIn("บฑ.2", issue["expected"])
            self.assertIn("บฑ.2", issue["fix"])
            # ต้องยกรายชื่อตามฟอร์มมาให้ครบ ไม่ใช่บอกแค่จำนวน
            for name in self.EXPECTED:
                self.assertIn(name, issue["expected"])

    def test_a_matching_count_reports_nothing(self):
        """ควบคุมเชิงลบ — ครบพอดีต้องเงียบ กฎนี้ไม่เทียบชื่อ"""
        rep = self._run(["ใครก็ได้", "อีกคน", "คนที่สาม"])
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(rep.zones["ORANGE"], [])


class ADuplicatedChapterInTheTocSaysWhichOne(unittest.TestCase):
    """สารบัญที่พิมพ์บทซ้ำ ต้องบอกว่าซ้ำบทไหนและอยู่หน้าไหน

    เล่มจริง (ก.ย. 2569) สารบัญหน้า ix พิมพ์ CHAPTER 2 กับ CHAPTER 3 ซ้ำอีกรอบ
    ระบบฟ้องว่า "สารบัญมี 8 บท เนื้อหามี 6 บท / จำนวนบทต้องเท่ากัน" เจ้าหน้าที่เปิด
    เล่มเห็น 6 บทตามที่ควรเป็น เลยนึกว่าระบบนับผิด — เรนเดอร์หน้า ix ออกมาดูแล้ว
    ยืนยันว่าสารบัญพิมพ์ซ้ำจริง ระบบไม่ได้นับผิด แต่ข้อความบอกไม่ตรงปัญหา
    """

    def test_the_finding_names_the_duplicated_chapters(self):
        source = inspect.getsource(checker_module.run_check)
        block = source.split("_toc_numbers = [c[0] for c in toc_ch]", 1)[1][:1400]
        # ต้องบอกทั้งจำนวนบทและชี้บทที่ซ้ำ ในข้อเดียว
        self.assertIn("เพราะสารบัญพิมพ์ซ้ำ", block)
        self.assertIn("สารบัญมี {len(toc_ch)} บท เนื้อหามี {len(body_ch)} บท", block)
        self.assertIn("_toc_chapter_label(n, toc_ch)", block)
        # ต้องชี้หน้าที่ซ้ำ ไม่ใช่ตำแหน่งลอย ๆ ว่า "สารบัญ vs เนื้อหา"
        self.assertIn("page_ref(_at[-1])", block)

    def test_the_plain_count_message_is_the_fallback(self):
        """ควบคุมเชิงลบ — จำนวนไม่เท่ากันโดยไม่มีบทซ้ำ ยังต้องฟ้องแบบเดิม"""
        source = inspect.getsource(checker_module.run_check)
        block = source.split("_toc_numbers = [c[0] for c in toc_ch]", 1)[1][:1400]
        self.assertIn('_want, _fix = "จำนวนบทต้องเท่ากัน"', block)

    def test_the_wording_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        for th in ("สารบัญมี 8 บท เนื้อหามี 6 บท เพราะสารบัญพิมพ์ซ้ำ: "
                   'บทที่ 2 "LITERATURE REVIEW" และ บทที่ 3 "RESEARCH METHODOLOGY"',
                   "แต่ละบทต้องมีรายการเดียวในสารบัญ",
                   "ลบรายการที่ซ้ำออกจากสารบัญ"):
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}: {en}")


class AStaffNoteDoesNotReachTheStudent(unittest.TestCase):
    """ข้อสีเหลืองที่กดไม่ผ่าน ต้องไม่พาท่อนที่เขียนถึงเจ้าหน้าที่ไปในข้อความสรุป

    เจ้าหน้าที่แจ้ง (ก.ย. 2569): กดไม่ผ่านข้อเลขหน้าในสารบัญ แล้วนักศึกษาได้บรรทัด
    "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว" ติดไปด้วย — "เฉพาะส่วนสรุป
    ไม่ต้องมีท่อนนี้" การ์ดในรายงานยังต้องมีเหมือนเดิม
    """

    NOTE = "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว"
    FIX = "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้"

    def _report(self):
        rep = Report()
        rep.add("YELLOW", "front_matter", "สารบัญ (หน้า v) กับบทที่ 2 (หน้า 12)",
                "สารบัญระบุหน้า 12 แต่หัวข้อเริ่มจริงหน้า 14", self.NOTE, self.FIX,
                "FRONT.TOC_PAGE_REF")
        return checker_module.check_result(rep)

    def test_the_rejected_note_keeps_what_was_found_but_not_the_staff_line(self):
        summary = checker_module.plain_summary(self._report(), failed=["YELLOW:0"])
        self.assertIn("สารบัญระบุหน้า 12 แต่หัวข้อเริ่มจริงหน้า 14", summary)
        self.assertNotIn("ไม่ได้ตรวจ", summary)
        self.assertNotIn("เป็นข้อสังเกต", summary)
        self.assertNotIn("ไม่ต้องแก้", summary)

    def test_the_report_card_still_says_it(self):
        """ควบคุมเชิงลบ — ตัดเฉพาะในข้อความสรุป ไม่ใช่ลบออกจากข้อ"""
        issue = self._report()["issues_by_zone"]["YELLOW"][0]
        self.assertEqual(issue["expected"], self.NOTE)
        self.assertEqual(issue["fix"], self.FIX)

    def test_an_ordinary_directive_still_reaches_the_summary(self):
        """ควบคุมเชิงลบ — ข้อที่ไม่มีค่าเดี่ยวให้ดึง ยังต้องได้บรรทัด "ต้องเป็น ..." เหมือนเดิม"""
        rep = Report()
        rep.add("RED", "front_matter", "หน้าลงนาม 1 (หน้า i)", "ไม่พบวันที่สอบ",
                "วันที่บนหน้าลงนาม = วันที่มีผลสอบผ่าน", "", "FORM.APPROVED_MATCH")
        summary = checker_module.plain_summary(checker_module.check_result(rep))
        self.assertIn("วันที่บนหน้าลงนาม = วันที่มีผลสอบผ่าน", summary)

    def test_every_staff_only_line_in_the_engine_is_covered(self):
        """ทุกบรรทัด expected/fix ในเครื่องตรวจที่ขึ้นต้นแบบนี้ ต้องถูกกันไว้"""
        source = inspect.getsource(checker_module)
        for phrase in (self.NOTE, self.FIX):
            self.assertIn(phrase, source)
            self.assertTrue(phrase.startswith(checker_module._STAFF_ONLY_DIRECTIVES), phrase)


class AStudentIdInThaiNumeralsIsRed(unittest.TestCase):
    """เจ้าหน้าที่ยืนยัน (ก.ย. 2569) ว่ารหัสนักศึกษาต้องเป็นเลขอารบิกเท่านั้น

    ของเดิมรหัสที่พิมพ์เป็นเลขไทยถูกนับเป็น "ฟอนต์ทำเพี้ยน อ่านไม่ออก" แล้วผ่านไปเป็นแค่
    ข้อมูลประกอบ ให้เจ้าหน้าที่ไปเปิดดูเอง
    """

    def test_thai_numerals_give_a_red_finding_with_the_arabic_value(self):
        found, expected, fix = checker_module.thai_numeral_student_id(
            "๖๔๓๗๐๒๘ PHPH/M", "6437028 PHPH/M", "บทคัดย่อไทย")
        self.assertIn("เลขไทย", found)
        self.assertIn('"๖๔๓๗๐๒๘ PHPH/M"', found)
        self.assertIn("เลขอารบิก", expected)
        self.assertTrue(expected.endswith('"6437028 PHPH/M"'), expected)
        self.assertIn("เลขอารบิก", fix)

    def test_arabic_numerals_are_left_to_the_usual_checks(self):
        """ควบคุมเชิงลบ — รหัสเลขอารบิกที่พิมพ์ผิดตัวเลข ยังใช้ข้อความเดิม"""
        self.assertIsNone(checker_module.thai_numeral_student_id(
            "6437029 PHPH/M", "6437028 PHPH/M", "บทคัดย่อไทย"))
        self.assertIsNone(checker_module.thai_numeral_student_id(
            "", "6437028 PHPH/M", "บทคัดย่อไทย"))

    def test_the_abstract_check_uses_it_as_red(self):
        source = inspect.getsource(checker_module.run_check)
        block = source.split("thai_digits = thai_numeral_student_id(", 1)[1][:500]
        self.assertIn('rep.add("RED"', block)
        self.assertIn("continue", block)

    def test_the_wording_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        for label in ("บทคัดย่ออังกฤษ", "บทคัดย่อไทย"):
            for th in checker_module.thai_numeral_student_id(
                    "๖๔๓๗๐๒๘ PHPH/M", "6437028 PHPH/M", label):
                en = i18n.tr_en(th, pairs)
                left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
                self.assertEqual(left, [], f"ยังไม่แปล {left}: {en}")


class ADegreeInTheWrongCaseIsRed(unittest.TestCase):
    """/code-review (ก.ย. 2569): ชื่อปริญญาที่ต่างกันแค่ตัวพิมพ์ ถูกลดเป็นเหลืองผ่านได้

    กิ่ง "ต่างเฉพาะวรรคตอน/ช่องว่าง" เทียบด้วย norm() ซึ่งแปลงเป็นตัวใหญ่หมด
    ตัวพิมพ์ผิดจึงตกกิ่งนี้ ได้สีเหลืองพร้อมคำอธิบายที่ผิด แล้วเล่มผ่าน
    """

    def test_a_case_only_difference_is_not_spacing(self):
        for page, want in (("MASTER OF SCIENCE (EPIDEMIOLOGY)",
                            "Master of Science (Epidemiology)"),
                           ("Master of Science (Epidemiology)",
                            "MASTER OF SCIENCE (EPIDEMIOLOGY)"),
                           ("M.SC. (EPIDEMIOLOGY)", "M.Sc. (Epidemiology)")):
            self.assertFalse(
                checker_module.degree_differs_only_in_spacing(want, page), page)

    def test_a_spacing_only_difference_still_is(self):
        """ควบคุมเชิงลบ — ข้อสังเกตสีเหลืองที่เจ้าหน้าที่กำหนดไว้ต้องยังทำงาน"""
        for page, want in (("M.Sc.(Epidemiology)", "M.Sc. (Epidemiology)"),
                           ("Master of Science  (Epidemiology)",
                            "Master of Science (Epidemiology)"),
                           ("วท.ม.(ระบาดวิทยา)", "วท.ม. (ระบาดวิทยา)")):
            self.assertTrue(
                checker_module.degree_differs_only_in_spacing(want, page), page)

    def test_both_degree_checks_use_it(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertEqual(source.count("degree_differs_only_in_spacing("), 2)
        self.assertNotIn("norm(expected_degree) in norm(spot_text)", source)
        self.assertNotIn("norm(abbr) in norm(abstract_text)", source)


class TheExamDateMustNotMatchALongerDay(unittest.TestCase):
    """/code-review (ก.ย. 2569): วันที่อนุมัติ "7" ผ่านบนหน้าที่พิมพ์ "17" หรือ "27"

    ไฟล์ eThesis เขียนวันที่ 1-9 เป็นเลขหลักเดียวเสมอ วันต้นเดือนจึงโดนทุกเล่ม
    """

    def test_a_longer_day_is_not_a_match(self):
        for want, page in (("7 July 2026", "Date 17 July 2026"),
                           ("7 July 2026", "Date 27 July 2026"),
                           ("1 พฤษภาคม 2569", "วันที่ 11 พฤษภาคม พ.ศ. 2569"),
                           ("1 พฤษภาคม 2569", "วันที่ 21 พฤษภาคม 2569")):
            self.assertFalse(checker_module.exam_date_on_page(want, page), page)

    def test_the_right_day_still_matches(self):
        """ควบคุมเชิงลบ — รูปแบบที่เคยผ่านต้องยังผ่าน"""
        for want, page in (("7 July 2026", "Date 7 July 2026"),
                           ("7 July 2026", "Date 07 July 2026"),
                           ("17 July 2026", "on 17 July 2026."),
                           ("11 พฤษภาคม 2569", "วันที่ 11 พฤษภาคม พ.ศ. 2569"),
                           ("7 July 2026", "Page 2" + chr(10) + "7 July 2026")):
            self.assertTrue(checker_module.exam_date_on_page(want, page), page)

    def test_the_signature_page_check_reports_it(self):
        rep = Report()
        checker_module._check_exam_date(rep, "7 July 2026", [0],
                                        ["Date of examination 17 July 2026"],
                                        lambda i: f"หน้า {i + 1}")
        self.assertEqual(len(rep.zones["RED"]), 1)
        self.assertIn("17 July 2026", rep.zones["RED"][0]["found"])


class AGarbledIdIsNotMistakenForASurname(unittest.TestCase):
    """/code-review (ก.ย. 2569): _THAI_LETTER ประกาศสองที่ ตัวหลังทับตัวแรกเงียบ ๆ

    unreadable_id_digits ตั้งใจใช้ "พยัญชนะไทย" แยกนามสกุลไทยที่ค้างในช่องรหัส แต่ได้
    ตัวที่ครอบสระ วรรณยุกต์ และเลขไทยไปแทน ตัวเลขที่ฟอนต์ทำเพี้ยนเป็นเลขไทยหรือสระ
    จึงถูกตัดสินว่าเป็นนามสกุล แล้วฟ้องแดง "ไม่พบรหัสนักศึกษา"
    """

    def test_thai_marks_in_the_id_slot_are_unreadable_digits(self):
        """ฟอนต์ที่ทำตัวเลขเพี้ยนเป็นสระ/วรรณยุกต์ไทย ต้องไม่ถูกนับเป็นนามสกุล"""
        page = "นางสาวทดสอบ ระบบ ิีึืุูั PHPH/M"
        self.assertEqual(
            checker_module.unreadable_id_digits(page, "6437028 PHPH/M"), "ิีึืุูั")

    def test_thai_numerals_are_not_font_damage(self):
        """เลขไทยทั้งช่องอ่านออกชัดเจน แค่ใช้เลขผิดระบบ — ต้องไปทางข้อแดง ไม่ใช่ข้อมูลประกอบ"""
        page = "นางสาวทดสอบ ระบบ ๖๔๓๗๐๒๘ PHPH/M"
        self.assertEqual(
            checker_module.unreadable_id_digits(page, "6437028 PHPH/M"), "")

    def test_a_thai_surname_in_the_slot_is_still_rejected(self):
        """ควบคุมเชิงลบ — เล่มที่ลืมพิมพ์รหัสบนหน้าไทยต้องยังฟ้องได้"""
        page = "นางสาวทดสอบ สมบูรณ์ PHPH/M"
        self.assertEqual(
            checker_module.unreadable_id_digits(page, "6437028 PHPH/M"), "")

    def test_the_two_regexes_no_longer_share_a_name(self):
        source = inspect.getsource(checker_module)
        self.assertEqual(source.count("_THAI_LETTER = re.compile"), 1)
        self.assertIn("_THAI_CONSONANT.search(slot)",
                      inspect.getsource(checker_module.unreadable_id_digits))


class ADotLeaderGluedToThePageNumber(unittest.TestCase):
    """จุดไข่ปลาที่ลากชนเลขหน้าโดยไม่มีช่องว่างคั่น ("RESULTS.........45")

    เจ้าหน้าที่สั่งแก้ (ก.ย. 2569) เพราะทำให้อ่านชื่อบทพลาด — พอไล่ดูทั้งเส้นทาง
    พบว่าไม่ได้พลาดแค่ชื่อบท ตัวอ่านบรรทัดสารบัญทุกตัวใช้ทางเดียวกันหมด จึงพลาด
    เป็นลูกโซ่ ต้องแก้ที่ต้นทาง (space_dot_leader) ไม่ใช่แก้ทีละที่
    """

    GLUED = ["ACKNOWLEDGEMENTS.........iii",
             "ABSTRACT (ENGLISH).........iv",
             "LIST OF TABLES.........vii",
             "CHAPTER 1 INTRODUCTION.........1",
             "CHAPTER 4 RESULTS.........45",
             "REFERENCES.........130"]

    def test_the_page_is_still_recognised_as_a_table_of_contents(self):
        """อาการหนักสุด — เดิมมองไม่ออกว่าเป็นหน้าสารบัญ แล้วฟ้องว่าไม่พบหน้าสารบัญ"""
        page = "TABLE OF CONTENTS" + chr(10) + chr(10).join(self.GLUED)
        self.assertTrue(checker_module.looks_like_contents_page(page))

    def test_the_heading_no_longer_carries_the_dots_and_the_page_number(self):
        for raw, want in zip(self.GLUED,
                             ["ACKNOWLEDGEMENTS", "ABSTRACT (ENGLISH)",
                              "LIST OF TABLES", "CHAPTER 1 INTRODUCTION",
                              "CHAPTER 4 RESULTS", "REFERENCES"]):
            self.assertEqual(checker_module._strip_toc_page_number(raw), want, raw)

    def test_the_page_number_is_readable_through_the_dots(self):
        """เดิมอ่านไม่เจอ แล้วฟ้องว่ารายการนี้ไม่ระบุเลขหน้า"""
        for raw, want in zip(self.GLUED, ["iii", "iv", "vii", "1", "45", "130"]):
            self.assertEqual(checker_module._toc_page_label(raw), want, raw)

    def test_thai_page_letters_and_ranges_work_too(self):
        for raw, head, label in (
                ("สารบัญตาราง.........ฎ", "สารบัญตาราง", "ฎ"),
                ("LIST OF TABLES.........xi-xii", "LIST OF TABLES", "xi"),
                ("บทที่ 6 สรุปผลการวิจัย…………120", "บทที่ 6 สรุปผลการวิจัย", "120")):
            self.assertEqual(checker_module._strip_toc_page_number(raw), head, raw)
            self.assertEqual(checker_module._toc_page_label(raw), label, raw)

    def test_dots_inside_a_heading_are_left_alone(self):
        """ควบคุมเชิงลบ — ตัดเฉพาะชุดจุดที่ลากไปหาเลขหน้าท้ายบรรทัดเท่านั้น"""
        for raw, want in (("CHAPTER 3 THE U.S. CONTEXT 30", "CHAPTER 3 THE U.S. CONTEXT"),
                          ("CHAPTER 3 THE U.S. CONTEXT.........30",
                           "CHAPTER 3 THE U.S. CONTEXT"),
                          ("ACKNOWLEDGEMENTS iii", "ACKNOWLEDGEMENTS")):
            self.assertEqual(checker_module._strip_toc_page_number(raw), want, raw)

    def test_a_line_with_no_dot_leader_is_untouched(self):
        for raw in ("LIST OF TABLES xi", "บทที่ 2 การทบทวนวรรณกรรม 12",
                    "TABLE OF CONTENTS"):
            self.assertEqual(checker_module.space_dot_leader(raw), raw, raw)

    def test_the_trailing_leader_without_a_page_number_still_goes(self):
        """ของเดิมรองรับอยู่แล้ว ต้องไม่หายไปตอนย้ายมาที่ต้นทาง"""
        self.assertEqual(
            checker_module._strip_toc_page_number("LIST OF TABLES ................"),
            "LIST OF TABLES")


class TheDuplicatedChapterIsNamedInTheBooksOwnLanguage(unittest.TestCase):
    """ข้อสารบัญซ้ำต้องบอกชื่อบทด้วย เล่มไทยได้ชื่อไทย เล่มอังกฤษได้ชื่ออังกฤษ

    เจ้าหน้าที่สั่ง (ก.ย. 2569) — บอกแค่ "บทที่ 2 และ บทที่ 3" ยังต้องเปิดสารบัญ
    ไล่หาเองว่าบรรทัดไหน ชื่อบทอ่านจากบรรทัดในสารบัญของเล่มเอง จึงเป็นภาษาเดียว
    กับเล่มโดยไม่ต้องเดา
    """

    # โครงเดียวกับ toc_ch ใน run_check: (เลขบท, ชื่อ norm, เลขหน้า, บรรทัดดิบ, หน้า, บรรทัดถัดไป)
    EN = [(2, "", 12, "CHAPTER 2 LITERATURE REVIEW 12", 8, ""),
          (2, "", 12, "CHAPTER 2 LITERATURE REVIEW 12", 8, ""),
          (3, "", 30, "CHAPTER 3 RESEARCH METHODOLOGY 30", 8, "")]
    TH = [(2, "", 12, "บทที่ 2 การทบทวนวรรณกรรม 12", 8, ""),
          (2, "", 12, "บทที่ 2 การทบทวนวรรณกรรม 12", 8, "")]

    def test_an_english_book_gets_the_english_title(self):
        self.assertEqual(checker_module._toc_chapter_label(2, self.EN),
                         'บทที่ 2 "LITERATURE REVIEW"')

    def test_a_thai_book_gets_the_thai_title(self):
        self.assertEqual(checker_module._toc_chapter_label(2, self.TH),
                         'บทที่ 2 "การทบทวนวรรณกรรม"')

    def test_the_title_is_quoted_so_the_english_report_keeps_it_verbatim(self):
        """ค่าที่อ่านได้จากเล่มต้องไม่ถูกแปล — เครื่องหมายคำพูดคือสิ่งที่กันไว้"""
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        th = ("สารบัญมี 4 บท เนื้อหามี 3 บท เพราะสารบัญพิมพ์ซ้ำ: "
              + checker_module._toc_chapter_label(2, self.TH))
        en = i18n.tr_en(th, pairs)
        self.assertIn('"การทบทวนวรรณกรรม"', en)
        self.assertIn("Chapter 2", en)

    def test_two_different_titles_under_one_number_are_both_shown(self):
        entries = [(2, "", 12, "CHAPTER 2 LITERATURE REVIEW 12", 8, ""),
                   (2, "", 40, "CHAPTER 2 RESULTS 40", 8, "")]
        self.assertEqual(checker_module._toc_chapter_label(2, entries),
                         'บทที่ 2 "LITERATURE REVIEW" / "RESULTS"')

    def test_a_chapter_line_without_a_title_falls_back_to_the_number(self):
        """ควบคุมเชิงลบ — อ่านชื่อไม่ได้ต้องไม่พังและไม่พิมพ์คำพูดเปล่า"""
        entries = [(2, "", None, "CHAPTER 2", 8, "")]
        self.assertEqual(checker_module._toc_chapter_label(2, entries), "บทที่ 2")

    def test_other_chapters_are_not_pulled_in(self):
        self.assertEqual(checker_module._toc_chapter_label(3, self.EN),
                         'บทที่ 3 "RESEARCH METHODOLOGY"')

    def _one(self, number, *raws):
        return checker_module._toc_chapter_label(
            number, [(number, "", None, r, 9, "") for r in raws])

    def test_every_chapter_number_works_not_just_two_and_three(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าต้องรองรับบทอื่นด้วย

        เล่มที่พบปัญหาบังเอิญซ้ำบทที่ 2 กับ 3 กฎนี้ไม่ได้ผูกกับเลขบทใด
        """
        for number, raw, want in (
                (1, "CHAPTER 1 INTRODUCTION", "INTRODUCTION"),
                (4, "CHAPTER 4 RESULTS 45", "RESULTS"),
                (6, "CHAPTER 6 CONCLUSION AND RECOMMENDATIONS 120",
                 "CONCLUSION AND RECOMMENDATIONS"),
                (10, "CHAPTER 10 APPENDIX OVERVIEW 200", "APPENDIX OVERVIEW")):
            self.assertEqual(self._one(number, raw), f'บทที่ {number} "{want}"', raw)

    def test_a_roman_numeral_entry_reads_the_same(self):
        """เล่มจริงปนเลขโรมันกับอารบิกในสารบัญเดียวกันได้"""
        self.assertEqual(self._one(2, "CHAPTER II LITERATURE REVIEW 12",
                                   "CHAPTER 2 LITERATURE REVIEW 12"),
                         'บทที่ 2 "LITERATURE REVIEW"')

    def test_a_dot_leader_glued_to_the_page_number_is_stripped(self):
        """"RESULTS.........45" ไม่มีช่องว่างคั่น ตัวตัดเลขหน้าจึงตัดไม่ได้เอง"""
        for raw in ("CHAPTER 4 RESULTS.................45",
                    "CHAPTER 4 RESULTS ................. 45",
                    "CHAPTER 4 RESULTS…………45"):
            self.assertEqual(self._one(4, raw), 'บทที่ 4 "RESULTS"', raw)

    def test_a_title_with_abbreviation_dots_survives(self):
        """ควบคุมเชิงลบของตัวตัดจุดไข่ปลา — จุดในตัวย่อต้องไม่ถูกกิน"""
        self.assertEqual(self._one(3, "CHAPTER 3 THE U.S. CONTEXT 30"),
                         'บทที่ 3 "THE U.S. CONTEXT"')

    def test_three_identical_entries_still_show_one_title(self):
        self.assertEqual(self._one(5, "CHAPTER 5 DISCUSSION 90",
                                   "CHAPTER 5 DISCUSSION 90",
                                   "CHAPTER 5 DISCUSSION 90"),
                         'บทที่ 5 "DISCUSSION"')

    def test_the_thai_book_reads_the_same_across_chapters(self):
        self.assertEqual(
            self._one(6, "บทที่ 6 สรุปผลการวิจัยและข้อเสนอแนะ.........120"),
            'บทที่ 6 "สรุปผลการวิจัยและข้อเสนอแนะ"')


class TooManyKeywordsIsOnlyANotice(unittest.TestCase):
    """keyword เกิน 5 คำ = ข้อสังเกตสีเหลือง ผ่านได้ (เจ้าหน้าที่สั่ง ก.ย. 2569)

    เดิมเป็นสีแดง ซึ่งทำให้เล่มที่เกินมาคำเดียวตกทั้งเล่ม
    """

    def test_the_zone_comes_from_the_rule_catalog(self):
        self.assertEqual(RULE_CATALOG["FRONT.KEYWORD_COUNT"]["failure_zone"], "YELLOW")
        self.assertEqual(checker_module.KEYWORD_COUNT_ZONE, "YELLOW")

    def test_the_finding_uses_that_zone(self):
        source = inspect.getsource(checker_module.run_check)
        block = source.split("if len(kws) > 5:", 1)[1][:400]
        self.assertIn("rep.add(KEYWORD_COUNT_ZONE", block)
        self.assertIn('"FRONT.KEYWORD_COUNT"', block)

    def test_the_other_abstract_rules_stay_red(self):
        """ควบคุมเชิงลบ — แยกรหัสกฎออกมาเพื่อไม่ให้ลากกฎอื่นของบทคัดย่อเป็นเหลืองด้วย"""
        self.assertNotIn("failure_zone", RULE_CATALOG["FRONT.ABSTRACT"])


class TheKeywordListIsCountedAcrossLines(unittest.TestCase):
    """รายการ keyword ที่ยาวเกินบรรทัดเดียว ต้องนับให้ครบ (เจ้าหน้าที่สั่ง ก.ย. 2569)

    เล่มจริงฝั่งอังกฤษมี 6 คำ แต่คำที่ 5-6 ตกไปบรรทัดถัดไป ระบบอ่านแค่บรรทัดแรกจึง
    นับได้ 4 แล้วปล่อยผ่าน ทั้งที่ฝั่งไทยของเล่มเดียวกันโดนฟ้อง 6 คำ

        KEYWORDS: E-cigarette control policy / Electronic cigarettes / Prevalence / Forecasting /
        Markov model / Population Attributable Fraction
        64 pages

    สำรวจเล่มจริง 5 เล่ม (ทั้งไทยและอังกฤษ) รายการ keyword จบด้วยบรรทัด
    "N pages" / "N หน้า" เสมอ จึงใช้บรรทัดนั้นเป็นจุดหยุดได้
    """

    def test_the_page_count_line_is_the_stop_signal(self):
        for line in ("64 pages", "157 pages", "106 หน้า", "79 หน้า"):
            self.assertTrue(checker_module.is_page_count_line(line), line)

    def test_ordinary_keyword_lines_are_not_mistaken_for_it(self):
        """ควบคุมเชิงลบ — ถ้าจับผิด รายการ keyword จะถูกตัดกลางคัน"""
        for line in ("Markov model / Population Attributable Fraction",
                     "ควบคุมบุหรีไฟฟ้า",
                     "Immunomodulation / Lactiplantibacillus plantarum"):
            self.assertFalse(checker_module.is_page_count_line(line), line)

    def test_the_reader_stops_at_the_page_count(self):
        """โครงของตัวนับต้องหยุดที่บรรทัดจำนวนหน้า ไม่ใช่กวาดไปทั้งหน้า"""
        source = inspect.getsource(checker_module.run_check)
        block = source.split("nl.startswith('KEYWORD')", 1)[1][:900]
        self.assertIn("is_page_count_line(more)", block)
        self.assertIn("raws[kw_idx + 1:kw_idx + 4]", block)


class OneMisspelledTocHeadingIsOneFinding(unittest.TestCase):
    """หัวข้อสารบัญที่สะกดผิด ต้องฟ้องข้อเดียว ไม่ใช่สองข้อจากสองกฎ

    เล่มจริง (ก.ย. 2569) เขียน "LIST OF TABLES (If any)" แล้วได้สองการ์ด

        FRONT.TOC          หัวข้อสารบัญในเล่มเขียนว่า "LIST OF TABLES (If any)"
        FRONT.TOC_CONTENT  สารบัญสะกดหัวข้อนี้ผิด เขียนว่า "..." มี "(If any)" เกินมา

    ข้อความสรุปที่ส่งนักศึกษารวมให้อยู่แล้ว แต่หน้ารายงานฝั่งเจ้าหน้าที่ยังเห็นซ้ำ
    เจ้าหน้าที่สั่งให้ยุบเหลือของ FRONT.TOC_CONTENT เพราะบอกได้ว่าเกินคำไหน
    """

    def test_the_generic_rule_waits_for_the_specific_one(self):
        source = inspect.getsource(checker_module.run_check)
        # เก็บไว้ก่อน ไม่ฟ้องทันทีตอนเจอ
        self.assertIn("toc_list_typos.append(", source)
        # กรองด้วยหัวข้อที่กฎเจาะจงฟ้องไปแล้ว
        self.assertIn("if norm(t[1]) not in toc_typos_reported]", source)

    def test_the_generic_rule_is_not_deleted(self):
        """ควบคุมเชิงลบ — ยังต้องฟ้องได้ ถ้ากฎเจาะจงไม่ได้แตะหัวข้อนั้น

        กฎเจาะจงทำงานเฉพาะกับส่วนที่ "มีอยู่จริงในเล่มแต่หายจากสารบัญ" ส่วนหัวข้อ
        LIST OF ... ที่สะกดผิดโดยไม่มีส่วนนั้นในเล่ม ยังต้องพึ่งกฎนี้อยู่
        """
        source = inspect.getsource(checker_module.run_check)
        tail = source.split("for _idx, _visible, _expected, _compared in toc_list_typos:",
                            1)[1][:400]
        self.assertIn('"FRONT.TOC"', tail)
        self.assertIn("แก้การสะกดหัวข้อสารบัญ", tail)


class DecisionsThatDeliberatelyChangeNothing(unittest.TestCase):
    """คำตัดสินของเจ้าหน้าที่ที่ "ให้คงไว้อย่างเดิม" (ก.ย. 2569)

    เขียนเป็นเทสต์ไว้เพราะการคงไว้ก็เป็นคำตัดสิน ไม่ใช่เรื่องที่ยังไม่ได้พิจารณา
    ถ้าไม่ล็อกไว้ คนที่มาอ่านทีหลังจะเห็นว่า "น่าจะปรับได้" แล้วเปลี่ยนโดยไม่รู้
    """

    def test_a_toc_entry_without_a_page_number_stays_yellow_per_chapter(self):
        """เล่มจริงได้ข้อสังเกต 6 ข้อจากรูปแบบสารบัญเดียวกัน เจ้าหน้าที่สั่งว่า
        "ตรวจเหมือนเดิม แต่ฟ้องเหลือง" คือไม่ยุบเป็นข้อเดียว และไม่ยกระดับสี
        """
        self.assertEqual(checker_module.TOC_PAGE_ZONE, "YELLOW")
        self.assertEqual(RULE_CATALOG["FRONT.TOC_PAGE_REF"]["failure_zone"], "YELLOW")
        source = inspect.getsource(checker_module.run_check)
        block = source.split('if not entry["page_label"]:', 1)[1][:320]
        self.assertIn("TOC_PAGE_ZONE", block)

    def test_stray_text_in_the_running_head_stays_orange(self):
        """ชื่อรูปที่วางล้ำเข้าเขตหัวกระดาษ — เจ้าหน้าที่ยืนยันว่าสีส้มถูกแล้ว
        (เล่มจริงหน้า 38 วางชื่อรูปที่ top 48.6 ซึ่งอยู่ในขอบบน 72 pt)
        """
        source = inspect.getsource(checker_module.run_check)
        block = source.split("พบข้อความอื่นนอกจากเลขหน้าในหัวกระดาษ", 1)[0][-400:]
        self.assertIn('rep.add("ORANGE"', block)


class TwoColumnsOnTheSameRowStayOnOneLine(unittest.TestCase):
    """หน้าลงนามเป็นสองคอลัมน์ อักขระที่อยู่แถวเดียวกันต้องอยู่บรรทัดเดียวกัน

    ของเดิมจัดบรรทัดด้วย round(top / 3.0) ซึ่งมีขอบช่องตายตัว เล่มจริง (ก.ย. 2569)
    ชื่อสองคอลัมน์อยู่ที่ top 283.4 กับ 283.9 ห่างกันแค่ 0.5 pt แต่คร่อมขอบช่องพอดี
    จึงถูกแยกเป็นสองบรรทัด แล้วบรรทัดเหนือคำว่า Candidate กลายเป็นชื่อของคอลัมน์ขวา

        First Name Last name,      <- ช่องชื่อนักศึกษา (ยังไม่ได้กรอก)
        Chayanan Sittibusaya,      <- ประธานกรรมการ คอลัมน์ขวา
        Candidate MD.

    ระบบจึงรายงานว่า "ชื่อนักศึกษาในเล่มเขียนว่า Chayanan Sittibusaya"
    ทั้งที่เจ้าหน้าที่เห็นว่าเป็น "First Name Last name"
    """

    @staticmethod
    def _chars(rows):
        out = []
        for top, x0, text in rows:
            out.append({"text": text, "top": top, "x0": x0, "x1": x0 + 6,
                        "size": 16.0, "upright": True})
        return out

    def test_half_a_point_apart_is_the_same_line(self):
        rows = [(283.4, 48.0, "A"), (283.9, 310.8, "B")]
        lines = checker_module._group_into_lines(self._chars(rows))
        self.assertEqual(len(lines), 1)

    def test_a_real_next_line_still_splits(self):
        """ควบคุมเชิงลบ — ระยะบรรทัดจริงของเล่มห่าง 13-18 pt ต้องไม่ถูกยุบรวม"""
        rows = [(283.4, 48.0, "A"), (297.1, 48.0, "B")]
        lines = checker_module._group_into_lines(self._chars(rows))
        self.assertEqual(len(lines), 2)

    def test_a_long_line_does_not_creep_into_the_next_one(self):
        """เทียบระยะกับตัวแรกของบรรทัด ไม่ใช่ตัวก่อนหน้า ไม่งั้นไหลไปทีละ 3 pt เรื่อย ๆ"""
        rows = [(100.0, 10.0, "A"), (102.0, 20.0, "B"), (104.0, 30.0, "C"),
                (106.0, 40.0, "D")]
        lines = checker_module._group_into_lines(self._chars(rows))
        self.assertGreater(len(lines), 1)

    def test_the_student_slot_is_the_left_column(self):
        page = NEWLINE.join([
            "……………………………………… ………………………………………",
            "First Name Last name, Chayanan Sittibusaya,",
            "Candidate MD.",
        ])
        self.assertEqual(checker_module.signature_printed_name(page),
                         "First Name Last name")


class PagesWhoseFontTurnsDigitsIntoLetters(unittest.TestCase):
    """หน้าที่ฟอนต์ทำให้ตัวเลขกลายเป็นตัวอักษร ต้องไม่ถูกฟ้องว่าเล่มพิมพ์ผิด

    เล่มจริงเล่มหนึ่งฝังฟอนต์ย่อย (subset) ที่ตาราง ToUnicode ผิด เฉพาะหน้าบทคัดย่อไทย
    รหัส "6437028" ถูกดึงออกมาเป็น "JKLMNOP" ส่วนหน้าบทคัดย่ออังกฤษของเล่มเดียวกัน
    อ่านได้ถูกต้อง เล่มไม่ได้ผิด ระบบอ่านไม่ออกเอง เดิมฟ้องแดงสองข้อพร้อมกัน
    (ชื่อสะกดผิด + ไม่พบรหัสนักศึกษา) แล้วสั่งให้แก้ข้อความที่ถูกอยู่แล้ว
    """

    ID = "6437028 PHPH/M"
    NAMES = ("KEERATI YOUPRASIT", "กีรติ อยู่ประสิทธิ์")

    def test_a_readable_page_reports_nothing(self):
        line = "KEERATI YOUPRASIT 6437028 PHPH/M"
        self.assertEqual(
            checker_module.unreadable_id_digits(line, self.ID, self.NAMES), "")

    def test_the_misread_digits_are_returned(self):
        line = "กีรติ ยุประสิทธิ JKLMNOP PHPH/M"
        self.assertEqual(
            checker_module.unreadable_id_digits(line, self.ID, self.NAMES), "JKLMNOP")

    def test_a_book_that_really_left_the_id_out_is_still_reported(self):
        """เล่มที่ลืมพิมพ์รหัสจริง ๆ ต้องยังโดนฟ้อง ไม่ใช่ถูกกลบด้วยกฎนี้"""
        for line in ("KEERATI YOUPRASIT PHPH/M", "กีรติ อยู่ประสิทธิ์ PHPH/M"):
            self.assertEqual(
                checker_module.unreadable_id_digits(line, self.ID, self.NAMES), "", line)

    def test_a_name_word_in_the_slot_is_not_mistaken_for_broken_digits(self):
        """นามสกุลยาวเท่าจำนวนหลักพอดี ต้องไม่ถูกนับว่าเป็นเลขที่อ่านไม่ออก"""
        line = "SOMCHAI JAIDEEX PHPH/M"
        self.assertEqual(
            checker_module.unreadable_id_digits(line, self.ID,
                                                ("SOMCHAI JAIDEEX",)), "")

    def test_the_length_must_match_the_approved_id(self):
        """ข้อมูลระบบบอกว่ารหัสมีกี่หลัก ใช้เป็นเงื่อนไขได้ ไม่ต้องเดา"""
        self.assertEqual(
            checker_module.unreadable_id_digits("ก ข JKLM PHPH/M", self.ID,
                                                self.NAMES), "")

    def test_a_page_without_the_programme_code_is_left_alone(self):
        self.assertEqual(
            checker_module.unreadable_id_digits("กีรติ ยุประสิทธิ JKLMNOP",
                                                self.ID, self.NAMES), "")

    def test_no_approved_id_means_nothing_to_compare(self):
        self.assertEqual(
            checker_module.unreadable_id_digits("กีรติ JKLMNOP PHPH/M", "",
                                                self.NAMES), "")

    def test_digits_turned_into_punctuation_are_caught_too(self):
        """อีกเล่มหนึ่ง (ก.ย. 2569) ฟอนต์เดียวกันเพี้ยนคนละแบบ

        เลข "6636480" ออกมาเป็น ",,-,./0" คือกลายเป็นเครื่องหมายวรรคตอน และรหัส
        หลักสูตรถูกแทรกช่องว่างเป็น "PHIE / M" จนแตกเป็นสามคำ ของเดิมจับได้เฉพาะ
        ตัวอักษรอังกฤษล้วนกับรหัสหลักสูตรคำเดียว เล่มนี้จึงโดนฟ้องแดงว่าไม่พบรหัส
        ทั้งที่พิมพ์อยู่ครบ
        """
        line = "อรณิชา หนูนาค ,,-,./0 PHIE / M"
        self.assertEqual(
            checker_module.unreadable_id_digits(
                line, "6636480 PHIE/M", ("ONNICHA NOONAK", "อรณิชา หนูนาค")),
            ",,-,./0")

    def test_a_thai_surname_in_the_slot_is_not_mistaken_for_broken_digits(self):
        """ควบคุมเชิงลบ — เล่มไทยที่ลืมพิมพ์รหัสจะเหลือนามสกุลไทยอยู่ตรงนั้น

        ยาวเท่าจำนวนหลักพอดีได้ จึงต้องกันด้วย "ห้ามมีอักษรไทย" ไม่ใช่ความยาวอย่างเดียว
        """
        self.assertEqual(
            checker_module.unreadable_id_digits(
                "อรณิชา หนูนาคสกุล PHIE / M", "6636480 PHIE/M",
                ("ONNICHA NOONAK",)), "")

    def test_the_page_is_not_reported_as_a_problem(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าห้ามฟ้องหน้านี้เป็นจุดผิด

        เรนเดอร์หน้าจริงออกมาดูแล้ว หน้ากระดาษถูกต้องทุกตัวอักษร เห็นรหัส 6437028
        ชัดเจน เสียแค่การดึงข้อความ (PyMuPDF ซึ่งเป็นคนละเอนจินก็ได้ JKLMNOP
        เหมือนกัน) ข้อสีส้มใบเดิมพูดซ้ำกับตารางผลเทียบที่บันทึกไว้อยู่แล้ว และยัง
        ลากผลตรวจของทั้งเล่มไปค้างที่ "รอยืนยัน" ด้วย
        """
        source = inspect.getsource(checker_module.run_check)
        head = source.split("unreadable_digit_pages = {}", 1)[1][:1400]
        self.assertIn("rep.add_info", head)
        self.assertNotIn('"UNCERTAIN.REVIEW", system_note=True', head)

    def test_staff_are_told_to_open_the_page_themselves(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่า "ปล่อยผ่าน ทำเพียงแจ้งบอกว่าเกิดปัญหาอะไร
        ให้เจ้าหน้าที่ทราบและต้องไปดูเอง" — คำสั่งให้ไปดูต้องอยู่ในบรรทัดนั้นด้วย
        ไม่งั้นอ่านแล้วไม่รู้ว่าต้องทำอะไรต่อ
        """
        source = inspect.getsource(checker_module.run_check)
        head = source.split("unreadable_digit_pages = {}", 1)[1][:3200]
        self.assertIn("กรุณาเปิดหน้านี้ดูรหัสนักศึกษาด้วยตาอีกครั้ง", head)

    def test_the_name_on_that_page_is_still_compared(self):
        """ของเดิมข้ามทั้งหน้า ทั้งที่ชื่อบนหน้านั้นอ่านได้ปกติ

        เล่มจริงเทียบชื่อไทยบนหน้าเดียวกันได้ 1.00 เต็ม (วรรณยุกต์ที่หายไปถูก norm
        ตัดทิ้งอยู่แล้ว) การข้ามทั้งหน้าจึงทิ้งผลที่ใช้ได้ไปเปล่า ๆ
        """
        source = inspect.getsource(checker_module.run_check)
        self.assertIn(
            "if aidx in unreadable_digit_pages and compared['status'] != 'exact':",
            source)

    def test_a_mismatched_name_on_that_page_is_never_accused(self):
        """ควบคุมเชิงลบของข้อบน — ชื่อที่เทียบไม่ตรงบนหน้าที่ฟอนต์เสีย แยกไม่ออกว่า
        เล่มพิมพ์ผิดหรือระบบอ่านมาไม่ครบ ต้องลงเป็น pending ไม่ใช่ฟ้องแดง
        """
        source = inspect.getsource(checker_module.run_check)
        block = source.split(
            "if aidx in unreadable_digit_pages and compared['status'] != 'exact':",
            1)[1][:700]
        self.assertIn('"pending", "ระบบอ่านข้อความบนหน้านี้ไม่ครบ"', block)
        self.assertNotIn('rep.add("RED"', block)

    def test_the_wording_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        for th in ("ระบบไม่ได้เทียบรหัสนักศึกษาที่ บทคัดย่อไทย (หน้า vi)",
                   "ฟอนต์ที่ฝังมาในไฟล์ทำให้ตัวเลขถูกดึงออกมาเป็น \"JKLMNOP\" "
                   "ส่วนหน้ากระดาษแสดงผลถูกต้องตามปกติ "
                   "และรหัสนักศึกษาถูกเทียบกับข้อมูลอนุมัติที่หน้าอื่นแล้ว "
                   "กรุณาเปิดหน้านี้ดูรหัสนักศึกษาด้วยตาอีกครั้ง",
                   "ระบบอ่านตัวเลขบนหน้านี้ไม่ออก",
                   "ระบบอ่านข้อความบนหน้านี้ไม่ครบ"):
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}: {en}")


class ContentsPagesAndTheirContinuationAreOneHeading(unittest.TestCase):
    """สารบัญ กับ สารบัญ (ต่อ) คือหัวข้อเดียวกัน (เจ้าหน้าที่สั่ง ก.ย. 2569)

    หน้าต่อของสารบัญหน้าสุดท้ายมักเหลือแค่ 1-2 รายการ คือภาคผนวกกับประวัติผู้วิจัย
    ซึ่งไม่ถึงเกณฑ์ "3 บรรทัดที่ลงท้ายด้วยเลขหน้า" ที่ใช้หาหน้าต่อ แล้วถูกตัดทิ้งทั้งหน้า
    ระบบจึงฟ้องผิดว่า "ไม่พบหัวข้อ ประวัติผู้วิจัย ในสารบัญ" ทั้งที่พิมพ์ไว้ครบ

    วัดกับเล่มจริง: ย้ายท้ายสารบัญ 2 บรรทัดไปหน้าถัดไปแล้วใส่หัวข้อ
    "TABLE OF CONTENTS (Cont.)" ระบบฟ้องผิด 1 ข้อ หลังแก้ไม่ฟ้องแล้ว
    """

    HEADS = ("สารบัญ", "สารบัญ (ต่อ)", "TABLE OF CONTENTS",
             "TABLE OF CONTENTS (Cont.)", "TABLE OF CONTENTS (CONT)",
             "TABLE OF CONTENTS (cont.)", "TABLE OF CONTENTS (Continued)")

    def test_every_spelling_counts_as_the_contents_heading(self):
        for head in self.HEADS:
            self.assertTrue(checker_module.is_toc_heading(head), head)

    def test_the_page_is_classified_as_contents(self):
        for head in self.HEADS:
            page = NEWLINE.join(["viii", head, "APPENDIX D 95", "BIOGRAPHY 97"])
            self.assertEqual(checker_module.front_section_kind(page)[0], "toc", head)

    def test_a_short_continuation_page_is_still_part_of_the_contents(self):
        """เกณฑ์จำนวนบรรทัดต้องไม่ตัดหน้าที่พิมพ์ "(ต่อ)" ไว้ชัด ๆ ทิ้ง"""
        pages = [
            NEWLINE.join(["vi", "TABLE OF CONTENTS", "ACKNOWLEDGEMENTS iii",
                          "ABSTRACT iv", "CHAPTER 1 INTRODUCTION 1", "REFERENCES 52"]),
            NEWLINE.join(["vii", "TABLE OF CONTENTS (Cont.)", "BIOGRAPHY 97"]),
        ]
        self.assertEqual(checker_module._toc_continuation_pages(pages, 0, len(pages)),
                         [0, 1])

    def test_the_continuation_heading_is_what_keeps_it(self):
        """ควบคุมเชิงลบ: ไม่มีหัวข้อ (ต่อ) หน้าที่มีรายการเดียวจะถูกตัดทิ้งตามเกณฑ์เดิม"""
        pages = [
            NEWLINE.join(["vi", "TABLE OF CONTENTS", "ACKNOWLEDGEMENTS iii",
                          "ABSTRACT iv", "CHAPTER 1 INTRODUCTION 1", "REFERENCES 52"]),
            NEWLINE.join(["vii", "BIOGRAPHY 97"]),
        ]
        self.assertEqual(checker_module._toc_continuation_pages(pages, 0, len(pages)), [0])

    def test_other_words_in_brackets_are_not_a_continuation(self):
        """ต้องแคบ ห้ามเหมาว่าวงเล็บอะไรก็ได้คือหน้าต่อ"""
        for head in ("TABLE OF CONTENTS (Chapter 3)", "LIST OF TABLES",
                     "LIST OF TABLES (Cont.)"):
            self.assertFalse(checker_module.is_toc_heading(head), head)

    def test_the_wrong_wording_is_reported_once_not_once_per_page(self):
        """เล่มที่เขียน CONTENTS ทั้งสองหน้า ต้องได้ข้อฟ้องถ้อยคำข้อเดียว"""
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("not toc_pages[:-1]", source)

    def test_stripping_the_marker_leaves_the_plain_heading(self):
        self.assertEqual(checker_module.without_continuation("TABLE OF CONTENTS (Cont.) vii"),
                         "TABLE OF CONTENTS")
        self.assertEqual(checker_module.without_continuation("สารบัญ (ต่อ)"), "สารบัญ")
        self.assertEqual(checker_module.without_continuation("APPENDIX A (Data)"),
                         "APPENDIX A (Data)")


class AppendixSingularAndPluralAreOneHeading(unittest.TestCase):
    """APPENDIX กับ APPENDICES คือหัวข้อเดียวกัน (เจ้าหน้าที่ยืนยัน ก.ย. 2569)

    ระบบรู้จักทั้งสองแบบอยู่แล้ว เทสต์ชุดนี้ล็อกไว้ไม่ให้หลุดตอนแก้กฎอื่น เพราะถ้าหลุด
    เล่มที่เขียน APPENDICES จะถูกฟ้องว่า "ไม่พบหัวข้อ ภาคผนวก ในสารบัญ" ทั้งที่มีอยู่
    """

    def test_both_spellings_are_the_appendix_entry_in_the_contents(self):
        for line in ("APPENDIX", "APPENDICES", "APPENDIX 130", "APPENDICES 130",
                     "APPENDIX A SUPPLEMENTARY 60", "ภาคผนวก", "ภาคผนวก ก 90"):
            self.assertEqual(checker_module._toc_section_kind(line), "appendix", line)

    def test_both_spellings_are_known_appendix_words(self):
        for word in ("APPENDIX", "APPENDICES"):
            self.assertTrue(any(checker_module.norm(word).startswith(term)
                                for term in N_APPENDIX), word)

    def test_the_contents_and_the_page_may_use_different_spellings(self):
        """สารบัญเขียน APPENDICES แต่หน้าจริงเขียน APPENDIX ต้องยังจับคู่กันได้"""
        self.assertEqual(checker_module._toc_section_kind("APPENDICES 130"),
                         checker_module._toc_section_kind("APPENDIX 130"))


class StaffChecksThatAddTheirOwnWordingToTheSummary(unittest.TestCase):
    """สิ่งที่ระบบตัดสินเองไม่ได้ เจ้าหน้าที่กดปุ่มแล้วถ้อยคำเข้าข้อความสรุปทันที

    เจ้าหน้าที่สั่ง (ก.ย. 2569) สามหัวข้อ
      โครงสร้างหน้าลงนาม — ระบบอ่านกรอบตาราง ลำดับการวางชื่อ ฟอนต์ และช่องที่ต้อง
        ถมขาว จาก PDF ไม่ได้ ต้องใช้สายตาคน
      เล่มไม่ผ่าน: มีค่าปรับไหม — ถ้อยคำ "แก้แล้วส่งกลับเข้าระบบ"
      เล่มผ่าน: ผ่านแบบมีค่าปรับหรือไม่ — ถ้อยคำ "เสร็จสิ้นแล้ว" กับขั้นตอนถัดไป

    สองหัวข้อหลังถามเรื่องเดียวกัน (ค่าปรับ) แต่ถ้อยคำขัดกันเอง จึงผูกกับผลตรวจคนละค่า
    ทุกหัวข้อมีถ้อยคำที่ส่งให้นักศึกษาเป็นชุดเดียวกันทุกเล่ม เจ้าหน้าที่จึงพิมพ์ย่อหน้าเดิม
    ซ้ำเองทุกครั้ง ถ้อยคำทั้งไทยและอังกฤษเจ้าหน้าที่เขียนมาเอง ระบบห้ามเรียบเรียงใหม่
    """

    WRONG = "SIGNATURE_LAYOUT_WRONG"
    FEE_NONE = "LATE_FEE_NONE"
    FEE_YES = "LATE_FEE_YES"
    PASS_NONE = "PASS_FEE_NONE"
    PASS_YES = "PASS_FEE_YES"

    def _clean_report(self):
        """เล่มที่ระบบไม่พบจุดผิดเลย — กรณีที่ปุ่มพวกนี้สำคัญที่สุด"""
        return {"verdict": "ผ่าน",
                "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []}}

    def _signature_report(self):
        rep = Report()
        rep.add("RED", "front_matter", "หน้าลงนาม 1 (หน้า ค)",
                "เลขหน้าของหน้านี้ไม่ถูกต้อง", 'ต้องเป็นเลขหน้า "ก"', "",
                "PAGE.SIGNATURE_LABEL")
        return {"verdict": "ไม่ผ่าน", "issues_by_zone": rep.zones}

    def _wording(self, choice_id):
        return checker_module.STAFF_CHOICE_BY_ID[choice_id][1]["text"]

    def test_the_registry_holds_both_topics(self):
        self.assertEqual([c["id"] for c in checker_module.STAFF_CHECKS],
                         ["SIGNATURE_LAYOUT", "LATE_FEE", "PASS_FEE"])
        for choice_id in (self.WRONG, self.FEE_NONE, self.FEE_YES,
                          self.PASS_NONE, self.PASS_YES):
            self.assertIn(choice_id, checker_module.STAFF_CHOICE_BY_ID)
        # หัวข้อค่าปรับสองอันผูกกับผลตรวจคนละค่า จะได้ไม่ออกมาพร้อมกัน
        scopes = {c["id"]: c.get("applies_to") for c in checker_module.STAFF_CHECKS}
        self.assertEqual(scopes["LATE_FEE"], "not_pass")
        self.assertEqual(scopes["PASS_FEE"], "pass")
        self.assertIsNone(scopes["SIGNATURE_LAYOUT"])

    def test_nothing_is_added_until_staff_presses(self):
        text = checker_module.plain_summary(self._clean_report())
        self.assertIn("ไม่พบจุดที่ต้องแก้ไข", text)
        self.assertNotIn("ปรับโครงสร้างของหน้า", text)
        self.assertNotIn("ค่าปรับ", text)

    def test_pressing_correct_adds_nothing(self):
        """ปุ่ม "ถูกต้อง" มีไว้บันทึกว่าตรวจแล้ว ไม่ใช่เพื่อเพิ่มถ้อยคำ"""
        text = checker_module.plain_summary(self._clean_report(),
                                            staff=["SIGNATURE_LAYOUT_OK"])
        self.assertNotIn("ปรับโครงสร้างของหน้า", text)
        self.assertNotIn("ค่าปรับ", text)

    def test_the_wording_reaches_the_summary_word_for_word(self):
        """ถ้อยคำของเจ้าหน้าที่ต้องไปถึงนักศึกษาครบทุกคำ ไม่ถูกย่อหรือเรียบเรียงใหม่

        เล่มที่ยังไม่ผ่านใช้ถ้อยคำชุด "แก้แล้วส่งกลับ" จึงต้องตรวจกับเล่มที่มีจุดผิด
        ชุด "ผ่าน" มีเทสต์ของตัวเองข้างล่าง
        """
        for choice_id in (self.WRONG, self.FEE_NONE, self.FEE_YES):
            text = checker_module.plain_summary(self._signature_report(),
                                                staff=[choice_id])
            for line in self._wording(choice_id).split(NEWLINE):
                if line.strip():
                    self.assertIn(line.strip(), text, choice_id)

    def test_the_pass_wording_reaches_the_summary_word_for_word(self):
        """ถ้อยคำชุด "ผ่าน" ก็ต้องไปถึงนักศึกษาครบทุกคำเช่นกัน"""
        for choice_id in (self.PASS_NONE, self.PASS_YES):
            text = checker_module.plain_summary(self._clean_report(),
                                                staff=[choice_id])
            wording = checker_module.STAFF_CHOICE_BY_ID[choice_id][1]["text"]
            for line in wording.split(NEWLINE):
                if line.strip():
                    self.assertIn(line.strip(), text, choice_id)

    def test_the_signature_wording_lands_in_the_signature_group(self):
        text = checker_module.plain_summary(self._clean_report(), staff=[self.WRONG])
        lines = text.split(NEWLINE)
        self.assertIn("หน้าลงนาม", lines)
        head = lines.index("หน้าลงนาม")
        self.assertTrue(lines[head + 1].startswith("1. ในหน้าลงนาม"), lines[head + 1])

    def test_the_signature_wording_is_counted_as_a_point_to_fix(self):
        report = self._signature_report()
        self.assertIn("ทั้งหมด 1 จุด", checker_module.plain_summary(report))
        self.assertIn("ทั้งหมด 2 จุด",
                      checker_module.plain_summary(report, staff=[self.WRONG]))

    def test_the_fee_wording_is_not_counted_as_a_point_to_fix(self):
        """ข้อความปิดท้ายไม่ใช่จุดที่นักศึกษาต้องแก้ นับรวมเมื่อไหร่ตัวเลขจะผิดทันที"""
        report = self._signature_report()
        text = checker_module.plain_summary(report, staff=[self.FEE_YES])
        self.assertIn("ทั้งหมด 1 จุด", text)
        self.assertIn("นักศึกษามีค่าปรับในการส่งเล่มล่าช้า", text)

    def test_the_fee_wording_comes_last(self):
        report = self._signature_report()
        text = checker_module.plain_summary(report,
                                            staff=[self.WRONG, self.FEE_NONE])
        self.assertLess(text.index("ปรับโครงสร้างของหน้า"),
                        text.index("นักศึกษาไม่มีค่าปรับ"))
        self.assertTrue(text.rstrip().endswith("supawit.mar@mahidol.ac.th"),
                        text[-80:])

    def test_the_fee_wording_follows_a_clean_book_too(self):
        """เล่มที่ผ่านสะอาดก็ต้องได้ข้อความปิดท้าย ไม่ใช่หายไปเพราะไม่มีจุดผิด

        แต่เป็นชุด "เสร็จสิ้นแล้ว" ไม่ใช่ชุด "แก้แล้วส่งกลับ" (ก.ย. 2569)
        """
        text = checker_module.plain_summary(self._clean_report(),
                                            staff=[self.PASS_NONE])
        self.assertIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", text)
        self.assertTrue(text.startswith("ผลการตรวจ: ผ่าน"), text[:40])

    def test_a_finished_book_is_not_also_told_there_is_nothing_to_fix(self):
        """"ผ่าน" + "ไม่พบจุดที่ต้องแก้ไข" อ่านรวมกันว่าจบแล้ว ไม่ต้องทำอะไรต่อ

        เจ้าหน้าที่รายงาน (ก.ย. 2569) ว่านักศึกษาหยุดอ่านตรงนั้นจริง แล้วพลาดกำหนดส่ง
        หน้าลงนามภายใน 30 วันที่อยู่ข้างล่าง ถ้อยคำชุด "ผ่าน" บอกอยู่แล้วว่าเสร็จสิ้น
        และยังมีขั้นตอนต่อ จึงไม่ต้องมีบรรทัดนั้นซ้ำ
        """
        for choice_id in (self.PASS_NONE, self.PASS_YES):
            text = checker_module.plain_summary(self._clean_report(),
                                                staff=[choice_id])
            self.assertNotIn("ไม่พบจุดที่ต้องแก้ไข", text, choice_id)
            self.assertIn("แต่ยังมีขั้นตอนที่ต้องดำเนินการต่อ กรุณาอ่านรายละเอียดด้านล่าง",
                          text, choice_id)
            # บรรทัดผลตรวจต้องตามด้วยถ้อยคำชุดผ่านทันที
            said = [ln for ln in text.split(NEWLINE) if ln.strip()]
            self.assertEqual(said[0], "ผลการตรวจ: ผ่าน")
            self.assertTrue(said[1].startswith("การส่ง E-thesis"), said[1])

    def test_a_passing_book_says_only_that_until_staff_answers(self):
        """เล่มที่ผ่านแต่ยังไม่กด ต้องได้แค่สองบรรทัด (เจ้าหน้าที่กำหนด ก.ย. 2569)

        ขั้นตอนถัดไปทั้งชุดผูกกับการกดปุ่ม เพราะระบบไม่รู้ว่ามีค่าปรับหรือไม่ และถ้อยคำ
        สองชุดต่างกันตั้งแต่ย่อหน้าแรก
        """
        text = checker_module.plain_summary(self._clean_report())
        self.assertEqual(text, "ผลการตรวจ: ผ่าน\n\nไม่พบจุดที่ต้องแก้ไข")

    def test_the_two_fee_topics_never_fire_on_the_same_verdict(self):
        """หน้าเว็บส่ง id มาพร้อมกันได้ (หน้าเก่าค้าง/กดตอนผลตรวจยังเป็นอีกอย่าง)

        ถ้าไม่กันตามผลตรวจ นักศึกษาจะได้ทั้ง "แก้แล้วส่งกลับเข้าระบบ" และ
        "เสร็จสิ้นแล้ว ส่งหน้าลงนามต่อ" ในข้อความเดียวกัน
        """
        both = [self.FEE_NONE, self.PASS_NONE]
        passed = checker_module.plain_summary(self._clean_report(), staff=both)
        self.assertIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", passed)
        self.assertNotIn("กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง", passed)
        failed = checker_module.plain_summary(self._signature_report(), staff=both)
        self.assertIn("กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง", failed)
        self.assertNotIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", failed)

    def test_only_closing_topics_may_be_scoped_to_a_verdict(self):
        """ผูก applies_to กับหัวข้อ placement=section ไม่ได้ และต้องกันไว้ไม่ให้ใครลอง

        จุดที่ต้องแก้ (section) ถูกนับก่อนจึงจะรู้ผลตรวจ — issues_to_fix() เรียก
        staff_choices() โดยยังไม่มี verdict เพราะ verdict คำนวณจากผลของ issues_to_fix()
        เอง ถ้ามีใครใส่ applies_to ให้หัวข้อ section มันจะถูกมองข้ามเงียบ ๆ แล้วถ้อยคำ
        จะออกทุกผลตรวจทั้งที่ตั้งใจให้ออกเฉพาะบางอัน
        """
        for check in checker_module.STAFF_CHECKS:
            if check["placement"] == "section":
                self.assertIsNone(check.get("applies_to"), check["id"])

    def test_the_verdict_helper_agrees_with_the_summary_it_prints(self):
        """หน้ารายงานใช้ค่าจาก summary_verdict() เลือกหัวข้อที่จะโชว์ ส่วนนักศึกษาอ่าน
        บรรทัดแรกของข้อความสรุป ทั้งสองค่าคำนวณคนละรอบ ถ้าเพี้ยนจากกันเมื่อไหร่
        เจ้าหน้าที่จะเห็นหัวข้อที่กดแล้วไม่มีผล
        """
        cases = [
            (self._clean_report(), [], None),
            (self._clean_report(), [self.PASS_NONE], None),
            # เล่มที่ระบบว่าผ่าน แต่เจ้าหน้าที่กดเพิ่มจุด ผลตรวจต้องกลายเป็นไม่ผ่าน
            (self._clean_report(), [self.WRONG], None),
            (self._signature_report(), [self.FEE_YES], None),
            ({"verdict": "รอยืนยัน",
              "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []}}, [], None),
        ]
        for report, staff, _ in cases:
            text = checker_module.plain_summary(report, staff=staff)
            verdict = checker_module.summary_verdict(report, staff=staff)
            self.assertEqual(text.split(NEWLINE)[0], "ผลการตรวจ: " + verdict,
                             (report.get("verdict"), staff))

    def test_no_english_wording_leaks_thai_letters(self):
        """คำแปลอังกฤษต้องไม่มีอักษรไทยหลงเหลือ

        หน้ารายงานแทนถ้อยคำไทยด้วยคู่แปลทั้งก้อน ไม่มีอะไรมาตรวจซ้ำทีหลัง ถ้ามีใคร
        วางภาษาไทยลงใน text_en นักศึกษาที่อ่านฉบับอังกฤษจะเห็นไทยปนโดยไม่มีใครรู้
        (เคยหลุดมาแล้วตอนเขียน "(pages ก - ข or i - ii only)")
        """
        thai = re.compile(r"[฀-๿]")
        for check in checker_module.STAFF_CHECKS:
            for key in ("item_en", "why_en"):
                self.assertFalse(thai.search(check[key]), (check["id"], key))
            for choice in check["choices"]:
                for key in ("label_en", "text_en"):
                    found = thai.search(choice[key])
                    self.assertFalse(found, (choice["id"], key,
                                             found.group(0) if found else ""))

    def test_no_two_choices_share_the_same_thai_wording(self):
        """หน้ารายงานจับคู่แปลด้วยตัวข้อความไทยเอง ไม่ได้ใช้ id

        ถ้าสองตัวเลือกมีถ้อยคำไทยเหมือนกันแต่คำแปลคนละอัน อันหนึ่งจะถูกแปลผิดเงียบ ๆ
        """
        seen = {}
        for check in checker_module.STAFF_CHECKS:
            for choice in check["choices"]:
                if not choice["text"]:
                    continue
                key = choice["text"].strip()
                self.assertNotIn(key, seen, (choice["id"], seen.get(key)))
                seen[key] = choice["id"]

    def test_the_pass_topic_is_ignored_when_the_book_did_not_pass(self):
        """รวมถึง "รอยืนยัน" ซึ่งยังไม่จบกระบวนการ"""
        for verdict in ("ไม่ผ่าน", "รอยืนยัน"):
            report = {"verdict": verdict,
                      "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []}}
            text = checker_module.plain_summary(report, staff=[self.PASS_NONE])
            self.assertNotIn(checker_module.SURVEY_URL, text, verdict)
            self.assertIn("ไม่พบจุดที่ต้องแก้ไข", text, verdict)

    def test_a_book_that_did_not_pass_gets_no_closing_without_a_staff_answer(self):
        """ควบคุมเชิงลบ — ค่าตั้งต้นนี้ใช้กับเล่มที่ผ่านเท่านั้น

        เล่มที่ยังไม่ผ่านต้องได้ถ้อยคำ "แก้แล้วส่งกลับ" ซึ่งขึ้นต้นด้วยเรื่องค่าปรับ
        จึงยังต้องรอเจ้าหน้าที่กดเหมือนเดิม
        """
        for report in (self._signature_report(),
                       {"verdict": "รอยืนยัน",
                        "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []}}):
            text = checker_module.plain_summary(report)
            self.assertNotIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", text)
            self.assertNotIn(checker_module.SURVEY_URL, text)

    def test_a_passing_book_is_never_told_to_resubmit(self):
        """ควบคุมเชิงลบของกฎข้างบน — เคยส่ง "กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง" ไปหาคนที่
        ไม่มีจุดต้องแก้สักจุด เพราะถ้อยคำปิดท้ายมีชุดเดียวไม่ว่าผลตรวจจะออกมาอย่างไร
        """
        for choice_id in (self.FEE_NONE, self.FEE_YES):
            text = checker_module.plain_summary(self._clean_report(),
                                                staff=[choice_id])
            self.assertNotIn("กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง", text, choice_id)
            self.assertNotIn("Remarks", text, choice_id)

    def test_a_book_still_to_fix_is_never_told_it_is_finished(self):
        """อีกด้านหนึ่ง — เล่มที่ยังมีจุดต้องแก้ต้องไม่ได้ถ้อยคำ "เสร็จสิ้นแล้ว"

        รวมถึงเล่มที่ระบบว่าผ่านแต่เจ้าหน้าที่กดเพิ่มจุดเอง ซึ่งผลตรวจถูกลดเป็นไม่ผ่าน
        """
        cases = [(self._signature_report(), [self.FEE_NONE]),
                 (self._clean_report(), [self.WRONG, self.FEE_NONE])]
        for report, staff in cases:
            text = checker_module.plain_summary(report, staff=staff)
            self.assertTrue(text.startswith("ผลการตรวจ: ไม่ผ่าน"), text[:40])
            self.assertNotIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", text)
            self.assertIn("กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง", text)

    def test_a_pending_book_is_not_treated_as_finished(self):
        """"รอยืนยัน" ไม่ใช่ผ่าน — ยังไม่ถึงขั้นตอนส่งหน้าลงนาม"""
        report = {"verdict": "รอยืนยัน",
                  "issues_by_zone": {"RED": [], "ORANGE": [], "YELLOW": []}}
        text = checker_module.plain_summary(report, staff=[self.FEE_NONE])
        self.assertNotIn("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว", text)
        self.assertIn("กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง", text)

    def test_the_survey_link_reaches_every_student_who_passed(self):
        """ลิงก์แบบสอบถามต้องอยู่ในถ้อยคำชุด "ผ่าน" ทั้งสองฝั่งค่าปรับ และทั้งสองภาษา

        แบบสอบถามถามว่า "ส่งตรวจรูปเล่มมาแล้วกี่รอบ" ซึ่งตอบถูกได้เมื่อจบกระบวนการแล้ว
        เท่านั้น จึงไม่ส่งลิงก์ให้เล่มที่ยังต้องแก้ และลิงก์เดิมที่ชี้ไปแบบประเมินงาน
        บริการภาพรวม (forms.gle) ถูกแทนที่แล้ว ต้องไม่หลงเหลืออยู่
        """
        owner = {"text": "งานบริการการศึกษา",
                 "text_en": "Academic Service Section"}
        label = {"text": "ลิงก์ตอบแบบสอบถาม  " + checker_module.SURVEY_URL,
                 "text_en": "Survey link: " + checker_module.SURVEY_URL}
        for choice_id in (self.PASS_NONE, self.PASS_YES):
            choice = checker_module.STAFF_CHOICE_BY_ID[choice_id][1]
            for field in ("text", "text_en"):
                self.assertIn(checker_module.SURVEY_URL, choice[field],
                              (choice_id, field))
                self.assertNotIn("forms.gle", choice[field], (choice_id, field))
                # คำเชิญต้องบอกว่าหน่วยงานไหนเป็นคนถาม (ก.ย. 2569)
                self.assertIn(owner[field], choice[field], (choice_id, field))
                # บรรทัดลิงก์ต้องจบที่ตัว URL พอดี ตัวอักษรที่หลุดมาต่อท้าย (เคยพิมพ์
                # "ฅ" ติดมา) ทำให้ลิงก์ย่อกลายเป็นคนละอันแล้วเปิดไม่ได้
                self.assertIn(label[field], choice[field], (choice_id, field))
                for line in choice[field].split(NEWLINE):
                    if checker_module.SURVEY_URL in line:
                        self.assertTrue(line.strip().endswith(
                            checker_module.SURVEY_URL), (choice_id, line))
        # ชุดที่ยังต้องแก้ไม่มีลิงก์
        for choice_id in (self.FEE_NONE, self.FEE_YES):
            choice = checker_module.STAFF_CHOICE_BY_ID[choice_id][1]
            for field in ("text", "text_en"):
                self.assertNotIn(checker_module.SURVEY_URL, choice[field],
                                 (choice_id, field))
        text = checker_module.plain_summary(self._signature_report(),
                                            staff=[self.FEE_NONE])
        self.assertNotIn(checker_module.SURVEY_URL, text)

    def test_the_wording_is_kept_exactly_as_staff_wrote_it(self):
        """รวมถึงคำที่ดูเหมือนพิมพ์ตก — เจ้าหน้าที่สั่งให้คงต้นฉบับทุกตัวอักษร (ก.ย. 2569)

        เคยแก้ให้แล้วถูกสั่งให้คืน เทสต์นี้กันไม่ให้ใครมาแก้ให้อีกโดยไม่ได้สั่ง ถ้าเจ้าหน้าที่
        เปลี่ยนใจให้แก้เมื่อไหร่ ให้แก้เทสต์นี้พร้อมกับถ้อยคำ
        """
        verbatim = {
            "SIGNATURE_LAYOUT_WRONG": [
                "ซึ่งนักศึกษาจะต้องทำดำเนินจัดทำรูปเล่มตามโครงสร้างทื่กำหนด",
                "สำหรับ ส่วนรายชื่อที่ว่างตามไฟล์ตัวอย่าง",
                # "ฝั่ง ละ 6 รายชื่อ" ถูกแล้ว เจ้าหน้าที่ยืนยัน (ก.ย. 2569) ว่าฝั่งซ้าย
                # ต้องนับชื่อนักศึกษารวมไปด้วย จึงไม่ใช่จำนวนกรรมการล้วน ๆ
                "จะใส่รายชื่อได้ฝั่ง ละ 6 รายชื่อ",
                # เจ้าหน้าที่อนุมัติถ้อยคำไทยท่อนนี้แล้ว (ก.ย. 2569) คู่กับฝั่งอังกฤษ
                # "and remove the position below the degree" ที่เจ้าหน้าที่เขียนมาเอง
                "ให้ใส่สีขาวไว้ และลบตำแหน่งที่อยู่ใต้คุณวุฒิออก",
            ],
            "LATE_FEE_NONE": [
                "Line Offical Account ID @322wjrbo",
                "หรือผ่านลิ้งค์",
            ],
            # ประโยคใบแจ้งหนี้เหลืออยู่เฉพาะชุด "มีค่าปรับ" แล้ว (เจ้าหน้าที่สั่งตัด
            # ออกจากชุด "ไม่มีค่าปรับ" ก.ย. 2569 เพราะขัดกับประโยคแรกของชุดนั้นเอง)
            "LATE_FEE_YES": [
                "เอกสารแจ้งค่าปรับ(Invoice)ผ่านระบบเมื่อ กระบวนการตรวจสอบเสร็จสิ้นแล้ว",
            ],
        }
        for choice_id, phrases in verbatim.items():
            text = self._wording(choice_id)
            for phrase in phrases:
                self.assertIn(phrase, text, (choice_id, phrase))
        english = checker_module.STAFF_CHOICE_BY_ID[
            "SIGNATURE_LAYOUT_WRONG"][1]["text_en"]
        self.assertIn("(Pages i - ii)", english)
        self.assertIn("Line Offical Account ID @322wjrbo",
                      checker_module.STAFF_CHOICE_BY_ID["LATE_FEE_NONE"][1]["text_en"])

    def test_the_signature_wording_starts_with_its_own_location_line(self):
        """เจ้าหน้าที่กำหนดรูปนี้เอง (ก.ย. 2569): บรรทัดตำแหน่ง แล้วสามย่อหน้า

        ของเดิมเป็นประโยคเดียวยาวเกือบ 300 ตัวอักษรที่มีคำสั่งซ้อนกันหกอย่าง และมี
        ตำแหน่งฝังอยู่กลางประโยคแรก ส่วนดอกจันหัวท้ายตั้งใจให้เป็นตัวหนา แต่ในอีเมล
        กับ Word ขึ้นเป็นดาวลอย
        """
        choice = checker_module.STAFF_CHOICE_BY_ID["SIGNATURE_LAYOUT_WRONG"][1]
        for field in ("text", "text_en"):
            lines = [ln for ln in choice[field].split(NEWLINE) if ln.strip()]
            self.assertEqual(len(lines), 4, field)
            self.assertNotIn("*", choice[field], field)
            # บรรทัดตำแหน่งต้องสั้น ไม่มีคำสั่งปนมา
            self.assertLess(len(lines[0]), 60, lines[0])
            for line in lines[1:]:
                # วัดเฉพาะเนื้อความ ไม่นับลิงก์ — ที่ห้ามคือประโยคยาวจนอ่านไม่จบ
                # ส่วนลิงก์คู่มือยาวตามที่มันเป็น จะตัดให้สั้นเองไม่ได้
                prose = re.sub(r"https?://\S+", "", line).strip()
                self.assertLess(len(prose), 260, prose[:60])
        self.assertTrue(choice["text"].startswith("ในหน้าลงนาม (หน้า i - ii หรือ ก - ข)"),
                        choice["text"][:60])
        self.assertTrue(choice["text_en"].startswith("Approval pages (Pages i - ii)"),
                        choice["text_en"][:60])

    def test_the_location_line_shows_first_in_the_summary(self):
        text = checker_module.plain_summary(self._clean_report(), staff=[self.WRONG])
        lines = text.split(NEWLINE)
        head = lines.index("หน้าลงนาม")
        self.assertEqual(lines[head + 1], "1. ในหน้าลงนาม (หน้า i - ii หรือ ก - ข)")
        self.assertTrue(lines[head + 2].startswith(
            checker_module.SUMMARY_INDENT + "ปรับโครงสร้างของหน้า"), lines[head + 2])

    def test_the_english_says_whether_this_student_has_a_fine(self):
        """เจ้าหน้าที่อนุมัติให้แก้เฉพาะจุดนี้ (ก.ย. 2569)

        "There is no fine for late submission" อ่านว่านโยบายไม่เก็บค่าปรับ ไม่ใช่ว่า
        นักศึกษาคนนี้ไม่มีค่าปรับ และฝั่ง "มีค่าปรับ" เดิมเขียนเป็นเงื่อนไข
        "If a fine is incurred" ทั้งที่ประโยคก่อนหน้ายืนยันไปแล้วว่ามี
        """
        none_en = checker_module.STAFF_CHOICE_BY_ID["LATE_FEE_NONE"][1]["text_en"]
        yes_en = checker_module.STAFF_CHOICE_BY_ID["LATE_FEE_YES"][1]["text_en"]
        self.assertTrue(none_en.startswith("You have no fine for late submission"),
                        none_en[:60])
        self.assertTrue(yes_en.startswith("You have a fine for late submission"),
                        yes_en[:60])
        self.assertNotIn("There is no fine", none_en)
        self.assertNotIn("There is a fine", yes_en)
        # ฝั่งที่ยืนยันว่ามีค่าปรับ ต้องไม่พูดเป็นเงื่อนไขอีก
        self.assertNotIn("If a fine is incurred", yes_en)
        self.assertIn("The invoice will be issued through the system", yes_en)
        # ฝั่งที่ไม่มีค่าปรับ ตัดประโยคเงื่อนไขออกแล้ว (เจ้าหน้าที่สั่ง ก.ย. 2569)
        # เพราะขัดกับประโยคแรกที่เพิ่งบอกว่าไม่มีค่าปรับ ทั้งสองภาษาตัดพร้อมกัน
        self.assertNotIn("If a fine is incurred", none_en)
        # ทั้งสองชุดไม่มีคำนี้แล้ว — ชุด "มีค่าปรับ" ตัดออกทีหลัง (เจ้าหน้าที่สั่งให้
        # สองภาษาเท่ากัน) เพราะประโยคแรกยืนยันไปแล้วว่ามี จะพูดเป็นเงื่อนไขซ้ำอีกไม่ได้
        for cid in ("LATE_FEE_NONE", "LATE_FEE_YES"):
            self.assertNotIn("กรณีที่นักศึกษามีค่าปรับ",
                             checker_module.STAFF_CHOICE_BY_ID[cid][1]["text"], cid)
        # ฝั่งที่มีค่าปรับต้องยังบอกเรื่องใบแจ้งหนี้อยู่ ทั้งสองภาษา
        yes = checker_module.STAFF_CHOICE_BY_ID["LATE_FEE_YES"][1]
        self.assertIn("เอกสารแจ้งค่าปรับ(Invoice)", yes["text"])
        self.assertIn("The invoice will be issued", yes["text_en"])

    def test_the_two_frame_sentences_do_not_contradict(self):
        """ย่อหน้า 2 กับ 4 เคยอ่านแล้วขัดกันเอง เจ้าหน้าที่สั่งให้ปรับ (ก.ย. 2569)

            ย่อหน้า 2  "...ไม่ต้องเลื่อนหรือปรับกรอบ"
            ย่อหน้า 4  "ปรับกรอบของ template ให้ตรงกันกับที่ set ไว้..."

        ทั้งสองย่อหน้าพูดคนละเรื่อง — ย่อหน้า 2 ห้ามขยับเพื่อให้พอดีกับจำนวนชื่อของ
        ตัวเอง ส่วนย่อหน้า 4 บอกว่ากรอบต้องเป็นค่ามาตรฐานของ template
        """
        choice = checker_module.STAFF_CHOICE_BY_ID["SIGNATURE_LAYOUT_WRONG"][1]
        th = [ln for ln in choice["text"].split(NEWLINE) if ln.strip()]
        en = [ln for ln in choice["text_en"].split(NEWLINE) if ln.strip()]
        # ห้ามกลับไปเป็นคำสั่งลอย ๆ ที่อ่านแล้วขัดกับย่อหน้าที่ 4
        self.assertNotIn("ไม่ต้องเลื่อนหรือปรับกรอบ", th[1])
        self.assertNotIn("without shifting or modifying the frames", en[1])
        # ต้องบอกด้วยว่าห้ามขยับ "เพื่ออะไร"
        self.assertIn("เพื่อให้พอดีกับจำนวนชื่อ", th[1])
        self.assertIn("to fit them", en[1])
        # ย่อหน้า 4 ยังต้องสั่งให้กรอบตรงกับ template เหมือนเดิม
        self.assertIn("ให้ตรงกันกับที่ set ไว้", th[3])
        self.assertIn("match the default settings", en[3])

    def test_the_wording_uses_the_full_word_for_student(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้ใช้คำว่า "นักศึกษา" ทั้งหมด ไม่ใช้ตัวย่อ

        ข้อความชุดนี้ส่งถึงนักศึกษาโดยตรง ตัวย่อในจดหมายราชการอ่านแล้วห้วน
        """
        for check in checker_module.STAFF_CHECKS:
            for choice in check["choices"]:
                for field in ("text", "text_en"):
                    self.assertNotIn("นศ.", choice.get(field) or "", choice["id"])

    def test_the_layout_wording_tells_them_to_remove_the_position(self):
        """เจ้าหน้าที่สั่งเพิ่มท่อนนี้ (ก.ย. 2569) ต้องมีทั้งสองภาษา

        จำนวนบรรทัดสองภาษาต้องเท่ากันเสมอ จึงต้องต่อท้ายย่อหน้าเดิม ไม่ขึ้นบรรทัดใหม่
        """
        choice = checker_module.STAFF_CHOICE_BY_ID["SIGNATURE_LAYOUT_WRONG"][1]
        self.assertIn("and remove the position below the degree", choice["text_en"])
        self.assertIn("และลบตำแหน่งที่อยู่ใต้คุณวุฒิออก", choice["text"])
        for field in ("text", "text_en"):
            lines = [ln for ln in choice[field].split(NEWLINE) if ln.strip()]
            self.assertEqual(len(lines), 4, field)

    def test_the_formatting_manual_comes_with_a_link(self):
        """เจ้าหน้าที่ส่งลิงก์คู่มือมาให้ (ก.ย. 2569) — เดิมเขียนว่า "คู่มือการจัดฯ"
        ลอย ๆ นักศึกษาเปิดไม่ได้ว่าอยู่ที่ไหน ต้องมีทั้งสองภาษา
        """
        choice = checker_module.STAFF_CHOICE_BY_ID["SIGNATURE_LAYOUT_WRONG"][1]
        for field in ("text", "text_en"):
            self.assertIn("https://www.canva.com/design/DAHA0Rwyb84/",
                          choice[field], field)
        # ลิงก์ต้องอยู่ย่อหน้าเดียวกับที่พูดถึงคู่มือ ไม่ใช่ขึ้นบรรทัดใหม่ —
        # จำนวนบรรทัดสองภาษาต้องเท่ากันเสมอ หน้ารายงานแปลทีละบรรทัด
        for field, marker in (("text", "คู่มือการจัดฯ"),
                              ("text_en", "formatting manual")):
            para = next(ln for ln in choice[field].split(NEWLINE) if marker in ln)
            self.assertIn("https://www.canva.com/", para, field)

    def test_the_two_fee_answers_differ_only_in_the_first_sentence(self):
        """ผิดคำเดียวคือบอกนักศึกษาผิดเรื่องเงิน — ล็อกไว้ว่าต่างกันแค่ มี/ไม่มี"""
        none_lines = self._wording(self.FEE_NONE).split(NEWLINE)
        yes_lines = self._wording(self.FEE_YES).split(NEWLINE)
        self.assertEqual(none_lines[1:], yes_lines[1:])
        self.assertIn("นักศึกษาไม่มีค่าปรับในการส่งเล่มล่าช้า", none_lines[0])
        self.assertIn("นักศึกษามีค่าปรับในการส่งเล่มล่าช้า", yes_lines[0])
        self.assertNotIn("ไม่มีค่าปรับในการส่งเล่มล่าช้า", yes_lines[0])

    def test_two_topics_on_the_same_page_do_not_collapse_into_one(self):
        """ควบคุมเชิงลบของกฎรวมรายการซ้ำ — จุดพวกนี้ไม่มี "ค่าที่ต้องแก้" ให้เทียบ

        เจ้าหน้าที่จะทยอยเพิ่มหัวข้อแบบนี้อีก พอมีสองหัวข้อที่อยู่ส่วนเดียวกันของเล่ม
        ทั้งคู่จะได้กุญแจ ("หน้าลงนาม", "") เท่ากัน แล้วถูกยุบเหลือจุดเดียว ถ้ากุญแจ
        ไม่นับถ้อยคำเข้าไปด้วย (คนละเรื่องกับ "หนึ่งหัวข้อตอบได้คำตอบเดียว" ข้างล่าง
        ซึ่งคุมตัวเลือกภายในหัวข้อเดียวกัน)
        """
        extra = {
            "id": "SIGNATURE_FONT",
            "item": "ฟอนต์หน้าลงนาม",
            "item_en": "Signature page font",
            "why": "-", "why_en": "-",
            "rule_id": "FRONT.SIGNATURE_LAYOUT",
            "placement": "section",
            "section": "หน้าลงนาม",
            "choices": [{
                "id": "SIGNATURE_FONT_WRONG", "label": "ฟอนต์ผิด",
                "label_en": "Wrong font", "tone": "fail",
                "text": "ในหน้าลงนาม ให้ใช้ฟอนต์ตามที่กำหนดในคู่มือ",
                "text_en": "On the signature page, use the font set in the manual.",
            }],
        }
        registry = list(checker_module.STAFF_CHECKS) + [extra]
        lookup = {ch["id"]: (c, ch) for c in registry for ch in c["choices"]
                  if ch["text"]}
        with mock.patch.object(checker_module, "STAFF_CHECKS", registry),                 mock.patch.object(checker_module, "STAFF_CHOICE_BY_ID", lookup):
            text = checker_module.plain_summary(
                self._clean_report(), staff=[self.WRONG, "SIGNATURE_FONT_WRONG"])
        self.assertIn("ทั้งหมด 2 จุด", text)
        self.assertIn("ปรับโครงสร้างของหน้า", text)
        self.assertIn("ให้ใช้ฟอนต์ตามที่กำหนดในคู่มือ", text)

    def test_only_one_answer_per_topic_survives(self):
        """ค่าที่ส่งมาจากหน้าเว็บเชื่อไม่ได้ ต้องกันข้อความที่ขัดกันเองไว้ที่ฝั่งเซิร์ฟเวอร์

        หน้าเว็บกดได้ทีละอันอยู่แล้ว แต่หน้าเก่าที่ค้างไว้ คำขอที่สวนกัน หรือคำขอที่ถูก
        ส่งซ้ำ ทำให้ส่งมาสองคำตอบพร้อมกันได้ ถ้าไม่คุม นักศึกษาจะได้ข้อความว่า
        "นักศึกษาไม่มีค่าปรับ" แล้วตามด้วย "นักศึกษามีค่าปรับ" ในย่อหน้าถัดไป
        """
        text = checker_module.plain_summary(self._signature_report(),
                                            staff=[self.FEE_NONE, self.FEE_YES])
        heads = [line for line in text.split(NEWLINE)
                 if "ค่าปรับในการส่งเล่มล่าช้า" in line]
        self.assertEqual(len(heads), 1, heads)
        # เล่มที่ผ่านก็ต้องกันเหมือนกัน แต่ถ้อยคำคนละชุด จึงดูที่บรรทัดขึ้นต้นของชุดผ่าน
        passed = checker_module.plain_summary(self._clean_report(),
                                              staff=[self.PASS_NONE, self.PASS_YES])
        opens = [line for line in passed.split(NEWLINE)
                 if line.startswith("การส่ง E-thesis")]
        self.assertEqual(len(opens), 1, opens)

    def test_an_unknown_button_id_is_ignored(self):
        """ค่าที่ส่งมาจากหน้าเว็บเชื่อไม่ได้ — id ที่ไม่มีในทะเบียนต้องไม่ทำให้พัง"""
        text = checker_module.plain_summary(self._clean_report(),
                                            staff=["NOT_A_REAL_CHECK", ""])
        self.assertTrue(text.startswith("ผลการตรวจ: ผ่าน"), text[:40])
        self.assertNotIn("ค่าปรับ", text)

    def test_the_order_follows_the_registry_not_the_order_pressed(self):
        """ลำดับที่กดเป็นเรื่องบังเอิญของแต่ละคน ข้อความที่ส่งต้องเรียงเหมือนกันทุกครั้ง"""
        one = checker_module.plain_summary(self._clean_report(),
                                           staff=[self.WRONG, self.FEE_NONE])
        two = checker_module.plain_summary(self._clean_report(),
                                           staff=[self.FEE_NONE, self.WRONG])
        self.assertEqual(one, two)

    def test_the_result_line_stops_saying_passed(self):
        """ควบคุมเชิงลบ: ถ้าไม่แก้บรรทัดผลตรวจ ข้อความจะขัดกันเองต่อหน้านักศึกษา

        "ผลการตรวจ: ผ่าน" แล้วบรรทัดถัดมา "กรุณาแก้ไขทั้งหมด 1 จุด"
        """
        text = checker_module.plain_summary(self._clean_report(), staff=[self.WRONG])
        self.assertTrue(text.startswith("ผลการตรวจ: ไม่ผ่าน"), text[:40])

    def test_a_passing_book_with_nothing_added_still_says_passed(self):
        self.assertTrue(checker_module.plain_summary(self._clean_report())
                        .startswith("ผลการตรวจ: ผ่าน"))

    def test_every_entry_carries_both_languages(self):
        """ทุกถ้อยคำต้องมีคู่แปล และวิธีจับคู่ต่างกันตาม placement

        placement = section  ถ้อยคำถูกแตกไปแทรกในรายการจุดที่ต้องแก้ทีละบรรทัด
                             หน้ารายงานจึงจับคู่ทีละบรรทัด จำนวนบรรทัดต้องเท่ากัน
                             ไม่งั้นบรรทัดที่เกินตกไปให้ TR แทนที่แบบเศษคำ แล้วได้ผล
                             ปนกันครึ่งไทยครึ่งอังกฤษ (วัดแล้วได้ "ในApproval pages
                             (page i-ii หรือ ก-ข) ปรับโครงสร้างของหน้า ...")
        placement = closing  ถ้อยคำเข้าสรุปทั้งก้อน หน้ารายงานจับคู่ทั้งก้อน จำนวน
                             บรรทัดจึงไม่ต้องเท่ากัน (ดู STAFF_BLOCK_EN ใน report.html)
        """
        for check in checker_module.STAFF_CHECKS:
            for key in ("id", "item", "item_en", "why", "why_en", "rule_id",
                        "placement", "choices"):
                self.assertTrue(check.get(key), (check.get("id"), key))
            self.assertIn(check["placement"], ("section", "closing"), check["id"])
            for choice in check["choices"]:
                for key in ("id", "label", "label_en", "tone"):
                    self.assertTrue(choice.get(key), (choice.get("id"), key))
                self.assertIn(choice["tone"], ("pass", "fail"), choice["id"])
                # ทุกชุดถ้อยคำที่มีอยู่ ต้องมีคู่แปลเสมอ ขาดข้างเดียวไม่ได้
                for th_key, en_key in (("text", "text_en"),
                                       ("text_pass", "text_pass_en")):
                    self.assertEqual(bool(choice.get(th_key)),
                                     bool(choice.get(en_key)),
                                     (choice["id"], th_key))
                if check["placement"] != "section":
                    continue
                th = [ln for ln in choice["text"].split(NEWLINE) if ln.strip()]
                en = [ln for ln in choice["text_en"].split(NEWLINE) if ln.strip()]
                self.assertEqual(len(th), len(en), choice["id"])

    def test_the_pass_wording_is_kept_exactly_as_staff_wrote_it(self):
        """เหมือนถ้อยคำชุดอื่น — ต้นฉบับจาก note V.2.txt ทุกตัวอักษร

        เจ้าหน้าที่อ่านทั้งสี่ชุดแล้วสั่งเกลาห้าจุด (ก.ย. 2569) จุดที่เหลือยังเป็นต้นฉบับ
        ถ้าจะเกลาเพิ่ม ต้องได้คำสั่งจากเจ้าหน้าที่ก่อน แล้วแก้เทสต์นี้พร้อมกับถ้อยคำ
        """
        none_th = checker_module.STAFF_CHOICE_BY_ID[self.PASS_NONE][1]["text"]
        yes_th = checker_module.STAFF_CHOICE_BY_ID[self.PASS_YES][1]["text"]
        for phrase in ("การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว",
                       "ภายใน 30 วัน นับจากวันที่สถานะในระบบแสดงว่า “เสร็จสิ้น”",
                       # ดอกจันคู่นี้เจ้าหน้าที่ยืนยันให้คงไว้ (ก.ย. 2569) ต่างจากกรณี
                       # หน้าลงนามที่สั่งให้ถอดออก อย่าถอดตามกรณีนั้น
                       "*ทั้งสองขั้นตอนสามารถดำเนินการควบคู่กันได้*"):
            self.assertIn(phrase, none_th, phrase)
        # ต้นฉบับพิมพ์ "1 .ชำระผ่าน" เว้นวรรคหน้าจุด ต่างจากข้อ 2 กับ 3 ในรายการเดียวกัน
        # เจ้าหน้าที่สั่งให้เกลา (ก.ย. 2569)
        self.assertIn("1. ชำระผ่าน QR Code payment", yes_th)
        self.assertNotIn("1 .ชำระผ่าน", yes_th)
        # ชื่อฟอร์มต้องพิมพ์แบบเดียวกันทั้งสองชุด ต้นฉบับชุดมีค่าปรับใช้ตัวใหญ่ทั้งคำ
        for text in (none_th, yes_th):
            self.assertIn("GR.5 (Requesting Degree)", text)
            self.assertNotIn("REQUESTING DEGREE", text)
        # สองคำนี้เจ้าหน้าที่สั่งให้แก้ (ก.ย. 2569) ต่างจากคำอื่นในแฟ้มนี้ที่ให้คงต้นฉบับ
        # ของเดิมพิมพ์ว่า "การการส่งเอกสาร" (การซ้ำ) และ "เฉพาหน้า" (ตก ะ)
        for text in (none_th, yes_th):
            self.assertIn("ขั้นตอนถัดไป: การส่งเอกสารหน้าลงนาม", text)
            self.assertIn("(เฉพาะหน้า ก - ข หรือ i - ii)", text)
            self.assertNotIn("การการส่ง", text)
            self.assertNotIn("เฉพาหน้า", text)
        self.assertIn("โดยส่งผ่านระบบ ใน tab Sign off page submission", yes_th)
        none_en = checker_module.STAFF_CHOICE_BY_ID[self.PASS_NONE][1]["text_en"]
        yes_en = checker_module.STAFF_CHOICE_BY_ID[self.PASS_YES][1]["text_en"]
        self.assertIn("Your E-thesis has been completed.", none_en)
        # เจ้าหน้าที่รวมท่อนหน้าลงนามฝั่งอังกฤษเป็นย่อหน้าเดียว (ก.ย. 2569)
        # ตัดรายการ "1. Entitled Page / 2. Approval Page" ออก ให้ตรงกับฝั่งไทย
        # ที่บอกช่วงหน้าไว้ในวงเล็บอยู่แล้ว
        for text in (none_en, yes_en):
            self.assertIn("signatures completed" + NEWLINE
                          + "through the system, in the Sign off page submission tab,",
                          text)
            self.assertNotIn("Entitled Page", text)
            self.assertNotIn("Submit the signed documents", text)
        self.assertIn("If you meet all graduation requirements," + NEWLINE
                      + "you may proceed to submit the GR.5 form", yes_en)
        # ต้นฉบับชุดมีค่าปรับเปิดเรื่องซ้ำสองรอบ บรรทัดแรกบอกว่าเสร็จแล้ว อีกสองบรรทัด
        # ถัดมาบอกซ้ำอีกครั้ง เจ้าหน้าที่สั่งให้ตัดประโยคที่ซ้ำออก (ก.ย. 2569)
        self.assertNotIn("Your E-Thesis submission has been successfully completed",
                         yes_en)
        for text in (none_en, yes_en):
            self.assertIn("Mr. Supawit at supawit.mar@mahidol.ac.th.", text)
            self.assertNotIn("Mr.Supawit", text)
        # กำหนดเวลาตรวจสอบการชำระเงินต้องบอกครบทั้งสองกรณีในทั้งสองภาษา ต้นฉบับอังกฤษ
        # มีแต่กรณีบัตรเครดิต นักศึกษาต่างชาติจึงไม่รู้ว่ากรณีทั่วไปใช้ 48 ชั่วโมง
        self.assertIn("ภายใน 48 ชั่วโมง", yes_th)
        self.assertIn("ภายใน 3 ชั่วโมง", yes_th)
        self.assertIn("within 48 hours", yes_en)
        self.assertIn("up to 3 hours", yes_en)

    def test_the_signature_pages_are_always_submitted_through_the_system(self):
        """หน้าลงนามส่งผ่านระบบเท่านั้น ไม่ใช่ถือเอกสารไปที่อาคารบัณฑิตวิทยาลัย

        ถ้อยคำต้นฉบับสามในสี่ชุดยังเป็นวิธีเดิม (เดินเอกสารไปศาลายา) เจ้าหน้าที่แก้เป็น
        tab Sign off page submission ทุกชุด (ก.ย. 2569) — ส่งผิดวิธีคือนักศึกษาเสียเที่ยว
        """
        link = "https://graduate.mahidol.ac.th/ethesis/stu/login.php"
        deadline = {"text": "ภายใน 30 วัน", "text_en": "within 30 days"}
        for choice_id in (self.PASS_NONE, self.PASS_YES):
            choice = checker_module.STAFF_CHOICE_BY_ID[choice_id][1]
            for field in ("text", "text_en"):
                text = choice[field]
                self.assertIn("Sign off page submission", text, (choice_id, field))
                self.assertIn(link, text, (choice_id, field))
                self.assertNotIn("ศาลายา", text, (choice_id, field))
                self.assertNotIn("Salaya", text, (choice_id, field))
                # กำหนด 30 วันต้องมีทุกฉบับ (ก.ย. 2569) — ต้นฉบับฉบับมีค่าปรับภาษาไทย
                # ตกไป นักศึกษากลุ่มนั้นจึงไม่รู้ว่ามีกำหนดเวลา
                self.assertIn(deadline[field], text, (choice_id, field))

    def test_the_pass_wording_points_at_the_next_step_not_at_resubmitting(self):
        """สาระของชุด "ผ่าน" คือขั้นตอนถัดไป — ส่งหน้าลงนาม และขออนุมัติปริญญา"""
        for choice_id in (self.PASS_NONE, self.PASS_YES):
            choice = checker_module.STAFF_CHOICE_BY_ID[choice_id][1]
            self.assertIn("GR.5", choice["text"], choice_id)
            self.assertIn("https://graduate.mahidol.ac.th/e-graduate/main/formlogin.php",
                          choice["text"], choice_id)
            self.assertIn("GR.5", choice["text_en"], choice_id)

    def test_the_pass_wording_exists_wherever_the_closing_wording_does(self):
        """ถ้อยคำปิดท้ายทุกชุดต้องบอกได้ว่าใช้กับผลตรวจไหน

        ปิดท้ายที่ไม่ระบุ applies_to จะออกทั้งเล่มผ่านและไม่ผ่าน ซึ่งถ้อยคำสองชุดนี้
        ขัดกันเอง — ชุดหนึ่งบอกให้ส่งกลับมาแก้ อีกชุดบอกว่าเสร็จสิ้นแล้ว
        """
        for check in checker_module.STAFF_CHECKS:
            if check["placement"] != "closing":
                continue
            self.assertIn(check.get("applies_to"), ("pass", "not_pass"),
                          check["id"])

    def test_choice_ids_are_unique(self):
        ids = [ch["id"] for c in checker_module.STAFF_CHECKS for ch in c["choices"]]
        self.assertEqual(len(ids), len(set(ids)), ids)

    def test_every_section_topic_is_filed_under_a_real_summary_group(self):
        for check in checker_module.STAFF_CHECKS:
            if check["placement"] != "section":
                continue
            self.assertIn(check["section"], checker_module.SUMMARY_SECTION_ORDER,
                          check["id"])
            issue = checker_module.staff_issue(check, check["choices"][-1])
            self.assertEqual(checker_module.summary_section(issue),
                             check["section"], check["id"])

    def test_every_topic_has_its_own_rule_in_the_catalogue(self):
        import ethesis_rules
        for check in checker_module.STAFF_CHECKS:
            self.assertIn(check["rule_id"], ethesis_rules.RULE_CATALOG, check["id"])

    def test_the_report_carries_the_registry_to_the_page(self):
        result = checker_module.check_result(Report())
        self.assertEqual(result["staff_findings"], list(checker_module.STAFF_CHECKS))


class TheReportSaysWhichFileItChecked(unittest.TestCase):
    """รายงานต้องบอกชื่อไฟล์และจำนวนหน้าที่ตรวจไป

    เจอจริง (ก.ย. 2569): เจ้าหน้าที่อัปโหลดไฟล์ eThesis เข้าช่อง "เล่ม" ระบบอ่านว่าเป็น
    เล่มภาษาไทยแล้วหยุดตรวจ ซึ่งถูกต้องตามที่มันเห็น แต่รายงานไม่ได้บอกชื่อไฟล์เลย
    จึงไล่ย้อนไม่ได้ว่าตรวจอะไรไป และดูเหมือนระบบอ่านภาษาผิด

    จำนวนหน้าเป็นตัวชี้ที่เร็วที่สุด ไฟล์ eThesis มี 2 หน้า เล่มจริงมีเป็นร้อย
    """

    def _render(self, pdf_name="book.pdf", n_pages=124):
        import jinja2
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(Path(checker_module.__file__).parent
                                               / "templates")),
            autoescape=True)
        report = checker_module.check_result(Report(), {"n_pages": n_pages})
        return env.get_template("report.html").render(
            report=report, zone_label={"RED": ("ไม่ผ่าน", "x"),
                                       "ORANGE": ("รอยืนยัน", "!"),
                                       "YELLOW": ("ข้อสังเกต", "i")},
            job_id="test", pdf_name=pdf_name, student={})

    def test_the_file_name_is_on_the_report(self):
        html = self._render(pdf_name="เล่มที่ 1.pdf")
        self.assertIn("ไฟล์ที่ตรวจ", html)
        self.assertIn("เล่มที่ 1.pdf", html)

    def test_the_page_count_is_on_the_report(self):
        html = self._render(n_pages=124)
        self.assertIn("(124 หน้า)", html)
        self.assertIn("(124 pages)", html)

    def test_a_two_page_upload_is_obvious(self):
        """ไฟล์ eThesis ที่อัปโหลดผิดช่อง ต้องเห็นได้ทันทีว่ามีแค่ 2 หน้า"""
        html = self._render(pdf_name="ระบบสารสนเทศ.pdf", n_pages=2)
        self.assertIn("ระบบสารสนเทศ.pdf", html)
        self.assertIn("(2 หน้า)", html)

    def test_a_report_with_no_page_count_still_renders(self):
        """ทางออกที่ไม่มี n_pages (เช่น ไม่ใช่ไฟล์ PDF) ต้องไม่พังและไม่โชว์วงเล็บเปล่า"""
        html = self._render(n_pages=0)
        self.assertIn("ไฟล์ที่ตรวจ", html)
        self.assertNotIn("(0 หน้า)", html)


class TheReportPageCanAlwaysReceiveStaffWording(unittest.TestCase):
    """หน้ารายงานต้องมีปุ่มครบ และมีกล่องสรุปให้ถ้อยคำลงเสมอ

    เล่มที่ระบบไม่พบจุดผิดเลยเป็นกรณีที่พลาดง่ายที่สุด: เดิมกล่องสรุปทั้งกล่องถูกสร้าง
    เฉพาะเมื่อมีข้อผิด เจ้าหน้าที่กดปุ่มแล้วถ้อยคำจึงไม่มีที่ไป
    """

    def _render(self, report):
        import jinja2
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(Path(checker_module.__file__).parent
                                               / "templates")),
            autoescape=True)
        return env.get_template("report.html").render(
            report=report, zone_label={"RED": ("ไม่ผ่าน", "x"),
                                       "ORANGE": ("รอยืนยัน", "!"),
                                       "YELLOW": ("ข้อสังเกต", "i")},
            job_id="test", pdf_name="book.pdf", student={})

    def _clean_result(self):
        return checker_module.check_result(Report())

    def test_a_clean_book_still_gets_every_button(self):
        html = self._render(self._clean_result())
        for check in checker_module.STAFF_CHECKS:
            self.assertIn(f'data-staff="{check["id"]}"', html)
            for choice in check["choices"]:
                self.assertIn(f'data-choice="{choice["id"]}"', html)
                self.assertIn(choice["label"], html)
                self.assertIn(choice["label_en"], html)

    def test_the_choices_are_one_two_way_switch_not_two_verdict_buttons(self):
        """เจ้าหน้าที่สั่ง (ก.ย. 2569): ให้เป็นลักษณะเปิด/ปิด สลับข้างแล้วถ้อยคำเปลี่ยนตาม

        "มีค่าปรับ" ไม่ใช่คำตัดสินว่าเล่มผิด จึงต้องไม่หน้าตาเหมือนปุ่มผ่าน/ไม่ผ่าน
        """
        html = self._render(self._clean_result())
        self.assertIn('<div class="sw" role="group">', html)
        self.assertIn(".sw .pf.pass.on {", html)
        self.assertIn(".sw .pf.fail.on {", html)
        for check in checker_module.STAFF_CHECKS:
            for choice in check["choices"]:
                self.assertNotIn(f'data-th="✓ {choice["label"]}"', html)
                self.assertNotIn(f'data-th="✗ {choice["label"]}"', html)
                self.assertIn(f'data-th="{choice["label"]}"', html)

    def test_only_the_choices_that_add_wording_are_marked(self):
        """บรรทัดสถานะต้องแยกได้ว่ากดแล้วมีถ้อยคำเข้าสรุป หรือแค่บันทึกว่าตรวจแล้ว"""
        html = self._render(self._clean_result())
        adds = [ch for c in checker_module.STAFF_CHECKS for ch in c["choices"]
                if ch["text"]]
        quiet = [ch for c in checker_module.STAFF_CHECKS for ch in c["choices"]
                 if not ch["text"]]
        self.assertTrue(adds and quiet)
        for choice in adds:
            block = html.split(f'data-choice="{choice["id"]}"', 1)[1][:200]
            self.assertIn('data-adds="1"', block, choice["id"])
        for choice in quiet:
            block = html.split(f'data-choice="{choice["id"]}"', 1)[1][:200]
            self.assertNotIn('data-adds="1"', block, choice["id"])

    def test_the_card_says_the_wording_went_in(self):
        html = self._render(self._clean_result())
        self.assertIn('<div class="sf-state" hidden></div>', html)
        self.assertIn("function renderStaffState(card)", html)
        self.assertIn("if (card.classList.contains('sf')) renderStaffState(card);", html)
        self.assertIn("renderAllStaffState();", html)

    def test_a_clean_book_still_gets_a_copy_box(self):
        html = self._render(self._clean_result())
        self.assertIn('class="copy-text"', html)
        self.assertIn("ผลการตรวจ:", html)

    def test_the_english_wording_travels_with_the_page(self):
        """คำแปลต้องมาจากทะเบียนเดียวกับฝั่งเซิร์ฟเวอร์ ไม่ใช่สำเนาที่หน้าเว็บเก็บเอง"""
        html = self._render(self._clean_result())
        self.assertIn('id="staff-findings-data"', html)
        self.assertIn("STAFF_LINE_EN[", html)
        self.assertIn("supawit.mar@mahidol.ac.th", html)

    def test_the_button_updates_the_summary(self):
        html = self._render(self._clean_result())
        self.assertIn("function collectStaff()", html)
        self.assertIn("staff: collectStaff()", html)
        self.assertIn(".sf .pf.on[data-choice]", html)
        self.assertIn("card.classList.contains('sf')", html)

    def test_a_failed_update_says_so_instead_of_going_quiet(self):
        """ผลของการกดปุ่มอยู่ในข้อความสรุปเท่านั้น เงียบ = เจ้าหน้าที่ส่งข้อความที่ตกรายการ"""
        html = self._render(self._clean_result())
        self.assertIn('id="copy-error"', html)
        self.assertIn("showCopyError(COPY_ERROR[kind]", html)

    def test_the_printed_report_carries_the_whole_summary(self):
        """textarea พิมพ์ออกกระดาษได้เฉพาะส่วนที่มองเห็น ที่เหลือหายเงียบ ๆ

        ข้อความสรุปยาวขึ้นมากตั้งแต่มีข้อความปิดท้าย เจ้าหน้าที่ที่บันทึกรายงานเป็น PDF
        จะได้ข้อความไม่ครบ จึงต้องมีสำเนาสำหรับพิมพ์ที่เติมไว้ตั้งแต่ฝั่งเซิร์ฟเวอร์
        """
        html = self._render(self._clean_result())
        self.assertIn('<div class="copy-print" aria-hidden="true">', html)
        self.assertIn(".copy-print { display:none; }", html)
        self.assertIn(".copy-print { display:block;", html)
        self.assertIn(".copy-text { display:none; }", html)
        self.assertIn("printable.textContent = text;", html)

    def test_the_copy_box_is_never_wiped_blank_by_the_script(self):
        """ควบคุมเชิงลบของกล่องคัดลอกว่าง — เคยเจอจริงจากภาพหน้าจอของเจ้าหน้าที่

        ถ้าอ่านบล็อก JSON ไม่ได้ (โปรแกรมเปิดไฟล์ที่บันทึกไว้บางตัวตัดทิ้ง)
        renderSummary จะเขียนทับข้อความที่เซิร์ฟเวอร์ใส่มาด้วยค่าว่าง
        """
        html = self._render(self._clean_result())
        self.assertIn("if (text) ta.value = text;", html)
        self.assertIn("const fallback = box ? (box.value || '').trim() : '';", html)


class EveryWayOutOfRunCheckRendersTheReportPage(unittest.TestCase):
    """ทุกทางออกของ run_check ต้องเปิดหน้ารายงานได้จริง

    เจอตอนใช้งานจริง: ด่าน "ภาษาของเล่มไม่ตรงกับที่อนุมัติ" คืน dict ที่คัดลอกคีย์มาจาก
    result ตัวหลัก แต่ result ตัวหลักเติม plain_summary ทีหลัง (นอก dict literal)
    คีย์นั้นจึงหายไป หน้ารายงานพัง Internal Server Error ที่บรรทัด
    {{ report.plain_summary | tojson }} — เล่มที่ภาษาไม่ตรงเปิดรายงานไม่ได้เลย
    ทั้งที่ตัวตรวจทำงานถูกและด่านทดสอบทั้งสี่ผ่านหมด

    เทสต์เดิมเทียบคีย์กับ "dict literal" ในโค้ด ซึ่งเป็นการเทียบผิดตัว จึงผ่านทั้งที่พัง
    ชุดนี้เรนเดอร์ report.html ของจริงแทน ถ้าตกคีย์ไหนจะพังตรงนี้ก่อนถึงมือเจ้าหน้าที่
    """

    CONTEXTS = {
        "ไม่ใช่ไฟล์ PDF": {},
        "PDF ไม่มีหน้า": {"n_pages": 0},
        "ภาษาของเล่มไม่ตรง": {"document_type": "THESIS", "option": None,
                              "chapters_mode": "strict", "n_pages": 100,
                              "approved_data": True},
        "ตรวจครบตามปกติ": {"document_type": "THESIS", "option": 1,
                            "chapters_mode": "strict", "n_pages": 100,
                            "approved_data": True},
    }

    def _report(self, context):
        rep = Report()
        rep.add("RED", "front_matter", "ทั้งเล่ม",
                "ภาษาในไฟล์รูปเล่มไม่ตรงกับที่ได้รับอนุมัติ เล่มที่ส่งมาจัดทำเป็นภาษาไทย",
                'ภาษาที่ได้รับอนุมัติคือ "ภาษาอังกฤษ" ต้องดำเนินการจัดทำเล่มเป็น "ภาษาอังกฤษ"',
                "", "FORM.BOOK_LANGUAGE")
        return checker_module.check_result(rep, context)

    def _render(self, report):
        import jinja2
        env = jinja2.Environment(
            loader=jinja2.FileSystemLoader(str(Path(checker_module.__file__).parent
                                               / "templates")),
            autoescape=True)
        return env.get_template("report.html").render(
            report=report, zone_label={"RED": ("ไม่ผ่าน", "x"),
                                       "ORANGE": ("รอยืนยัน", "!"),
                                       "YELLOW": ("ข้อสังเกต", "i")},
            job_id="test", pdf_name="book.pdf", student={})

    def test_every_way_out_renders(self):
        for name, context in self.CONTEXTS.items():
            html = self._render(self._report(context))
            self.assertIn("ภาษาในไฟล์รูปเล่มไม่ตรงกับที่ได้รับอนุมัติ", html, name)

    def test_every_way_out_carries_the_same_keys(self):
        keysets = {name: set(self._report(ctx)) for name, ctx in self.CONTEXTS.items()}
        first = next(iter(keysets.values()))
        for name, keys in keysets.items():
            self.assertEqual(keys, first, name)

    def test_the_summary_text_is_built_for_every_way_out(self):
        """report.html อ่าน report.plain_summary ตรง ๆ ขาดไม่ได้สักทางออกเดียว"""
        for name, context in self.CONTEXTS.items():
            report = self._report(context)
            self.assertIn("plain_summary", report, name)
            self.assertIn("ผลการตรวจ:", report["plain_summary"], name)

    def test_the_copy_box_already_holds_the_text_before_any_script_runs(self):
        """กล่องคัดลอกต้องไม่ว่างรอ JS เติม

        ถ้าสคริปต์ในหน้าพัง (เคยเจอตอน TR มีช่องว่างในอาร์เรย์ แล้วการแปลตายทั้งก้อน)
        เจ้าหน้าที่จะได้กล่องคัดลอกว่างเปล่าโดยไม่มีอะไรบอกว่าพัง และหน้าที่บันทึก
        หรือพิมพ์ออกมาก็ไม่มีข้อความสรุปติดไปด้วย
        """
        html = self._render(self._report(self.CONTEXTS["ภาษาของเล่มไม่ตรง"]))
        found = re.search(r'<textarea class="copy-text"[^>]*>([\s\S]*?)</textarea>', html)
        self.assertIsNotNone(found, "หา textarea ของกล่องคัดลอกไม่เจอ")
        inside = found.group(1)
        self.assertIn("ผลการตรวจ:", inside)
        self.assertIn("ภาษาในไฟล์รูปเล่มไม่ตรงกับที่ได้รับอนุมัติ", inside)

    def test_dropping_a_key_really_breaks_the_page(self):
        """ควบคุมเชิงลบ: พิสูจน์ว่าเทสต์นี้จับของจริง ไม่ได้ผ่านลอย ๆ"""
        report = self._report(self.CONTEXTS["ภาษาของเล่มไม่ตรง"])
        report.pop("plain_summary")
        with self.assertRaises(Exception):
            self._render(report)

    def test_run_check_has_no_hand_written_result_dict_left(self):
        """ทางออกทุกทางต้องผ่าน check_result ไม่ใช่ประกอบ dict เอง"""
        source = inspect.getsource(checker_module.run_check)
        self.assertNotIn('"issues_by_zone": rep.zones', source)
        self.assertEqual(source.count("return check_result("), 4)


class BookLanguageIsReadFromTemplateText(unittest.TestCase):
    """ภาษาของเล่มตัดสินจาก "ข้อความตายตัวของ template" ไม่ใช่สัดส่วนตัวอักษรบนหน้า

    เล่มไทยกับเล่มอังกฤษใช้ template คนละชุด ข้อความบังคับบนหน้าปก ประโยคบนหน้าลงนาม
    และคำนำหน้าหัวบท จึงเป็นตัวบอกภาษาที่ตรงที่สุด ส่วนการนับสัดส่วนตัวอักษรจะโดน
    เล่มไทยที่มีศัพท์อังกฤษเยอะหลอกได้ (ปัญหาเดียวกับที่ title_script เจอ)

    วัดกับเล่มจริงสามเล่ม (นานาชาติ / ไทย-อังกฤษ / ไทย) สัญญาณทั้งสามตัวชี้ตรงกัน
    หมดทุกเล่ม ไม่มีเล่มไหนที่สัญญาณสวนทางกันเลย
    """

    TH_COVER = ("วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร@"
                "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล@ลิขสิทธิ์ของมหาวิทยาลัยมหิดล")
    EN_COVER = ("A THESIS SUBMITTED IN PARTIAL FULFILLMENT OF THE REQUIREMENTS "
                "FOR THE DEGREE OF@FACULTY OF GRADUATE STUDIES@MAHIDOL UNIVERSITY@"
                "COPYRIGHT OF MAHIDOL UNIVERSITY")
    TH_SIG = "วิทยานิพนธ์@เรื่อง@ชื่อเรื่อง@นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร"
    EN_SIG = ("Thesis@entitled@A TITLE@was submitted to the Faculty of Graduate Studies, "
              "Mahidol University for the degree of")
    TH_CH = "บทที่ 1@บทนำ"
    EN_CH = "CHAPTER 1@INTRODUCTION"

    def pages(self, *blocks):
        return [b.replace("@", NEWLINE) for b in blocks]

    def signals(self, *blocks):
        return checker_module.book_language_signals(self.pages(*blocks), 0, "THESIS")

    def template_signals(self, *blocks):
        """เฉพาะสามสัญญาณที่มาจากข้อความตายตัวของ template

        คีย์ "script" เป็นตัวสำรองคนละชนิด (สัดส่วนตัวอักษรทั้งไฟล์) ไม่ใช่ส่วนของเล่ม
        เทสต์ในคลาสนี้พูดถึงข้อความ template เท่านั้น
        """
        got = self.signals(*blocks)
        return {k: got[k] for k in ("cover", "signature", "chapter")}

    def test_a_thai_book_is_read_as_thai(self):
        got = self.signals(self.TH_COVER, self.TH_SIG, self.TH_CH)
        self.assertEqual(self.template_signals(self.TH_COVER, self.TH_SIG, self.TH_CH),
                         {"cover": "thai", "signature": "thai", "chapter": "thai"})
        self.assertEqual(checker_module.book_language(got), "thai")

    def test_an_english_book_is_read_as_english(self):
        got = self.signals(self.EN_COVER, self.EN_SIG, self.EN_CH)
        self.assertEqual(self.template_signals(self.EN_COVER, self.EN_SIG, self.EN_CH),
                         {"cover": "en", "signature": "en", "chapter": "en"})
        self.assertEqual(checker_module.book_language(got), "en")

    def test_two_signals_are_enough(self):
        """ปกอ่านไม่ออก (ฟอนต์พัง หน้าสแกน) ต้องยังตัดสินได้จากอีกสองตัว"""
        got = self.signals("", self.TH_SIG, self.TH_CH)
        self.assertEqual(got["cover"], "")
        self.assertEqual(checker_module.book_language(got), "thai")

    def test_one_signal_alone_is_not_enough(self):
        got = self.signals(self.TH_COVER, "", "")
        self.assertEqual(checker_module.book_language(got), "")

    def test_a_file_with_no_readable_text_decides_nothing(self):
        self.assertEqual(checker_module.book_language(self.signals("", "", "")), "")

    def test_signals_that_disagree_decide_nothing(self):
        """ปกอังกฤษ แต่หน้าลงนามกับหัวบทเป็นไทย — ต้องไม่ฟันธงแล้วหยุดตรวจทั้งเล่ม"""
        got = self.signals(self.EN_COVER, self.TH_SIG, self.TH_CH)
        self.assertEqual(self.template_signals(self.EN_COVER, self.TH_SIG, self.TH_CH),
                         {"cover": "en", "signature": "thai", "chapter": "thai"})
        self.assertEqual(checker_module.book_language(got), "")

    def test_the_unanimity_rule_is_what_stops_it(self):
        """ควบคุมเชิงลบ: ถ้าตัดสินด้วยเสียงข้างมาก เคสข้างบนจะถูกฟันธงว่าเป็นเล่มไทย"""
        got = self.signals(self.EN_COVER, self.TH_SIG, self.TH_CH)
        votes = [v for v in got.values() if v]
        majority = max(set(votes), key=votes.count)
        self.assertEqual(majority, "thai")
        self.assertNotEqual(checker_module.book_language(got), majority)

    def test_a_page_carrying_both_languages_decides_nothing(self):
        got = self.signals(self.TH_COVER + NEWLINE + self.EN_COVER.replace("@", NEWLINE),
                           "", "")
        self.assertEqual(got["cover"], "")


class TheLastResortIsTheScriptOfTheWholeFile(unittest.TestCase):
    """เล่มที่ไม่ทำตาม template เลย ก็ยังต้องบอกได้ว่าเป็นภาษาอะไร

    เจ้าหน้าที่ทัก (ก.ย. 2569) ว่าระบบไม่น่าจะอ่านไม่ได้ว่าเล่มไหนเป็นภาษาไหน ถูกต้อง —
    ไฟล์ที่ดึงข้อความไม่ได้เลยถูกปฏิเสธตั้งแต่ตอนอัปโหลด (main._pdf_readability_issue)
    ไฟล์ที่เข้ามาถึงตัวตรวจจึงมีข้อความให้อ่านเสมอ ถ้าข้อความตายตัวของ template เงียบ
    หมดทั้งสามจุด ยังเหลือสัดส่วนตัวอักษรทั้งไฟล์ให้ใช้เป็นตัวสำรอง

    วัดกับเล่มจริง เล่มอังกฤษได้ไทย 0.1% กับ 3.7% (เล่มหลังมีบทคัดย่อไทยเต็ม ๆ)
    เล่มไทยได้ไทย 96.1% เกณฑ์จึงเว้นช่องว่างตรงกลางไว้กว้างมาก
    """

    def _thai(self, n=40):
        return ["ทดสอบเนื้อหาภาษาไทยของเล่มวิทยานิพนธ์ " * n]

    def _english(self, n=40):
        return ["This is the English body text of the thesis document " * n]

    def test_a_thai_file_reads_as_thai(self):
        self.assertEqual(checker_module.book_language_by_script(self._thai()), "thai")

    def test_an_english_file_reads_as_english(self):
        self.assertEqual(checker_module.book_language_by_script(self._english()), "en")

    def test_a_mixed_file_still_decides_nothing(self):
        """ครึ่งไทยครึ่งอังกฤษ = เดาไม่ได้ ห้ามฟันธง"""
        mixed = ["ไทยไทยไทยไทยไทย English English English " * 30]
        self.assertEqual(checker_module.book_language_by_script(mixed), "")

    def test_a_file_with_almost_no_text_decides_nothing(self):
        self.assertEqual(checker_module.book_language_by_script(["สั้นมาก"]), "")
        self.assertEqual(checker_module.book_language_by_script([]), "")

    def test_an_english_book_with_a_thai_abstract_is_still_english(self):
        """เล่มหลักสูตรไทย-อังกฤษมีบทคัดย่อไทยเต็ม ๆ อยู่ในเล่ม ต้องไม่ถูกอ่านเป็นเล่มไทย"""
        pages = self._english(60) + self._thai(3)
        self.assertEqual(checker_module.book_language_by_script(pages), "en")

    def test_the_fallback_only_speaks_when_the_template_is_silent(self):
        """ควบคุมเชิงลบ: ข้อความ template แม่นกว่า ห้ามให้ตัวสำรองมาชี้ขาดทับ"""
        # อ่านได้แค่ปก — เดิมเงียบ ต้องยังเงียบเหมือนเดิม แม้ตัวสำรองจะชี้ทางเดียวกัน
        self.assertEqual(checker_module.book_language(
            {"cover": "thai", "signature": "", "chapter": "", "script": "thai"}), "")
        # สองตัวตรงกัน ตัวสำรองชี้สวนทาง ต้องเชื่อ template
        self.assertEqual(checker_module.book_language(
            {"cover": "thai", "signature": "thai", "chapter": "", "script": "en"}), "thai")
        # ขัดกันเอง ต้องยังไม่ฟันธง
        self.assertEqual(checker_module.book_language(
            {"cover": "en", "signature": "thai", "chapter": "thai", "script": "thai"}), "")

    def test_the_fallback_answers_when_the_template_says_nothing(self):
        self.assertEqual(checker_module.book_language(
            {"cover": "", "signature": "", "chapter": "", "script": "thai"}), "thai")
        self.assertEqual(checker_module.book_language(
            {"cover": "", "signature": "", "chapter": "", "script": ""}), "")

    def test_the_signals_carry_the_fallback_along(self):
        got = checker_module.book_language_signals(
            ["ไม่มีข้อความ template แต่เป็นภาษาไทยล้วน " * 30], 0, "THESIS")
        self.assertEqual(got["cover"], "")
        self.assertEqual(got["script"], "thai")
        self.assertEqual(checker_module.book_language(got), "thai")

    def test_the_fallback_is_not_a_part_of_the_document(self):
        """ห้ามโผล่ในรายชื่อ "ส่วนที่จัดทำเป็นอีกภาษา" เพราะไม่ใช่ส่วนของเล่ม"""
        self.assertNotIn("script", checker_module.BOOK_LANGUAGE_PARTS)
        source = inspect.getsource(checker_module.book_language_rows)
        self.assertIn('for key in ("cover", "signature", "chapter")', source)


class ApprovedBookLanguageComesFromTheProgramme(unittest.TestCase):
    """thai_english กับ international ทำเล่มเป็นภาษาอังกฤษเหมือนกัน"""

    def test_each_programme_maps_to_a_book_language(self):
        cases = {"thai": "thai", "thai_english": "en", "international": "en"}
        for programme, want in cases.items():
            self.assertEqual(
                checker_module.approved_book_language({"program_language": programme}),
                want, programme)

    def test_the_two_english_programmes_do_not_clash_with_each_other(self):
        """เล่ม thai_english จับคู่กับข้อมูล international ต้องไม่ถูกฟ้องว่าผิดภาษา"""
        self.assertEqual(
            checker_module.approved_book_language({"program_language": "thai_english"}),
            checker_module.approved_book_language({"program_language": "international"}))

    def test_no_programme_means_nothing_to_compare(self):
        for approved in ({}, {"program_language": ""}, None):
            self.assertEqual(checker_module.approved_book_language(approved), "")


class BookLanguageFindingComesFirstInTheReport(unittest.TestCase):
    """ข้อ "เล่มผิดภาษา" ต้องอยู่บนสุด ก่อนข้อชื่อเรื่อง (เจ้าหน้าที่สั่ง ก.ย. 2569)

    ตำแหน่งของข้อนี้คือ "ทั้งเล่ม" ซึ่งเดิมตกกลุ่ม "เนื้อหา (บท)" กลางรายงาน
    จึงจัดกลุ่มจากรหัสกฎแทนการเดาจากคำ แบบเดียวกับกฎลำดับส่วนประกอบ
    """

    ISSUE = {"rule_id": "FORM.BOOK_LANGUAGE", "part": "front_matter",
             "location": "ทั้งเล่ม",
             "found": "ภาษาในไฟล์รูปเล่มไม่ตรงกับที่ได้รับอนุมัติ "
                      "เล่มที่ส่งมาจัดทำเป็นภาษาไทย",
             "expected": 'ภาษาที่ได้รับอนุมัติคือ "ภาษาอังกฤษ" '
                         'ต้องดำเนินการจัดทำเล่มเป็น "ภาษาอังกฤษ"'}
    TITLE_ISSUE = {"rule_id": "FORM.APPROVED_MATCH", "part": "front_matter",
                   "location": "หน้าปก", "found": "ชื่อเรื่องไม่ตรงกับข้อมูลในระบบ",
                   "expected": "ต้องตรงข้อมูลอนุมัติทุกตัวอักษร"}

    def test_the_group_is_the_first_one_in_the_report(self):
        self.assertEqual(checker_module.SUMMARY_SECTIONS[0][0], "ภาษาของเล่ม")

    def test_the_finding_lands_in_that_group(self):
        self.assertEqual(summary_section(self.ISSUE), "ภาษาของเล่ม")
        self.assertEqual(checker_module.classify(self.ISSUE), "ภาษาของเล่ม")

    def test_it_sorts_ahead_of_the_title_finding(self):
        both = sorted([self.TITLE_ISSUE, self.ISSUE], key=issue_sort_key)
        self.assertEqual(both[0]["rule_id"], "FORM.BOOK_LANGUAGE")

    def test_without_the_rule_id_shortcut_it_sinks_into_the_body_group(self):
        """ควบคุมเชิงลบ: ถ้าเดาจากคำ ตำแหน่ง "ทั้งเล่ม" จะตกกลุ่มเนื้อหา"""
        loose = dict(self.ISSUE, rule_id="")
        self.assertEqual(summary_section(loose), "เนื้อหา (บท)")
        self.assertEqual(checker_module.classify(loose), "ภาษาไม่ครบตามหลักสูตร")

    def test_the_group_and_category_have_english_names(self):
        import tools.check_i18n as i18n
        catmap = i18n.load_catmap()
        self.assertIn(summary_section(self.ISSUE), catmap)
        self.assertIn(checker_module.classify(self.ISSUE), catmap)

    def test_the_summary_keeps_the_wording_the_staff_asked_for(self):
        """ห้ามย่อเหลือ 'ต้องแก้เป็น "ภาษาอังกฤษ"' — ต้องเป็นประโยคเต็มตามที่สั่ง"""
        rep = Report()
        rep.add("RED", "front_matter", self.ISSUE["location"], self.ISSUE["found"],
                self.ISSUE["expected"], "", "FORM.BOOK_LANGUAGE")
        for it in rep.zones["RED"]:
            it["category"] = checker_module.classify(it)
            it["section"] = summary_section(it)
        text = plain_summary({"verdict": "ไม่ผ่าน", "issues_by_zone": rep.zones})
        self.assertIn(self.ISSUE["expected"], text)
        self.assertNotIn('ต้องแก้เป็น "ภาษาอังกฤษ"', text)


class BookLanguageMismatchStopsTheWholeCheck(unittest.TestCase):
    """เล่มผิดภาษาทั้งเล่ม = หยุดตรวจส่วนอื่น แล้วแจ้งจุดผิดข้อเดียว

    วัดกับเล่มจริง: เล่มไทยที่จับคู่กับข้อมูลอนุมัติ "เล่มอังกฤษ" เคยได้แดง 24 ข้อ
    โดยไม่มีข้อไหนบอกสาเหตุจริง และบางข้อยกชื่อเรื่องมาอ้างว่าเป็นชื่อนักศึกษา
    หลังเพิ่มกฎเหลือแดงข้อเดียว
    """

    def _gate_source(self):
        source = inspect.getsource(checker_module.run_check)
        start = source.index('"FORM.BOOK_LANGUAGE")')
        return source[start:source.index("if wrong_parts:", start)]

    def test_the_gate_returns_before_any_other_rule_runs(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertLess(source.index("approved_book_language(approved)"),
                        source.index("ไม่พบกิตติกรรมประกาศ"))

    # เทสต์ "คีย์ครบไหม" เคยอยู่ตรงนี้ โดยเทียบคีย์กับ dict literal ในโค้ด ซึ่งเทียบผิดตัว
    # (result ตัวหลักเติม plain_summary ทีหลัง นอก literal) จึงผ่านทั้งที่หน้ารายงานพัง 500
    # ย้ายไปเป็นการเรนเดอร์ report.html ของจริงแล้ว ดู
    # EveryWayOutOfRunCheckRendersTheReportPage

    def test_the_report_says_the_rest_was_not_checked(self):
        self.assertIn("หยุดตรวจ", checker_module.BOOK_LANGUAGE_STOPPED)
        self.assertIn("BOOK_LANGUAGE_STOPPED", self._gate_source())

    def test_the_gate_is_off_for_the_translation_tool(self):
        """check_i18n จับคู่ข้อมูลสมมติกับเล่มไหนก็ได้ ถ้าไม่ปิดจะเก็บข้อความไม่ได้"""
        source = inspect.getsource(checker_module.run_check)
        self.assertRegex(source, r"(?s)approved and same_student and not skip_identity_check")

    def test_parts_are_joined_with_a_word_not_a_symbol(self):
        self.assertEqual(checker_module._join_and(["หน้าปก"]), "หน้าปก")
        self.assertEqual(checker_module._join_and(["หน้าปก", "หน้าลงนาม"]),
                         "หน้าปก และ หน้าลงนาม")
        for ch in "\u00b7\u2014\u2013\u2192\u2194\u2022\u2264":
            self.assertNotIn(ch, checker_module._join_and(["หน้าปก", "หน้าลงนาม"]))


class TheLanguageOfTheDocumentShowsInTheVerificationTable(unittest.TestCase):
    """ตาราง "ผลเทียบข้อมูลอนุมัติรายตำแหน่ง" ต้องมีหัวข้อภาษาที่เขียนด้วย

    เจ้าหน้าที่ทัก (ก.ย. 2569) ว่าหัวข้อนี้หายไปจากตาราง ทั้งที่ระบบตรวจอยู่แล้ว
    ถ้าลงเฉพาะตอนผิด เจ้าหน้าที่จะแยกไม่ออกว่า "ตรวจแล้วผ่าน" กับ "ไม่ได้ตรวจ"
    และต้องเห็น **ภาษาของทั้งสองฝั่ง** ไม่ใช่บอกแค่ว่าตรงหรือไม่ตรง
    """

    TOPIC = "ภาษาที่เขียน"

    def _rows(self, signals, program_language="thai"):
        want = checker_module.approved_book_language(
            {"program_language": program_language})
        if not want:
            return []
        return [{"location": loc, "status": status, "detail": detail}
                for loc, status, detail
                in checker_module.book_language_rows(want, dict(signals))]

    def test_run_check_files_the_rows_itself(self):
        """ต้องถูกเรียกจริงใน run_check ไม่ใช่มีแต่ฟังก์ชันลอย ๆ"""
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("book_language_rows(want_language, language_signals)", source)
        self.assertIn('rep.add_verification("ภาษาที่เขียน", where, status, detail)',
                      source)

    def test_both_sides_of_the_comparison_are_shown(self):
        """เจ้าหน้าที่สั่งให้เห็นว่าอนุมัติภาษาอะไร และในไฟล์เป็นภาษาอะไร"""
        rows = self._rows({"cover": "thai", "signature": "thai", "chapter": "thai"})
        self.assertEqual([r["location"] for r in rows],
                         ["ข้อมูลอนุมัติ", "ในไฟล์รูปเล่ม"])
        self.assertEqual([r["detail"] for r in rows], ["ภาษาไทย", "ภาษาไทย"])
        self.assertEqual([r["status"] for r in rows], ["pass", "pass"])

    def test_a_mismatch_shows_the_two_different_languages(self):
        rows = self._rows({"cover": "en", "signature": "en", "chapter": "en"})
        self.assertEqual([r["status"] for r in rows], ["fail", "fail"])
        self.assertEqual(rows[0]["detail"], "ภาษาไทย")
        self.assertEqual(rows[1]["detail"], "ภาษาอังกฤษ")

    def test_conflicting_signals_name_the_parts_that_are_wrong(self):
        """ตัดสินทั้งเล่มไม่ได้ แต่รู้แน่ว่าส่วนไหนคนละภาษา ต้องบอกให้ชัด"""
        rows = self._rows({"cover": "en", "signature": "thai", "chapter": "thai"})
        self.assertEqual([r["status"] for r in rows], ["fail", "fail"])
        self.assertEqual(rows[1]["detail"], "ภาษาอังกฤษ ในส่วน หน้าปก")

    def test_a_file_the_system_cannot_read_is_pending_not_a_pass(self):
        """อ่านไม่ออกไม่ใช่ผ่าน ถ้านับเป็นผ่านเจ้าหน้าที่จะเชื่อว่าตรวจครบแล้ว"""
        rows = self._rows({"cover": "", "signature": "", "chapter": ""})
        self.assertEqual([r["status"] for r in rows], ["pending", "pending"])
        self.assertEqual(rows[1]["detail"], "ระบบอ่านภาษาจากไฟล์ไม่ได้")

    def test_no_approved_programme_means_no_rows(self):
        """ไม่มีข้อมูลอนุมัติ = ไม่มีอะไรให้เทียบ ห้ามขึ้นแถวลอย ๆ"""
        self.assertEqual(self._rows({"cover": "thai", "signature": "thai",
                                     "chapter": "thai"}, program_language=""), [])

    def test_every_word_the_table_shows_has_an_english_translation(self):
        """ช่องในตารางแปลด้วย TR (.tr-dyn) ถ้าไม่มีกฎ รายงานอังกฤษจะเหลือคำไทยค้าง"""
        report_html = (Path(checker_module.__file__).parent
                       / "templates" / "report.html").read_text(encoding="utf-8")
        for phrase in ("^ภาษาที่เขียน$", "^ข้อมูลอนุมัติ$", "^ในไฟล์รูปเล่ม$",
                       "^ระบบอ่านภาษาจากไฟล์ไม่ได้$", "^ภาษาไทย$", "^ภาษาอังกฤษ$",
                       "ในส่วน"):
            self.assertIn(phrase, report_html, phrase)


class TitleLanguageComesFromTheSystemDataOnly(unittest.TestCase):
    """ชื่อเรื่องสองภาษาต้องเอาจากข้อมูลระบบ (eThesis/บฑ.1) ไม่ใช่เดาจากหน้ากระดาษ

    กฎ "หน้านี้ต้องไม่มีชื่อเรื่องอีกภาษา" สั่งให้ลบข้อความออกจากเล่ม ถ้าเดาเองว่า
    ข้อความไหนคือชื่อเรื่องแล้วเดาผิด จะสั่งให้นักศึกษาลบข้อความที่ถูกต้องทิ้ง
    """

    TH = "การพัฒนาระบบ RFID สำหรับการจัดการคลังสินค้า"
    EN = "DEVELOPMENT OF AN RFID SYSTEM FOR WAREHOUSE MANAGEMENT"

    def test_each_field_is_read_by_its_own_label(self):
        A = {"title_th": self.TH, "title_en": self.EN}
        self.assertEqual(checker_module.approved_title(A, "thai"), self.TH)
        self.assertEqual(checker_module.approved_title(A, "en"), self.EN)

    def test_a_dash_is_not_a_thai_title(self):
        """หลักสูตรนานาชาติกรอกช่องชื่อเรื่องภาษาไทยเป็นขีด (เล่มทดสอบที่ 1)"""
        self.assertEqual(checker_module.approved_title({"title_th": "-"}, "thai"), "")

    def test_an_empty_or_missing_field_is_not_used(self):
        self.assertEqual(checker_module.approved_title({"title_th": ""}, "thai"), "")
        self.assertEqual(checker_module.approved_title({}, "en"), "")

    def test_fields_filled_in_the_wrong_language_are_not_used(self):
        """ถ้าข้อมูลระบบสลับช่องกันไว้ ให้ข้ามดีกว่าเทียบผิดช่องแล้วฟ้องมั่ว"""
        swapped = {"title_th": self.EN, "title_en": self.TH}
        self.assertEqual(checker_module.approved_title(swapped, "thai"), "")
        self.assertEqual(checker_module.approved_title(swapped, "en"), "")


class ThaiTitlesMayContainEnglishLetters(unittest.TestCase):
    """ชื่อเรื่องไทยมีอักษรอังกฤษปนได้ ต้องไม่ถูกนับเป็นชื่อเรื่องภาษาอังกฤษ

    เจ้าหน้าที่เตือนไว้ตรง ๆ (ก.ย. 2569) ว่าหัวข้อไทยบางหัวข้อมีตัวอักษรภาษาอังกฤษ
    ผสมอยู่ — ชื่อเทคโนโลยี ตัวย่อ ชื่อสารเคมี ตัวชี้ขาดจึงเป็น "มีอักษรไทยไหม"
    ไม่ใช่ "มีอักษรอังกฤษไหม" เพราะชื่อเรื่องภาษาอังกฤษไม่มีอักษรไทยปนเลยสักตัว
    แต่ชื่อเรื่องไทยมีอักษรอังกฤษปนได้เกินครึ่งบรรทัด

    ชื่อเรื่องไทยแบบนี้ไปคล้ายชื่อเรื่องภาษาอังกฤษของตัวเองเสมอ วัดคะแนนตอนไม่กรอง
    ภาษาได้ 0.53-0.74 แล้วแต่เล่ม ซึ่งคาบเกี่ยวและข้ามเกณฑ์ 0.6 ได้จริง
    """

    TH = "ผลของ machine learning based clinical decision support system ต่อการวินิจฉัย"
    EN = ("EFFECT OF A MACHINE LEARNING BASED CLINICAL DECISION SUPPORT SYSTEM "
          "ON DIAGNOSIS")
    OTHER_TH = "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ"
    COVER = ("วิทยานิพนธ์@ผลของ machine learning based clinical decision support "
             "system ต่อการวินิจฉัย@นางสาวสมหญิง ดีงาม@"
             "ลิขสิทธิ์ของมหาวิทยาลัยมหิดล").replace("@", NEWLINE)

    def test_a_title_that_is_mostly_english_letters_is_still_thai(self):
        for thai in (self.TH, self.OTHER_TH):
            self.assertEqual(checker_module.title_script(thai), "thai")
        self.assertEqual(checker_module.title_script(self.EN), "en")

    def test_the_thai_cover_is_not_accused_of_carrying_the_english_title(self):
        self.assertEqual(checker_module.title_printed_on_page(self.COVER, self.EN), "")

    def test_without_the_language_filter_that_same_cover_is_accused(self):
        """ควบคุมเชิงลบ: ถ้าไม่แยกภาษาก่อนเทียบ หน้าปกไทยที่ถูกต้องจะโดนฟ้อง"""
        with mock.patch.object(checker_module, "title_script", lambda text: "en"):
            self.assertNotEqual(
                checker_module.title_printed_on_page(self.COVER, self.EN), "")

    def test_the_english_title_really_added_is_still_found(self):
        """กรองภาษาแล้วต้องยังจับของจริงได้ ไม่ใช่ปิดกฎทิ้ง"""
        cover = self.COVER + NEWLINE + self.EN
        self.assertIn("MACHINE LEARNING",
                      checker_module.title_printed_on_page(cover, self.EN))


class CoverAndSignatureCarryOneTitleLanguage(unittest.TestCase):
    """หน้าปกและหน้าลงนามต้องมีชื่อเรื่องภาษาเดียว ตามภาษาของเล่ม

    เกณฑ์ตัดสินวัดจากเล่มจริงสามเล่ม (นานาชาติ / ไทย-อังกฤษ / ไทย): หน้าที่ไม่มี
    ชื่อเรื่องอีกภาษาได้คะแนนสูงสุด 0.21 หน้าที่พิมพ์ไว้จริงได้ 0.69 ขึ้นไป
    """

    TH = "การมีส่วนร่วมของประชาชนในการประเมินผลกระทบสิ่งแวดล้อมของโครงการปิโตรเลียม"
    EN = ("PUBLIC PARTICIPATION IN ENVIRONMENTAL IMPACT ASSESSMENT "
          "OF PETROLEUM DEVELOPMENT PROJECTS")
    TH_TAIL = ("นายสมชาย ใจดี@วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร@"
               "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล@ลิขสิทธิ์ของมหาวิทยาลัยมหิดล")
    EN_TAIL = ("SOMCHAI JAIDEE@A THESIS SUBMITTED IN PARTIAL FULFILLMENT@"
               "FACULTY OF GRADUATE STUDIES@MAHIDOL UNIVERSITY@"
               "COPYRIGHT OF MAHIDOL UNIVERSITY")

    def page(self, *blocks):
        return NEWLINE.join(blocks).replace("@", NEWLINE)

    def test_a_thai_cover_with_only_the_thai_title_is_clean(self):
        cover = self.page(self.TH, self.TH_TAIL)
        self.assertEqual(checker_module.title_printed_on_page(cover, self.EN), "")

    def test_an_english_cover_with_only_the_english_title_is_clean(self):
        cover = self.page(self.EN, self.EN_TAIL)
        self.assertEqual(checker_module.title_printed_on_page(cover, self.TH), "")

    def test_an_english_title_added_to_a_thai_cover_is_reported(self):
        cover = self.page(self.TH, self.EN, self.TH_TAIL)
        self.assertIn("PUBLIC PARTICIPATION",
                      checker_module.title_printed_on_page(cover, self.EN))

    def test_a_thai_title_added_to_an_english_cover_is_reported(self):
        cover = self.page(self.EN, self.TH, self.EN_TAIL)
        self.assertIn("การมีส่วนร่วม",
                      checker_module.title_printed_on_page(cover, self.TH))

    def test_a_title_broken_across_lines_is_still_found(self):
        """ชื่อเรื่องยาวถูกตัดขึ้นหลายบรรทัดเสมอ ต้องรวมบรรทัดก่อนเทียบ"""
        wrapped = "@".join(["PUBLIC PARTICIPATION IN ENVIRONMENTAL",
                            "IMPACT ASSESSMENT OF PETROLEUM",
                            "DEVELOPMENT PROJECTS"])
        cover = self.page(self.TH, wrapped, self.TH_TAIL)
        self.assertNotEqual(checker_module.title_printed_on_page(cover, self.EN), "")

    def test_template_wording_alone_is_not_mistaken_for_the_title(self):
        """หน้าปกอังกฤษมีข้อความ template เต็มหน้า ต้องไม่ถูกนับเป็นชื่อเรื่องไทย"""
        self.assertEqual(
            checker_module.title_printed_on_page(self.page(self.EN_TAIL), self.TH), "")

    def test_the_rule_stays_off_when_the_system_has_no_such_title(self):
        """หลักสูตรนานาชาติไม่มีชื่อเรื่องภาษาไทยในระบบ จึงไม่มีอะไรให้เทียบ"""
        cover = self.page(self.EN, self.EN_TAIL)
        self.assertEqual(
            checker_module.title_printed_on_page(
                cover, checker_module.approved_title({"title_th": "-"}, "thai")), "")


class TitleLanguageFindingsAreNotFiledAsTitleMismatch(unittest.TestCase):
    """ข้อ "หน้านี้มีชื่อเรื่องอีกภาษาอยู่ด้วย" ไม่ใช่ "ชื่อเรื่องไม่ตรง บฑ.1"

    ชื่อเรื่องตรง บฑ.1 ทุกตัวอักษร แต่มีชื่อเรื่องเกินมาอีกอัน วิธีแก้คือลบข้อความออก
    ไม่ใช่ไล่เทียบตัวอักษรกับ บฑ.1
    """

    ISSUE = {"rule_id": "FRONT.TITLE_ONE_LANGUAGE", "location": "หน้าปก",
             "found": 'หน้านี้มีชื่อเรื่องภาษาอังกฤษอยู่ด้วย: "PUBLIC PARTICIPATION"',
             "expected": "เล่มภาษาไทยต้องมีเฉพาะชื่อเรื่องภาษาไทย ทั้งบนหน้าปกและหน้าลงนาม"}

    def test_the_finding_lands_in_its_own_category(self):
        self.assertEqual(checker_module.classify(self.ISSUE), "ภาษาของชื่อเรื่อง")

    def test_without_the_shortcut_it_looks_like_a_title_mismatch(self):
        """ควบคุมเชิงลบ: ถ้าเดาจากคำ คำว่าชื่อเรื่องจะพาไปหมวดเทียบ บฑ.1"""
        loose = dict(self.ISSUE, rule_id="")
        self.assertEqual(checker_module.classify(loose), "ชื่อเรื่องไม่ตรง บฑ.1")

    def test_the_category_has_an_english_name(self):
        import tools.check_i18n as i18n
        self.assertIn(checker_module.classify(self.ISSUE), i18n.load_catmap())


class CoverPageMustBeTheFirstSheet(unittest.TestCase):
    """หน้าปกต้องอยู่แผ่นแรกของไฟล์เสมอ (เจ้าหน้าที่ยืนยัน ก.ย. 2569)

    เดิมโค้ดถือว่า pages[0] คือหน้าปกเสมอ เล่มที่มีใบปะหน้า ใบรับรอง หรือหน้าว่าง
    มาก่อนจึงถูกตรวจผิดจุดทั้งชุด วัดกับเล่มจริงที่แทรกหน้าเกินไว้หน้าสุด ได้ข้อฟ้อง
    บนหน้าปก 8/8/7 ข้อ โดยไม่มีข้อไหนบอกสาเหตุจริงเลย

    ตัวชี้ขาดคือ "บรรทัดลิขสิทธิ์" ซึ่งวัดจากเล่มจริงทั้งสามเล่ม (นานาชาติ /
    ไทย-อังกฤษ / ไทย) แล้วพบเฉพาะบนแผ่นแรกแผ่นเดียว ส่วนชื่อบัณฑิตวิทยาลัยกับ
    ชื่อมหาวิทยาลัยไปโผล่บนหน้าลงนามและหน้าบทคัดย่อด้วย จึงใช้ชี้ขาดไม่ได้
    """

    TH_COVER = "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร\nลิขสิทธิ์ของมหาวิทยาลัยมหิดล"
    EN_COVER = ("FACULTY OF GRADUATE STUDIES\nMAHIDOL UNIVERSITY\nCOPYRIGHT OF MAHIDOL UNIVERSITY")

    def test_a_normal_book_has_its_cover_on_the_first_sheet(self):
        for cover in (self.TH_COVER, self.EN_COVER):
            self.assertEqual(checker_module.find_cover_page([cover, "ANY", "MORE"]), 0)

    def test_sheets_inserted_before_the_cover_are_found(self):
        for cover in (self.TH_COVER, self.EN_COVER):
            pages = ["ใบรับรอง", "", cover, "CHAPTER I"]
            self.assertEqual(checker_module.find_cover_page(pages), 2)

    def test_a_book_with_no_copyright_line_is_not_guessed(self):
        """ถ้าไม่มีบรรทัดลิขสิทธิ์เลย ห้ามเดา — คืนแผ่นแรกเท่าเดิม

        การเดาผิดอันตรายกว่า และเล่มที่ไม่มีบรรทัดลิขสิทธิ์ถูกฟ้องด้วยกฎ
        ข้อความบังคับบนหน้าปกอยู่แล้ว
        """
        self.assertEqual(checker_module.find_cover_page(["A", "B", "C"]), 0)
        self.assertEqual(checker_module.find_cover_page([]), 0)

    def test_the_signature_page_is_not_mistaken_for_the_cover(self):
        """หน้าลงนามมีชื่อบัณฑิตวิทยาลัยและชื่อมหาวิทยาลัยเหมือนกัน แต่ไม่มีบรรทัดลิขสิทธิ์"""
        sig = "FACULTY OF GRADUATE STUDIES\nMAHIDOL UNIVERSITY\nTHESIS ENTITLED"
        self.assertEqual(checker_module.find_cover_page([sig, self.EN_COVER]), 1)

    def test_the_search_does_not_run_to_the_end_of_the_book(self):
        """หน้าปกอยู่ต้นเล่มเสมอ ไม่ต้องไล่ทั้งเล่มให้เสี่ยงไปเจอข้อความที่อ้างถึงลิขสิทธิ์"""
        pages = ["x"] * 30 + [self.EN_COVER]
        self.assertEqual(checker_module.find_cover_page(pages), 0)

    def test_the_cover_checks_read_the_sheet_that_was_found(self):
        """ถ้ายังอ่าน pages[0] อยู่ ข้อฟ้องบนหน้าปกจะกลับมาผิดทั้งชุดเหมือนเดิม"""
        src = inspect.getsource(checker_module.run_check)
        for line in src.splitlines():
            if "หน้าปก" in line and ("spots" in line or "_check_cover_year" in line
                                      or "cover_digits" in line):
                self.assertNotIn("pages[0]", line, line.strip())

    def test_the_rule_has_a_reference(self):
        ref = checker_module.rule_reference("FRONT.COVER_FIRST")
        self.assertNotEqual(ref, checker_module.rule_reference("FORM.REQUIRED"),
                            "รหัสกฎพิมพ์ผิดจะเงียบ แล้วไปคืนที่มาของกฎอื่นแทน")

    def test_the_message_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        for th in ("หน้าปกอยู่แผ่นที่ 2 ของไฟล์ ไม่ใช่แผ่นแรก",
                   "หน้าปกต้องเป็นแผ่นแรกของไฟล์",
                   "ลบหน้าที่อยู่ก่อนหน้าปกออก หรือย้ายหน้าปกขึ้นเป็นแผ่นแรก"):
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}: {en}")


class EndMatterMustBeInOrder(unittest.TestCase):
    """ลำดับส่วนท้ายเล่ม: รายการอ้างอิง แล้วภาคผนวก (ถ้ามี) แล้วประวัติผู้วิจัย

    เจ้าหน้าที่ระบุลำดับทั้งไฟล์ไว้ (ส.ค. 2569)
        หน้าปก / หน้าลงนาม 1 / หน้าลงนาม 2 / กิตติกรรมประกาศ / บทคัดย่อ / สารบัญ /
        เนื้อหารายบท / รายการอ้างอิง / ภาคผนวก (ถ้ามี) / ประวัติผู้วิจัย

    เดิมส่วนท้ายตรวจแค่ "ต้องไม่มีอะไรต่อจากประวัติผู้วิจัย" เล่มที่วางภาคผนวกไว้
    ก่อนรายการอ้างอิงจึงหลุดไป ทั้งที่ผิดลำดับเหมือนกัน
    """

    def _order_issue(self, ref, app, bio):
        """สร้างข้อฟ้องลำดับจากเลขหน้าที่ให้มา (None = ไม่มีส่วนนั้น)"""
        want = []
        if ref is not None:
            want.append(("รายการอ้างอิง/บรรณานุกรม", ref))
        if app is not None:
            want.append(("ภาคผนวก", app))
        want.append(("ประวัติผู้วิจัย", bio))
        got = sorted(want, key=lambda item: item[1])
        return [n for n, _i in got] != [n for n, _i in want], want, got

    def test_the_correct_order_is_not_reported(self):
        wrong, _w, _g = self._order_issue(ref=120, app=125, bio=130)
        self.assertFalse(wrong)

    def test_an_appendix_before_the_references_is_caught(self):
        """เคสที่กฎเดิมมองไม่เห็น — ประวัติผู้วิจัยยังอยู่ท้ายสุด แต่ลำดับผิด"""
        wrong, _w, got = self._order_issue(ref=120, app=90, bio=130)
        self.assertTrue(wrong)
        self.assertEqual([n for n, _i in got],
                         ["ภาคผนวก", "รายการอ้างอิง/บรรณานุกรม", "ประวัติผู้วิจัย"])

    def test_a_biography_that_is_not_last_is_still_caught(self):
        """กฎเดิมจับเคสนี้ได้ กฎใหม่ต้องไม่ทำให้หลุด"""
        wrong, _w, _g = self._order_issue(ref=120, app=135, bio=130)
        self.assertTrue(wrong)

    def test_a_book_without_an_appendix_still_works(self):
        self.assertFalse(self._order_issue(ref=120, app=None, bio=130)[0])
        self.assertTrue(self._order_issue(ref=130, app=None, bio=120)[0])


class OrderMessagesPointAtTheSheetNotThePrintedNumber(unittest.TestCase):
    """ข้อความลำดับต้องบอก "แผ่นที่ N ของไฟล์" ไม่ใช่เลขหน้าที่พิมพ์ในเล่ม

    เล่มที่ผิดลำดับส่วนใหญ่เกิดจากรวมไฟล์สลับกันโดยไม่ได้ใส่เลขหน้าใหม่ เลขที่พิมพ์
    จึงสลับตามไปด้วย พอรายงานด้วยเลขที่พิมพ์จะได้ข้อความที่ดูขัดกับตัวเอง

        ลำดับที่พบ: ภาคผนวก (หน้า 60) แล้ว รายการอ้างอิง (หน้า 52)

    อ่านแล้วเหมือนระบบเรียงผิดเอง ทั้งที่เรียงตามไฟล์ถูกแล้ว (เจอตอนทดลองสลับ
    ส่วนท้ายของเล่มจริงทั้งเล่มไทยและเล่มอังกฤษ) แผ่นที่ของไฟล์มีเสมอ ไม่ซ้ำ และ
    เป็นตัวที่เจ้าหน้าที่ใช้เปิดไปดูใน PDF จริง
    """

    def test_the_order_rules_do_not_use_page_ref(self):
        src = inspect.getsource(checker_module.run_check)
        for line in src.splitlines():
            if "actual_end" in line or "actual_front_sections" in line:
                self.assertNotIn("page_ref(", line,
                                 "ข้อความลำดับต้องใช้ order_ref ไม่ใช่ page_ref: "
                                 + line.strip())

    def test_the_sheet_number_starts_at_one(self):
        """ดัชนีในโค้ดเริ่มที่ 0 แต่เจ้าหน้าที่นับแผ่นแรกของไฟล์เป็นแผ่นที่ 1"""
        src = inspect.getsource(checker_module.run_check)
        self.assertRegex(
            src, r'(?s)def order_ref\(page_index\):.*?แผ่นที่ \{page_index \+ 1\} ของไฟล์',
            "order_ref ต้องคืนแผ่นที่โดยบวกหนึ่งจากดัชนี")

    def test_the_sheet_phrase_translates(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        en = i18n.tr_en("ภาคผนวก (แผ่นที่ 71 ของไฟล์)", pairs)
        self.assertNotIn("แผ่น", en)
        self.assertIn("71", en)


class OrderMessagesUseWordsNotArrows(unittest.TestCase):
    """ข้อความลำดับต้องไม่มีสัญลักษณ์

    เจ้าหน้าที่คัดลอกข้อความสรุปไปวางในอีเมล/Word ซึ่งฟอนต์ปลายทางแสดงสัญลักษณ์
    เพี้ยน (เคยเจอ "·" กลายเป็นรูปโทรศัพท์) กฎลำดับส่วนนำเคยใช้ลูกศร "→"
    ซึ่งหลุดเข้าสรุปมาตลอด เพราะเล่มทดสอบไม่เคยผลิตข้อความนี้ ด่าน --corpus จึงไม่เห็น
    """

    BANNED = "·—–→↔•≤✆"

    def test_the_joiner_is_a_word(self):
        self.assertEqual(checker_module._ORDER_JOIN.strip(), "แล้ว")
        for ch in self.BANNED:
            self.assertNotIn(ch, checker_module._ORDER_JOIN)

    def test_no_banned_symbol_reaches_the_summary(self):
        join = checker_module._ORDER_JOIN
        rep = Report()
        rep.add("RED", "front_matter", "ส่วนนำ",
                "ลำดับที่พบ: " + join.join(["หน้าลงนาม (แผ่นที่ 3 ของไฟล์)",
                                            "สารบัญ (แผ่นที่ 5 ของไฟล์)",
                                            "กิตติกรรมประกาศ (แผ่นที่ 6 ของไฟล์)"]),
                "ลำดับที่ต้องเป็น: " + join.join(["หน้าลงนาม", "กิตติกรรมประกาศ",
                                                  "สารบัญ"]),
                "ย้ายแต่ละส่วนของส่วนนำให้เรียงตามลำดับที่กำหนด", "FRONT.ORDER")
        rep.add("RED", "end_matter", "ส่วนท้ายเล่ม",
                "ลำดับที่พบ: " + join.join(["ภาคผนวก (แผ่นที่ 71 ของไฟล์)",
                                            "รายการอ้างอิง/บรรณานุกรม (แผ่นที่ 108 ของไฟล์)",
                                            "ประวัติผู้วิจัย (แผ่นที่ 118 ของไฟล์)"]),
                "ลำดับที่ต้องเป็น: " + join.join(["รายการอ้างอิง/บรรณานุกรม", "ภาคผนวก",
                                                  "ประวัติผู้วิจัย"]),
                "ย้ายแต่ละส่วนของส่วนท้ายเล่มให้เรียงตามลำดับที่กำหนด", "END.STRUCTURE")
        for it in rep.zones["RED"]:
            it["category"] = checker_module.classify(it)
            it["section"] = summary_section(it)
        text = plain_summary({"verdict": "ไม่ผ่าน", "issues_by_zone": rep.zones})
        self.assertEqual([c for c in self.BANNED if c in text], [])

    def test_the_order_messages_translate(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        join = checker_module._ORDER_JOIN
        for th in ("ลำดับที่พบ: " + join.join(["ภาคผนวก (แผ่นที่ 71 ของไฟล์)",
                                               "รายการอ้างอิง/บรรณานุกรม (แผ่นที่ 108 ของไฟล์)",
                                               "ประวัติผู้วิจัย (แผ่นที่ 118 ของไฟล์)"]),
                   "ลำดับที่ต้องเป็น: " + join.join(["รายการอ้างอิง/บรรณานุกรม", "ภาคผนวก",
                                                     "ประวัติผู้วิจัย"]),
                   "ย้ายแต่ละส่วนของส่วนท้ายเล่มให้เรียงตามลำดับที่กำหนด",
                   "ส่วนท้ายเล่ม"):
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}\n  TH: {th}\n  EN: {en}")


class SignaturePagesAreNamedTheSameWayEverywhere(unittest.TestCase):
    """ทุกกฎบนหน้าลงนามต้องเรียกตำแหน่งแบบเดียวกัน

    เจ้าหน้าที่กำหนด ส.ค. 2569: หัวกลุ่มเป็น "หน้าลงนาม" ตำแหน่งเป็น
    "หน้าลงนาม 1" / "หน้าลงนาม 2"

    เดิมกฎแต่ละตัวเรียกหน้าเดียวกันคนละแบบ ("หน้าอาจารย์ที่ปรึกษา",
    "หน้าลงนามหน้า 1", "หน้าลงนาม 1") เจ้าหน้าที่อ่านแล้วนึกว่าเป็นคนละหน้า
    """

    def test_the_position_comes_from_the_order_in_the_file(self):
        pos = checker_module.signature_page_position
        self.assertEqual(pos([2, 3], 2), "หน้าลงนาม 1")
        self.assertEqual(pos([2, 3], 3), "หน้าลงนาม 2")
        # ลำดับในไฟล์ ไม่ใช่เลขหน้า — หน้าลงนามอยู่แผ่นไหนก็ได้
        self.assertEqual(pos([7, 9], 7), "หน้าลงนาม 1")
        self.assertEqual(pos([7, 9], 9), "หน้าลงนาม 2")

    def test_a_page_outside_the_list_does_not_crash(self):
        self.assertEqual(checker_module.signature_page_position([2, 3], 9), "หน้าลงนาม")
        self.assertEqual(checker_module.signature_page_position([], 0), "หน้าลงนาม")

    def test_the_page_number_rule_uses_the_same_name(self):
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [2, 3], {2: "Thesis entitled X\nz", 3: "Thesis entitled X\nz"},
            lambda i: "หน้า z")
        self.assertEqual([it["location"] for it in rep.zones["ORANGE"]],
                         ["หน้าลงนาม 1 (หน้า z)", "หน้าลงนาม 2 (หน้า z)"])

    def test_every_signature_location_still_groups_under_one_heading(self):
        """ตำแหน่งเปลี่ยนแล้ว หัวกลุ่มในสรุปต้องยังเป็น "หน้าลงนาม" กลุ่มเดียว"""
        for loc in ("หน้าลงนาม 1 (หน้า i)", "หน้าลงนาม 2 (หน้า ii)",
                    "หน้าลงนาม 1 ช่องคณบดีคณะ (มุมล่างขวา) (หน้า i)"):
            issue = {"location": loc, "found": "x", "expected": "y",
                     "part": "front_matter"}
            self.assertEqual(summary_section(issue), "หน้าลงนาม", loc)


class SignatureTemplateIsSeparateFromTheDegree(unittest.TestCase):
    """ข้อความ template หน้าลงนาม ต้องแยกจากชื่อปริญญา

    เจ้าหน้าที่สั่ง ส.ค. 2569: "แยกประเด็นข้อความ template นำหน้าชื่อปริญญาไม่ครบ
    ออกจากชื่อปริญญา ... อ่าน template สิ และแยกชื่อปริญญาออกมา นอกนั้นก็เป็น
    ข้อความ template"

    template ทางการวางไว้ว่า "...for the degree of <Degree (Field of Study)>"
    ชื่อปริญญาจึงต่อท้ายประโยค template พอดี ถ้าไม่ตัดออกก่อน ตัวหาช่วงที่ใกล้เคียง
    จะคร่อมชื่อปริญญาเข้ามาแล้วรายงานยกชื่อปริญญามาอ้างว่าเป็นข้อความ template
    """

    DEGREE_EN = "Doctor of Philosophy (Tropical Medicine)"
    DEGREE_TH = "ปรัชญาดุษฎีบัณฑิต (สาขาวิชาอายุรศาสตร์เขตร้อน)"

    PAGE_NO_TEMPLATE = ("Thesis entitled\nX\nWISIT K\n"
                        "Doctor of Philosophy (Tropical Medicine)\n"
                        "Faculty of Graduate Studies, Mahidol University\non 1 June 2026")

    def test_the_degree_is_cut_out_of_the_template_zone(self):
        zone = checker_module.signature_template_zone(
            "was submitted to the Faculty of Graduate Studies, Mahidol University "
            "for the degree of\n" + self.DEGREE_EN + "\non 1 June 2026",
            self.DEGREE_EN)
        self.assertIn("for the degree of", zone)
        self.assertNotIn("Tropical Medicine", zone)

    def test_a_missing_template_sentence_never_quotes_the_degree(self):
        """เคสที่เคยพัง: ไม่มีประโยค template เลย ระบบไปคว้าชื่อปริญญามาแทน"""
        zone = checker_module.signature_template_zone(
            self.PAGE_NO_TEMPLATE, self.DEGREE_EN)
        self.assertNotIn("Tropical Medicine", zone)
        near = checker_module._closest_run(
            zone, SIGNATURE_TEMPLATE_EN,
            min_ratio=checker_module.SIGNATURE_TEMPLATE_MIN_RATIO)
        self.assertEqual(near, "")      # ไม่ยกอะไรมาอ้าง จะฟ้องว่า "ไม่พบข้อความ template"

    def test_a_real_typo_in_the_sentence_is_still_quoted(self):
        """ลดการปนแล้วต้องไม่กลืนเคสที่ควรฟ้อง — เล่มพิมพ์ประโยคมาแต่ผิดคำ"""
        page = ("WISIT K\nwas submitted to the Faculty of Graduate Study, "
                "Mahidol University for the degree of\n" + self.DEGREE_EN)
        zone = checker_module.signature_template_zone(page, self.DEGREE_EN)
        near = checker_module._closest_run(
            zone, SIGNATURE_TEMPLATE_EN,
            min_ratio=checker_module.SIGNATURE_TEMPLATE_MIN_RATIO)
        self.assertTrue(near)
        self.assertIn("Study,", near)
        self.assertNotIn("Tropical", near)
        self.assertEqual(describe_diff(near, SIGNATURE_TEMPLATE_EN),
                         'ต่างที่ "Study," ต้องเป็น "Studies,"')

    def test_the_thai_page_works_the_same_way(self):
        page = ("วิทยานิพนธ์ เรื่อง\nX\n"
                "ได้รับการพิจารณาให้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตรปริญญา\n"
                + self.DEGREE_TH + "\nวันที่ 11 พฤษภาคม 2569")
        zone = checker_module.signature_template_zone(page, self.DEGREE_TH)
        self.assertNotIn("อายุรศาสตร์เขตร้อน", zone)
        near = checker_module._closest_run(
            zone, SIGNATURE_TEMPLATE_TH,
            min_ratio=checker_module.SIGNATURE_TEMPLATE_MIN_RATIO)
        self.assertEqual(describe_diff(near, SIGNATURE_TEMPLATE_TH), 'ขาด "นับ"')

    def test_it_survives_a_missing_degree_field(self):
        """ฟอร์มไม่ได้กรอกชื่อปริญญามา ต้องไม่พังและไม่ตัดอะไรทิ้ง"""
        for degree in ("", None):
            zone = checker_module.signature_template_zone(self.PAGE_NO_TEMPLATE, degree)
            self.assertIn("Mahidol University", zone)

    def test_the_threshold_sits_between_the_measured_cases(self):
        """เกณฑ์ 0.8 มาจากการวัด ไม่ใช่เลขสุ่ม

        ประโยคที่มีอยู่แต่ผิด ได้ 0.87-0.99 · ช่วงที่คร่อมชื่อปริญญา ได้ 0.68
        """
        self.assertGreater(checker_module.SIGNATURE_TEMPLATE_MIN_RATIO, 0.70)
        self.assertLess(checker_module.SIGNATURE_TEMPLATE_MIN_RATIO, 0.86)


class SourceFormFollowsThePagePosition(unittest.TestCase):
    """ฟอร์มต้นทางเลือกจากลำดับหน้า ไม่ใช่หัวข้อบนหน้า

    เจ้าหน้าที่กำหนด ส.ค. 2569: "บฑ.1 หน้าลงนาม 1 และบทคัดย่อ · บฑ.2 หน้าลงนาม 2"
    """

    def test_each_position_maps_to_its_own_form(self):
        pick = checker_module.signature_page_committee
        form = checker_module.committee_source_form
        self.assertEqual(pick([2, 3], 2), "advisory")
        self.assertEqual(form(pick([2, 3], 2)), "บฑ.1")
        self.assertEqual(pick([2, 3], 3), "exam")
        self.assertEqual(form(pick([2, 3], 3)), "บฑ.2")

    def test_the_abstract_page_always_uses_the_advisory_form(self):
        self.assertEqual(checker_module.committee_source_form("advisory"), "บฑ.1")

    def test_a_third_signature_page_is_not_compared(self):
        """template มีหน้าลงนามสองหน้า หน้าที่เกินมาไม่มีฟอร์มให้เทียบ"""
        self.assertEqual(checker_module.signature_page_committee([2, 3], 9), "")
        self.assertEqual(checker_module.signature_page_committee([], 0), "")

    def test_the_heading_does_not_override_the_position(self):
        """เล่มที่สลับสองหน้ากันคือเล่มที่ผิด ถ้ายึดหัวข้อจะตามน้ำไปเทียบให้ถูกชุด
        แล้วความผิดนั้นหายไปจากรายงาน จึงต้องยึดลำดับหน้าเสมอ"""
        pick = checker_module.signature_page_committee
        # ลำดับหน้าเป็นตัวตัดสินอย่างเดียว ฟังก์ชันไม่รับข้อความบนหน้าเลย
        self.assertEqual(pick([5, 6], 5), "advisory")
        self.assertEqual(pick([5, 6], 6), "exam")


class SwappedSignaturePagesAreReported(unittest.TestCase):
    """หัวข้อบนหน้าขัดกับลำดับหน้า = อาจสลับสองหน้ากัน ต้องบอก ไม่ใช่เงียบ

    ระบบเทียบตามลำดับหน้าเสมอ (บฑ.1 กับหน้า 1, บฑ.2 กับหน้า 2) เล่มที่สลับหน้ากัน
    จึงได้ข้อฟ้อง "รายชื่อไม่ครบ" ตามมา ถ้าไม่บอกว่าอาจสลับหน้า เจ้าหน้าที่จะนึกว่า
    เล่มขาดคน ทั้งที่ของจริงคือสลับหน้า ซึ่งแก้คนละอย่างกัน
    """

    SWAPPED_WHY = ("หัวข้อบนหน้านี้ไม่ตรงกับลำดับหน้า อาจสลับหน้ากัน "
                   "ระบบเทียบตามลำดับหน้าไว้ก่อน โปรดตรวจว่าหน้าลงนาม 1 "
                   "เป็นคณะกรรมการที่ปรึกษา และหน้าลงนาม 2 เป็นคณะกรรมการสอบ")

    def test_the_page_kind_survives_a_wrong_or_missing_page_label(self):
        """เลขหน้าผิดหรือหายไม่ทำให้แยกหน้าไม่ออก เพราะยังถอยไปดูหัวข้อได้"""
        kind = checker_module.signature_page_kind
        self.assertEqual(kind("ค", "คณะกรรมการที่ปรึกษาวิทยานิพนธ์"), "advisory")
        self.assertEqual(kind("ค", "คณะกรรมการสอบวิทยานิพนธ์"), "exam")
        self.assertEqual(kind("", "THESIS ADVISORY COMMITTEE"), "advisory")
        self.assertEqual(kind("", "THESIS EXAMINATION COMMITTEE"), "exam")

    def test_it_gives_up_only_when_both_signals_are_gone(self):
        kind = checker_module.signature_page_kind
        self.assertFalse(kind("ค", "xxxxx"))
        self.assertFalse(kind("", ""))

    def test_it_goes_to_the_purple_list_not_the_student_fix_list(self):
        """เจ้าหน้าที่สั่ง ส.ค. 2569: "เอาเป็นสีม่วงไหม เพราะแบบนั้นต้องตรวจตาอยู่แล้ว"

        และเหตุผลที่หนักกว่านั้น: สีส้มเข้าใบสั่งแก้ที่ส่งให้นักศึกษาโดยปริยาย
        นักศึกษาแก้เล่มยังไงข้อนี้ก็ไม่หาย เพราะเป็นข้อจำกัดของการอ่านไฟล์
        ไม่ใช่จุดผิดของเล่ม (กติกาเดียวกับ system_note)
        """
        # _check_committees ต้องเปิด PDF จริงจึงทดสอบตรง ๆ ในชุดนี้ไม่ได้
        # (ด่าน --detail ของ regress_books ยืนยันกับเล่มจริงแล้ว) ตรงนี้ล็อกกติกา
        # ปลายทางแทน: ข้อแบบนี้ต้องอยู่รายการสีม่วง ไม่กระทบคำตัดสิน ไม่เข้าใบสั่งแก้
        rep = Report()
        rep.add_human("หน้าลงนาม 1 (หน้า ค)", self.SWAPPED_WHY, "UNCERTAIN.REVIEW")
        self.assertEqual(rep.verdict(), "ผ่าน")          # ไม่กระทบคำตัดสิน
        self.assertEqual(checker_module.issues_to_fix({"issues_by_zone": rep.zones}), [])
        self.assertIn("อาจสลับหน้ากัน", rep.human_checklist[0]["why"])

    def test_the_purple_message_has_an_english_translation(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        en = i18n.tr_en(self.SWAPPED_WHY, pairs)
        left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
        self.assertEqual(left, [], f"ยังไม่แปล {left}\n  EN: {en}")

    def test_page_one_and_two_do_not_depend_on_the_page_label_at_all(self):
        """ป้าย "หน้าลงนามหน้า 1/2" มาจากลำดับในไฟล์ ไม่ใช่เลขหน้า"""
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [7, 9], {7: "Thesis entitled X", 9: "Thesis entitled X"},
            lambda i: f"แผ่นที่ {i + 1}")
        self.assertEqual([it["location"] for it in rep.zones["ORANGE"]],
                         ["หน้าลงนาม 1 (แผ่นที่ 8)", "หน้าลงนาม 2 (แผ่นที่ 10)"])


class ContinuedHeadingIsTheSameChapter(unittest.TestCase):
    """บรรทัด "(ต่อ)" ในสารบัญคือบทเดิม ไม่ใช่บทใหม่

    เจ้าหน้าที่กำหนด ส.ค. 2569: บางเล่มเขียนสารบัญเป็น

        บทที่ 3 วิธีดำเนินการวิจัย        21
        บทที่ 3 วิธีดำเนินการวิจัย (ต่อ)   25

    สองบรรทัดนี้คือบทที่ 3 บทเดียว ไม่ผิด ใช้กับทุกบทและภาษาอังกฤษด้วย
    """

    CONTINUED = (
        "บทที่ 3 วิธีดำเนินการวิจัย (ต่อ)",
        "บทที่ 3 วิธีดำเนินการวิจัย (ต่อ) 25",
        "บทที่ 3 วิธีดำเนินการวิจัย .......... (ต่อ) 25",
        "CHAPTER 3 RESEARCH METHODOLOGY (cont.)",
        "CHAPTER 3 RESEARCH METHODOLOGY (Cont.) 25",
        "CHAPTER 3 RESEARCH METHODOLOGY (continued) 25",
        # PDF ทำวรรณยุกต์หลุดเป็นประจำ ต้องยังจับได้
        "บทท 3 วธดาเนนการวจย (ตอ) 25",
    )

    NOT_CONTINUED = (
        "บทที่ 3 วิธีดำเนินการวิจัย 21",
        "CHAPTER 3 RESEARCH METHODOLOGY 21",
        # วงเล็บท้ายบรรทัดที่ไม่ใช่คำว่าต่อ ต้องไม่ถูกกลืน
        "บทที่ 2 วรรณกรรมและงานวิจัยที่เกี่ยวข้อง (ฉบับปรับปรุง) 9",
        "CHAPTER 5 DISCUSSION (WITH APPENDIX) 76",
    )

    def test_continuation_lines_are_recognised(self):
        for line in self.CONTINUED:
            self.assertTrue(checker_module.is_continuation_heading(line), line)

    def test_ordinary_headings_are_not_swallowed(self):
        for line in self.NOT_CONTINUED:
            self.assertFalse(checker_module.is_continuation_heading(line), line)

    def test_it_applies_to_every_chapter_number(self):
        for n in range(1, 10):
            self.assertTrue(checker_module.is_continuation_heading(
                f"บทที่ {n} ชื่อบท (ต่อ) {n * 10}"), n)
            self.assertTrue(checker_module.is_continuation_heading(
                f"CHAPTER {n} SOME TITLE (cont.) {n * 10}"), n)

    def test_an_empty_parenthesis_is_not_a_continuation(self):
        self.assertFalse(checker_module.is_continuation_heading("บทที่ 3 ชื่อบท () 25"))


class SignaturePageNumbersMustBeFirstAndSecond(unittest.TestCase):
    """หน้าลงนามหน้าแรกต้องเป็นหน้า i (ไทย: ก) หน้าที่สองต้องเป็น ii (ไทย: ข)

    เจ้าหน้าที่ย้ำว่า "เลขหน้า ก ข i ii ยังต้องตรวจนะ" — สองเลขนี้ไม่ใช่แค่การเรียงเลข
    แต่เป็นตัวบอกว่าหน้าไหนเป็นของคณะกรรมการที่ปรึกษา หน้าไหนเป็นของคณะกรรมการสอบ
    ซึ่งกำหนดว่าหน้านั้นจะถูกเทียบกับ บฑ.1 หรือ บฑ.2
    """

    def _run(self, first, second):
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [0, 1], [first, second], lambda i: f"แผ่นที่ {i + 1}")
        return [(it["location"], it["found"]) for it in rep.zones["ORANGE"]]

    def test_correct_labels_pass(self):
        self.assertEqual(self._run("Thesis entitled X\ni", "Thesis entitled X\nii"), [])
        self.assertEqual(self._run("วิทยานิพนธ์ เรื่อง X\nก", "วิทยานิพนธ์ เรื่อง X\nข"), [])

    def test_the_same_number_on_both_pages_is_caught(self):
        """เล่มจริง (เล่มทดสอบ 3) พิมพ์ "ค" ทั้งสองหน้า ต้องฟ้องทั้งคู่"""
        bad = self._run("วิทยานิพนธ์ เรื่อง X\nค", "วิทยานิพนธ์ เรื่อง X\nค")
        self.assertEqual(len(bad), 2)
        self.assertTrue(all(f == "เลขหน้าของหน้านี้ไม่ถูกต้อง" for _loc, f in bad), bad)

    def test_the_finding_does_not_repeat_the_page_label(self):
        """ตำแหน่งบอกเลขที่พิมพ์แล้ว บรรทัด "สิ่งที่พบ" ต้องไม่พูดซ้ำ

        เจ้าหน้าที่สั่ง ส.ค. 2569: กฎนี้พิเศษกว่ากฎอื่นตรงที่ "เลขหน้าที่ใช้ระบุตำแหน่ง"
        กับ "สิ่งที่ผิด" เป็นค่าเดียวกัน เดิมจึงได้ ค สองรอบในข้อเดียว
        """
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [0, 1], ["วิทยานิพนธ์ เรื่อง X\nค", "วิทยานิพนธ์ เรื่อง X\nค"],
            lambda i: "หน้า ค")
        issue = rep.zones["ORANGE"][0]
        self.assertIn("ค", issue["location"])          # ตำแหน่งยังบอกเลขที่พิมพ์
        self.assertNotIn("ค\"", issue["found"])        # แต่บรรทัดสองไม่พูดซ้ำ
        self.assertNotIn("พิมพ์เลขหน้าว่า", issue["found"])

    def test_a_page_with_no_label_keeps_its_only_locator(self):
        """หน้าที่ไม่มีเลขหน้า ตำแหน่งบอกแผ่นที่ของไฟล์ ซึ่งไม่ซ้ำกับสิ่งที่พบอยู่แล้ว

        จึงต้องไม่ถูกตัดทิ้งไปด้วย ไม่งั้นจะไม่เหลืออะไรชี้ว่าอยู่แผ่นไหน
        """
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [0, 1], ["Thesis entitled X", "Thesis entitled X"],
            lambda i: f"หน้าไม่ระบุเลข (แผ่นที่ {i + 3} ของไฟล์)")
        issue = rep.zones["ORANGE"][0]
        self.assertIn("แผ่นที่ 3 ของไฟล์", issue["location"])
        self.assertEqual(issue["found"], "ไม่พบเลขหน้าบนหน้า")

    def test_swapped_pages_are_caught(self):
        """สลับหน้ากันแปลว่าหน้าที่ปรึกษากับหน้ากรรมการสอบสลับที่ ต้องไม่ปล่อยผ่าน"""
        bad = self._run("Thesis entitled X\nii", "Thesis entitled X\ni")
        self.assertEqual(len(bad), 2)

    def test_a_page_with_no_number_says_so(self):
        """ไม่มีเลขหน้ากับพิมพ์เลขผิด เป็นคนละเรื่อง วิธีแก้ต่างกัน"""
        bad = self._run("Thesis entitled X", "Thesis entitled X")
        self.assertEqual([f for _loc, f in bad],
                         ["ไม่พบเลขหน้าบนหน้า", "ไม่พบเลขหน้าบนหน้า"])

    def test_the_number_does_not_have_to_be_on_the_first_line(self):
        """บางเล่มมีหัวเรื่องนำหน้า เลขหน้าจึงไม่ได้อยู่บรรทัดแรก"""
        page = "วิทยานิพนธ์\nก\nเรื่อง ...\nได้รับการพิจารณา..."
        self.assertEqual(self._run(page, "วิทยานิพนธ์ เรื่อง X\nข"), [])

    def test_the_expected_label_is_stated_in_both_scripts(self):
        """เจ้าหน้าที่ต้องรู้ทันทีว่าหน้านั้นต้องเป็นเลขอะไร ไม่ต้องเปิดคู่มือ"""
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [0, 1], ["Thesis entitled X\nz", "Thesis entitled X\nz"],
            lambda i: f"แผ่นที่ {i + 1}")
        self.assertIn('"ก" (ไทย) หรือ "i" (อังกฤษ)', rep.zones["ORANGE"][0]["expected"])
        self.assertIn('"ข" (ไทย) หรือ "ii" (อังกฤษ)', rep.zones["ORANGE"][1]["expected"])

    def test_a_wrong_label_is_orange_not_red(self):
        """เจ้าหน้าที่สั่ง ส.ค. 2569: เลขหน้าลงนามผิดให้แจ้งเป็นสีส้ม

        เพราะมันพ่วงไปทำให้เลขหน้าส่วนนำหน้าอื่นผิดตาม กฎ "เลขหน้าไม่ต่อเนื่อง"
        จึงฟ้องซ้ำอีกข้อจากต้นเหตุเดียวกัน ถ้าแดงทั้งคู่จะได้สองข้อจากความผิดเดียว
        """
        self.assertEqual(rule_zone("PAGE.SIGNATURE_LABEL"), "ORANGE")
        self.assertEqual(checker_module.SIG_LABEL_ZONE, "ORANGE")
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [0, 1], ["วิทยานิพนธ์ เรื่อง X\nค", "วิทยานิพนธ์ เรื่อง X\nค"],
            lambda i: "หน้า ค")
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(len(rep.zones["ORANGE"]), 2)
        self.assertEqual([it["rule_id"] for it in rep.zones["ORANGE"]],
                         ["PAGE.SIGNATURE_LABEL"] * 2)

    def test_the_fix_line_warns_that_later_pages_shift_too(self):
        """เหตุผลที่เป็นส้มคือมันพ่วง — ข้อความต้องบอกด้วย ไม่งั้นดูเหมือนเรื่องเล็ก"""
        rep = Report()
        checker_module._report_signature_page_labels(
            rep, [0, 1], ["วิทยานิพนธ์ เรื่อง X\nค", "วิทยานิพนธ์ เรื่อง X\nค"],
            lambda i: "หน้า ค")
        self.assertIn("ไล่เลขหน้าส่วนนำที่เหลือใหม่", rep.zones["ORANGE"][0]["fix"])

    def test_the_page_number_decides_which_form_the_page_is_checked_against(self):
        """เหตุผลที่กฎนี้ยังต้องอยู่ — เลขหน้าคือตัวเลือกฟอร์ม"""
        for label, form in (("i", "บฑ.1"), ("ii", "บฑ.2"),
                            ("ก", "บฑ.1"), ("ข", "บฑ.2")):
            kind = checker_module.signature_page_kind(label, "")
            self.assertEqual(checker_module.committee_source_form(kind), form, label)


class CommitteeCountIsCheckedAgainstTheSourceForm(unittest.TestCase):
    """หน้าลงนาม/หน้าบทคัดย่อ: ตรวจ "จำนวน" รายชื่อเทียบฟอร์มต้นทาง ไม่เทียบชื่อ

    เจ้าหน้าที่สั่ง (ส.ค. 2569): *"ทั้งไทยและอังกฤษ ไม่ต้องเทียบชื่อ สมมุติว่าใน บฑ.1
    หรือ บฑ.2 เป็น 3 ชื่อ ในเล่มมี 2 หรือ 4 ชื่อ ... แจ้งว่ารายชื่อไม่ครบตามที่ได้รับ
    อนุมัติใน บฑ.1 หรือ 2 แล้วแต่หน้า และก็บอกว่าควรมีชื่ออะไรบ้าง"*
    """

    FORM3 = [{"name": "คนางค์ คันธมธุรพจน์"},
             {"name": "ธเนศ เกษศิลป์"},
             {"name": "สุภาภรณ์ สงค์ประชา"}]

    def _count(self, read, form="บฑ.2"):
        rep = Report()
        checker_module._report_committee_count(
            rep, self.FORM3, read, "หน้ากรรมการสอบ (หน้า ข)", form)
        return rep

    def test_each_page_names_its_own_source_form(self):
        self.assertEqual(checker_module.committee_source_form("advisory"), "บฑ.1")
        self.assertEqual(checker_module.committee_source_form("exam"), "บฑ.2")
        # หน้าที่อ่านชนิดไม่ออก ยังต้องบอกฟอร์มได้ ไม่ใช่คืนค่าว่าง
        self.assertEqual(checker_module.committee_source_form(None), "บฑ.1")

    def test_the_form_follows_the_page_all_the_way_from_its_page_number(self):
        """เลขหน้า -> ชนิดหน้า -> ชื่อฟอร์ม ต้องต่อกันถูกทั้งเล่มไทยและเล่มอังกฤษ

        เจ้าหน้าที่สั่งว่า "ต้องเปลี่ยนเงื่อนไข บฑ. ไปแต่ละหน้า" — หน้าลงนาม 1 กับ
        หน้าบทคัดย่อใช้ บฑ.1 ส่วนหน้าลงนาม 2 ใช้ บฑ.2
        """
        for label, form in (("i", "บฑ.1"), ("ii", "บฑ.2"),
                            ("ก", "บฑ.1"), ("ข", "บฑ.2")):
            kind = checker_module.signature_page_kind(label, "")
            self.assertEqual(checker_module.committee_source_form(kind), form, label)

    def test_the_abstract_page_always_cites_the_advisory_form(self):
        """หน้าบทคัดย่อพิมพ์คณะกรรมการที่ปรึกษา จึงอ้าง บฑ.1 เสมอ ไม่ว่าจะภาษาอะไร"""
        committees = {"advisory": [{"name": "คนางค์ ก", "role": ""},
                                    {"name": "ธเนศ ข", "role": ""}]}
        pages = {
            "ไทย": "คณะกรรมการที่ปรึกษาวิทยานิพนธ์: คนางค์ ก, ปร.ด.\nบทคัดย่อ",
            "อังกฤษ": "THESIS ADVISORY COMMITTEE: KANANG KOR, Ph.D.\nABSTRACT",
        }
        for lang, page in pages.items():
            rep = Report()
            en = [0] if lang == "อังกฤษ" else []
            th = [] if lang == "อังกฤษ" else [0]
            _check_abstract_committees(rep, committees, en, th, [page],
                                       lambda i: "หน้า iv")
            found = " ".join(i["found"] for i in
                             rep.zones["RED"] + rep.zones["ORANGE"])
            self.assertIn("บฑ.1", found, lang)
            self.assertNotIn("บฑ.2", found, lang)

    def test_names_are_never_compared(self):
        """คนละคนกันทั้งชุด แต่จำนวนครบ ต้องไม่ฟ้อง"""
        rep = self._count(["สมชาย ใจดี", "สมหญิง รักเรียน", "สมศักดิ์ ตั้งใจ"])
        self.assertEqual(rep.zones["ORANGE"], [])
        self.assertEqual(rep.zones["RED"], [])
        # ตัวเทียบชื่อต้องถูกถอดออกไปแล้วจริง ๆ ไม่ใช่แค่เลิกเรียก
        for gone in ("_report_committee_names", "committee_names_match",
                     "committee_name_script", "_committee_name_key"):
            self.assertFalse(hasattr(checker_module, gone), gone)

    def test_too_few_names_says_the_list_is_incomplete(self):
        issue = self._count(["ก ก", "ข ข"]).zones["RED"][0]
        self.assertIn("รายชื่อไม่ครบตามที่ได้รับอนุมัติใน บฑ.2", issue["found"])
        self.assertIn("หน้านี้มี 2 ชื่อ แต่อนุมัติไว้ 3 ชื่อ", issue["found"])

    def test_too_many_names_says_the_list_is_over(self):
        # ทั้งขาดและเกิน = สีแดง (เจ้าหน้าที่สั่ง ก.ย. 2569)
        issue = self._count(["ก ก", "ข ข", "ค ค", "ง ง"]).zones["RED"][0]
        self.assertIn("รายชื่อเกินจากที่ได้รับอนุมัติใน บฑ.2", issue["found"])
        self.assertIn("หน้านี้มี 4 ชื่อ แต่อนุมัติไว้ 3 ชื่อ", issue["found"])

    def test_the_finding_lists_who_should_be_there(self):
        """เจ้าหน้าที่สั่งให้บอกด้วยว่าควรมีชื่ออะไรบ้าง ดึงจากฟอร์มต้นทาง"""
        issue = self._count(["ก ก", "ข ข"]).zones["RED"][0]
        for name in ("คนางค์ คันธมธุรพจน์", "ธเนศ เกษศิลป์", "สุภาภรณ์ สงค์ประชา"):
            self.assertIn(name, issue["expected"], name)
        self.assertIn("ตาม บฑ.2", issue["expected"])

    def test_the_right_count_says_nothing(self):
        rep = self._count(["ก ก", "ข ข", "ค ค"])
        self.assertEqual(rep.zones["RED"] + rep.zones["ORANGE"], [])

    def test_only_cells_holding_a_name_are_counted(self):
        # ช่องคงที่ของ template ถูกคัดออกตั้งแต่ _sig_clean_name (คืน None)
        # เหลือกันเศษที่ไม่มีตัวอักษรเลย ไม่ให้ถูกนับเป็นอาจารย์อีกคน
        members = {1: "คนางค์ ก", 2: None, 3: "....", 4: "ธเนศ ข", 5: "  "}
        self.assertEqual(checker_module.committee_name_list(members),
                         ["คนางค์ ก", "ธเนศ ข"])

    def test_a_broken_read_never_becomes_an_accusation(self):
        """ตารางลายเซ็นอ่านพลาดได้หลายแบบ ทุกแบบทำให้ "จำนวน" ผิดไปด้วย

        แต่ละเคสคือความพลาดที่เคยเจอในเล่มจริง บันทึกไว้ในคอมเมนต์ของ
        signature_committee_slots / _rejoin_thai_marks — ถ้าฟ้องดื้อ ๆ จะกลายเป็น
        บอกว่าเล่มที่ถูกอยู่แล้วมีรายชื่อไม่ครบ
        """
        broken = {
            "เส้นแบ่งคอลัมน์เพี้ยน ชื่อแตกเป็นสองคน": ["หอมสนิท", "มยุรี", "ธเนศ เกษศิลป์"],
            "ชื่อขาดครึ่ง": ["หอมสนิท", "ธเนศ เกษศิลป์"],
            "อ่านไม่ออกเลย": [],
        }
        for label, read in broken.items():
            self.assertFalse(checker_module.committee_read_is_trustworthy(read), label)

    def test_a_clean_read_is_countable(self):
        """ด่านกันการอ่านเพี้ยนต้องไม่กลืนเคสที่ควรฟ้องจริงไปด้วย"""
        self.assertTrue(checker_module.committee_read_is_trustworthy(
            ["มยุรี หอมสนิท", "รศ.ดร. ธเนศ เกษศิลป์"]))

    def test_purple_note_says_whether_the_count_could_be_trusted(self):
        counted, unclear = Report(), Report()
        checker_module._note_committee_reference(
            counted, [{"name": "คนางค์ ก"}], "หน้าอาจารย์ที่ปรึกษา (หน้า ก)",
            form="บฑ.1", status="counted")
        checker_module._note_committee_reference(
            unclear, [{"name": "คนางค์ ก"}], "หน้ากรรมการสอบ (หน้า ข)",
            form="บฑ.2", status="unclear")
        self.assertIn("นับจำนวนอาจารย์เทียบกับ บฑ.1 ให้แล้ว แต่ไม่ได้เทียบชื่อ",
                      counted.human_checklist[0]["why"])
        self.assertIn("อ่านรายชื่อบนหน้านี้ได้ไม่ชัด จึงนับจำนวนเทียบกับ บฑ.2 ไม่ได้",
                      unclear.human_checklist[0]["why"])
        self.assertIn("คนางค์ ก", counted.human_checklist[0]["why"])

    def test_every_message_of_this_rule_has_an_english_translation(self):
        """ข้อความชุดนี้ไม่โผล่ในเล่มทดสอบ ด่าน --corpus จึงตรวจคำแปลให้ไม่ได้

        เคยหลุดมาแล้วจนรายงานอังกฤษออกมาเป็นไทยปนอังกฤษ ต้องเรียกกฎตรง ๆ แบบนี้
        """
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        rep = self._count(["ก ก", "ข ข"])
        checker_module._report_committee_count(
            rep, self.FORM3, ["ก ก", "ข ข", "ค ค", "ง ง"],
            "หน้าอาจารย์ที่ปรึกษา (หน้า ก)", "บฑ.1")
        for status in ("counted", "unclear"):
            checker_module._note_committee_reference(
                rep, [{"name": "คนางค์ ก"}], "หน้ากรรมการสอบ (หน้า ข)",
                form="บฑ.2", status=status)
        texts = [f for it in rep.zones["ORANGE"]
                 for f in (it["found"], it["expected"], it["fix"])]
        texts += [h["why"] for h in rep.human_checklist]
        # รายชื่อคนที่ต่อท้าย "... คือ" เป็นข้อมูลจากต้นทาง ต้องคงเป็นไทย
        # ตัดออกก่อนตรวจด้วยกติกาเดียวกับด่าน --corpus
        texts = [i18n.re.sub(r"(บฑ\.\d?\)?) คือ.*$", r"\1 คือ", t) for t in texts]
        for th in texts:
            en = i18n.tr_en(th, pairs)
            left = i18n.re.findall(r"[ก-๙]+", i18n.re.sub(r'"[^"]*"', "", en))
            self.assertEqual(left, [], f"ยังไม่แปล {left}\n  TH: {th}\n  EN: {en}")


class DegreeSpacingIsOnlyANotice(unittest.TestCase):
    """ชื่อปริญญาที่ต่างเฉพาะช่องว่าง/วรรคตอน = เหลือง ผ่านได้

    เจ้าหน้าที่สั่ง ส.ค. 2569: *"ชื่อปริญญา ที่มีช่องเว้นว่างเช่น M.Sc. () กับ M.Sc.()
    ให้เป็นสีเหลือง ไม่ใช่สีแดง"* (เดิมเป็นส้ม ซึ่งทำให้เล่มยังไม่ผ่าน)
    """

    def test_the_rule_is_registered_as_a_yellow_notice(self):
        self.assertIn("FORM.DEGREE_SPACING", RULE_CATALOG)
        self.assertEqual(rule_zone("FORM.DEGREE_SPACING"), "YELLOW")
        self.assertEqual(checker_module.DEGREE_SPACING_ZONE, "YELLOW")

    def test_a_pure_spacing_difference_already_passes_outright(self):
        """ต่างแค่ช่องว่างล้วน ๆ ถือว่าตรง ไม่ต้องแจ้งอะไรเลย"""
        for want, printed in (("M.Sc. ()", "M.Sc.()"),
                              ("M.Sc. (Nursing)", "M.Sc.(Nursing)"),
                              ("ปร.ด. (การพยาบาล)", "ปร.ด.(การพยาบาล)")):
            text = f"WISIT KAWAYAPANIK 6236350 NSNS/D\n{printed}\nTHESIS ADVISORY COMMITTEE"
            compared = compare_reference_text(text, want, "degree", degree_line=True)
            self.assertEqual(compared["status"], "exact", (want, printed))

    def test_a_punctuation_difference_is_a_yellow_notice(self):
        """ต่างที่เครื่องหมายวรรคตอน ตัวอักษรยังครบ = เหลือง เล่มผ่านได้"""
        want = "M.Sc. (Technology of Information System Management)"
        printed = "M.Sc. [Technology of Information System Management]"
        text = f"WISIT KAWAYAPANIK 6236350 NSNS/D\n{printed}\nTHESIS ADVISORY COMMITTEE"
        compared = compare_reference_text(text, want, "degree", degree_line=True)
        self.assertNotEqual(compared["status"], "exact")
        self.assertIn(norm(want), norm(text))      # ตัวอักษรครบทุกตัว -> เข้าทางเหลือง

    def test_a_real_spelling_difference_is_still_red(self):
        """ตัวอักษรหายจริง ไม่ใช่แค่ช่องว่าง ต้องยังเป็นแดง"""
        want = "M.Sc. (Nursing)"
        text = "WISIT KAWAYAPANIK 6236350 NSNS/D\nM.Sc. (Nursery)\nTHESIS ADVISORY COMMITTEE"
        self.assertNotIn(norm(want), norm(text))


class TocPageReferencesAreOnlyANotice(unittest.TestCase):
    """เลิกตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว (กติกา ส.ค. 2569)

    เจ้าหน้าที่สั่งว่าไม่ต้องเทียบเลขหน้าในสารบัญกับหน้าจริง และไม่ต้องตรวจการเรียง
    เลขหน้าของรายการในสารบัญ ถ้าพบให้ติดเหลืองไว้เฉย ๆ เล่มที่มีแต่ข้อนี้ต้องผ่าน
    """

    NOTICES = (
        ("front_matter", "สารบัญ (หน้า viii) กับบทที่ 3 (หน้า 45)",
         "สารบัญระบุหน้า 42 แต่บทอยู่จริงหน้า 45"),
        ("front_matter", "สารบัญ (หน้า viii)",
         "หัวข้อ ภาคผนวก ไม่มีเลขหน้า"),
    )

    def _report_with_only_toc_page_notices(self):
        report = Report()
        for part, loc, found in self.NOTICES:
            report.add(checker_module.TOC_PAGE_ZONE, part, loc, found,
                       "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว",
                       "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้",
                       "FRONT.TOC_PAGE_REF")
        return report

    def test_the_rule_is_registered_as_a_yellow_notice(self):
        self.assertIn("FRONT.TOC_PAGE_REF", RULE_CATALOG)
        self.assertEqual(rule_zone("FRONT.TOC_PAGE_REF"), "YELLOW")
        self.assertEqual(checker_module.TOC_PAGE_ZONE, "YELLOW")

    def test_a_book_with_only_these_notices_passes(self):
        report = self._report_with_only_toc_page_notices()
        self.assertEqual(len(report.zones["YELLOW"]), len(self.NOTICES))
        self.assertFalse(report.zones["RED"])
        self.assertFalse(report.zones["ORANGE"])
        self.assertEqual(report.verdict(), "ผ่าน")

    def test_the_notices_stay_out_of_the_student_fix_list(self):
        report = self._report_with_only_toc_page_notices()
        result = {"issues_by_zone": report.zones}
        self.assertEqual(checker_module.issues_to_fix(result), [])
        # เจ้าหน้าที่ยังกด "ไม่ผ่าน" รายข้อได้ ถ้าเห็นว่าเล่มนี้ควรแก้จริง
        self.assertEqual(len(checker_module.issues_to_fix(result, failed={"YELLOW:0"})), 1)

    def test_every_toc_page_reference_finding_uses_the_notice_zone(self):
        """กันไม่ให้มีจุดไหนหลุดกลับไปใช้สีแดง/ส้มกับกฎนี้"""
        import ast
        tree = ast.parse(Path(checker_module.__file__).read_text(encoding="utf-8"))
        zones = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "add"):
                continue
            rule = [a for a in node.args
                    if isinstance(a, ast.Constant) and a.value == "FRONT.TOC_PAGE_REF"]
            if not rule or not node.args:
                continue
            zones.append(ast.dump(node.args[0]))
        self.assertTrue(zones, "ไม่พบจุดที่ใช้กฎ FRONT.TOC_PAGE_REF เลย")
        self.assertEqual(set(zones), {ast.dump(ast.Name(id="TOC_PAGE_ZONE", ctx=ast.Load()))})


class TocVersusBodyMustNotTellYouToIntroduceATypo(unittest.TestCase):
    """สารบัญกับเนื้อหาไม่ตรงกัน ต้องไม่ยึดสารบัญเป็นถูกเสมอ

    เล่มจริงพิมพ์สารบัญว่า "LITURATURE REVIEW" ส่วนเนื้อหาว่า "LITERATURE REVIEW"
    ระบบเดิมยึดสารบัญเป็นหลัก จึงสั่งให้แก้เนื้อหาที่ถูกอยู่แล้วให้กลายเป็นคำที่ผิด
    เจ้าหน้าที่สั่งว่า "บทที่ 2 ถึงจะไม่ต้องตรวจเข้ม แต่ก็ควรให้ชื่อบทสะกดถูก"
    """

    def test_body_matching_the_regulation_wins(self):
        self.assertEqual(
            _correctly_spelled_side("LITERATURE REVIEW", "LITURATURE REVIEW", 2, 1),
            "body")

    def test_toc_matching_the_regulation_wins(self):
        self.assertEqual(
            _correctly_spelled_side("LITURATURE REVIEW", "LITERATURE REVIEW", 2, 1),
            "toc")

    def test_neither_matching_gives_no_winner(self):
        """ทั้งสองฝั่งไม่ตรงประกาศ = บอกไม่ได้ว่าใครถูก ห้ามชี้ให้แก้ฝั่งใดฝั่งหนึ่ง"""
        self.assertIsNone(
            _correctly_spelled_side("MY OWN TITLE", "ANOTHER TITLE", 2, 1))

    def test_both_matching_gives_no_winner(self):
        self.assertIsNone(
            _correctly_spelled_side("LITERATURE REVIEW", "LITERATURE REVIEW", 2, 1))

    def test_exact_beats_a_handbook_variant(self):
        """ตรงประกาศเป๊ะ ชนะตัวสะกดที่คู่มือยอมรับ — ยึดประกาศเป็นตัวชี้ขาด"""
        variants = dict(checker_module.CANONICAL_ACCEPTED_VARIANTS)
        variants[(1, 2)] = ("LITERATURE REVIEWS",)
        with mock.patch.object(checker_module, "CANONICAL_ACCEPTED_VARIANTS", variants):
            self.assertEqual(
                _correctly_spelled_side("LITERATURE REVIEW", "LITERATURE REVIEWS", 2, 1),
                "body")
            self.assertEqual(
                _correctly_spelled_side("LITERATURE REVIEWS", "LITERATURE REVIEW", 2, 1),
                "toc")

    def test_a_handbook_variant_beats_a_typo(self):
        variants = dict(checker_module.CANONICAL_ACCEPTED_VARIANTS)
        variants[(1, 2)] = ("LITERATURE REVIEWS",)
        with mock.patch.object(checker_module, "CANONICAL_ACCEPTED_VARIANTS", variants):
            self.assertEqual(
                _correctly_spelled_side("LITERATURE REVIEWS", "LITURATURE REVIEW", 2, 1),
                "body")

    def test_out_of_range_chapter_is_safe(self):
        self.assertIsNone(_correctly_spelled_side("A", "B", 99, 1))


class AcademicTitleIsNotCountedAsALowercaseName(unittest.TestCase):
    """ตำแหน่งวิชาการมีตัวพิมพ์เล็กเป็นปกติ ต้องไม่ทำให้ชื่อถูกฟ้องว่าพิมพ์เล็ก

    เล่มจริงพิมพ์ "Asst. Prof. SAOWALEE KAEWCHUAY, Ed.D." ชื่อพิมพ์ใหญ่ครบ ผิดแค่
    มีตำแหน่งวิชาการนำหน้า ซึ่งถูกฟ้องเป็นข้อของตัวเองอยู่แล้ว แต่กฎตัวพิมพ์ใหญ่
    เอาตำแหน่งมานับด้วย จึงได้สองข้อจากความผิดเดียว แถมข้อหลังยังบอกผิดว่าชื่อพิมพ์เล็ก
    """

    PAGE = "\n".join([
        "ฉ", "FACILITY MANAGEMENT OF EXERCISE FOR THE ELDERLY",
        "PAKKAWAT KONGKAEN 6637922 SHSM/M",
        "M.A. (SPORT MANAGEMENT)",
        "THESIS ADVISORY COMMITTEE: Asst. Prof. SAOWALEE KAEWCHUAY, Ed.D., "
        "Asst. Prof. SIWAPORN PHUPAN, Ed.D.",
        "ABSTRACT",
        "This study aimed to examine the opinions of the elderly.",
    ])

    def _founds(self, page):
        rep = Report()
        _check_abstract_committees(rep, {}, [0], [], [page], lambda i: "หน้า ฉ")
        return [it["found"] for it in rep.zones["RED"]]

    def test_only_the_academic_title_is_reported(self):
        founds = self._founds(self.PAGE)
        self.assertTrue(any("ตำแหน่งทางวิชาการนำหน้า" in f for f in founds), founds)
        self.assertFalse(any("ตัวพิมพ์ใหญ่" in f for f in founds), founds)

    def test_a_genuinely_lowercase_name_is_still_reported(self):
        page = self.PAGE.replace("SAOWALEE KAEWCHUAY", "Saowalee Kaewchuay")
        founds = self._founds(page)
        self.assertTrue(any("ตัวพิมพ์ใหญ่" in f for f in founds), founds)
        # ต้องยกเฉพาะชื่อ ไม่ใช่ตำแหน่งวิชาการที่ตัดออกไปแล้ว
        self.assertTrue(any('"Saowalee Kaewchuay"' in f for f in founds), founds)


class BrokenFontMarksAreNotReadAsLetters(unittest.TestCase):
    """ฟอนต์ที่ map วรรณยุกต์ผิดเป็นตัวอักษรอื่น ต้องไม่โผล่ในรายงาน

    เล่มจริงเล่มหนึ่งได้ "พรีซีซั่น" ออกมาเป็น "พรีซีซั8น" และ "ที่ปรึกษา" เป็น
    "ที8ปรึกษา" เจ้าหน้าที่อ่านแล้วนึกว่าเล่มพิมพ์ผิด ทั้งที่เล่มถูก
    แยกออกจากตัวเลขจริงได้ด้วยความกว้าง: วรรณยุกต์กว้างศูนย์ ตัวเลขจริงมีความกว้าง
    """

    @staticmethod
    def _char(text, width):
        return {"text": text, "x0": 100.0, "x1": 100.0 + width}

    def _kept(self, *chars):
        return [c["text"] for c in checker_module._thai_chars(list(chars))]

    def test_zero_width_letter_is_dropped(self):
        """"8" กว้างศูนย์ = วรรณยุกต์ที่ฟอนต์ map ผิด ต้องไม่หลุดไปอยู่ในข้อความ"""
        self.assertEqual(
            self._kept(self._char("ซ", 7.0), self._char("ั", 0.0),
                       self._char("8", 0.0), self._char("น", 7.4)),
            ["ซ", "ั", "น"])

    def test_real_digits_are_kept(self):
        """ตัวเลขจริงมีความกว้าง ห้ามทิ้ง (รหัสนักศึกษา เลขหน้า ปี)"""
        self.assertEqual(
            self._kept(self._char("6", 7.0), self._char("8", 7.0),
                       self._char("7", 7.0)),
            ["6", "8", "7"])

    def test_zero_width_space_still_becomes_nikhahit(self):
        self.assertEqual(self._kept(self._char("จ", 7.0), self._char(" ", 0.0)),
                         ["จ", "ํ"])

    def test_zero_width_thai_mark_is_kept(self):
        self.assertEqual(self._kept(self._char("ก", 7.0), self._char("ี", 0.0)),
                         ["ก", "ี"])


class ThaiDiffPointsAtWholeSyllables(unittest.TestCase):
    """จุดต่างของข้อความไทยต้องเป็นคำที่อ่านออก ไม่ใช่เศษพยางค์

    ตัวชี้จุดต่างเคยไล่ทีละ code point จึงตัดกลางพยางค์ เล่มจริงที่ชื่อเรื่องขาดคำว่า
    "กีฬา" รายงานออกมาว่า ขาด "ีฬาก" (สระอียกไปไว้หน้า แล้วลาก ก ของคำถัดไปมาด้วย)
    เจ้าหน้าที่อ่านแล้วไม่รู้ว่าต้องเติมอะไร
    """

    def test_missing_word_is_reported_whole(self):
        self.assertEqual(
            describe_diff(
                "แนวทางการพัฒนาสิ่งอำนวยความสะดวกสนามกรีฑาสำหรับนักกีฬาคนพิการ",
                "แนวทางการพัฒนาสิ่งอำนวยความสะดวกสนามกีฬากรีฑาสำหรับนักกีฬาคนพิการ"),
            'ขาด "กีฬา"')

    def test_vowel_stays_with_its_consonant(self):
        self.assertEqual(describe_diff("ประวัติผู้จัย", "ประวัติผู้วิจัย"), 'ขาด "วิ"')

    def test_missing_syllable_in_the_middle(self):
        self.assertEqual(describe_diff("ระเบียบวิธีวิจัย", "ระเบียบวิธีการวิจัย"),
                         'ขาด "การ"')

    def test_single_missing_letter(self):
        self.assertEqual(describe_diff("อาชีวนามัย", "อาชีวอนามัย"), 'ขาด "อ"')

    def test_english_word_level_diff_is_unchanged(self):
        self.assertEqual(describe_diff("RESEARCH METHODLOGY", "RESEARCH METHODOLOGY"),
                         'ต่างที่ "METHODLOGY" ต้องเป็น "METHODOLOGY"')

    def test_graphemes_keep_marks_with_their_base(self):
        self.assertEqual(_graphemes("กีฬา"), ["กี", "ฬ", "า"])
        self.assertEqual(_graphemes("เชียงใหม่"), ["เชี", "ย", "ง", "ให", "ม่"])
        self.assertEqual(_graphemes("ABC"), ["A", "B", "C"])


class MissingCommaBetweenNameAndDegree(unittest.TestCase):
    """ลืมจุลภาคระหว่างชื่อกับคุณวุฒิของตัวเอง ต้องฟ้องว่าขาดจุลภาค ไม่ใช่ว่าชื่อพิมพ์เล็ก

    เล่มจริงพิมพ์ "CHAKRIT SUVANJUMRAT, D.Eng., WATCHARAPONG CHOOKAEW D. Eng."
    คนที่สองลืมจุลภาคหลังชื่อ ระบบเดิมอ่านทั้งก้อนเป็น "ชื่อ" แล้วฟ้องว่า
    "ชื่อกรรมการไม่ได้เป็นตัวพิมพ์ใหญ่ทั้งหมด" ทั้งที่ชื่อพิมพ์ใหญ่ครบ
    ของจริงคือขาดจุลภาค ซึ่งแก้คนละอย่างกัน
    """

    BLOCK = "CHAKRIT SUVANJUMRAT, D.Eng., WATCHARAPONG CHOOKAEW D. Eng."

    def test_name_and_degree_are_separated(self):
        names, degrees = split_abstract_committee(self.BLOCK)
        self.assertEqual(names, ["CHAKRIT SUVANJUMRAT", "WATCHARAPONG CHOOKAEW"])
        self.assertEqual(degrees, ["D.Eng.", "D. Eng."])

    def test_the_missing_comma_is_reported_with_the_whole_entry(self):
        """ยกทั้งก้อนตามที่พิมพ์จริง พร้อมข้อความที่แก้แล้วของคนนั้นให้ก๊อปไปใช้ได้เลย"""
        self.assertEqual(abstract_committee_missing_degree_commas(self.BLOCK),
                         [("WATCHARAPONG CHOOKAEW D. Eng.",
                           "WATCHARAPONG CHOOKAEW, D. Eng.")])

    def test_case_rule_no_longer_fires_on_a_correct_name(self):
        rep = Report()
        page = "THESIS ADVISORY COMMITTEE: " + self.BLOCK + "\nABSTRACT\nBody."
        _check_abstract_committees(rep, {}, [0], [], [page], lambda i: "หน้า iv")
        founds = [it["found"] for it in rep.zones["YELLOW"]]
        self.assertTrue(any("ไม่ได้คั่นด้วยจุลภาค" in f for f in founds), founds)
        reds = [it["found"] for it in rep.zones["RED"]]
        self.assertFalse(any("ตัวพิมพ์ใหญ่" in f for f in reds), reds)

    def test_a_missing_comma_is_only_a_notice(self):
        """เจ้าหน้าที่สั่ง ส.ค. 2569: เรื่องจุลภาคให้แจ้งเป็นสีเหลือง เล่มยังผ่านได้"""
        rep = Report()
        page = "THESIS ADVISORY COMMITTEE: " + self.BLOCK + "\nABSTRACT\nBody."
        _check_abstract_committees(rep, {}, [0], [], [page], lambda i: "หน้า iv")
        self.assertEqual(rep.zones["RED"], [])
        self.assertEqual(rep.verdict(), "ผ่าน")
        self.assertEqual([it["rule_id"] for it in rep.zones["YELLOW"]],
                         ["FRONT.ABSTRACT_COMMA"])
        self.assertEqual(rule_zone("FRONT.ABSTRACT_COMMA"), "YELLOW")
        # ยังต้องบอกให้ครบว่าขาดตรงไหนและต้องเป็นอะไร ไม่ใช่แค่ "มีบางอย่างไม่เรียบร้อย"
        notice = rep.zones["YELLOW"][0]
        self.assertIn("WATCHARAPONG CHOOKAEW D. Eng.", notice["found"])
        self.assertIn("WATCHARAPONG CHOOKAEW, D. Eng.", notice["expected"])

    def test_a_full_stop_used_instead_of_a_comma(self):
        """เล่มจริงคั่นด้วยจุดแทนจุลภาค — ที่ควรเป็นต้องเปลี่ยนจุดเป็นจุลภาค ไม่ใช่เติมจุลภาค"""
        block = "SUPAPORN KIATTISIN, Ph.D.., ADISORN LEELASANTITHAM. Ph.D."
        self.assertEqual(abstract_committee_missing_degree_commas(block),
                         [("ADISORN LEELASANTITHAM. Ph.D.",
                           "ADISORN LEELASANTITHAM, Ph.D.")])
        self.assertEqual(split_abstract_committee(block)[0],
                         ["SUPAPORN KIATTISIN", "ADISORN LEELASANTITHAM."])

    def test_only_a_separator_dot_is_dropped_not_an_initial(self):
        """จุดที่เล่มใช้แทนจุลภาคต้องตัด แต่จุดของอักษรย่อในชื่อห้ามตัด"""
        self.assertEqual(_drop_separator_dot("ADISORN LEELASANTITHAM."),
                         "ADISORN LEELASANTITHAM")
        self.assertEqual(_drop_separator_dot("SOMCHAI J."), "SOMCHAI J.")
        self.assertEqual(_drop_separator_dot("WATCHARAPONG CHOOKAEW"),
                         "WATCHARAPONG CHOOKAEW")

    def test_properly_punctuated_block_is_untouched(self):
        block = "CHAKRIT SUVANJUMRAT, D.Eng., WATCHARAPONG CHOOKAEW, D.Eng."
        self.assertEqual(abstract_committee_missing_degree_commas(block), [])
        self.assertEqual(split_abstract_committee(block)[0],
                         ["CHAKRIT SUVANJUMRAT", "WATCHARAPONG CHOOKAEW"])

    def test_thai_block_without_a_comma(self):
        block = "บุรัสกร โตรัตน, ปร.ด., กฤษณ รักษาชีวจริญ ปร.ด."
        self.assertEqual(abstract_committee_missing_degree_commas(block),
                         [("กฤษณ รักษาชีวจริญ ปร.ด.", "กฤษณ รักษาชีวจริญ, ปร.ด.")])
        self.assertEqual(split_abstract_committee(block)[0],
                         ["บุรัสกร โตรัตน", "กฤษณ รักษาชีวจริญ"])

    def test_a_plain_name_is_not_split(self):
        """ชื่อธรรมดาที่ไม่มีคุณวุฒิห้อยท้าย ต้องไม่ถูกตัด"""
        block = "CHAKRIT SUVANJUMRAT, D.Eng., WATCHARAPONG CHOOKAEW"
        self.assertEqual(abstract_committee_missing_degree_commas(block), [])
        self.assertEqual(split_abstract_committee(block)[0],
                         ["CHAKRIT SUVANJUMRAT", "WATCHARAPONG CHOOKAEW"])


class BoldCannotBeJudgedWhenFontNamesAreAnonymous(unittest.TestCase):
    """ไฟล์ที่ไม่ได้เก็บชื่อฟอนต์ไว้ ห้ามสรุปว่า "ไม่เป็นตัวหนา"

    โปรแกรมแปลง PDF บางตัวตั้งชื่อฟอนต์ย่อยเป็น "CIDFont+F1", "F2" ไล่ตามลำดับที่พบ
    ในหน้านั้น ๆ ไม่ใช่ชื่อฟอนต์จริง — เล่มจริงเจอ CIDFont+F1 บนหน้าสารบัญเป็นตัวหนา
    แต่ CIDFont+F1 บนหน้าเนื้อหาเป็นตัวธรรมดา คือชื่อเดียวกันคนละฟอนต์
    เล่มที่หัวข้อสารบัญหนาครบทุกหัวข้อจึงเคยถูกฟ้องว่า "ไม่เป็นตัวหนา 13 หัวข้อ"
    """

    class _Page:
        def __init__(self, fonts):
            self.chars = [{"text": "A", "fontname": f} for f in fonts]

    def test_anonymous_subset_names_mean_undetectable(self):
        page = self._Page(["CIDFont+F1", "CIDFont+F2", "CIDFont+F3"])
        self.assertTrue(bold_is_undetectable(page))

    def test_real_font_names_stay_detectable(self):
        for fonts in (
            ["TimesNewRomanPSMT", "TimesNewRomanPS-BoldMT"],
            ["BCDIEE+THSarabunNew", "BCDEEE+THSarabunNew-Bold"],
            ["TimesNewRomanPSMT"],          # ไม่มีตัวหนาเลย แต่ชื่อบอกวงศ์ฟอนต์ = ตัดสินได้
        ):
            self.assertFalse(bold_is_undetectable(self._Page(fonts)), fonts)

    def test_page_without_text_is_not_treated_as_undetectable(self):
        self.assertFalse(bold_is_undetectable(self._Page([])))

    def test_anonymous_name_with_a_bold_marker_is_still_detectable(self):
        page = self._Page(["CIDFont+F1", "ABCDEF+ArialBold"])
        self.assertFalse(bold_is_undetectable(page))


class StudentNameAndIdMustShareOneLine(unittest.TestCase):
    """template กำหนดบรรทัดเดียวว่า "ชื่อ นามสกุล  รหัส  รหัสหลักสูตร/ระดับ"

    เจ้าหน้าที่สั่งว่า "ช่วงนี้มันต้องตรวจทั้งชื่อและรหัสนะ" — เดิมค้นชื่อกับรหัสแยกกัน
    ทั้งหน้า เล่มที่พิมพ์ชื่อไว้คนละบรรทัดกับรหัสจึงผ่านได้ทั้งที่ผิดรูปแบบ
    """

    def _run(self, page, name="เอมิกา หงสชั้น", student_id="6526627 NSMY/M"):
        rep = Report()
        _check_student_line_pairs_name_with_id(
            rep, page, name, student_id, "บทคัดย่อไทย (หน้า ง)", "ชื่อภาษาไทย")
        return rep

    def test_same_line_passes(self):
        rep = self._run("ชื่อเรื่อง\nเอมิกา หงสชั้น 6526627 NSMY/M\nพย.ม. (การพยาบาล)")
        self.assertEqual(sum(len(v) for v in rep.zones.values()), 0)

    def test_name_on_another_line_is_flagged(self):
        rep = self._run("ชื่อเรื่อง\nเอมิกา หงสชั้น\n6526627 NSMY/M\nพย.ม. (การพยาบาล)")
        oranges = [it["found"] for it in rep.zones["ORANGE"]]
        self.assertEqual(len(oranges), 1)
        self.assertIn("ไม่มีชื่อภาษาไทยอยู่ด้วย", oranges[0])
        self.assertIn("6526627 NSMY/M", oranges[0])

    def test_page_without_an_id_line_is_left_to_the_id_rule(self):
        """ไม่มีบรรทัดรหัสเลย = กฎรหัสนักศึกษาฟ้องไปแล้ว ห้ามฟ้องซ้ำ"""
        rep = self._run("ชื่อเรื่อง\nเอมิกา หงสชั้น\nพย.ม. (การพยาบาล)")
        self.assertEqual(sum(len(v) for v in rep.zones.values()), 0)

    def test_wrong_id_is_left_to_the_id_rule(self):
        rep = self._run("เอมิกา หงสชั้น\n6536627 NSMY/M", student_id="6526627 NSMY/M")
        self.assertEqual(sum(len(v) for v in rep.zones.values()), 0)

    def test_finds_the_line_that_carries_the_id(self):
        page = "ชื่อเรื่อง\nEMIKA HONGCHAN 6526627 NSMY/M\nบทคัดยอ"
        self.assertEqual(student_id_line(page), "EMIKA HONGCHAN 6526627 NSMY/M")
        self.assertEqual(student_id_line("ชื่อเรื่อง\nบทคัดยอ"), "")


class AbstractCommitteeBlockStopsAtTheAbstractHeading(unittest.TestCase):
    """บล็อกรายชื่อกรรมการต้องหยุดที่หัวข้อบทคัดย่อ แม้วรรณยุกต์จะหายตอนดึงข้อความ

    การดึงข้อความจาก PDF ไทยทำวรรณยุกต์/การันต์หายได้ เล่มจริงได้ "บทคัดยอ" (ไม่มี ่)
    ตัวหยุดเดิมเทียบตรงตัวกับ "บทคัดย่อ" จึงหาไม่เจอ แล้วบล็อกลากยาวกินเนื้อความ
    บทคัดย่อเข้ามา 4 บรรทัด ระบบเลยฟ้องผิดสองข้อรวดบนเล่มที่รูปแบบถูกต้อง
        รายชื่อกรรมการที่ปรึกษามีสาขาวิชาในวงเล็บ      <- คือเลขข้อ "1)" ในบทคัดย่อ
        ไม่มีจุลภาคคั่นหน้าชื่อ "บทคัดยอ การศึกษาวิจัย..."  <- คือย่อหน้าแรกทั้งย่อหน้า
    """

    # ข้อความตามที่ระบบดึงได้จริงจากเล่มที่เจ้าหน้าที่ส่งมา (วรรณยุกต์หายหลายตัว)
    PAGE = "\n".join([
        "ง",
        "การศึกษาความพรอมในการปรับเปลี่ยนเปนองคกรดิจิทัลของกำลังพล",
        "วรรณคดี มณฑลจรัส 6537481 SHPP/ M",
        "รป.ม. (นโยบายสาธารณะและการจัดการภาครัฐ)",
        "คณะกรรมการที่ปรึกษาวิทยานิพนธ: บุรัสกร โตรัตน, ปร.ด., กฤษณ รักษาชีวจริญ, ปร.ด.",
        "บทคัดยอ",
        "การศึกษาวิจัยครั้งนี้มีวัตถุประสงคเพื่อ 1) ศึกษาระดับความพรอมตอการปรับเปลี่ยน",
        "ดิจิทัลของกำลังพล 2) เปรียบเทียบความแตกตางของระดับความพรอม",
    ])

    def test_block_holds_only_the_committee_line(self):
        is_english, block = abstract_committee_block(self.PAGE)
        self.assertFalse(is_english)
        self.assertEqual(block, "บุรัสกร โตรัตน, ปร.ด., กฤษณ รักษาชีวจริญ, ปร.ด.")
        self.assertNotIn("การศึกษาวิจัย", block)

    def test_correct_page_is_not_reported(self):
        rep = Report()
        _check_abstract_committees(rep, {}, [], [0], [self.PAGE], lambda i: "หน้า ง")
        self.assertEqual(sum(len(v) for v in rep.zones.values()), 0,
                         [it["found"] for v in rep.zones.values() for it in v])

    def test_block_stops_even_when_the_heading_is_unreadable(self):
        """ไม่มีหัวข้อบทคัดย่อให้จับเลย ก็ยังต้องหยุดถูก

        เจ้าหน้าที่ทักว่า "ระบบควรดูที่คำ และพิจารณาว่าคืออะไร ไม่ใช่จำบรรทัด
        ถ้าดูที่บรรทัดแล้วแต่ละเล่มไม่เท่ากัน ก็ตรวจเพี้ยน" — รายชื่อกรรมการห่อคำ
        กี่บรรทัดก็ได้ตามความยาวชื่อ จึงห้ามนับบรรทัดตัดสิน
        """
        page = "\n".join([
            "คณะกรรมการที่ปรึกษาวิทยานิพนธ: บุรัสกร โตรัตน, ปร.ด., กฤษณ รักษาชีวจริญ, ปร.ด.",
            "การศึกษาวิจัยครั้งนี้มีวัตถุประสงคเพื่อ 1) ศึกษาระดับความพรอมตอการปรับเปลี่ยน",
            "ดิจิทัลของกำลังพล 2) เปรียบเทียบความแตกตางของระดับความพรอม",
        ])
        _is_english, block = abstract_committee_block(page)
        self.assertEqual(block, "บุรัสกร โตรัตน, ปร.ด., กฤษณ รักษาชีวจริญ, ปร.ด.")

    def test_a_name_wrapped_without_its_degree_is_still_joined(self):
        """ชื่อถูกห่อคำไปบรรทัดถัดไปโดยบรรทัดแรกไม่มีคุณวุฒิเลย ต้องต่อให้ครบ"""
        page = "\n".join([
            "ADVISORY COMMITTEE: SOMCHAI",
            "JAIDEE, Ph.D.",
            "ABSTRACT",
            "Body text here.",
        ])
        _is_english, block = abstract_committee_block(page)
        self.assertEqual(block, "SOMCHAI JAIDEE, Ph.D.")

    def test_english_page_still_stops_at_ABSTRACT(self):
        page = "\n".join([
            "iv", "TITLE OF THE THESIS",
            "PIYAORN CHORNCHOEM 6136017 TMTM/D",
            "Ph.D. (TROPICAL MEDICINE)",
            "THESIS ADVISORY COMMITTEE: NARISARA CHANTRATITA, Ph.D., NITAYA",
            "INDRAWATTANA, Ph.D.",
            "ABSTRACT",
            "Probiotics are widely marketed as dietary supplements (1) and dairy products.",
        ])
        is_english, block = abstract_committee_block(page)
        self.assertTrue(is_english)
        self.assertNotIn("Probiotics", block)
        self.assertIn("INDRAWATTANA", block)


class EnglishReportHasNoThaiLeftOver(unittest.TestCase):
    """ประโยคที่ระบบ "ประกอบขึ้นเอง" ต้องแปลอังกฤษได้ครบ ไม่มีไทยปน

    ด่าน check_i18n --corpus ตรวจจากเล่มทดสอบ 3 เล่ม ซึ่งไม่เคยผลิตข้อความบางแบบเลย
    เจ้าหน้าที่จึงเจอของจริงว่า 'ชื่อบท in the document reads "DISCUSSIONS"' และ
    'Counted 5 กรรมการที่ปรึกษา on this page' บนเล่มที่ไม่ได้อยู่ในชุดทดสอบ

    ชุดนี้จึงไม่พึ่งเล่ม แต่เรียก describe_diff จริงเพื่อสร้าง "รูปประโยคทุกแบบ"
    ที่มันผลิตได้ (แทนที่ / ขาด / เกิน) แล้วยัดเข้าประโยคของผู้เรียกทุกตัว
    """

    @classmethod
    def setUpClass(cls):
        tools = Path(__file__).resolve().parents[1] / "tools"
        sys.path.insert(0, str(tools))
        import check_i18n
        cls.i18n = check_i18n
        _block, cls.pairs = check_i18n.load_tr()

    def _thai_left(self, thai):
        english = self.i18n.tr_en(thai, self.pairs)
        # ค่าที่อยู่ในเครื่องหมายคำพูดคือข้อความจากเล่มจริง ต้องคงเป็นไทยอยู่แล้ว
        stripped = re.sub(r'"[^"]*"', "", english)
        return [w for w in re.findall(r"[ก-๙]+", stripped)
                if w not in self.i18n.THAI_PAGE_LETTERS and w not in self.i18n.KEEP_THAI]

    # (ข้อความที่พบ, ข้อความที่ถูกต้อง) ครอบ opcode ของ difflib ครบทุกแบบ
    DIFFS = [
        ("DISCUSSIONS", "DISCUSSION"),                       # เกินมา
        ("SUBMITTED IN PARTIAL", "A THESIS SUBMITTED IN PARTIAL"),   # ขาด
        ("CONCLUSIONS AND RECOMMENDATIONS",
         "CONCLUSION AND RECOMMENDATIONS"),                  # แทนที่
        ("ประวัติผู้จัย", "ประวัติผู้วิจัย"),                          # ขาด (ไทย)
        ("REQUIREMENT FOR THE DEGREE", "REQUIREMENTS FOR THE DEGREE"),
    ]

    def test_every_diff_phrase_has_an_english_rule(self):
        for found, expected in self.DIFFS:
            diff = describe_diff(found, expected)
            self.assertTrue(diff, f"describe_diff ว่างสำหรับ {found!r}")
            self.assertEqual(self._thai_left(diff), [],
                             f'แปลไม่ครบ: {diff!r} -> {self.i18n.tr_en(diff, self.pairs)!r}')

    def test_diff_phrases_read_naturally_inside_their_sentences(self):
        """ผู้เรียก describe_diff ทุกตัวต่อท้ายประโยคของตัวเอง ต้องแปลได้ทั้งประโยค"""
        for found, expected in self.DIFFS:
            diff = describe_diff(found, expected)
            for sentence in (
                f'ชื่อบทในเล่มเขียนว่า "{found}" {diff}',
                f'ชื่อเรื่องในเล่มเขียนว่า "{found}" {diff}',
                f'หน้าปกพิมพ์ "{found}" ไม่ตรงข้อความบังคับ (ข้อความประเภทงาน) {diff}',
                f'สารบัญสะกดหัวข้อนี้ผิด เขียนว่า "{found}" {diff}',
                f'ช่องประธานหลักสูตร (มุมล่างขวา) เขียนว่า "{found}" {diff}',
            ):
                self.assertEqual(
                    self._thai_left(sentence), [],
                    f'แปลไม่ครบ: {sentence!r} -> {self.i18n.tr_en(sentence, self.pairs)!r}')

    def test_committee_count_sentence_translates_both_labels(self):
        """ป้าย "กรรมการ"/"กรรมการที่ปรึกษา" ถูกยัดเป็นตัวแปรกลางประโยค ต้องแปลด้วย"""
        for label in ("กรรมการ", "กรรมการที่ปรึกษา"):
            for thai in (
                f'นับรายชื่อ{label}บนหน้านี้ได้ 5 คน แต่ข้อมูลอนุมัติมี 4 คน '
                f'ระบบอ่านได้ว่า 1. "INGA THORSDOTTIR"',
                f'ต้องมี{label} 4 คนตามข้อมูลอนุมัติ (บฑ.)',
            ):
                self.assertEqual(self._thai_left(thai), [],
                                 f'แปลไม่ครบ: {thai!r} -> {self.i18n.tr_en(thai, self.pairs)!r}')


class TheNamePrintedInTheStudentSlotIsWhatGetsReported(unittest.TestCase):
    """ต้องรายงาน "ข้อความที่อยู่ในช่องของชื่อ" ไม่ใช่บรรทัดที่คล้ายชื่อที่สุดทั้งหน้า

    เล่มจริงสองเล่มที่เจ้าหน้าที่ส่งมา (ก.ย. 2569) ลืมแก้ placeholder ของ template
    เหลือคำว่า "FIRSTNAME LASTNAME" ไว้ทั้งบนหน้าปกและหน้าบทคัดย่อ ระบบรายงานว่า
    เล่มเขียนชื่อนักศึกษาว่า "OF PM2.5 IN NORTHERN THAILAND" (เศษชื่อเรื่อง) และ
    "ABSTRACT" (หัวข้อ) เพราะการวัดความคล้ายทีละบรรทัดให้คะแนนข้อความพวกนั้นสูงกว่า
    บรรทัดชื่อจริง เจ้าหน้าที่จึงได้คำสั่งให้ไปแก้ข้อความที่ไม่ได้อยู่ในช่องนั้นเลย

    ทั้งสามหน้าชี้ช่องได้จากโครงสร้างของ template: หน้าปกใช้บรรทัดก่อนข้อความ
    "A THESIS SUBMITTED..." หน้าลงนามใช้บรรทัดเหนือป้าย "Candidate"/"ผู้วิจัย"
    หน้าบทคัดย่อใช้ข้อความหน้ารหัสนักศึกษา
    """

    NAME = "NITTAYA CHAKHAMRUN"
    TITLE = ("GRID BASED EMISSION INVENTORY AND REGIONAL SCALE AIR POLLUTION "
             "MODEL OF PM2.5 IN NORTHERN THAILAND")
    COVER = NEWLINE.join([
        "GRID BASED EMISSION INVENTORY",
        "AND REGIONAL SCALE AIR POLLUTION MODEL",
        "OF PM2.5 IN NORTHERN THAILAND",
        "FIRSTNAME LASTNAME",
        "A THESIS SUBMITTED IN PARTIAL FULFILLMENT",
        "OF THE REQUIREMENTS FOR THE DEGREE OF",
        "DOCTOR OF PHILOSOPHY (ENVIRONMENTAL TECHNOLOGY)",
        "FACULTY OF GRADUATE STUDIES",
        "MAHIDOL UNIVERSITY",
        "2026",
        "COPYRIGHT OF MAHIDOL UNIVERSITY",
    ])
    THAI_COVER = NEWLINE.join([
        "การมีส่วนร่วมของประชาชนในการประเมินผลกระทบสิ่งแวดล้อม",
        "ชื่อ นามสกุล",
        "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
        "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล",
        "ลิขสิทธิ์ของมหาวิทยาลัยมหิดล",
    ])
    SIGNATURE = NEWLINE.join([
        "ii",
        "Thesis",
        "entitled",
        "GRID BASED EMISSION INVENTORY",
        "was submitted to the Faculty of Graduate Studies, Mahidol University",
        "Thesis Advisory Committees",
        "First Name Last name, Asst.Prof Thanakrit Neamhom,",
        "Candidate Ph.D (Environmental Technology)",
        "Mahidol University",
    ])
    ABSTRACT = NEWLINE.join([
        "v",
        "GRID BASED EMISSION INVENTORY",
        "AND REGIONAL SCALE AIR POLLUTION MODEL",
        "FIRSTNAME LASTNAME 6636201 PHEH/M",
        "Ph.D. (ENVIRONMENTAL TECHNOLOGY)",
        "THESIS ADVISORY COMMITTEE : THANAKRIT NEAMHOM, Ph.D",
        "ABSTRACT",
        "Coffee cultivation and primary processing contribute significantly.",
    ])

    def test_the_cover_slot_holds_the_placeholder(self):
        self.assertEqual(
            checker_module.cover_printed_name(self.COVER, self.TITLE),
            "FIRSTNAME LASTNAME")

    def test_the_thai_cover_slot_is_found_by_its_own_template_wording(self):
        self.assertEqual(
            checker_module.cover_printed_name(self.THAI_COVER), "ชื่อ นามสกุล")

    def test_the_signature_slot_holds_the_placeholder(self):
        """สองคอลัมน์ถูกดึงมารวมบรรทัดเดียว ชื่อนักศึกษาคือท่อนซ้ายก่อนจุลภาค"""
        self.assertEqual(
            checker_module.signature_printed_name(self.SIGNATURE),
            "First Name Last name")

    def test_the_abstract_slot_holds_the_placeholder(self):
        self.assertEqual(
            checker_module.abstract_printed_name(self.ABSTRACT),
            "FIRSTNAME LASTNAME")

    def test_similarity_alone_reports_text_from_somewhere_else(self):
        """ควบคุมเชิงลบ: วิธีเดิมได้เศษชื่อเรื่อง หัวข้อ และคุณวุฒิมาแทนชื่อ"""
        self.assertEqual(checker_module.closest_text_line(self.COVER, self.NAME),
                         "OF PM2.5 IN NORTHERN THAILAND")
        for page in (self.ABSTRACT, self.SIGNATURE):
            self.assertNotIn(
                "LASTNAME",
                checker_module.closest_text_line(page, self.NAME).upper())

    def test_a_cover_without_any_name_does_not_blame_the_title(self):
        """เล่มลืมพิมพ์ชื่อ = ช่องว่างเปล่า ห้ามรายงานเศษชื่อเรื่องว่าเป็นชื่อนักศึกษา"""
        no_name = self.COVER.replace("FIRSTNAME LASTNAME" + NEWLINE, "")
        self.assertEqual(
            checker_module.cover_printed_name(no_name, self.TITLE), "")

    def test_pages_without_the_template_anchor_say_nothing(self):
        plain = "ABSTRACT" + NEWLINE + "Coffee cultivation."
        self.assertEqual(checker_module.cover_printed_name(plain), "")
        self.assertEqual(checker_module.signature_printed_name(plain), "")
        self.assertEqual(checker_module.abstract_printed_name(plain), "")

    # ถ้อยคำที่ตามหลังชื่อนักศึกษาบนหน้าปก ไล่มาจาก template ครบทั้ง 18 ใบ
    # (นานาชาติ / เล่มอังกฤษของหลักสูตรไทย / เล่มไทย คูณสามประเภทเล่ม คูณสองรูปแบบ)
    COVER_STOPS = [
        "A THESIS SUBMITTED IN PARTIAL FULFILLMENT",
        "A THEMATIC PAPER SUBMITTED IN PARTIAL FULFILLMENT",
        "AN INDEPENDENT STUDY SUBMITTED IN PARTIAL FULFILLMENT",
        "A DISSERTATION SUBMITTED IN PARTIAL FULFILLMENT",
        "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร ปริญญา",
        "สารนิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร ปริญญา",
        "การค้นคว้าอิสระนี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร ปริญญา",
    ]

    def test_the_cover_anchor_covers_every_template(self):
        """เล่มการค้นคว้าอิสระภาษาอังกฤษใช้ "AN" ไม่ใช่ "A" — เคยหลุดไป 4 ใบจาก 18"""
        for stop in self.COVER_STOPS:
            page = NEWLINE.join(["ชื่อเรื่อง", "FIRSTNAME LASTNAME", stop,
                                 "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล"])
            self.assertEqual(checker_module.cover_printed_name(page),
                             "FIRSTNAME LASTNAME", stop)

    def test_the_signature_anchor_covers_both_languages(self):
        """template อังกฤษใช้ป้าย "Candidate" 24 ใบ เล่มไทยใช้ "ผู้วิจัย" 12 ใบ"""
        for label, name in (("Candidate Ph.D (Environmental Technology)",
                             "First Name Last name"),
                            ("ผู้วิจัย Ph.D. (Social Science)", "ชื่อ นามสกุล")):
            page = NEWLINE.join(["วิทยานิพนธ์", "เรื่อง", "ชื่อเรื่อง",
                                 name + ", รองศาสตราจารย์ คนางค์ คันธมธุรพจน์,",
                                 label])
            self.assertEqual(checker_module.signature_printed_name(page), name)

    def test_all_three_slots_are_wired_into_the_check(self):
        """ควบคุมเชิงลบ: ถ้าลืมต่อสาย ตัวตรวจจะกลับไปวัดความคล้ายทั้งหน้าเงียบ ๆ"""
        source = inspect.getsource(checker_module.run_check)
        for call in ("cover_printed_name(", "signature_printed_name(",
                     "abstract_printed_name("):
            self.assertIn(call, source)

    def test_the_reported_sentence_names_the_placeholder(self):
        compared = checker_module.compare_values(
            checker_module.abstract_printed_name(self.ABSTRACT), self.NAME,
            "student_name")
        self.assertEqual(
            checker_module.mismatch_detail("ชื่อภาษาอังกฤษ", compared, self.NAME),
            'ชื่อภาษาอังกฤษในเล่มเขียนว่า "FIRSTNAME LASTNAME"')


class EachAbstractPageCarriesOnlyItsOwnLanguageTitle(unittest.TestCase):
    """หน้าบทคัดย่อไทยต้องไม่มีชื่อเรื่องภาษาอังกฤษ (เจ้าหน้าที่ยืนยัน ก.ย. 2569)

    template วางชื่อเรื่องภาษาเดียวไว้หัวหน้าบทคัดย่อแต่ละภาษา — หน้าอังกฤษขึ้นต้นด้วย
    "THESIS TITLE" หน้าไทยขึ้นต้นด้วย "หัวข้อวิทยานิพนธ์ภาษาไทย" กฎ "ชื่อเรื่องภาษาเดียว"
    เดิมตัดสินจากภาษาของเล่ม จึงครอบไม่ถึงหน้าบทคัดย่อที่ตั้งใจใช้คนละภาษากันอยู่แล้ว
    """

    TH = "การประเมินการปลดปล่อยก๊าซเรือนกระจกจากการเพาะปลูกกาแฟ และการแปรรูปกาแฟขั้นต้น"
    EN = ("THE EVALUATION OF GREENHOUSE GAS EMISSIONS IN PRIMARY COFFEE "
          "CULTIVATION AND PRIMARY PROCESSING")
    TAIL = NEWLINE.join([
        "ณัฐรดา วิชานุชิต 6636201 PHEH/M",
        "วท.ม.(อนามัยสิ่งแวดล้อม)",
        "บทคัดย่อ",
        "การเพาะปลูกและการแปรรูปกาแฟขั้นต้นเป็นแหล่งสำคัญของการปลดปล่อยก๊าซเรือนกระจก",
    ])

    def test_a_thai_abstract_page_with_only_the_thai_title_is_clean(self):
        page = NEWLINE.join(["vii", self.TH, self.TAIL])
        self.assertEqual(checker_module.title_printed_on_page(page, self.EN), "")

    def test_the_english_title_added_to_the_thai_abstract_is_reported(self):
        page = NEWLINE.join(["vii", self.TH, self.EN, self.TAIL])
        self.assertIn("GREENHOUSE GAS",
                      checker_module.title_printed_on_page(page, self.EN))

    def test_the_rule_reaches_the_abstract_pages_at_all(self):
        """ควบคุมเชิงลบ: ถ้าลืมต่อสายหน้าบทคัดย่อเข้ากฎ จะไม่มีตำแหน่งนี้ในโค้ดเลย"""
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("abstract_title_spots", source)
        self.assertIn("หน้าบทคัดย่อ", source)

    def test_both_wordings_have_an_english_translation(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        for thai, english in (
            ("หน้าบทคัดย่อภาษาไทยต้องมีเฉพาะชื่อเรื่องภาษาไทย",
             "The Thai abstract page must carry the Thai title only"),
            ("หน้าบทคัดย่อภาษาอังกฤษต้องมีเฉพาะชื่อเรื่องภาษาอังกฤษ",
             "The English abstract page must carry the English title only"),
        ):
            self.assertEqual(i18n.tr_en(thai, pairs), english)


class TheReferenceHeadingIsReportedOnThePageThatCarriesIt(unittest.TestCase):
    """หน้าอ้างอิงที่เลือกสองคำ ต้องฟ้องที่หน้านั้น ไม่ใช่ฟ้องสารบัญที่ถูกอยู่แล้ว

    เล่มจริง (ก.ย. 2569) พิมพ์หัวข้อหน้า 116 ว่า "REFERENCES/BIBLIOGRAPHY" ส่วนสารบัญ
    เขียน "REFERENCES" ถูกต้อง ระบบเทียบกันเป็นเซตแล้วรายงานได้คำแรกคำเดียว จึงได้
    ประโยคที่ขัดกันเอง 'สารบัญใช้คำ "REFERENCES" แต่หน้าอ้างอิงจริงใช้ "REFERENCES"'
    พร้อมสั่งให้แก้สารบัญเป็นคำเดิม
    """

    def test_the_page_heading_with_two_terms_yields_two_terms(self):
        self.assertEqual(
            checker_module.reference_terms("REFERENCES/BIBLIOGRAPHY"),
            ["REFERENCES", "BIBLIOGRAPHY"])

    def test_a_single_term_page_still_compares_against_the_toc(self):
        self.assertEqual(checker_module.reference_terms("BIBLIOGRAPHY"),
                         ["BIBLIOGRAPHY"])
        self.assertNotEqual(set(checker_module.reference_terms("REFERENCES")),
                            set(checker_module.reference_terms("BIBLIOGRAPHY")))

    def test_the_comparison_is_skipped_when_the_page_uses_more_than_one_term(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("page_one_word = len(page_terms) == 1", source)

    def test_the_page_finding_says_what_is_wrong(self):
        source = inspect.getsource(checker_module.run_check)
        self.assertIn("หัวข้อในหน้านี้เลือกหลายคำ", source)

    def test_that_wording_has_an_english_translation(self):
        import tools.check_i18n as i18n
        _block, pairs = i18n.load_tr()
        self.assertEqual(
            i18n.tr_en('หัวข้อในหน้านี้เลือกหลายคำ: "REFERENCES/BIBLIOGRAPHY"', pairs),
            'The heading on this page uses multiple terms: "REFERENCES/BIBLIOGRAPHY"')


if __name__ == "__main__":
    unittest.main()
