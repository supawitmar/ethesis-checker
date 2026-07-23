import unittest

from checker import (
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
    compare_canonical_title,
    compare_values,
    cover_required_items,
    exact_reference_status,
    fuzzy_contains,
    norm,
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

    def test_chapter_title_policy_variant_vs_wrong(self):
        # นโยบายเจ้าหน้าที่: REVIEW/REVIEWS (ประกาศ vs คู่มือ) = ส้ม (variant)
        # สะกดผิดจนไม่ใช่คำ เช่น METHODLOGY/RECOMMENDATONS = แดง (wrong) ทุกตำแหน่ง
        kind, _, _ = canonical_title_status("LITERATURE REVIEW", 2, 1)
        self.assertEqual(kind, "exact")
        kind, _, _ = canonical_title_status("LITERATURE REVIEWS", 2, 1)
        self.assertEqual(kind, "variant")
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


if __name__ == "__main__":
    unittest.main()
