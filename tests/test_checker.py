import sys

import translit
import unittest

from checker import (
    _COMMITTEE_TRANSLATE_MSG,
    NOT_CHECKED,
    N_APPENDIX,
    Report,
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
    _committee_keyname,
    _committee_translation,
    signature_page_kind,
    _is_white_fill,
    _report_sig_placeholders,
    _sig_clean_name,
    _strip_committee_title,
    sig_visible_placeholders,
    _report_thai_committee,
    _report_committee_positions,
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
    fuzzy_contains,
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

    def test_exact_fuzzy_match(self):
        found, score = fuzzy_contains(norm("สมชาย ใจดี"), "สมชาย ใจดี")
        self.assertTrue(found)
        self.assertEqual(score, 1.0)

    def test_empty_needle_is_not_a_match(self):
        self.assertEqual(fuzzy_contains(norm("ข้อความ"), ""), (False, 0.0))


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

    def test_report_item_always_contains_a_fix_recommendation(self):
        report = Report()
        report.add("RED", "body", "หน้า 12", "พบข้อผิดพลาด", "ข้อความที่ถูกต้อง", "")
        self.assertTrue(report.zones["RED"][0]["fix"])

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
        # ของ APPENDIX B ที่มีจริง จึงใช้ข้อความอธิบายแบบภาคผนวกหลายชุด (ยังเป็นส้ม)
        self.assertTrue(
            toc_page_mismatch_is_appendix_alt("appendix", "87", self.APPENDIX_PAGES))

    def test_appendix_pointing_at_a_page_with_no_appendix_is_generic(self):
        # ชี้ไปหน้าที่ไม่มีภาคผนวกเลย = mismatch ธรรมดา (ไม่ใช่ alt) แต่ก็ยังเป็นส้ม
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
        members, quals, bottom = signature_committee_slots(page)
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
        members, _quals, bottom = signature_committee_slots(page)
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
        members, _quals, _bottom = signature_committee_slots(page)
        self.assertNotIn("Ghost Member", [v for v in members.values() if v])

    def test_qualification_presence_detected_per_member(self):
        rows = [
            ("Candidate,", "A One,", "", "Ph.D."),                 # m1 มีคุณวุฒิ
            ("Academic rank First Name Last name,", "B Two,", "", "Degree (Subject)"),  # m2 placeholder=ไม่มี
            ("Academic rank First Name Last name,", "C Three,"),   # m3 ไม่มีบรรทัดคุณวุฒิ
            ("Dean", "Program Director"),
        ]
        page = self._Page(842, 595, self._dot_then_names(rows))
        members, quals, bottom = signature_committee_slots(page)
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


class ThaiCommitteeSetDiffTests(unittest.TestCase):
    """เล่มไทย: เทียบชื่อกรรมการแบบชุด แยก ถูกต้อง/สลับ/ขาด/เกิน โดยไม่ฟ้องเลื่อนทั้งแถว"""

    def _run(self, expected_names, members):
        rep = Report()
        expected = [{"name": n, "role": ""} for n in expected_names]
        _report_thai_committee(rep, expected, members, "หน้ากรรมการสอบ (หน้า ก)")
        return rep

    def _reds(self, rep):
        return [i["found"] for i in rep.zones["RED"]]

    def test_all_correct_positions_report_nothing(self):
        rep = self._run(["คนางค์ ก", "สุภาภรณ์ ข", "ธเนศ ค"],
                        {1: "คนางค์ ก", 2: "สุภาภรณ์ ข", 3: "ธเนศ ค"})
        self.assertEqual(self._reds(rep), [])

    def test_honorific_prefix_is_ignored(self):
        rep = self._run(["คนางค์ ก"], {1: "ดร. คนางค์ ก"})
        self.assertEqual(self._reds(rep), [])

    def test_two_swapped_report_single_combined_item(self):
        rep = self._run(["คนางค์ ก", "สุภาภรณ์ ข", "ธเนศ ค"],
                        {1: "คนางค์ ก", 2: "ธเนศ ค", 3: "สุภาภรณ์ ข"})
        reds = self._reds(rep)
        self.assertEqual(len(reds), 1)          # รวมเป็น 1 ไม่ใช่ 2
        self.assertIn("สลับตำแหน่งกัน", reds[0])
        self.assertIn("คนที่ 2", reds[0])
        self.assertIn("คนที่ 3", reds[0])

    def test_three_cycle_reports_correct_order_once(self):
        # หมุน 3 ตำแหน่ง (ไม่ใช่คู่สลับ) → บอกลำดับที่ถูกครั้งเดียว
        rep = self._run(["A A", "B B", "C C"],
                        {1: "C C", 2: "A A", 3: "B B"})
        reds = self._reds(rep)
        self.assertEqual(len(reds), 1)
        self.assertIn("เรียงผิดตำแหน่ง", reds[0])

    def test_missing_member_is_named_not_cascaded(self):
        # ขาดกรรมการกลาง แล้วดันชื่อขึ้น → ต้องฟ้อง 'ไม่พบ B' + 'พบ C เกินตำแหน่ง' ไม่ใช่แดงรัวทั้งแถว
        rep = self._run(["A A", "B B", "C C"], {1: "A A", 2: "C C"})
        reds = self._reds(rep)
        self.assertEqual(len(reds), 1)
        self.assertIn("ไม่พบกรรมการ", reds[0])
        self.assertIn("B B", reds[0])

    def test_extra_name_not_in_committee_is_flagged(self):
        rep = self._run(["A A", "B B"], {1: "A A", 2: "B B", 3: "X Stranger"})
        reds = self._reds(rep)
        self.assertTrue(any("ไม่อยู่ในรายชื่อกรรมการอนุมัติ" in r and "Stranger" in r
                            for r in reds))

    def test_keyname_normalizes_prefix_and_spacing(self):
        self.assertEqual(_committee_keyname("ดร. คนางค์  ก"),
                         _committee_keyname("คนางค์ ก"))

    def test_academic_rank_prefix_is_ignored(self):
        # ตรวจเฉพาะชื่อ — คำนำหน้าตำแหน่งวิชาการต่างกันไม่นับว่าผิด
        clean = _committee_keyname("คนางค์ คันธมธุรพจน์")
        for titled in ("รองศาสตราจารย์ ดร. คนางค์ คันธมธุรพจน์",
                       "ศาสตราจารย์ ดร. คนางค์ คันธมธุรพจน์",
                       "ผู้ช่วยศาสตราจารย์คนางค์ คันธมธุรพจน์",
                       "อาจารย์ คนางค์ คันธมธุรพจน์"):
            self.assertEqual(_committee_keyname(titled), clean, titled)

    def test_rank_change_between_ethesis_and_book_still_matches(self):
        # eThesis เป็น รศ. แต่เล่มพิมพ์ ศ. (เลื่อนตำแหน่ง) → ชื่อเดียวกัน ต้องผ่าน
        rep = self._run(["รองศาสตราจารย์ ดร. คนางค์ ก"],
                        {1: "ศาสตราจารย์ ดร. คนางค์ ก"})
        self.assertEqual(self._reds(rep), [])

    def test_thai_name_starting_with_title_letter_is_not_eaten(self):
        # ชื่อจริงขึ้นต้นด้วย ศ (เช่น ศศิธร) ต้องไม่ถูกตัดเพราะเข้าใจผิดว่าเป็น 'ศ.'
        self.assertEqual(_committee_keyname("ศศิธร ก"), _committee_keyname("ศศิธร ก"))
        self.assertIn("ศศ", _committee_keyname("ศศิธร ก"))


class EnglishCommitteeFuzzyTests(unittest.TestCase):
    """เล่มอังกฤษที่แปลชื่อสำเร็จ: เทียบตามลำดับเหมือนเล่มไทย (เทียบหลวมจากชื่อแปล) = สีแดง"""

    def _run(self, expected_en, members):
        rep = Report()
        _report_committee_positions(rep, expected_en, members,
                                    "Examination committee page (page i)", fuzzy=True)
        return [i["found"] for i in rep.zones["RED"]]

    def test_correct_order_with_spelling_variation_passes(self):
        # ชื่อแปลสะกดต่างเล็กน้อยจากในเล่ม แต่ตำแหน่งถูก → ต้องผ่าน (ratio ≥ 0.7)
        reds = self._run(["Narisara Chantratita", "Supaporn Songprachaa"],
                         {1: "Narisara Chantaratid", 2: "Supaporn Songpracha"})
        self.assertEqual(reds, [])

    def test_swapped_english_names_report_single_item(self):
        reds = self._run(["Alice Adams", "Bob Brown", "Carol Clark"],
                         {1: "Alice Adams", 2: "Carol Clark", 3: "Bob Brown"})
        self.assertEqual(len(reds), 1)
        self.assertIn("สลับตำแหน่งกัน", reds[0])

    def test_missing_english_member_named(self):
        reds = self._run(["Alice Adams", "Bob Brown", "Carol Clark"],
                         {1: "Alice Adams", 2: "Carol Clark"})
        self.assertTrue(any("ไม่พบกรรมการ" in r and "Bob Brown" in r for r in reds))

    def test_stranger_english_name_flagged(self):
        reds = self._run(["Alice Adams", "Bob Brown"],
                         {1: "Alice Adams", 2: "Bob Brown", 3: "Zebra Zulu"})
        self.assertTrue(any("ไม่อยู่ในรายชื่อกรรมการอนุมัติ" in r and "Zebra" in r
                            for r in reds))

    def test_full_word_english_title_is_ignored(self):
        # เล่มพิมพ์คำนำหน้าเต็ม 'Associate Professor Dr.' → ตรวจเฉพาะชื่อ ต้องผ่าน
        reds = self._run(["Alice Adams", "Bob Brown"],
                         {1: "Associate Professor Dr. Alice Adams",
                          2: "Assistant Professor Bob Brown"})
        self.assertEqual(reds, [])


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

    def _run(self, committees, abs_en, abs_th, pages, name_en=None, translation_ok=False):
        rep = Report()
        _check_abstract_committees(rep, committees, abs_en, abs_th, pages,
                                   lambda i: f"หน้า {i}", name_en or {}, translation_ok)
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

    def test_thai_names_matched_against_ethesis(self):
        committees = {"advisory": [{"name": "คนางค์ ก", "role": ""},
                                    {"name": "ธเนศ ข", "role": ""}]}
        # สลับชื่อ → ต้องฟ้อง (เทียบไทยตรง)
        pages = ["คณะกรรมการที่ปรึกษาวิทยานิพนธ์: ธเนศ ข, ปร.ด., คนางค์ ก, พย.ด.\nบทคัดย่อ"]
        reds = self._reds(self._run(committees, [], [0], pages))
        self.assertTrue(any("สลับตำแหน่งกัน" in r for r in reds))

    def test_clean_english_abstract_passes(self):
        committees = {"advisory": [{"name": "นริศรา จันทราทิตย์", "role": ""}]}
        pages = ["THESIS ADVISORY COMMITTEE: NARISARA CHANTRATITA, Ph.D.\nABSTRACT"]
        name_en = {"นริศรา จันทราทิตย์": "Narisara Chantratita"}
        reds = self._reds(self._run(committees, [0], [], pages, name_en, translation_ok=True))
        self.assertEqual(reds, [])

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
        self.assertEqual(_committee_keyname("ธเนศ เกษศิลป์, ผู้ช่วยศาสตราจารย์"),
                         _committee_keyname("ธเนศ เกษศิลป์"))

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

    def test_duplicate_name_is_orange_not_red(self):
        rep = Report()
        expected = [{"name": "คนางค์ ก"}, {"name": "ธเนศ ข"}]
        members = {1: "คนางค์ ก", 2: "ธเนศ ข", 9: "ธเนศ ข, ผู้ช่วยศาสตราจารย์"}
        _report_thai_committee(rep, expected, members, "หน้าลงนาม")
        self.assertEqual(rep.zones["RED"], [])
        self.assertTrue(any("ปรากฏซ้ำ" in i["found"] for i in rep.zones["ORANGE"]))

    def test_real_stranger_is_still_red(self):
        rep = Report()
        expected = [{"name": "คนางค์ ก"}]
        members = {1: "คนางค์ ก", 9: "สมชาย ไม่รู้จัก"}
        _report_thai_committee(rep, expected, members, "หน้าลงนาม")
        self.assertTrue(any("ไม่อยู่ในรายชื่อ" in i["found"] for i in rep.zones["RED"]))


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


class _NoTranslit:
    """จำลองเครื่องที่ยังไม่ได้ติดตั้ง pythainlp เพื่อบังคับให้ตกไปทาง AI"""

    @staticmethod
    def romanize_names(names):
        return []

    @staticmethod
    def match_threshold():
        return 0.60


class CommitteeEnglishNameSourceTests(unittest.TestCase):
    """ชื่ออังกฤษของกรรมการได้จากการถอดชื่อไทย (ไฟล์ eThesis ไม่มีชื่ออังกฤษ)

    ทางหลักคือถอดในเครื่องด้วย translit (ฟรี ไม่ต้องใช้ API)
    ถ้าถอดในเครื่องไม่ได้จึงตกไปใช้ AI แล้วค่อยเป็น 'ไม่มีเครื่องมือ'
    """

    def test_offline_transliteration_is_the_primary_path(self):
        committees = {"advisory": [{"name": "ยอด สุขะมงคล"}]}
        name_en, ok, reason = _committee_translation(committees)
        if translit.enabled():
            self.assertTrue(ok)
            self.assertEqual(reason, "offline")   # ไม่แตะ AI เลย
            self.assertTrue(name_en["ยอด สุขะมงคล"].strip())
        else:
            self.assertFalse(ok)

    def test_reports_reason_when_no_tool_available(self):
        # ถอดในเครื่องไม่ได้ + ไม่ได้ตั้ง API key = ปัญหาการติดตั้ง ต้องบอกให้ตรงจุด
        committees = {"advisory": [{"name": "ก ข"}], "exam": [{"name": "ค ง"}]}
        sys.modules["translit"] = _NoTranslit
        try:
            name_en, ok, reason = _committee_translation(committees)
        finally:
            sys.modules.pop("translit", None)
        self.assertFalse(ok)
        self.assertEqual(name_en, {})
        self.assertEqual(reason, "no_tool")
        self.assertIn("pythainlp", _COMMITTEE_TRANSLATE_MSG[reason])

    def test_no_committees_is_not_usable(self):
        self.assertEqual(_committee_translation({}), ({}, False, ""))

    def test_duplicate_names_are_translated_once(self):
        committees = {"advisory": [{"name": "ก ข"}],
                      "exam": [{"name": "ก ข"}, {"name": "ค ง"}]}
        captured = {}

        class _Stub:
            @staticmethod
            def enabled():
                return True

            @staticmethod
            def translate_names(names):
                captured["names"] = list(names)
                return ["A B", "C D"]

        sys.modules["llm_assist"] = _Stub
        sys.modules["translit"] = _NoTranslit      # บังคับให้ตกไปทาง AI
        try:
            name_en, ok, reason = _committee_translation(committees)
        finally:
            sys.modules.pop("llm_assist", None)
            sys.modules.pop("translit", None)
        self.assertTrue(ok)
        self.assertEqual(captured["names"], ["ก ข", "ค ง"])   # ไม่ส่งชื่อซ้ำไปแปล
        self.assertEqual(name_en, {"ก ข": "A B", "ค ง": "C D"})


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

    def test_white_detection_across_colour_spaces(self):
        self.assertTrue(_is_white_fill((1,)))          # grayscale
        self.assertTrue(_is_white_fill((1, 1, 1)))     # RGB
        self.assertTrue(_is_white_fill((0, 0, 0, 0)))  # CMYK
        self.assertFalse(_is_white_fill((0, 0, 0)))
        self.assertFalse(_is_white_fill(None))


class FrontPageNumberTests(unittest.TestCase):
    """เลขหน้าส่วนนำ: เล่มอังกฤษ=โรมัน เล่มไทย=พยัญชนะ และต้องเรียงต่อเนื่อง"""

    def _run(self, labels, style=None, start=1, stop=None):
        rep = Report()
        page_labels = {i: lab for i, lab in enumerate(labels) if lab}
        _check_front_page_numbers(rep, page_labels,
                                  lambda i: f"หน้า {page_labels.get(i, '?')}",
                                  start, len(labels) if stop is None else stop, style)
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
        reds = [i["found"] for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn('ถูกใช้ซ้ำ 3 หน้า', reds[0])

    def test_skipped_label_reported(self):
        rep = self._run(["", "i", "ii", "v", "vi"], style="roman")
        reds = [i["found"] for i in rep.zones["RED"]]
        self.assertEqual(len(reds), 1)
        self.assertIn('กระโดดจาก "ii" ไป "v"', reds[0])

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
        rep = self._run(["", "i", "ii", "", "iv"], style="roman")
        self.assertEqual(rep.zones["RED"], [])
        self.assertTrue(any("อ่านเลขหน้าส่วนนำไม่ได้" in i["found"]
                            for i in rep.zones["ORANGE"]))

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
                     '"RESEARCH METHODLOGY" — ต่างที่ "METHODLOGY" → "METHODOLOGY"',
            "expected": 'ควรเป็น "RESEARCH METHODOLOGY"', "fix": "แก้การสะกด",
        }])
        text = plain_summary(report)
        self.assertIn("1. ในสารบัญ (หน้า viii) บทที่ 3:", text)
        self.assertIn('แตกต่างที่ "METHODLOGY"', text)
        self.assertIn('ให้แก้ไขเป็น: "RESEARCH METHODOLOGY"', text)
        # ห้ามมีเครื่องหมายนำรายการหรือลูกศร และไม่หลงเหลือ (typo, ...)
        self.assertNotIn("- ", text)
        self.assertNotIn("→", text)
        self.assertNotIn("typo", text)
        self.assertNotIn("[", text)

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
        self.assertEqual(text.count("ในบทที่ 2 (หน้า 6)"), 1)

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
        self.assertIn("ไม่พบจุดที่ต้องแก้ไข", plain_summary(report))
        self.assertIn("ทั้งหมด 1 จุด", plain_summary(report, failed=["YELLOW:0"]))


if __name__ == "__main__":
    unittest.main()
