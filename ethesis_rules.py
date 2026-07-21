"""Central rule registry for the deterministic e-thesis checker.

Keep policy decisions here.  ``checker.py`` implements these rules but should
not silently invent a different threshold or expand the body-content scope.
"""

# Source precedence is intentional.  The manual contains illustrative text
# that occasionally conflicts with the announcement (for example,
# "LITERATURE REVIEWS" vs. "LITERATURE REVIEW").  In a conflict, use the
# higher-ranked source and record the discrepancy instead of silently choosing.
SOURCE_PRECEDENCE = (
    "announcement_2569",
    "formatting_manual_2569",
    "official_template_2026",
    "approved_ethesis_data",
    "staff_operational_policy",
)

SOURCE_DOCUMENTS = {
    "announcement_2569": {
        "title": "ประกาศบัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล เรื่องข้อกำหนดและแนวปฏิบัติสำหรับการจัดทำรูปเล่มอิเล็กทรอนิกส์ พ.ศ. 2569",
        "file": "../PR07283.pdf",
        "effective": "2 มีนาคม 2569",
        "authority": 1,
    },
    "formatting_manual_2569": {
        "title": "คู่มือการจัดรูปแบบรูปเล่มอิเล็กทรอนิกส์",
        "file": "../คู่มือ_การจัดรูปแบบรูปเล่มอิเล็กทรอนิกส์.pdf",
        "authority": 2,
    },
    "official_template_2026": {
        "title": "Electronic File template-2026",
        "file": "../Electronic File template-2026/",
        "authority": 3,
    },
    "approved_ethesis_data": {
        "title": "ข้อมูลอนุมัติจากระบบ eThesis/เอกสาร บฑ.1",
        "file": "ข้อมูลที่เจ้าหน้าที่กรอกในแบบฟอร์ม",
        "authority": 4,
    },
    "staff_operational_policy": {
        "title": "กติกาปฏิบัติงานตรวจอย่างเข้มของเจ้าหน้าที่",
        "file": "กฎภายในระบบ",
        "authority": 5,
    },
}

RULE_CATALOG = {
    "FILE.PDF": {
        "title": "ไฟล์เดียวและเป็น PDF ที่อ่านได้",
        "basis": "ข้อกำหนดทางการ",
        "references": ("ประกาศฯ ข้อ 5.1 หน้า 7",),
    },
    "PAGE.NUMBERING": {
        "title": "การลำดับเลขหน้าและความต่อเนื่อง",
        "basis": "ประกาศฯ และคู่มือ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1.5 หน้า 4 และข้อ 4.2.2.2 หน้า 7",
            "คู่มือ หน้า 6, 16-21, 23, 42, 59, 82 และ 98",
        ),
    },
    "FRONT.STRUCTURE": {
        "title": "องค์ประกอบและลำดับของส่วนนำ",
        "basis": "ประกาศฯ และคู่มือ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1 หน้า 3-4 และข้อ 4.2.1 หน้า 6",
            "คู่มือ หน้า 7-17, 24-34, 83-91 และ 99-107",
        ),
    },
    "FRONT.COVER": {
        "title": "ข้อมูลบนหน้าปก",
        "basis": "ประกาศฯ คู่มือ และข้อมูลอนุมัติ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1.1 หน้า 3",
            "คู่มือ หน้า 10 และ 27 (ตัวอย่างรูปเล่มอังกฤษ)",
            "ข้อมูลอนุมัติจาก eThesis/บฑ.1",
        ),
    },
    "FRONT.COVER_REQUIRED": {
        "title": "ข้อความบังคับบนหน้าปก",
        "basis": "ประกาศฯ คู่มือ และ template ทางการ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1.1 หน้า 3",
            "คู่มือ หน้า 8, 10, 25, 27, 45, 47, 85, 87, 101 และ 103",
            "Electronic File template-2026 ตามประเภทและภาษาของเล่ม",
        ),
    },
    "FRONT.APPROVAL": {
        "title": "หน้าลงนาม/หน้าอนุมัติ",
        "basis": "ประกาศฯ คู่มือ และข้อมูลอนุมัติ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1.2 หน้า 3-4",
            "คู่มือ หน้า 11-12 และหน้าตัวอย่างของรูปแบบที่เลือก",
            "ข้อมูลอนุมัติจาก eThesis/บฑ.1 และ บฑ.2",
        ),
    },
    "FRONT.ACKNOWLEDGEMENTS": {
        "title": "กิตติกรรมประกาศ",
        "basis": "ประกาศฯ และคู่มือ",
        "references": ("ประกาศฯ ข้อ 4.1.1.3 หน้า 4", "คู่มือ หน้า 13 และหน้าตัวอย่างของรูปแบบที่เลือก"),
    },
    "FRONT.ACK_AUTHOR": {
        "title": "ชื่อผู้เขียนท้ายกิตติกรรมประกาศ",
        "basis": "คู่มือและ template ทางการ",
        "references": (
            "คู่มือ หน้า 13 และหน้าตัวอย่างกิตติกรรมประกาศของรูปแบบที่เลือก",
            "ข้อมูลชื่อผู้เขียนจากแบบฟอร์ม",
        ),
    },
    "FRONT.ABSTRACT": {
        "title": "ภาษา องค์ประกอบ และความยาวของบทคัดย่อ",
        "basis": "ประกาศฯ และคู่มือ",
        "references": ("ประกาศฯ ข้อ 4.1.1.4 หน้า 4", "คู่มือ หน้า 14-15 และหน้าตัวอย่างของรูปแบบที่เลือก"),
    },
    "FRONT.TOC": {
        "title": "สารบัญและเลขหน้าที่อ้างถึง",
        "basis": "ประกาศฯ และคู่มือ",
        "references": ("ประกาศฯ ข้อ 4.1.1.5-4.1.1.6 หน้า 4", "คู่มือ หน้า 16-17 และหน้าตัวอย่างของรูปแบบที่เลือก"),
    },
    "FRONT.ORDER": {
        "title": "ความครบถ้วนและลำดับของส่วนนำ",
        "basis": "ประกาศฯ คู่มือ และ template ทางการ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1 หน้า 3-4 และข้อ 4.2.1 หน้า 6",
            "คู่มือ หน้า 7, 24, 44, 61, 83 และ 99",
        ),
    },
    "FRONT.TOC_CONTENT": {
        "title": "หัวข้อสำคัญและเลขหน้าในสารบัญ",
        "basis": "ประกาศฯ คู่มือ และ template ทางการ",
        "references": (
            "ประกาศฯ ข้อ 4.1.1.5-4.1.1.6 หน้า 4",
            "คู่มือ หน้า 16-17, 33-34 และหน้าตัวอย่างของรูปแบบที่เลือก",
        ),
    },
    "BODY.OPTION1": {
        "title": "โครงสร้างแบบดั้งเดิม",
        "basis": "ประกาศฯ (ใช้เป็นหลักเมื่อข้อความในคู่มือขัดกัน)",
        "references": ("ประกาศฯ ข้อ 4.1.2 หน้า 4-5", "คู่มือ หน้า 7, 16, 18 และ 83-93"),
    },
    "BODY.OPTION2": {
        "title": "โครงสร้างแบบจากผลงานตีพิมพ์",
        "basis": "ประกาศฯ (ใช้เป็นหลักเมื่อข้อความในคู่มือขัดกัน)",
        "references": ("ประกาศฯ ข้อ 4.2.2 หน้า 6-7", "คู่มือ หน้า 24, 33-37 และ 99-111"),
    },
    "END.STRUCTURE": {
        "title": "รายการอ้างอิง ภาคผนวก และประวัติผู้วิจัย",
        "basis": "ประกาศฯ และคู่มือ",
        "references": ("ประกาศฯ ข้อ 4.1.3 หน้า 6 และข้อ 4.2.3 หน้า 7", "คู่มือ หน้า 19-21, 38-40, 94-96 และ 112-114"),
    },
    "FORMAT.BOLD": {
        "title": "หัวข้อหลักในสารบัญต้องเป็นตัวหนา",
        "basis": "คู่มือและตัวอย่างทางการ",
        "failure_zone": "ORANGE",
        "references": ("คู่มือ หน้า 6, 16, 23, 82 และ 98", "Electronic File template-2026 ตามประเภท/ภาษา/รูปแบบ"),
    },
    "FORMAT.ABSTRACT_BOLD": {
        "title": "พบข้อความตัวหนาในบทคัดย่อ",
        "basis": "กติกาปฏิบัติงานของเจ้าหน้าที่",
        "failure_zone": "YELLOW",
        "references": ("กติกาปฏิบัติงาน: แจ้งเป็นข้อสังเกต แต่ผ่านได้",),
    },
    "PAGE.BLANK": {
        "title": "หน้าว่างและความต่อเนื่องของเลขหน้า",
        "basis": "คู่มือและกติกาปฏิบัติงานของเจ้าหน้าที่",
        "failure_zone": "YELLOW",
        "references": ("คู่มือ หน้า 6, 23, 42, 59, 82 และ 98", "กติกาปฏิบัติงาน: หน้าว่างที่เลขหน้าเรียงถูกต้องผ่านได้"),
    },
    "UNCERTAIN.REVIEW": {
        "title": "ผลที่ระบบยืนยันไม่ได้ ต้องให้เจ้าหน้าที่ตรวจสอบ",
        "basis": "กติกาปฏิบัติงานของเจ้าหน้าที่",
        "failure_zone": "ORANGE",
        "references": ("กติกาปฏิบัติงาน: ข้อมูลไม่แน่ชัดให้แจ้งเจ้าหน้าที่ตรวจสอบ",),
    },
    "FORM.REQUIRED": {
        "title": "ข้อมูลอ้างอิงในแบบฟอร์มต้องครบ",
        "basis": "กติกาปฏิบัติงานของระบบ",
        "references": ("ข้อมูลอนุมัติจาก eThesis/บฑ.1", "กติกาตรวจอย่างเข้มของเจ้าหน้าที่"),
    },
    "FORM.APPROVED_MATCH": {
        "title": "ข้อมูลส่วนบุคคลและข้อมูลอนุมัติต้องตรงทุกตำแหน่งที่กำหนด",
        "basis": "ประกาศฯ คู่มือ ข้อมูลอนุมัติ และกติกาปฏิบัติงาน",
        "references": (
            "ประกาศฯ ข้อ 4.1.1.1-4.1.1.4 หน้า 3-4",
            "คู่มือ หน้า 10-16 และหน้าตัวอย่างของรูปแบบที่เลือก",
            "ข้อมูลอนุมัติจาก eThesis/บฑ.1",
            "กติกาตรวจอย่างเข้มของเจ้าหน้าที่: ผิด/ขาด/typo = สีแดง",
        ),
    },
}

DEFAULT_RULE_BY_PART = {
    "front_matter": "FRONT.STRUCTURE",
    "body": "BODY.OPTION1",
    "body/end": "PAGE.NUMBERING",
    "end_matter": "END.STRUCTURE",
    "-": "FILE.PDF",
}


def rule_reference(rule_id):
    """Return stable, display-ready provenance for one rule."""
    rule = RULE_CATALOG.get(rule_id) or RULE_CATALOG["FORM.REQUIRED"]
    return {
        "rule_id": rule_id,
        "rule_title": rule["title"],
        "rule_basis": rule["basis"],
        "rule_references": list(rule["references"]),
    }


def rule_zone(rule_id, fallback="RED"):
    """Return the centrally assigned result zone for a rule."""
    return RULE_CATALOG.get(rule_id, {}).get("failure_zone", fallback)

MATCH_RULES = {
    "title": {
        "case_sensitive": True,
        "typo_threshold": 0.90,
        "description": "ตรงข้อมูลอนุมัติทุกตัวอักษร รวมตัวพิมพ์เล็ก-ใหญ่",
    },
    "student_name": {
        "case_sensitive": False,
        "typo_threshold": 0.92,
        "description": "สะกดตรงข้อมูลอนุมัติ ไม่รวมคำนำหน้านาม",
    },
    "degree": {
        "case_sensitive": True,
        "typo_threshold": 0.90,
        "description": "ตรงชื่อปริญญาที่กำหนดสำหรับหน้าประเภทนั้น",
    },
    "toc_heading": {
        "case_sensitive": True,
        "typo_threshold": 0.90,
        "description": "หัวข้อหลักในสารบัญต้องสะกดตรงกฎกลาง",
    },
}

FRONT_MATTER_RULES = {
    "strict": True,
    "failure_zone": "RED",
    "required_form_fields": {
        # ชื่อปริญญาแยกตามตำแหน่งที่ใช้ตรวจ: ปก = ต้นฉบับ eThesis, หน้าลงนาม =
        # Sentence case (อังกฤษ) / ไทยคงเดิม, บทคัดย่อ = ตัวย่อ
        "international": (
            "title_en", "student_name", "student_id",
            "degree_cover_en", "degree_sig_en", "degree_abbr_en", "exam_date", "year",
        ),
        "thai": (
            "title_en", "title_th", "student_name", "student_name_th", "student_id",
            "degree_cover_th", "degree_sig_th", "degree_abbr_en", "degree_abbr_th",
            "exam_date", "year",
        ),
        "thai_english": (
            "title_en", "title_th", "student_name", "student_name_th", "student_id",
            "degree_cover_en", "degree_sig_en", "degree_abbr_en", "degree_abbr_th",
            "exam_date", "year",
        ),
    },
}

FORM_FIELD_LABELS = {
    "title_en": "ชื่อเรื่องภาษาอังกฤษ",
    "title_th": "ชื่อเรื่องภาษาไทย",
    "student_name": "ชื่อนักศึกษาภาษาอังกฤษ",
    "student_name_th": "ชื่อนักศึกษาภาษาไทย",
    "student_id": "รหัสนักศึกษา",
    "degree_cover_en": "ชื่อปริญญาต้นฉบับจาก eThesis (อังกฤษ) — ใช้ตรวจหน้าปก",
    "degree_cover_th": "ชื่อปริญญาต้นฉบับจาก eThesis (ไทย) — ใช้ตรวจหน้าปก",
    "degree_sig_en": "ชื่อปริญญาที่ใช้ตรวจหน้าลงนาม (อังกฤษ)",
    "degree_sig_th": "ชื่อปริญญาที่ใช้ตรวจหน้าลงนาม (ไทย)",
    "degree_abbr_en": "ชื่อปริญญาแบบย่อในบทคัดย่อ (อังกฤษ)",
    "degree_abbr_th": "ชื่อปริญญาแบบย่อในบทคัดย่อ (ไทย)",
    "exam_date": "วันที่สอบผ่าน",
    "year": "ปีบนหน้าปก",
}

# The body is not proofread semantically.  Only the first few lines are used
# to locate chapter openings, plus the top/bottom line used for page numbers.
BODY_RULES = {
    "heading_scan_lines": 4,
    "check_page_sequence": True,
    "check_toc_chapter_presence": True,
    "check_toc_page_numbers": True,
    "check_body_chapter_count": True,
    "check_toc_title_against_body": True,
    # เปิดตรวจชื่อบทในเนื้อหากับประกาศด้วย — จับกรณีสารบัญและเนื้อหาสะกดผิด
    # "ตรงกัน" ซึ่งการเทียบ body↔TOC มองไม่เห็น (นโยบายเจ้าหน้าที่: สะกดผิด
    # จนไม่ตรงประกาศ = แดงทุกตำแหน่ง ยกเว้นคำที่คู่มือรองรับ = ส้ม)
    "check_body_title_against_canonical": True,
}

CANONICAL_OPTION_1 = [
    ("บทนำ", "INTRODUCTION"),
    ("วรรณกรรมและงานวิจัยที่เกี่ยวข้อง", "LITERATURE REVIEW"),
    ("วิธีการดำเนินการวิจัย", "RESEARCH METHODOLOGY"),
    ("ผลการวิจัย", "RESULTS"),
    ("การอภิปรายผล", "DISCUSSION"),
    ("บทสรุปและข้อเสนอแนะ", "CONCLUSION AND RECOMMENDATIONS"),
]

# คำสะกดที่คู่มือ (อำนาจรองจากประกาศ) แสดงต่างจากประกาศ — นโยบายเจ้าหน้าที่:
# เจอแบบนี้ให้เป็น "ส้ม" รอเจ้าหน้าที่ยืนยัน (ไม่ใช่แดง เพราะมีเอกสารทางการรองรับ)
# ทุกการสะกดผิดแบบอื่นของชื่อบทถือว่า "ชื่อบทผิด" = แดง ทั้งในสารบัญและเนื้อหา
# key คือ (option, chapter_no)
CANONICAL_ACCEPTED_VARIANTS = {
    (1, 2): ("LITERATURE REVIEWS",),
}

CANONICAL_OPTION_2 = [
    ("บทสรุป", "SUMMARY"),
    ("ผลงานตีพิมพ์", "PUBLICATION"),
    ("เนื้อหาเพิ่มเติม", "ADDITIONAL CONTEXT"),
]

# จำนวนบทที่ "ประกาศบังคับชื่อ" ของแต่ละรูปแบบ
#   รูปแบบ 1 = ครบทั้ง 6 บท
#   รูปแบบ 2 = เฉพาะบทที่ 1 (บทสรุป) และบทที่ 2 (ผลงานตีพิมพ์)
#              บทที่ 3 มีได้แต่ไม่บังคับชื่อ — ตรวจแค่สารบัญตรงกับเนื้อหา
CANONICAL_ENFORCED_COUNT = {1: 6, 2: 2}

TOC_ALLOWED_LIST_HEADINGS = (
    "LIST OF TABLES",
    "LIST OF FIGURES",
    "LIST OF ABBREVIATIONS",
    "LIST OF ILLUSTRATIONS",
)

TYPE_MARKERS = {
    "THESIS": ("A THESIS SUBMITTED", "วิทยานิพนธ์นี้เป็นส่วนหนึ่ง"),
    "THEMATIC PAPER": ("A THEMATIC PAPER SUBMITTED", "สารนิพนธ์นี้เป็นส่วนหนึ่ง"),
    "INDEPENDENT STUDY": ("AN INDEPENDENT STUDY SUBMITTED", "การค้นคว้าอิสระนี้เป็นส่วนหนึ่ง"),
}

NOT_CHECKED = (
    "ขนาดกระดาษ ระยะขอบ ระยะบรรทัด ชนิดและขนาดฟอนต์",
    "Plagiarism หรือเปอร์เซ็นต์ความซ้ำซ้อน",
    "มาตรฐาน PDF/A, embedded fonts และความคมชัดของภาพ",
    "รายชื่อ/บทบาทกรรมการ ตำแหน่งลายเซ็น และข้อความมุมล่างขวาของหน้าลงนาม",
    "การมี/ไม่มีลายเซ็นและการตรวจความแท้จริงของลายเซ็น",
    "ขั้นตอนส่งเล่ม กำหนดเวลา และสิทธิ์การเผยแพร่",
    "การสะกด ไวยากรณ์ หรือความถูกต้องเชิงวิชาการของเนื้อหาแต่ละย่อหน้า",
)
