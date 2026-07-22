#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
E-Thesis Staff Checker — core rule engine (no LLM).
v12: Thai-robust text matching. PDF extraction scrambles Thai combining marks
(สระบน/ล่าง วรรณยุกต์ เรียงเพี้ยน เช่น "บทคัดย่อ" → "บทคดัยอ่") so ALL Thai
comparisons are done on a normalized form that strips combining marks and
treats ำ as า. Section headings are detected from top-of-page lines only.
"""
import re
import difflib
from pathlib import Path
import pdfplumber

from ethesis_rules import (
    BODY_RULES,
    CANONICAL_ACCEPTED_VARIANTS,
    CANONICAL_ENFORCED_COUNT,
    CANONICAL_OPTION_1,
    CANONICAL_OPTION_2,
    DEFAULT_RULE_BY_PART,
    FORM_FIELD_LABELS,
    MATCH_RULES,
    FRONT_MATTER_RULES,
    NOT_CHECKED,
    TOC_ALLOWED_LIST_HEADINGS,
    TYPE_MARKERS,
    rule_reference,
    rule_zone,
)

FRONT_FAILURE_ZONE = FRONT_MATTER_RULES['failure_zone']
BOLD_FAILURE_ZONE = rule_zone("FORMAT.BOLD", "ORANGE")
ABSTRACT_BOLD_ZONE = rule_zone("FORMAT.ABSTRACT_BOLD", "YELLOW")
BLANK_PAGE_ZONE = rule_zone("PAGE.BLANK", "YELLOW")
UNCERTAIN_ZONE = rule_zone("UNCERTAIN.REVIEW", "ORANGE")

FUZZY_NAME_THRESHOLD = 0.82

# Thai combining marks: MAI HAN-AKAT, SARA I..SARA UU, PHINTHU, MAITAIKHU,
# tone marks, THANTHAKHAT, NIKHAHIT, YAMAKKAN
_TH_MARKS = re.compile('[ัิ-ฺ็-๎]')


def norm(s):
    s = (s or '').upper()
    s = s.replace('ำ', 'า')          # ำ -> า
    s = _TH_MARKS.sub('', s)                    # strip combining marks
    return re.sub(r'[^A-Zก-๙0-9]', '', s)


def soft(s):
    return re.sub(r'\s+', ' ', (s or '')).strip()


def _page_text(page):
    """ดึงข้อความหน้า PDF โดยจัดลำดับสระบน/ล่างและวรรณยุกต์ไทยให้ถูกต้อง

    pdfplumber.extract_text() เรียงอักขระตามพิกัด x ทำให้ combining mark ของไทย
    (สระบน-ล่าง/วรรณยุกต์/การันต์) หลุดไปอยู่หลังพยัญชนะตัวถัดไป เช่น "วิจัย"→"วิจยั",
    "อภิปราย"→"อภปิราย" ทำให้ข้อความที่แสดงในรายงานอ่านไม่ออก (แม้ผลตัดสินยังถูก
    เพราะ norm() ตัดวรรณยุกต์ทิ้งก่อนเทียบ)

    อาศัยข้อเท็จจริงว่า combining mark ถูกวาดต่อท้ายพยัญชนะฐานทันที จึงมี x0 ≈ x1
    ของฐานเสมอ → ผูก mark กลับเข้ากับฐานที่ขอบขวา (x1) ใกล้ x0 ของ mark ที่สุด
    แล้วประกอบใหม่เรียงตามพิกัด x  หน้าที่ไม่มี chars (หน้าภาพ/สแกน) คืน extract_text()
    """
    chars = getattr(page, 'chars', None)
    if not chars:
        return page.extract_text() or ''
    rows = {}
    for c in chars:
        rows.setdefault(round(c['top'] / 3.0), []).append(c)
    out_lines = []
    for key in sorted(rows):
        row = rows[key]
        bases = sorted((c for c in row if not _TH_MARKS.match(c['text'])),
                       key=lambda c: c['x0'])
        if not bases:
            continue
        attached = {id(b): [] for b in bases}
        for m in row:
            if _TH_MARKS.match(m['text']):
                base = min(bases, key=lambda b: abs(b['x1'] - m['x0']))
                attached[id(base)].append(m)
        parts, prev = [], None
        for b in bases:
            if prev is not None and (b['x0'] - prev['x1']) > 1.2:
                parts.append(' ')
            marks = ''.join(m['text'] for m in sorted(attached[id(b)],
                                                      key=lambda m: (m['x0'], m['top'])))
            parts.append(b['text'] + marks)
            prev = b
        line = re.sub(r' +', ' ', ''.join(parts)).replace('ํา', 'ำ').strip()
        if line:
            out_lines.append(line)
    return '\n'.join(out_lines)


def top_lines(page_text, k=10):
    return [l.strip() for l in page_text.split('\n') if l.strip()][:k]


def _is_blank_page_text(page_text):
    """Treat a page containing only its printed page label as blank content."""
    lines = [line.strip() for line in (page_text or '').splitlines() if line.strip()]
    return not any(
        not re.fullmatch(r'(?:\d{1,3}|[ivxlcdm]+|[ก-ฮ])', line, re.I)
        for line in lines
    )


def _extract_page_label(page_text):
    """Read the page label printed at the top or bottom of a document page."""
    lines = [line.strip() for line in (page_text or '').splitlines() if line.strip()]
    candidates = (lines[:1] + lines[-1:]) if lines else []
    for candidate in candidates:
        if re.fullmatch(r'\d{1,4}', candidate):
            return str(int(candidate))
        if re.fullmatch(r'[ivxlcdm]{1,10}', candidate, re.I):
            return candidate.lower()
        if re.fullmatch(r'[ก-ฮ]', candidate):
            return candidate
    return ""


def fuzzy_contains(haystack_norm, needle, threshold=FUZZY_NAME_THRESHOLD):
    n = norm(needle)
    if not n:
        return False, 0.0
    if n in haystack_norm:
        return True, 1.0
    L = len(n)
    best = 0.0
    step = max(1, L // 4)
    for i in range(0, max(1, len(haystack_norm) - L + 1), step):
        window = haystack_norm[i:i + L + step]
        r = difflib.SequenceMatcher(None, n, window).ratio()
        best = max(best, r)
        if best >= 0.999:
            break
    return best >= threshold, best


def strip_name_prefix(name):
    """Remove honorifics that must not be printed as part of the student name."""
    return re.sub(
        r'^(?:นาย|นางสาว|นาง|ดร\.?|MR\.?|MRS\.?|MISS|MS\.?|DR\.?)\s*',
        '', soft(name), flags=re.I,
    )


def person_name_sentence_case(name):
    """Convert the approved English name to the mixed-case form used in templates."""
    name = strip_name_prefix(name)
    return ' '.join(part[:1].upper() + part[1:].lower() for part in name.split())


def cover_required_items(doc_type, program_language):
    """Return display labels and exact fixed cover text required by the selected template."""
    if program_language == "thai":
        type_text = {
            "THESIS": "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
            "THEMATIC PAPER": "สารนิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
            "INDEPENDENT STUDY": "การค้นคว้าอิสระนี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
        }.get(doc_type, "")
        return (
            ("ข้อความประเภทงาน", type_text),
            ("ชื่อบัณฑิตวิทยาลัยและมหาวิทยาลัย", "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล"),
            ("ข้อความลิขสิทธิ์", "ลิขสิทธิ์ของมหาวิทยาลัยมหิดล"),
        )
    article = "AN" if doc_type == "INDEPENDENT STUDY" else "A"
    work_name = doc_type or "THESIS"
    return (
        ("ข้อความประเภทงาน", f"{article} {work_name} SUBMITTED IN PARTIAL FULFILLMENT OF THE REQUIREMENTS FOR THE DEGREE OF"),
        ("ชื่อบัณฑิตวิทยาลัย", "FACULTY OF GRADUATE STUDIES"),
        ("ชื่อมหาวิทยาลัย", "MAHIDOL UNIVERSITY"),
        ("ข้อความลิขสิทธิ์", "COPYRIGHT OF MAHIDOL UNIVERSITY"),
    )


def _best_cover_match(expected, cover_text):
    """หา 'ข้อความบนหน้าปกที่ใกล้เคียงที่สุด' กับข้อความบังคับ

    คืน (ข้อความช่วงที่พบจริงบนหน้าปก, คะแนนความใกล้เคียง 0-1) เพื่อชี้ให้เห็นว่า
    เล่มพิมพ์อะไรมา ต่างจากข้อความบังคับตรงไหน (เช่น ตก S ท้ายคำ) ไม่ใช่แค่บอกว่า
    "ไม่พบ" ลอย ๆ  หน้าปกมักตัดข้อความขึ้นหลายบรรทัด จึงเทียบแบบรวมบรรทัดเป็นคำ
    """
    flat = re.sub(r'\s+', ' ', cover_text).strip()
    expected_norm = norm(expected)
    if not flat or not expected_norm:
        return '', 0.0
    words = flat.split(' ')
    target_len = len(expected.split())
    best_ratio, best_snippet = 0.0, ''
    for size in range(max(1, target_len - 3), target_len + 4):
        for i in range(0, len(words) - size + 1):
            window = ' '.join(words[i:i + size])
            ratio = difflib.SequenceMatcher(None, norm(window), expected_norm).ratio()
            if ratio > best_ratio:
                best_ratio, best_snippet = ratio, window
    return best_snippet, best_ratio


def exact_reference_status(page_text, expected):
    """Compare approved text at one required location without hiding case changes.

    ชื่อเรื่องยาวบนหน้าปก/หน้าลงนามมักถูกตัดขึ้นหลายบรรทัด และการดึงข้อความ PDF
    อาจไม่ใส่ช่องว่างตรงรอยตัด (เช่น "FINE\nPARTICULATE" -> "FINEPARTICULATE")
    ทำให้ substring แบบตรงตัวพลาดทั้งที่ข้อความครบ จึงเทียบแบบตัดช่องว่างทิ้ง
    โดยยังคงตรวจตัวพิมพ์เล็ก-ใหญ่ได้
    """
    expected = soft(expected)
    page_flat = soft(page_text)
    if not expected:
        return True, ""
    if re.search(r'[ก-๙]', expected):
        return norm(expected) in norm(page_text), "text"
    if expected in page_flat:
        return True, "exact"
    nows = lambda s: re.sub(r'\s+', '', s)
    expected_nows, page_nows = nows(expected), nows(page_flat)
    if expected_nows in page_nows:
        # ต่างเฉพาะการตัดบรรทัด/ช่องว่าง ถือว่าข้อความถูกต้อง
        return True, "exact"
    if expected_nows.casefold() in page_nows.casefold():
        return False, "case"
    if expected.casefold() in page_flat.casefold():
        return False, "case"
    return False, "text"


def closest_text_line(page_text, expected):
    """Return a short, human-readable line closest to the approved value."""
    lines = [soft(line) for line in (page_text or '').splitlines() if soft(line)]
    if not lines:
        return "(ไม่พบข้อความ)"
    target = norm(expected)
    return max(lines, key=lambda line: difflib.SequenceMatcher(None, target, norm(line)).ratio())


def toc_page_mismatch_zone(section_kind, toc_label, appendix_labels):
    """ระดับสีเมื่อเลขหน้าของหัวข้อหลักในสารบัญไม่ตรงหน้าจริง

    หัวข้อหลักต้องตรงหน้าจริงเสมอ = แดง  ยกเว้นภาคผนวกที่มักมีหลายชุด
    (APPENDIX A/B/C...) ถ้าเลขหน้าที่สารบัญระบุเป็นหน้าเริ่มของภาคผนวกชุดอื่น
    ที่มีอยู่จริงในเล่ม ถือเป็นกรณีก้ำกึ่ง = ส้ม ให้เจ้าหน้าที่ตัดสิน
    """
    if section_kind == "appendix" and toc_label in appendix_labels:
        return "ORANGE"
    return "RED"


def closest_degree_line(page_text, expected):
    """หาข้อความชื่อปริญญาบนหน้านั้น รองรับกรณีถูกตัดขึ้นหลายบรรทัด

    ชื่อปริญญาบนหน้าปกมักถูกตัดเป็น 2-3 บรรทัด ได้หลายแบบ เช่น
      "MASTER OF SCIENCE" / "(INFORMATION TECHNOLOGY MANAGEMENT)"   (ขึ้นบรรทัดตรงวงเล็บ)
      "MASTER OF SCIENCE (WELL-BEING AND" / "SUSTAINABILITY)"        (วงเล็บเปิดค้าง)
    จึงสร้างตัวเลือกจาก "หน้าต่างบรรทัดต่อเนื่อง 1-3 บรรทัด" รอบบรรทัดที่มีคำบ่งชี้
    แล้วเลือกอันที่ใกล้เคียงข้อมูลอนุมัติที่สุด (เล่มไทยต้องมีคำบ่งชี้ไทยด้วย)
    """
    lines = [soft(line) for line in (page_text or '').splitlines() if soft(line)]
    markers = ('DEGREE', 'MASTER', 'DOCTOR', 'BACHELOR', 'MENG', 'MSC', 'PHD',
               norm('ปริญญา'), norm('มหาบัณฑิต'), norm('ดุษฎีบัณฑิต'))
    candidates = []
    for k, line in enumerate(lines):
        if not any(marker in norm(line) for marker in markers):
            continue
        for span in (1, 2, 3):
            if k + span <= len(lines):
                candidates.append(' '.join(lines[k:k + span]))
    if not candidates:
        return closest_text_line(page_text, expected)
    # ชื่อปริญญามีสาขาในวงเล็บเสมอ — ถ้ามีตัวเลือกที่วงเล็บครบให้ใช้ชุดนั้นก่อน
    balanced = [line for line in candidates if '(' in line and ')' in line]
    if balanced:
        candidates = balanced
    target = norm(expected)
    return max(candidates, key=lambda line: difflib.SequenceMatcher(None, target, norm(line)).ratio())


def compare_values(actual, expected, rule_name):
    """Apply one centrally configured matching policy to two visible values."""
    rule = MATCH_RULES[rule_name]
    actual, expected = soft(actual), soft(expected)
    if rule['case_sensitive']:
        if actual == expected:
            return {'status': 'exact', 'actual': actual, 'score': 1.0}
        if actual.casefold() == expected.casefold():
            return {'status': 'case', 'actual': actual, 'score': 1.0}
        # ภาษาไทยไม่มีตัวพิมพ์เล็ก-ใหญ่ และการดึงข้อความ PDF ทำสระ/วรรณยุกต์
        # เรียงเพี้ยนได้ จึงเทียบแบบ normalize เช่นเดียวกับ exact_reference_status
        if re.search(r'[ก-๙]', expected) and norm(actual) == norm(expected):
            return {'status': 'exact', 'actual': actual, 'score': 1.0}
    elif norm(actual) == norm(expected):
        return {'status': 'exact', 'actual': actual, 'score': 1.0}
    score = difflib.SequenceMatcher(None, norm(expected), norm(actual)).ratio()
    status = 'typo' if score >= rule['typo_threshold'] else 'mismatch'
    return {'status': status, 'actual': actual, 'score': score}


def compare_reference_text(page_text, expected, rule_name, degree_line=False):
    """Find the relevant PDF line, then classify exact/case/typo/mismatch."""
    rule = MATCH_RULES[rule_name]
    if not rule['case_sensitive'] and norm(expected) in norm(page_text):
        return {'status': 'exact', 'actual': soft(expected), 'score': 1.0}
    matched, reason = exact_reference_status(page_text, expected)
    if matched:
        return {'status': 'exact', 'actual': soft(expected), 'score': 1.0}
    actual = closest_degree_line(page_text, expected) if degree_line else closest_text_line(page_text, expected)
    compared = compare_values(actual, expected, rule_name)
    if reason == 'case':
        compared['status'] = 'case'
    return compared


def describe_diff(found, expected):
    """ชี้ว่า 'ข้อความที่พบ' ต่างจาก 'ข้อความที่ถูกต้อง' ตรงไหน อย่างไร

    - อังกฤษที่มีช่องว่าง: เทียบระดับคำ (เช่น "REQUIREMENT" → "REQUIREMENTS")
    - ไทย/คำเดียว: เทียบระดับตัวอักษร (เช่น ขาด "อ")
    คืน '' ถ้าต่างกันมากจนการชี้จุดไม่ช่วย (ให้ผู้ใช้ดูข้อความเต็มที่ให้ไว้แทน)
    """
    found_s, expected_s = soft(found), soft(expected)
    if not found_s or not expected_s or norm(found_s) == norm(expected_s):
        return ''

    def _diff(a, b, keyfn, join):
        matcher = difflib.SequenceMatcher(None, keyfn(a), keyfn(b))
        if matcher.ratio() < 0.5:
            return ''
        parts = []
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                continue
            got, want = join(a[i1:i2]), join(b[j1:j2])
            if not want:
                parts.append(f'"{got}" เกินมา (ควรตัดออก)')
            elif not got:
                parts.append(f'ขาด "{want}"')
            else:
                parts.append(f'"{got}" → "{want}"')
        return "; ".join(parts)

    # อังกฤษหลายคำ: ลองเทียบระดับคำก่อน (อ่านง่าย เห็นเป็นคำ) ถ้าทุกคำต่างกัน
    # จนเทียบไม่ได้ ค่อยตกไปเทียบระดับตัวอักษร (เช่น "LITTERATURE" ต่าง T กับ S)
    if re.search(r'[A-Za-z]', expected_s) and ' ' in expected_s.strip():
        by_word = _diff(found_s.split(), expected_s.split(),
                        lambda xs: [x.upper() for x in xs], ' '.join)
        if by_word:
            return by_word
    return _diff(list(found_s), list(expected_s), lambda xs: xs, ''.join)


def mismatch_detail(label, compared, expected=''):
    """Make small differences visible instead of silently accepting fuzzy matches.

    ถ้าส่ง expected มาด้วย จะต่อท้ายว่า "ต่างที่ ..." ชี้ตำแหน่ง/วิธีที่ผิด
    """
    if compared['status'] == 'case':
        detail = f'{label}ตัวพิมพ์เล็ก-ใหญ่ไม่ตรง: "{compared["actual"]}"'
    elif compared['status'] == 'typo':
        detail = (f'{label}พิมพ์ผิดเล็กน้อย (typo, ความใกล้เคียง {compared["score"]:.2f}): '
                  f'"{compared["actual"]}"')
    else:
        detail = f'{label}ข้อความไม่ตรง: "{compared["actual"]}"'
    # ชี้จุดต่างเฉพาะเมื่อใกล้เคียงกัน (typo/ตัวพิมพ์) — ถ้าเป็นคนละข้อความ
    # (mismatch) การไล่ทีละตัวอักษรจะรกและสับสน ให้ดูข้อความที่ถูกต้องแทน
    if expected and compared['status'] in ('typo', 'case'):
        diff = describe_diff(compared['actual'], expected)
        if diff:
            detail += f' — ต่างที่ {diff}'
    return detail


def _is_bold_font(fontname):
    font = (fontname or '').upper()
    return any(marker in font for marker in ('BOLD', 'BLACK', 'SEMIBOLD', 'DEMI'))


def _font_lines(pdf_page, tolerance=2.5):
    """Group extracted PDF words into visual lines and calculate their bold ratio."""
    words = sorted(
        pdf_page.extract_words(extra_attrs=['fontname']) or [],
        key=lambda word: (float(word.get('top', 0)), float(word.get('x0', 0))),
    )
    grouped = []
    for word in words:
        top = float(word.get('top', 0))
        if not grouped or abs(grouped[-1]['top'] - top) > tolerance:
            grouped.append({'top': top, 'words': [word]})
        else:
            grouped[-1]['words'].append(word)
    results = []
    for group in grouped:
        line_words = sorted(group['words'], key=lambda word: float(word.get('x0', 0)))
        text = ' '.join(word.get('text', '') for word in line_words).strip()
        if not text:
            continue
        heading_words = list(line_words)
        if heading_words and re.fullmatch(r'(?:\d+|[IVXLCDM]+)', heading_words[-1].get('text', ''), re.I):
            heading_words = heading_words[:-1]
        total = sum(len(re.sub(r'\s+', '', word.get('text', ''))) for word in heading_words)
        bold = sum(
            len(re.sub(r'\s+', '', word.get('text', '')))
            for word in heading_words if _is_bold_font(word.get('fontname'))
        )
        results.append({'text': text, 'bold_ratio': (bold / total if total else 0.0)})
    return results


def _is_toc_major_heading(text):
    base = re.sub(r'\s+(?:\d+|[ivxlcdm]+)\s*$', '', soft(text), flags=re.I)
    normalized = norm(base)
    return (
        normalized in N_ACK + N_TOC + N_LISTS + N_BIO + [N_ABSTRACT_TH, 'ABSTRACT']
        or normalized.startswith(N_ABSTRACT_TH)
        or normalized.startswith('ABSTRACT')
        or normalized.startswith('LISTOF')
        or any(normalized.startswith(term) for term in N_REF)
        or bool(re.match(r'^(CHAPTER|บทท)\d{1,2}', normalized))
    )


def _strip_toc_page_number(text):
    return re.sub(r'\s+(?:\d+|[ivxlcdm]+|[ก-ฮ])\s*$', '', soft(text), flags=re.I)


def _toc_page_label(text):
    """Return the page label printed at the end of one TOC entry."""
    match = re.search(r'\s(\d{1,4}|[ivxlcdm]+|[ก-ฮ])\s*$', soft(text), re.I)
    if not match:
        return ""
    label = match.group(1)
    return str(int(label)) if label.isdigit() else label.lower()


def _toc_section_kind(text):
    """Classify one non-chapter TOC entry using its visible heading."""
    normalized = norm(_strip_toc_page_number(text))
    if normalized in N_ACK:
        return "ack"
    if normalized.startswith(norm("บทคัดย่อภาษาอังกฤษ")):
        return "abstract_en"
    if normalized == N_ABSTRACT_TH or normalized.startswith(norm("บทคัดย่อภาษาไทย")):
        return "abstract_th"
    if normalized.startswith("ABSTRACTTHAI"):
        return "abstract_th"
    if normalized == "ABSTRACT" or normalized.startswith("ABSTRACTENGLISH"):
        return "abstract_en"
    if normalized in (norm("สารบัญตาราง"), "LISTOFTABLES"):
        return "list_tables"
    if normalized in (norm("สารบัญรูป"), norm("สารบัญรูปภาพ"), norm("สารบัญภาพ"), "LISTOFFIGURES", "LISTOFILLUSTRATIONS"):
        return "list_figures"
    if normalized in (norm("คำย่อ"), norm("คำอธิบายสัญลักษณ์/คำย่อ"), "LISTOFABBREVIATIONS"):
        return "list_abbreviations"
    if any(normalized.startswith(term) for term in N_REF):
        return "references"
    if normalized in N_BIO:
        return "biography"
    if any(normalized.startswith(term) for term in N_APPENDIX):
        return "appendix"
    return ""


def _is_abstract_heading(text):
    """หัวเรื่อง 'บทคัดย่อ'/'ABSTRACT' เป็นตัวหนาตาม template อยู่แล้ว ไม่ใช่ข้อสังเกต"""
    nl = norm(_strip_toc_page_number(text))
    return (nl in ('ABSTRACT', 'ABSTRACTTHAI', 'ABSTRACTENGLISH')
            or nl == N_ABSTRACT_TH
            or nl.startswith(norm('บทคัดย่อภาษา')))


def _toc_chapter_title(text):
    """Return only the visible chapter title, without chapter/page numbers.

    PDF ภาษาไทยมักดึง "บทที่ 1" ออกมาเป็น "บทท ี่ 1" (สระ/วรรณยุกต์หลุดจากตำแหน่ง)
    จึงยอมรับ combining mark และช่องว่างแทรกระหว่างคำนำหน้ากับเลขบท
    """
    return re.sub(
        r'^(?:CHAPTER|บทท)[ั-๎\s.]*(?:\d+\s*|[IVXL]+\s+)',
        '',
        _strip_toc_page_number(text),
        flags=re.I,
    ).strip()


# ---------- normalized heading keys ----------
N_ABSTRACT_TH = norm('บทคัดย่อ')
N_ACK = [norm('กิตติกรรมประกาศ'), 'ACKNOWLEDGEMENT', 'ACKNOWLEDGEMENTS']
N_TOC = [norm('สารบัญ'), 'TABLEOFCONTENTS', 'CONTENTS']
N_LISTS = [norm('สารบัญตาราง'), norm('สารบัญรูป'), norm('สารบัญรูปภาพ'), norm('สารบัญภาพ'),
           norm('คำย่อ'), norm('คำอธิบายสัญลักษณ์/คำย่อ'),
           'LISTOFTABLES', 'LISTOFFIGURES', 'LISTOFABBREVIATIONS', 'LISTOFILLUSTRATIONS']
N_ENTITLED = ['ENTITLED', norm('เรื่อง')]
N_REF = ['REFERENCES', 'REFERENCE', 'BIBLIOGRAPHY', norm('รายการอ้างอิง'), norm('บรรณานุกรม')]
N_BIO = ['BIOGRAPHY', norm('ประวัติผู้วิจัย'), norm('ประวัติผู้เขียน')]
N_APPENDIX = ['APPENDIX', 'APPENDICES', norm('ภาคผนวก')]

CANONICAL_OPT1 = CANONICAL_OPTION_1
CANONICAL_OPT2 = CANONICAL_OPTION_2


def compare_canonical_title(actual_title, canonical_pair):
    """เทียบชื่อบทกับชื่อมาตรฐานทั้ง 2 ภาษา แล้วรายงานด้วยภาษาที่ใกล้ที่สุด

    ชื่อบทมาตรฐานเป็นคู่ (ไทย, อังกฤษ) — เล่มไทยต้องเทียบชื่อไทย เล่มอังกฤษ
    เทียบชื่ออังกฤษ การเทียบข้างเดียวทำให้เล่มไทยถูกฟ้องว่า "ควรเป็น INTRODUCTION"
    """
    return max(
        ((compare_values(actual_title, candidate, 'toc_heading'), candidate)
         for candidate in canonical_pair),
        key=lambda pair: (pair[0]['status'] == 'exact', pair[0]['score']),
    )


def canonical_title_status(actual_title, chapter_no, option):
    """จัดชั้นชื่อบทเทียบประกาศ: exact | variant (ตามคู่มือ = ส้ม) | wrong (= แดง)"""
    canon = CANONICAL_OPT1 if option == 1 else CANONICAL_OPT2
    compared, expected = compare_canonical_title(actual_title, canon[chapter_no - 1])
    if compared['status'] == 'exact':
        return 'exact', compared, expected
    for variant in CANONICAL_ACCEPTED_VARIANTS.get((option, chapter_no), ()):
        if norm(actual_title) == norm(variant):
            return 'variant', compared, expected
    return 'wrong', compared, expected


def _roman_to_int(text):
    """แปลงเลขโรมัน (I–XLIX) เป็นจำนวนเต็ม คืน None ถ้าไม่ใช่/เกินช่วงเลขบท

    บางเล่มใช้เลขโรมันในหัวบท/สารบัญ (CHAPTER II) แทนเลขอารบิก (CHAPTER 2)
    ทั้งสองแบบถูกต้องตามรูปแบบของบัณฑิตวิทยาลัย
    """
    values = {'I': 1, 'V': 5, 'X': 10, 'L': 50}
    s = text.upper()
    if not s or any(ch not in values for ch in s):
        return None
    total, prev = 0, 0
    for ch in reversed(s):
        v = values[ch]
        total += -v if v < prev else v
        prev = v
    return total if 1 <= total <= 49 else None


def _chapter_match(line):
    """Return chapter number if the (normalized) line is 'CHAPTER n' / 'บทที่ n'.

    รองรับทั้งเลขอารบิก (CHAPTER 2) และเลขโรมัน (CHAPTER II)
    """
    nl = norm(line)
    m = re.fullmatch(r'(CHAPTER|บทท)([IVXL]+|\d{1,2})', nl)
    if not m:
        return None
    num = m.group(2)
    return int(num) if num.isdigit() else _roman_to_int(num)


def resolve_option(body_ch, approved, chapters_mode):
    """Resolve document option without forcing canonical titles in free mode.

    รูปแบบตีพิมพ์ (option 2) ขึ้นต้นเล่มด้วยบท "บทสรุป/SUMMARY" — ต้องดูเฉพาะ
    บทที่ 1 เท่านั้น เพราะเล่มรูปแบบดั้งเดิมจบด้วย "บทสรุปและข้อเสนอแนะ"
    ซึ่งขึ้นต้นเหมือนกัน หากกวาดทุกบทจะเดาเล่มไทยที่ถูกต้องเป็นรูปแบบ 2
    """
    first_chapter = next((c for c in body_ch if c[0] == 1), None)
    inferred = 2 if first_chapter is not None and (
        norm(first_chapter[1]).startswith(norm(CANONICAL_OPT2[0][0]))
        or norm(first_chapter[1]).startswith(norm(CANONICAL_OPT2[0][1]))
    ) else 1
    selected = str((approved or {}).get("format", ""))
    if chapters_mode == "free" and selected in {"1", "2"}:
        return int(selected)
    return inferred




def classify(issue):
    f, e, loc = issue.get("found", ""), issue.get("expected", ""), issue.get("location", "")
    text = f + " " + e + " " + loc
    if "พิมพ์ผิดเล็กน้อย" in text or "typo" in text.lower():
        return "สะกดผิดเล็กน้อย (typo)"
    if "ตัวอักษรหนา" in text or "ตัวหนา" in text:
        return "รูปแบบตัวอักษร"
    if "รหัสนักศึกษา" in text:
        return "ข้อมูลนักศึกษาไม่ถูกต้อง"
    if "ชื่อปริญญา" in text:
        return "ชื่อปริญญาไม่ตรงข้อมูลอนุมัติ"
    if "คำนำหน้านาม" in text:
        return "คำนำหน้านาม"
    if "Keywords" in text or "คำสำคัญ" in text:
        return "เกินจำนวนที่กำหนด"
    if "กินพื้นที่" in text:
        return "เกินจำนวนหน้า"
    if "ระบุจำนวนหน้า" in text or "จำนวนหน้ารวม" in text:
        return "จำนวนหน้าไม่ตรง"
    if "เลขหน้า" in text:
        return "เลขหน้า"
    if "ชื่อบท" in text and "ประกาศ" in text:
        return "ชื่อบทไม่ตรงประกาศ"
    if "ชื่อบท" in text:
        return "สะกดผิด (typo)"
    if "ชื่อเรื่อง" in text:
        return "ชื่อเรื่องไม่ตรง บฑ.1"
    if "สะกด" in text or "คะแนน" in f:
        return "สะกดผิด (typo)"
    if "ไม่พบ" in f or "หาหน้า" in f or "ไม่ได้กรอก" in f:
        return "ขาดหาย/ไม่พบ"
    if "บทคัดย่อ: EN" in f or "ภาษาไทย" in text or "ภาษาอังกฤษ" in text:
        return "ภาษาไม่ครบตามหลักสูตร"
    if "สารบัญ" in text or "บท" in text or "หน้าลงนาม" in text or "BIOGRAPHY" in text:
        return "โครงสร้างเล่ม"
    if "รูปแบบ" in text or "ประเภท" in text:
        return "ไม่ตรงข้อมูลอนุมัติ"
    return "อื่นๆ"


class Report:
    def __init__(self):
        self.zones = {"RED": [], "ORANGE": [], "YELLOW": []}
        self.info = []
        self.human_checklist = []
        self.verification = []

    def add_verification(self, topic, location, status, detail=""):
        """บันทึกผลเทียบข้อมูลอนุมัติรายตำแหน่ง — status: pass | fail | pending"""
        group = next((g for g in self.verification if g["topic"] == topic), None)
        if group is None:
            group = {"topic": topic, "checks": []}
            self.verification.append(group)
        group["checks"].append({"location": location, "status": status,
                                "detail": soft(detail)})

    def add(self, zone, part, loc, found, expected, fix="", rule_id=None):
        rule_id = rule_id or DEFAULT_RULE_BY_PART.get(part, "FORM.REQUIRED")
        fix = fix or f"แก้ไขให้เป็นไปตามข้อกำหนด: {expected}"
        self.zones[zone].append({
            "part": part,
            "location": loc,
            "found": found,
            "expected": expected,
            "fix": fix,
            **rule_reference(rule_id),
        })

    def add_info(self, part, topic, detail):
        self.info.append({"part": part, "topic": topic, "detail": detail})

    def add_human(self, item, why, rule_id="FRONT.APPROVAL"):
        self.human_checklist.append({"item": item, "why": why, **rule_reference(rule_id)})

    def verdict(self):
        if self.zones["RED"]:
            return "ไม่ผ่าน"
        if self.zones["ORANGE"]:
            return "รอยืนยัน"
        return "ผ่าน"


def run_check(pdf_path, approved, chapters_mode="strict", progress=None):
    def _p(msg):
        if progress:
            try:
                progress(msg)
            except Exception:
                pass

    rep = Report()
    if not str(pdf_path).lower().endswith(".pdf"):
        rep.add("ORANGE", "-", Path(pdf_path).name, "ไม่ใช่ไฟล์ PDF", "ระบบตรวจ PDF เท่านั้น", "ส่งไฟล์ PDF")
        return {"verdict": rep.verdict(), "issues_by_zone": rep.zones, "info": rep.info,
                "human_checklist": rep.human_checklist, "not_checked": NOT_CHECKED,
                "verification": rep.verification,
                "summary": {z.lower(): len(v) for z, v in rep.zones.items()}, "context": {}}

    _p("เปิดไฟล์ PDF")
    pages = []
    with pdfplumber.open(pdf_path) as _pdf:
        n = len(_pdf.pages)
        if n == 0:
            rep.add("ORANGE", "-", Path(pdf_path).name, "ไฟล์ PDF ไม่มีหน้าเอกสาร",
                    "ต้องเป็น PDF ที่มีเนื้อหาอย่างน้อย 1 หน้า", "สร้างไฟล์ PDF ใหม่แล้วลองอีกครั้ง")
            return {"verdict": rep.verdict(), "issues_by_zone": rep.zones, "info": rep.info,
                    "human_checklist": rep.human_checklist, "not_checked": NOT_CHECKED,
                    "verification": rep.verification,
                    "summary": {z.lower(): len(v) for z, v in rep.zones.items()}, "context": {"n_pages": 0}}
        for _i, _pg in enumerate(_pdf.pages):
            if _i % 5 == 0 or _i == n - 1:
                _p(f"อ่านข้อความแบบละเอียด (หน้า {_i+1}/{n})")
            pages.append(_page_text(_pg))
            try:
                _pg.flush_cache()
            except Exception:
                pass

    all_norm = norm("\n".join(pages))
    doc_type = next((t for t, ms in TYPE_MARKERS.items()
                     if any(norm(m) in all_norm for m in ms)), None)

    # ---------- แผนที่ section ส่วนนำ (จากหัวเรื่องบนหน้าเท่านั้น) ----------
    _p("ระบุตำแหน่ง section ส่วนนำ")
    front_limit = min(n, 20)
    sig_pages, abs_th_pages, abs_en_pages, ack_pages, toc_pages, list_pages = [], [], [], [], [], []
    for i in range(front_limit):
        tls = top_lines(pages[i], 12)
        nls = [norm(l) for l in tls]
        if any(x in N_ENTITLED for x in nls):
            sig_pages.append(i)
            continue
        # สแกนหัวเรื่องให้ลึกพอ — เล่มที่ชื่อเรื่องยาว 3-4 บรรทัด คำว่า ABSTRACT
        # จะไปอยู่บรรทัดที่ 9-10 ของหน้า ถ้าสแกนตื้นจะหาหน้าบทคัดย่อไม่เจอ
        for j, nl in enumerate(nls[:12]):
            if nl == N_ABSTRACT_TH:
                abs_th_pages.append(i); break
            if nl == 'ABSTRACT' or re.match(r'^ABSTRACT\(', nl):
                abs_en_pages.append(i); break
            if nl in N_ACK:
                ack_pages.append(i); break
            if nl in N_TOC:
                toc_pages.append(i); break
            if nl in N_LISTS:
                list_pages.append(i); break

    abs_th_idx = abs_th_pages[0] if abs_th_pages else None
    abs_en_idx = abs_en_pages[0] if abs_en_pages else None
    has_th_abs, has_en_abs = abs_th_idx is not None, abs_en_idx is not None
    if not ack_pages:
        rep.add("RED", "front_matter", "ส่วนนำ", "ไม่พบกิตติกรรมประกาศ",
                "ส่วนนำต้องมีกิตติกรรมประกาศ", "เพิ่มกิตติกรรมประกาศก่อนบทคัดย่อ",
                "FRONT.ORDER")

    # ---------- เลขหน้า ----------
    _p("ตรวจเลขหน้าและความต่อเนื่อง")
    page_labels = {i: label for i, text in enumerate(pages)
                   if (label := _extract_page_label(text))}
    printed = {i: int(label) for i, label in page_labels.items() if label.isdigit()}

    def page_ref(page_index):
        label = page_labels.get(page_index, "")
        return f"หน้า {label}" if label else "หน้าไม่ระบุเลข"

    seq = sorted(printed.items())
    arabic_sequence_ok = bool(seq) and seq[0][1] == 1 and all(
        seq[k][1] == seq[k - 1][1] + 1 for k in range(1, len(seq))
    )
    if BODY_RULES['check_page_sequence']:
        if seq and seq[0][1] != 1:
            rep.add("RED", "body", page_ref(seq[0][0]), f"เลขหน้าอารบิกแรกที่พบคือ {seq[0][1]}",
                    "เลขหน้าอารบิกต้องเริ่มที่ 1 ณ บทที่ 1", "แก้การตั้งเลขหน้า", "PAGE.NUMBERING")
        for k in range(1, len(seq)):
            a, b = seq[k-1][1], seq[k][1]
            if b != a + 1:
                rep.add("RED", "body/end", f"ช่วงเลขหน้า {a}→{b}", f"เลขหน้ากระโดดจาก {a} ไป {b}",
                        "เลขหน้าต้องต่อเนื่อง ไม่ซ้ำ ไม่ข้าม", "", "PAGE.NUMBERING")
    last_arabic = max(printed.values()) if printed else None

    # หน้าว่าง: ถ้ายืนยันเลขหน้าอารบิกและลำดับต่อเนื่องได้ เป็นเพียงข้อสังเกต
    # หากไม่มีเลขหน้าที่อ่านได้ ให้เจ้าหน้าที่ตรวจสอบแทนการฟันธง
    # หน้าที่ไม่มีข้อความให้ดึงเลยมักเป็นหน้ารูปภาพ/สแกน (เช่น ภาคผนวก)
    # จึงรวมหน้าติดกันเป็นรายการเดียว ไม่ฟ้องแยกทีละหน้า
    blank_runs = []
    for blank_idx, page_text in enumerate(pages):
        if not _is_blank_page_text(page_text):
            continue
        if blank_runs and blank_runs[-1][-1] == blank_idx - 1:
            blank_runs[-1].append(blank_idx)
        else:
            blank_runs.append([blank_idx])
    for run in blank_runs:
        run_ref = page_ref(run[0]) if len(run) == 1 else \
            f"{page_ref(run[0])}–{page_ref(run[-1])} ({len(run)} หน้า)"
        image_like = any(not (pages[i] or '').strip() for i in run)
        kind = ("ไม่มีข้อความให้ดึงเลย อาจเป็นหน้ารูปภาพ/สแกน เช่น ภาคผนวก"
                if image_like else "มีเฉพาะเลขหน้า อาจเป็นหน้าว่างที่ตั้งใจเว้น")
        if all(i in printed for i in run) and arabic_sequence_ok:
            rep.add(BLANK_PAGE_ZONE, "body/end", run_ref,
                    f"พบหน้าที่ระบบดึงข้อความไม่ได้ ({kind}) แต่เลขหน้าเรียงต่อเนื่องถูกต้อง",
                    "หน้าลักษณะนี้ที่การเรียงเลขหน้ายังคงถูกต้องเป็นข้อสังเกตและผ่านได้",
                    "ตรวจว่าเป็นหน้าภาพหรือหน้าว่างที่ตั้งใจเว้นไว้", "PAGE.BLANK")
        else:
            rep.add(UNCERTAIN_ZONE, "-", run_ref,
                    f"พบหน้าที่ระบบดึงข้อความไม่ได้ ({kind}) และยืนยันลำดับเลขหน้าไม่ได้",
                    "เจ้าหน้าที่ตรวจสอบว่าเป็นหน้าภาพ/หน้าว่าง และเลขหน้ายังเรียงถูกต้อง",
                    "ตรวจด้วยตา", "UNCERTAIN.REVIEW")

    # เลขหน้าลงนาม i/ii หรือ ก/ข
    if len(sig_pages) != 2:
        rep.add(FRONT_FAILURE_ZONE, "front_matter", "หน้าลงนาม",
                f"พบหน้าลงนาม {len(sig_pages)} หน้า", "ต้องมี 2 หน้า (Advisory + Examination)",
                "ตรวจด้วยตา", "FRONT.APPROVAL")
    expected_labels = [("i", "ก"), ("ii", "ข")]
    for k, i2 in enumerate(sig_pages[:2]):
        lines2 = [l.strip() for l in pages[i2].split('\n') if l.strip()]
        tokens = (lines2[:1] + lines2[-1:]) if lines2 else []
        lab_en, lab_th = expected_labels[k]
        if not any(t.lower() == lab_en or norm(t) == norm(lab_th) for t in tokens):
            rep.add(FRONT_FAILURE_ZONE, "front_matter", f"หน้าลงนามหน้า {k+1} ({page_ref(i2)})",
                    f"ระบบไม่พบเลขหน้า \"{lab_en}\" หรือ \"{lab_th}\" บนหัว/ท้ายหน้า",
                    f"หน้าลงนามหน้า {k+1} ต้องมีเลขหน้า {lab_en} (อังกฤษ) หรือ {lab_th} (ไทย)",
                    "ตรวจด้วยตา — PDF บางไฟล์ดึงเลขหน้าไม่ได้", "PAGE.NUMBERING")

    # ---------- สารบัญ ↔ บท ----------
    _p("ตรวจสารบัญและชื่อบท")
    # สารบัญอาจยาวหลายหน้า — สแกนตั้งแต่หน้าแรกของสารบัญไปจนถึง section ถัดไป
    # ของส่วนนำ (เช่น LIST OF TABLES) ไม่ใช่แค่หน้าเดียวถัดจากหน้าสารบัญ
    if toc_pages:
        toc_start = toc_pages[0]
        front_boundaries = sorted(set(
            sig_pages + abs_th_pages + abs_en_pages + ack_pages + list_pages))
        after_toc = [b for b in front_boundaries if b > toc_start]
        toc_stop = min(after_toc) if after_toc else toc_start + 3
        toc_page_indices = list(range(toc_start, min(toc_stop, toc_start + 4, n)))
    else:
        toc_page_indices = []
    toc_lines = [(page_idx, line) for page_idx in toc_page_indices
                 for line in pages[page_idx].split('\n')]
    toc_text = "\n".join(line for _page_idx, line in toc_lines)
    toc_entries = []
    for source_page_idx, line in toc_lines:
        kind = _toc_section_kind(line)
        if kind:
            toc_entries.append({
                "kind": kind,
                "source_page_idx": source_page_idx,
                "raw": line.strip(),
                "page_label": _toc_page_label(line),
            })
    toc_ch = []   # (chap_no, title_norm, page_no, raw_line, source_page_idx)
    for source_page_idx, line in toc_lines:
        raw = line.strip()
        if not raw:
            continue
        m_pg = re.search(r'(\d{1,3})\s*$', raw)
        nl = norm(raw)
        m_ch = re.match(r'^(CHAPTER|บทท)(\d{1,2})', nl)
        if m_ch:
            chap_no = int(m_ch.group(2))
            title_n = nl[m_ch.end():]
            if m_pg:
                title_n = re.sub(r'\d+$', '', title_n)
        else:
            # เลขโรมัน (เช่น "CHAPTER II LITERATURE REVIEWS") — norm ตัดช่องว่างทำให้
            # เลขบทติดกับชื่อบท (II+INTRODUCTION) จึงต้องอ่านจากบรรทัดดิบที่ยังมี
            # ช่องว่างคั่นเลขบทกับชื่อบท
            head = raw[:m_pg.start()] if m_pg else raw
            m_r = re.match(r'^\s*(?:CHAPTER|บทท[ีิ่\s]*)\s*([IVXL]+)\s+(.+)$',
                           head, re.I)
            chap_no = _roman_to_int(m_r.group(1)) if m_r else None
            if chap_no is None:
                continue
            title_n = norm(m_r.group(2))
        if not title_n:
            continue
        toc_ch.append((chap_no, title_n,
                       int(m_pg.group(1)) if m_pg else None, raw,
                       source_page_idx))

    body_ch = []  # (chap_no, title_raw, pdf_idx, printed_no)
    for i, t in enumerate(pages):
        tls = top_lines(t, BODY_RULES['heading_scan_lines'])
        for j, l in enumerate(tls):
            cn = _chapter_match(l)
            if cn is not None and j + 1 < len(tls):
                title = tls[j+1]
                if not re.match(r'\d', title):
                    body_ch.append((cn, title, i, printed.get(i)))
                break
    rep.add_info("body", "บทที่พบในเนื้อหา",
                 [f"บทที่ {c[0]}: {c[1]} ({page_ref(c[2])})" for c in body_ch])

    if toc_ch:
        if BODY_RULES['check_toc_chapter_presence'] and len(toc_ch) != len(body_ch):
            rep.add("RED", "body", "สารบัญ vs เนื้อหา",
                    f"สารบัญมี {len(toc_ch)} บท เนื้อหามี {len(body_ch)} บท",
                    "จำนวนบทต้องเท่ากัน", "อัปเดตสารบัญหรือเนื้อหา", "FRONT.TOC")
        toc_map = {c[0]: (c[1], c[2], c[3], c[4]) for c in toc_ch}
        for cn, title, ppage, pno in body_ch:
            if cn in toc_map:
                t_title_n, t_pno, t_raw, toc_page_idx = toc_map[cn]
                nb = norm(title)
                if BODY_RULES['check_toc_title_against_body'] and t_title_n != nb:
                    toc_title = _toc_chapter_title(t_raw)
                    compared = compare_values(title, toc_title, 'toc_heading')
                    rep.add("RED", "body", f"บทที่ {cn} ({page_ref(ppage)})",
                            mismatch_detail("ชื่อบทในเนื้อหา", compared, toc_title),
                            f'ต้องสะกดตรงกับชื่อบทในสารบัญ: "{toc_title}"',
                            "แก้ชื่อบทในเนื้อหาหรือสารบัญให้ตรงกัน", "FRONT.TOC")
                if BODY_RULES['check_toc_page_numbers'] and t_pno is None:
                    rep.add("RED", "front_matter", f"สารบัญ ({page_ref(toc_page_idx)}) บทที่ {cn}",
                            f"หัวข้อ \"{t_raw}\" ไม่มีเลขหน้า",
                            "หัวข้อบทในสารบัญต้องระบุเลขหน้า", "เพิ่มเลขหน้าให้ตรงกับบทจริง", "FRONT.TOC")
                elif BODY_RULES['check_toc_page_numbers'] and pno is not None and t_pno != pno:
                    rep.add("RED", "body", f"สารบัญ ({page_ref(toc_page_idx)}) ↔ บทที่ {cn} ({page_ref(ppage)})",
                            f"สารบัญระบุหน้า {t_pno} แต่บทอยู่จริงหน้า {pno}",
                            "เลขหน้าในสารบัญต้องตรงตำแหน่งจริง", "อัปเดตสารบัญ", "FRONT.TOC")
            elif BODY_RULES['check_toc_chapter_presence']:
                rep.add("RED", "body", f"บทที่ {cn} ({page_ref(ppage)})", "ไม่อยู่ในสารบัญ",
                        "ทุกบทต้องปรากฏในสารบัญ", "", "FRONT.TOC")
    else:
        toc_problem = "ไม่พบรายการบทในสารบัญ" if toc_pages else "ไม่พบหน้าสารบัญ"
        rep.add("RED", "front_matter",
                f"สารบัญ ({page_ref(toc_pages[0])})" if toc_pages else "ส่วนนำ",
                toc_problem, "ส่วนนำต้องมีสารบัญและระบุบททุกบทพร้อมเลขหน้า",
                "เพิ่มหรืออัปเดตสารบัญให้ครบ", "FRONT.TOC_CONTENT")

    # หัวข้อระดับหลักในสารบัญต้องเป็นตัวหนา (ไม่บังคับหัวข้อย่อย 1.1, 1.2, ...)
    toc_scan_pages = toc_page_indices
    if toc_scan_pages:
        try:
            with pdfplumber.open(pdf_path) as _pl:
                for toc_idx in toc_scan_pages:
                    nonbold = []
                    for line in _font_lines(_pl.pages[toc_idx]):
                        if _is_toc_major_heading(line['text']) and line['bold_ratio'] < 0.8:
                            nonbold.append(re.sub(r'\s+(?:\d+|[ivxlcdm]+)\s*$', '', line['text'], flags=re.I))
                    if nonbold:
                        rep.add(
                            BOLD_FAILURE_ZONE, "front_matter", f"สารบัญ ({page_ref(toc_idx)})",
                            "หัวข้อหลักไม่เป็นตัวหนา: " + ", ".join(nonbold),
                            "ACKNOWLEDGEMENTS, ABSTRACT, LIST OF ..., ชื่อบท, REFERENCE(S) และ BIOGRAPHY ต้องเป็นตัวหนา",
                            "ตั้งหัวข้อระดับหลักในสารบัญเป็นตัวหนา",
                            "FORMAT.BOLD",
                        )
        except Exception:
            rep.add("ORANGE", "front_matter", "สารบัญ",
                    "ระบบอ่านรูปแบบตัวหนาในสารบัญไม่ได้", "หัวข้อหลักในสารบัญต้องเป็นตัวหนา",
                    "ตรวจด้วยตา", "FORMAT.BOLD")

    # ชื่อบทตามประกาศ
    option = resolve_option(body_ch, approved, chapters_mode)

    # ตรวจ typo เฉพาะหัวข้อหลักในสารบัญ ไม่อ่านหรือพิสูจน์อักษรเนื้อหาแต่ละย่อหน้า
    for toc_page_idx, raw in toc_lines:
        visible = _strip_toc_page_number(raw)
        if norm(visible).startswith('LISTOF'):
            expected = max(
                TOC_ALLOWED_LIST_HEADINGS,
                key=lambda candidate: difflib.SequenceMatcher(None, norm(candidate), norm(visible)).ratio(),
            )
            compared = compare_values(visible, expected, 'toc_heading')
            if compared['status'] != 'exact':
                rep.add("RED", "front_matter", f"สารบัญ ({page_ref(toc_page_idx)})",
                        mismatch_detail("หัวข้อสารบัญ", compared, expected),
                        f"ควรเป็น \"{expected}\"", "แก้การสะกดหัวข้อสารบัญ", "FRONT.TOC")

    # ประกาศบังคับชื่อบทเท่าที่กำหนดไว้: รูปแบบ 1 ครบ 6 บท, รูปแบบ 2 เฉพาะบท 1-2
    # (บทที่ 3 ของรูปแบบ 2 ไม่บังคับชื่อ — ตรวจแค่สารบัญตรงกับเนื้อหา)
    enforced_chapters = CANONICAL_ENFORCED_COUNT.get(option, 0)

    if chapters_mode == 'strict':
        for chapter_no, _title_norm, _page_no, raw, toc_page_idx in toc_ch:
            if 1 <= chapter_no <= enforced_chapters:
                actual_title = _toc_chapter_title(raw)
                kind, compared, expected_title = canonical_title_status(
                    actual_title, chapter_no, option)
                if kind == 'variant':
                    rep.add("ORANGE", "front_matter", f"สารบัญ ({page_ref(toc_page_idx)}) บทที่ {chapter_no}",
                            f'ชื่อบทสะกดตามคู่มือ: "{actual_title}"',
                            f"ประกาศใช้ \"{expected_title}\" แต่คู่มือแสดงแบบที่พบ — เจ้าหน้าที่ยืนยันได้",
                            "ยืนยันตามคู่มือ หรือแก้ให้ตรงประกาศ",
                            "BODY.OPTION1" if option == 1 else "BODY.OPTION2")
                elif kind == 'wrong':
                    rep.add("RED", "front_matter", f"สารบัญ ({page_ref(toc_page_idx)}) บทที่ {chapter_no}",
                            mismatch_detail("ชื่อบทในสารบัญ", compared, expected_title),
                            f"ควรเป็น \"{expected_title}\"", "แก้การสะกดชื่อบทในสารบัญ",
                            "BODY.OPTION1" if option == 1 else "BODY.OPTION2")

    if chapters_mode == "strict" and body_ch and BODY_RULES['check_body_chapter_count']:
        if option == 1 and len(body_ch) != 6:
            rep.add("RED", "body", "ทั้งเล่ม", f"พบ {len(body_ch)} บท",
                    "ประกาศ 2569: รูปแบบดั้งเดิมต้องมี 6 บท", "ปรับโครงบทตามประกาศ", "BODY.OPTION1")
        if option == 2 and len(body_ch) not in (2, 3):
            rep.add("RED", "body", "ทั้งเล่ม", f"พบ {len(body_ch)} บท",
                    "รูปแบบตีพิมพ์ต้องมี 2-3 บท", "", "BODY.OPTION2")

    # ชื่อบทต้องตรงกันทั้ง 3 ทาง: ประกาศ ↔ สารบัญ ↔ เนื้อหา โดยยึดประกาศเป็นหลัก
    # จึงเทียบเนื้อหากับประกาศเสมอ แม้สารบัญกับเนื้อหาจะต่างกันไปแล้ว (เดิมข้ามไป
    # ทำให้ไม่รู้ว่าฝั่งไหนผิดจากประกาศ)
    if chapters_mode == "strict" and body_ch and BODY_RULES['check_body_title_against_canonical']:
        canon = CANONICAL_OPT1 if option == 1 else CANONICAL_OPT2
        for cn, title, body_page_idx, _ in body_ch:
            if not (1 <= cn <= enforced_chapters):
                continue
            kind, compared, expected_title = canonical_title_status(title, cn, option)
            if kind == 'exact':
                continue
            # หัวบทยาวอาจถูกตัดขึ้นบรรทัดใหม่ — ยอมรับกรณีชื่อมาตรฐานขึ้นต้นด้วยข้อความที่พบ
            nb = norm(title)
            if len(nb) >= 8 and any(norm(cand).startswith(nb) for cand in canon[cn - 1]):
                continue
            if kind == 'variant':
                rep.add("ORANGE", "body", f"บทที่ {cn} ({page_ref(body_page_idx)})",
                        f'ชื่อบทในเนื้อหาสะกดตามคู่มือ: "{title}"',
                        f"ประกาศใช้ \"{expected_title}\" แต่คู่มือแสดงแบบที่พบ — เจ้าหน้าที่ยืนยันได้",
                        "ยืนยันตามคู่มือ หรือแก้ให้ตรงประกาศ",
                        "BODY.OPTION1" if option == 1 else "BODY.OPTION2")
            else:
                rep.add("RED", "body", f"บทที่ {cn} ({page_ref(body_page_idx)})",
                        mismatch_detail("ชื่อบทในเนื้อหา", compared, expected_title),
                        f"ตามประกาศ 2569 ควรเป็น \"{expected_title}\"", "แก้ชื่อบทให้ตรงประกาศ",
                        "BODY.OPTION1" if option == 1 else "BODY.OPTION2")

    # ---------- ส่วนท้ายเล่ม ----------
    _p("ตรวจส่วนท้ายเล่ม (อ้างอิง/ภาคผนวก/ประวัติ)")
    ref_head = None
    bio_page = None
    last_major = None
    has_appendix_body = False
    appendix_page = None
    appendix_pages = []
    # ส่วนท้ายเล่มอยู่ "หลังเนื้อหา" เสมอ จึงต้องไม่สแกนส่วนนำ/สารบัญ มิฉะนั้นบรรทัด
    # ในสารบัญ เช่น "APPENDIX D 90" จะถูกนับเป็นหัวบทภาคผนวกจริง ทำให้หน้าเริ่มของ
    # ภาคผนวกกลายเป็นหน้าส่วนนำ (เช่น "x") แล้วฟ้องเลขหน้าผิดทั้งที่เล่มถูก
    end_scan_start = min((c[2] for c in body_ch), default=0)
    for i, t in enumerate(pages):
        if i < end_scan_start:
            continue
        for l in top_lines(t, 3):
            nl = norm(l)
            ref_groups = [
                ('REFERENCES', 'REFERENCE'), ('BIBLIOGRAPHY',),
                (norm('รายการอ้างอิง'),), (norm('บรรณานุกรม'),),
            ]
            n_ref_terms = sum(1 for group in ref_groups if any(w in nl for w in group))
            if n_ref_terms and (nl in N_REF or n_ref_terms > 1):
                ref_head = (l, i, n_ref_terms)
                last_major = ("REF", i)
            if nl in N_BIO:
                bio_page = i
                last_major = ("BIO", i)
            if any(nl.startswith(w) for w in N_APPENDIX):
                has_appendix_body = True
                appendix_pages.append(i)
                appendix_page = i if appendix_page is None else appendix_page
                last_major = ("APP", i)
    if ref_head:
        if ref_head[2] > 1 or '/' in ref_head[0]:
            rep.add("RED", "end_matter", page_ref(ref_head[1]),
                    f"หัวข้อ \"{ref_head[0]}\"", "เลือกคำเดียว: REFERENCES หรือ BIBLIOGRAPHY", "ลบคำที่ไม่ใช้")
    else:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบหน้ารายการอ้างอิง",
                "ต้องมี REFERENCES/BIBLIOGRAPHY เสมอ", "")
    if bio_page is None:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบประวัติผู้วิจัย (BIOGRAPHY)",
                "ต้องมีและเป็นหน้าสุดท้ายของเล่ม", "")
    elif last_major and last_major[0] != "BIO":
        rep.add("RED", "end_matter", page_ref(last_major[1]),
                "หลัง BIOGRAPHY ยังมีส่วนอื่น", "ประวัติผู้วิจัยต้องเป็นหน้าสุดท้าย", "ย้ายไปท้ายสุด")

    appendix_toc_idx = next(
        (page_idx for page_idx, line in toc_lines if any(w in norm(line) for w in N_APPENDIX)),
        None,
    )
    toc_has_appendix = appendix_toc_idx is not None
    toc_location = f"สารบัญ ({page_ref(toc_pages[0])})" if toc_pages else "สารบัญ"
    if has_appendix_body and not toc_has_appendix:
        rep.add("RED", "front_matter", toc_location, "เล่มมีภาคผนวก (APPENDIX) แต่ไม่ปรากฏในสารบัญ",
                "หัวข้อภาคผนวกต้องอยู่ในสารบัญ", "เพิ่ม APPENDIX/ภาคผนวก ในสารบัญ", "FRONT.TOC")
    if toc_has_appendix and not has_appendix_body:
        rep.add("RED", "front_matter", f"สารบัญ ({page_ref(appendix_toc_idx)})", "สารบัญระบุภาคผนวก (APPENDIX) แต่ไม่พบในเนื้อหาเล่ม",
                "สารบัญต้องตรงกับเนื้อหาจริง", "ลบออกจากสารบัญ หรือเพิ่มภาคผนวกในเล่ม", "FRONT.TOC")

    # ---------- ขนาด section ส่วนนำ ----------
    _p("ตรวจบทคัดย่อและกิตติกรรมประกาศ")
    boundaries = sorted(set(sig_pages + abs_th_pages + abs_en_pages + ack_pages + toc_pages + list_pages))
    first_chapter = body_ch[0][2] if body_ch else front_limit

    def span_of(start):
        nxt = [b for b in boundaries if b > start] + [first_chapter]
        return max(1, min(nxt) - start)

    for grp_pages, gname, gmax in ((ack_pages, "กิตติกรรมประกาศ", 1),
                                    (abs_en_pages, "บทคัดย่อ (อังกฤษ)", 2),
                                    (abs_th_pages, "บทคัดย่อ (ไทย)", 2)):
        if grp_pages:
            sp = span_of(grp_pages[0])
            if sp > gmax:
                source_rule = "FRONT.ACKNOWLEDGEMENTS" if gname == "กิตติกรรมประกาศ" else "FRONT.ABSTRACT"
                rep.add("RED", "front_matter", f"{gname} (เริ่ม{page_ref(grp_pages[0])})",
                        f"กินพื้นที่ {sp} หน้า", f"{gname}ต้องไม่เกิน {gmax} หน้า",
                        "ตัดเนื้อหาให้สั้นลง", source_rule)

    # ---------- กฎหน้าบทคัดย่อ (ตรวจทั้งช่วงของบทคัดย่อ ไม่ใช่แค่หน้าแรก) ----------
    abstract_idxs = sorted(set(abs_en_pages + abs_th_pages))
    for ai in abstract_idxs:
        span_pgs = list(range(ai, min(ai + span_of(ai), n)))
        lbl = f"บทคัดย่อ ({page_ref(ai)})"
        # จำนวนหน้า "xxx pages / xxx หน้า" — ค้นทุกหน้าในช่วง (มักอยู่หน้าสุดท้ายของบทคัดย่อ)
        m2 = None
        for sp in span_pgs:
            for raw in pages[sp].split('\n'):
                m2 = re.search(r'(\d{1,4})\s*PAGES?', raw, re.I) or \
                     re.search(r'(\d{1,4})(หนา)', norm(raw))
                if m2:
                    break
            if m2:
                break
        if not m2:
            rep.add(FRONT_FAILURE_ZONE, "front_matter", lbl,
                    "ระบบไม่พบการระบุจำนวนหน้า (เช่น 123 pages / 123 หน้า)",
                    "ท้ายบทคัดย่อต้องระบุจำนวนหน้ารวมของเล่ม", "ตรวจด้วยตา", "FRONT.ABSTRACT")
        elif last_arabic is not None and int(m2.group(1)) != last_arabic:
            stated_pages = int(m2.group(1))
            # คลาดเคลื่อนเล็กน้อยอาจมาจากการอ่านเลขหน้า PDF ของระบบเอง
            # จึงให้เจ้าหน้าที่ยืนยันจากไฟล์จริงแทนการฟันธง
            count_zone = "ORANGE" if abs(stated_pages - last_arabic) <= 2 else "RED"
            rep.add(count_zone, "front_matter", lbl,
                    f"ระบุจำนวนหน้า {stated_pages} แต่เลขหน้าสุดท้ายที่ระบบอ่านได้คือ {last_arabic}",
                    f"จำนวนหน้าที่ระบุต้องเท่ากับเลขหน้าสุดท้ายของเล่ม",
                    "เจ้าหน้าที่ยืนยันเลขหน้าสุดท้ายจากไฟล์จริง แล้วให้แก้ตัวเลขให้ตรง",
                    "FRONT.ABSTRACT")
        # keywords ≤5 — ค้นทุกหน้าในช่วง
        for sp in span_pgs:
            done_kw = False
            for raw in pages[sp].split('\n'):
                nl = norm(raw)
                if nl.startswith('KEYWORD') or nl.startswith(norm('คำสำคัญ')):
                    tail = raw.split(':', 1)[1] if ':' in raw else raw
                    kws = [k for k in re.split(r'[,;/]', tail) if k.strip()]
                    if len(kws) > 5:
                        rep.add("RED", "front_matter", f"บทคัดย่อ ({page_ref(sp)})",
                                f"Keywords {len(kws)} คำ", "ไม่เกิน 5 คำตามประกาศ",
                                "ตัดให้เหลือ ≤5", "FRONT.ABSTRACT")
                    done_kw = True
                    break
            if done_kw:
                break

    # พบข้อความตัวหนาในบทคัดย่อ = ข้อสังเกตสีเหลือง แต่ยังผ่านได้
    if abstract_idxs:
        try:
            with pdfplumber.open(pdf_path) as _pl:
                for ai in abstract_idxs:
                    for abs_page_idx in range(ai, min(ai + span_of(ai), n)):
                        bold_lines = [
                            line['text'] for line in _font_lines(_pl.pages[abs_page_idx])
                            if line['bold_ratio'] > 0 and len(norm(line['text'])) >= 2
                            and not _is_abstract_heading(line['text'])
                        ]
                        if bold_lines:
                            examples = ", ".join(f'"{line}"' for line in bold_lines[:5])
                            more = f" และอีก {len(bold_lines) - 5} บรรทัด" if len(bold_lines) > 5 else ""
                            rep.add(ABSTRACT_BOLD_ZONE, "front_matter",
                                    f"บทคัดย่อ ({page_ref(abs_page_idx)})",
                                    f"พบข้อความตัวหนา: {examples}{more}",
                                    "แจ้งเป็นข้อสังเกตเรื่องตัวหนา แต่เล่มยังผ่านได้",
                                    "เจ้าหน้าที่พิจารณาว่าต้องแก้หรือไม่", "FORMAT.ABSTRACT_BOLD")
        except Exception:
            rep.add(UNCERTAIN_ZONE, "front_matter", "บทคัดย่อ",
                    "ระบบอ่านรูปแบบตัวหนาในบทคัดย่อไม่ได้",
                    "เจ้าหน้าที่ตรวจสอบรูปแบบตัวหนาในบทคัดย่อ",
                    "ตรวจด้วยตา", "UNCERTAIN.REVIEW")

    # ---------- เทียบข้อมูลอนุมัติ ----------
    _p("เทียบข้อมูลอนุมัติ (ชื่อเรื่อง/ชื่อนักศึกษา)")
    sig_text = "\n".join(pages[i] for i in sig_pages) if sig_pages else ""
    if approved:
        A = approved
        program_language = A.get("program_language", "")
        required_fields = FRONT_MATTER_RULES["required_form_fields"].get(program_language, ())
        for field_name in required_fields:
            if not soft(A.get(field_name, "")):
                rep.add(
                    FRONT_FAILURE_ZONE,
                    "front_matter",
                    "ข้อมูลอ้างอิงในแบบฟอร์ม",
                    f"ไม่ได้กรอก{FORM_FIELD_LABELS[field_name]}",
                    "การตรวจอย่างเข้มต้องมีข้อมูลอ้างอิงครบทุกช่องที่กำหนด",
                    "กรอกข้อมูลให้ครบแล้วตรวจใหม่",
                    "FORM.REQUIRED",
                )

        cover_text = pages[0] if pages else ""
        missing_cover_items = [
            (label, expected_text)
            for label, expected_text in cover_required_items(A.get("doc_type", ""), program_language)
            if expected_text and norm(expected_text) not in norm(cover_text)
        ]
        for label, expected_text in missing_cover_items:
            snippet, ratio = _best_cover_match(expected_text, cover_text)
            # เกณฑ์ 0.8: ข้อความพิมพ์ผิดเล็กน้อย (ตก S/สลับคำ) จะได้คะแนนสูงกว่านี้
            # ส่วนการบังเอิญไปตรง substring คนละบรรทัด (โดยเฉพาะไทย) จะต่ำกว่า
            if ratio >= 0.8 and snippet:
                diff = describe_diff(snippet, expected_text)
                found_msg = f"หน้าปกพิมพ์ \"{snippet}\" ไม่ตรงข้อความบังคับ ({label})"
                if diff:
                    found_msg += f" — ต่างที่ {diff}"
            else:
                found_msg = f"ไม่พบข้อความบังคับ ({label}) บนหน้าปก"
            rep.add(
                "RED", "front_matter", "หน้าปก",
                found_msg,
                f"ข้อความที่ถูกต้อง: \"{expected_text}\"",
                "แก้ข้อความบนหน้าปกให้ตรง template ทางการทุกตัวอักษร",
                "FRONT.COVER_REQUIRED",
            )
        if A.get("doc_type") and doc_type and A["doc_type"] != doc_type:
            rep.add("RED", "front_matter", "หน้าปก", f"เล่มเป็น {doc_type}",
                    f"ข้อมูลอนุมัติ: {A['doc_type']}", "ตรวจว่าใช้ template ประเภทถูก", "FORM.APPROVED_MATCH")
        if chapters_mode == "strict" and A.get("format") and str(option) != str(A["format"]):
            rep.add("RED", "body", "โครงบท", f"เล่มเป็นรูปแบบ {option}",
                    f"ข้อมูลอนุมัติ: รูปแบบ {A['format']}", "", "FORM.APPROVED_MATCH")

        thai_book = A.get("program_language") == "thai"

        ordered_front_sections = []
        if sig_pages:
            ordered_front_sections.append(("หน้าลงนาม", max(sig_pages)))
        if ack_pages:
            ordered_front_sections.append(("กิตติกรรมประกาศ", ack_pages[0]))
        if program_language == "thai":
            if abs_th_idx is not None:
                ordered_front_sections.append(("บทคัดย่อภาษาไทย", abs_th_idx))
            if abs_en_idx is not None:
                ordered_front_sections.append(("บทคัดย่อภาษาอังกฤษ", abs_en_idx))
        else:
            if abs_en_idx is not None:
                ordered_front_sections.append(("บทคัดย่อภาษาอังกฤษ", abs_en_idx))
            if program_language == "thai_english" and abs_th_idx is not None:
                ordered_front_sections.append(("บทคัดย่อภาษาไทย", abs_th_idx))
        if toc_pages:
            ordered_front_sections.append(("สารบัญ", toc_pages[0]))
        for list_idx in sorted(set(list_pages)):
            list_heading = next((line for line in top_lines(pages[list_idx], 8)
                                 if _toc_section_kind(line).startswith("list_")), "LIST OF ...")
            ordered_front_sections.append((_strip_toc_page_number(list_heading), list_idx))
        if body_ch:
            ordered_front_sections.append(("บทที่ 1/ส่วนเนื้อหา", body_ch[0][2]))
        actual_front_sections = sorted(ordered_front_sections, key=lambda item: item[1])
        if [name for name, _idx in actual_front_sections] != [name for name, _idx in ordered_front_sections]:
            actual_order = " → ".join(
                f"{name} ({page_ref(page_idx)})" for name, page_idx in actual_front_sections
            )
            expected_order = " → ".join(name for name, _idx in ordered_front_sections)
            rep.add(
                "RED", "front_matter", "ส่วนนำ",
                f"ลำดับที่พบ: {actual_order}",
                f"ลำดับที่ต้องเป็น: {expected_order}",
                "ย้ายแต่ละส่วนของส่วนนำให้เรียงตามลำดับที่กำหนด",
                "FRONT.ORDER",
            )

        main_title = (A.get("title_th") if thai_book else A.get("title_en")) or ""
        alt_title = "" if A.get("program_language") == "international" else \
            ((A.get("title_en") if thai_book else A.get("title_th")) or "")

        if main_title:
            spots = [("หน้าปก", pages[0] if pages else "")]
            for k2, i2 in enumerate(sig_pages):
                spots.append((f"หน้าลงนาม {k2+1} ({page_ref(i2)})", pages[i2]))
            main_abs = abs_th_idx if thai_book else abs_en_idx
            if main_abs is not None:
                spots.append((f"บทคัดย่อ ({page_ref(main_abs)})", pages[main_abs]))
            for spot_name, spot_text in spots:
                compared = compare_reference_text(spot_text, main_title, 'title')
                rep.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", spot_name,
                                     "pass" if compared['status'] == 'exact' else "fail",
                                     "" if compared['status'] == 'exact' else compared['actual'])
                if compared['status'] != 'exact':
                    rep.add("RED", "front_matter", spot_name,
                            mismatch_detail("ชื่อเรื่อง", compared, main_title),
                            f"ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: \"{main_title}\"",
                            "แก้ข้อความและตัวพิมพ์เล็ก-ใหญ่ให้ตรงข้อมูลอนุมัติ", "FORM.APPROVED_MATCH")
        if alt_title:
            alt_abs = abs_en_idx if thai_book else abs_th_idx
            alt_lbl = "บทคัดย่อภาษาอังกฤษ" if thai_book else "บทคัดย่อภาษาไทย"
            if alt_abs is not None:
                compared = compare_reference_text(pages[alt_abs], alt_title, 'title')
                rep.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", f"{alt_lbl} ({page_ref(alt_abs)})",
                                     "pass" if compared['status'] == 'exact' else "fail",
                                     "" if compared['status'] == 'exact' else compared['actual'])
                if compared['status'] != 'exact':
                    rep.add("RED", "front_matter", f"{alt_lbl} ({page_ref(alt_abs)})",
                            mismatch_detail("ชื่อเรื่องอีกภาษา", compared, alt_title),
                            f"ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: \"{alt_title}\"",
                            "แก้ให้ตรงข้อมูลอนุมัติ", "FORM.APPROVED_MATCH")
            else:
                rep.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", alt_lbl, "pending",
                                     "ระบบหาหน้าบทคัดย่อภาษานี้ไม่เจอ")
                rep.add(FRONT_FAILURE_ZONE, "front_matter", alt_lbl, "ระบบหาหน้าบทคัดย่อภาษานี้ไม่เจอ",
                        f"ชื่อเรื่อง \"{alt_title[:40]}...\" ต้องปรากฏในบทคัดย่อภาษานั้น",
                        "ตรวจด้วยตา", "FORM.APPROVED_MATCH")

        student_name = strip_name_prefix(A.get("student_name", ""))
        student_name_th = strip_name_prefix(A.get("student_name_th", ""))
        primary_student_name = student_name_th if thai_book else student_name

        if ack_pages and (student_name_th if thai_book else student_name):
            ack_start = ack_pages[0]
            ack_page_indices = range(ack_start, min(ack_start + span_of(ack_start), n))
            ack_lines = [
                soft(line)
                for page_idx in ack_page_indices
                for line in pages[page_idx].splitlines()
                if soft(line)
                and norm(line) not in N_ACK
                and not re.fullmatch(r'(?:\d{1,4}|[ivxlcdm]+|[ก-ฮ])', soft(line), re.I)
            ]
            ack_full_text = soft(" ".join(ack_lines))
            ack_tail_text = soft(" ".join(ack_lines[-8:]))
            expected_ack_name = student_name_th if thai_book else person_name_sentence_case(student_name)
            if thai_book:
                exact_at_end = norm(expected_ack_name) in norm(ack_tail_text)
                name_elsewhere = norm(expected_ack_name) in norm(ack_full_text)
                wrong_case = False
            else:
                exact_at_end = expected_ack_name in ack_tail_text
                name_elsewhere = expected_ack_name in ack_full_text
                wrong_case = expected_ack_name.casefold() in ack_tail_text.casefold()
            if not exact_at_end:
                if wrong_case:
                    found_ack = f"พบชื่อผู้เขียนท้ายกิตติกรรมประกาศ แต่ตัวพิมพ์ไม่ตรงรูปแบบ: {ack_tail_text[-120:]}"
                elif name_elsewhere:
                    found_ack = "พบชื่อผู้เขียนในกิตติกรรมประกาศ แต่ไม่อยู่ในส่วนท้าย"
                else:
                    found_ack = "ไม่พบชื่อผู้เขียนในส่วนท้ายของกิตติกรรมประกาศ"
                rep.add(
                    "RED", "front_matter", f"กิตติกรรมประกาศ ({page_ref(ack_start)})",
                    found_ack,
                    f"ท้ายกิตติกรรมประกาศต้องเป็นชื่อผู้เขียน \"{expected_ack_name}\"",
                    f"เพิ่มหรือแก้ชื่อผู้เขียนท้ายกิตติกรรมประกาศเป็น \"{expected_ack_name}\"",
                    "FRONT.ACK_AUTHOR",
                )

        if primary_student_name:
            name_spots = [("หน้าปก", 0)] + [
                (f"หน้าลงนาม {k + 1} ({page_ref(idx)})", idx) for k, idx in enumerate(sig_pages)
            ]
            for spot_name, spot_idx in name_spots:
                compared = compare_reference_text(pages[spot_idx], primary_student_name, 'student_name')
                rep.add_verification("ชื่อนักศึกษา", spot_name,
                                     "pass" if compared['status'] == 'exact' else "fail",
                                     "" if compared['status'] == 'exact' else compared['actual'])
                if compared['status'] != 'exact':
                    rep.add("RED", "front_matter", spot_name,
                            mismatch_detail("ชื่อนักศึกษา", compared, primary_student_name),
                            f"ต้องสะกดตรงข้อมูลอนุมัติทุกหน้า: \"{primary_student_name}\"",
                            "แก้การสะกดชื่อ", "FORM.APPROVED_MATCH")

        # ชื่อนักศึกษาในบทคัดย่อ: ไม่พบ = 🔴, มีคำนำหน้า = 🟠
        if A.get("program_language") in ("thai", "thai_english"):
            name_checks = [
                (student_name_th, abs_th_idx, "บทคัดย่อภาษาไทย", "ชื่อภาษาไทย", True),
                (student_name, abs_en_idx, "บทคัดย่อภาษาอังกฤษ", "ชื่อภาษาอังกฤษ", True),
            ]
        else:
            name_checks = [(student_name, abs_en_idx, "บทคัดย่อ", "ชื่อนักศึกษา", False)]
        PREFIX_RE = r"(นางสาว|นาง|นาย|MRS\.?|MISS|MS\.?|MR\.?|ดร\.?|DR\.?)"
        for nm3, aidx, albl, nlbl, required in name_checks:
            if not nm3:
                if required:
                    rep.add(FRONT_FAILURE_ZONE, "front_matter", albl, f"ไม่ได้กรอก{nlbl}ของนักศึกษาในฟอร์ม",
                            f"หลักสูตรไทยต้องตรวจ{nlbl}ในหน้า{albl}",
                            "กรอกฟอร์มให้ครบแล้วตรวจใหม่", "FORM.REQUIRED")
                continue
            if aidx is None:
                rep.add_verification("ชื่อนักศึกษา", albl, "pending",
                                     f"ระบบหาหน้า{albl}ไม่เจอ")
                rep.add(FRONT_FAILURE_ZONE, "front_matter", albl, f"ระบบหาหน้า{albl}ไม่เจอ จึงเทียบ{nlbl}ไม่ได้",
                        f"{nlbl} \"{nm3}\" ต้องปรากฏในหน้า{albl}", "ตรวจด้วยตา", "FORM.APPROVED_MATCH")
                continue
            compared = compare_reference_text(pages[aidx], nm3, 'student_name')
            if compared['status'] != 'exact':
                rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})",
                                     "fail", compared['actual'])
                rep.add("RED", "front_matter", f"{albl} ({page_ref(aidx)})",
                        mismatch_detail(f"{nlbl}", compared, nm3),
                        f"{nlbl}ของนักศึกษาในหน้า{albl}ต้องสะกดตรงข้อมูลอนุมัติ: \"{nm3}\"",
                        "ตรวจการสะกด", "FORM.APPROVED_MATCH")
            else:
                first_tok = nm3.split()[0]
                if re.search(PREFIX_RE + r"\s*" + re.escape(norm(first_tok))[:12], norm(pages[aidx]), re.I) and \
                   re.search(PREFIX_RE, pages[aidx], re.I):
                    rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})",
                                         "pending", "พบคำนำหน้านามหน้าชื่อ")
                    rep.add(FRONT_FAILURE_ZONE, "front_matter", f"{albl} ({page_ref(aidx)})",
                            f"พบคำนำหน้านามหน้า{nlbl} (เช่น นาย/นางสาว/Mr./Miss)",
                            "ชื่อนักศึกษาต้องไม่มีคำนำหน้านาม",
                            "ลบคำนำหน้านามออก แล้วให้เจ้าหน้าที่ยืนยัน", "FORM.APPROVED_MATCH")
                else:
                    rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})", "pass")

        # รหัสนักศึกษา = เลข 7 หลัก + รหัสหลักสูตร (เช่น "6838141 SHSS/M") ต้องตรวจทั้งชุด
        # และต้องปรากฏในบทคัดย่อ "ทุกภาษาที่เล่มมี" (นานาชาติมีเฉพาะอังกฤษ)
        student_id = soft(A.get("student_id", ""))
        if student_id:
            digits_only = re.sub(r'\D', '', student_id)
            cover_digits = re.sub(r'[^\d]', '', pages[0] if pages else "")
            if digits_only and digits_only in cover_digits:
                rep.add_verification("รหัสนักศึกษา", "หน้าปก (ต้องไม่มีรหัส)", "fail",
                                     "พบรหัสบนหน้าปก")
                rep.add("RED", "front_matter", "หน้าปก",
                        f"พบรหัสนักศึกษา {student_id} ต่อท้าย/อยู่ใกล้ชื่อนักศึกษา",
                        "หน้าปกต้องแสดงเฉพาะชื่อ-นามสกุล โดยไม่มีรหัสนักศึกษา",
                        "ลบรหัสนักศึกษาออกจากหน้าปก", "FORM.APPROVED_MATCH")
            else:
                rep.add_verification("รหัสนักศึกษา", "หน้าปก (ต้องไม่มีรหัส)", "pass")

            abstract_spots = [(abs_en_idx, "บทคัดย่ออังกฤษ"), (abs_th_idx, "บทคัดย่อไทย")]
            if not any(idx is not None for idx, _ in abstract_spots):
                rep.add_verification("รหัสนักศึกษา", "บทคัดย่อ", "pending",
                                     "ระบบหาหน้าบทคัดย่อไม่เจอ")
            for abs_idx, abs_label in abstract_spots:
                if abs_idx is None:
                    continue
                loc = f"{abs_label} ({page_ref(abs_idx)})"
                if norm(student_id) in norm(pages[abs_idx]):
                    rep.add_verification("รหัสนักศึกษา", loc, "pass")
                else:
                    rep.add_verification("รหัสนักศึกษา", loc, "fail",
                                         f"ไม่พบรหัส {student_id}")
                    rep.add("RED", "front_matter", loc,
                            f"ไม่พบรหัสนักศึกษา \"{student_id}\" (ต้องมีทั้งตัวเลขและรหัสหลักสูตร)",
                            f"บรรทัดชื่อนักศึกษาใน{abs_label}ต้องมีรหัส \"{student_id}\"",
                            "เพิ่ม/แก้รหัสนักศึกษาให้ครบทั้งตัวเลขและรหัสหลักสูตร",
                            "FORM.APPROVED_MATCH")

        # ชื่อปริญญาแยกตามตำแหน่งที่ใช้ตรวจ (ตามข้อมูลอนุมัติจาก eThesis):
        #   หน้าปก      = ต้นฉบับ eThesis ตรง ๆ (อังกฤษเป็นตัวพิมพ์ใหญ่)
        #   หน้าลงนาม   = Sentence case สำหรับเล่มอังกฤษ / ภาษาไทยคงเดิม
        #   บทคัดย่อ    = ตัวย่อ (ดู _check_degree_abbr ด้านล่าง)
        # เล่มหลักสูตรไทย ปก/หน้าลงนามเป็นภาษาไทย นอกนั้นใช้ชุดภาษาอังกฤษ
        cover_degree = soft(A.get("degree_cover_th" if thai_book else "degree_cover_en", ""))
        sig_degree = soft(A.get("degree_sig_th" if thai_book else "degree_sig_en", ""))
        if cover_degree or sig_degree:
            degree_spots = []
            if cover_degree:
                degree_spots.append(("หน้าปก", pages[0] if pages else "", cover_degree))
            if sig_degree:
                degree_spots.extend((f"หน้าลงนาม {k + 1} ({page_ref(idx)})", pages[idx], sig_degree)
                                    for k, idx in enumerate(sig_pages))
            for spot_name, spot_text, expected_degree in degree_spots:
                compared = compare_reference_text(spot_text, expected_degree, 'degree', degree_line=True)
                if compared['status'] == 'exact':
                    rep.add_verification("ชื่อปริญญา", spot_name, "pass")
                    continue
                if norm(expected_degree) in norm(spot_text):
                    # ตัวอักษรครบทุกตัว ต่างเฉพาะเครื่องหมายวรรคตอน/การเว้นวรรค
                    # (เช่น comma ในวงเล็บสาขา) — ส้มให้เจ้าหน้าที่ยืนยัน
                    rep.add_verification("ชื่อปริญญา", spot_name, "pending",
                                         "ต่างเฉพาะวรรคตอน/ช่องว่าง")
                    rep.add("ORANGE", "front_matter", spot_name,
                            f'พบชื่อปริญญาแต่เครื่องหมายวรรคตอน/ช่องว่างต่างจากข้อมูลอนุมัติ: "{compared["actual"]}"',
                            f"ข้อมูลอนุมัติ: \"{expected_degree}\"",
                            "เจ้าหน้าที่ยืนยันว่ายอมรับได้หรือให้แก้", "FORM.APPROVED_MATCH")
                else:
                    rep.add_verification("ชื่อปริญญา", spot_name, "fail", compared['actual'])
                    rep.add("RED", "front_matter", spot_name,
                            mismatch_detail("ชื่อปริญญา", compared, expected_degree),
                            f"ต้องเป็น \"{expected_degree}\"",
                            "แก้ชื่อปริญญาให้ตรงข้อมูลอนุมัติ", "FORM.APPROVED_MATCH")

        # ตรวจชื่อปริญญาแบบย่อในบทคัดย่อ — เล่มหลักสูตรไทย/ไทย-อังกฤษ ต้องตรวจทั้ง
        # บทคัดย่ออังกฤษ (M.Sc./Ph.D.) และบทคัดย่อไทย (วท.ม./ปร.ด.) จึงทำเป็น helper
        def _check_degree_abbr(abbr, abstract_idx, lang):
            if not abbr or abstract_idx is None:
                return
            abstract_text = pages[abstract_idx]
            compared = compare_reference_text(abstract_text, abbr, 'degree', degree_line=True)
            vloc = f"ชื่อย่อใน{lang} ({page_ref(abstract_idx)})"
            box = f"{lang} ({page_ref(abstract_idx)})"
            if compared['status'] == 'exact':
                # ชื่อย่อพบครบ แต่บรรทัดนั้นต้องไม่มีคำอื่นเกิน เช่น "DEGREE M.Sc. (...)"
                abbr_lines = [soft(line) for line in abstract_text.splitlines()
                              if abbr in soft(line)]
                if abbr_lines and not any(norm(line) == norm(abbr) for line in abbr_lines):
                    rep.add_verification("ชื่อปริญญา", vloc, "fail",
                                         f"มีข้อความเกิน: {abbr_lines[0]}")
                    rep.add("RED", "front_matter", box,
                            f'บรรทัดชื่อปริญญาแบบย่อมีข้อความเกิน: "{abbr_lines[0]}"',
                            f"บรรทัดนี้ต้องเป็น \"{abbr}\" เท่านั้น ไม่มีคำอื่นนำหน้าหรือต่อท้าย",
                            "ลบข้อความเกินออกจากบรรทัดชื่อปริญญา", "FORM.APPROVED_MATCH")
                else:
                    rep.add_verification("ชื่อปริญญา", vloc, "pass")
            elif norm(abbr) in norm(abstract_text):
                # ตัวอักษรครบ ต่างเฉพาะวรรคตอน/ช่องว่าง — ส้มให้เจ้าหน้าที่ยืนยัน
                rep.add_verification("ชื่อปริญญา", vloc, "pending", "ต่างเฉพาะวรรคตอน/ช่องว่าง")
                rep.add("ORANGE", "front_matter", box,
                        f'พบชื่อปริญญาแบบย่อแต่เครื่องหมายวรรคตอน/ช่องว่างต่างจากข้อมูลอนุมัติ: "{compared["actual"]}"',
                        f"ข้อมูลอนุมัติ: \"{abbr}\"",
                        "เจ้าหน้าที่ยืนยันว่ายอมรับได้หรือให้แก้", "FORM.APPROVED_MATCH")
            else:
                rep.add_verification("ชื่อปริญญา", vloc, "fail", compared['actual'])
                rep.add("RED", "front_matter", box,
                        mismatch_detail("ชื่อปริญญาแบบย่อ", compared, abbr),
                        f"ต้องเป็น \"{abbr}\" ตามรูปแบบชื่อย่อและสาขาในวงเล็บ",
                        "แก้ชื่อปริญญาแบบย่อให้ตรงข้อมูลอนุมัติ", "FORM.APPROVED_MATCH")

        _check_degree_abbr(soft(A.get("degree_abbr_en", "")), abs_en_idx, "บทคัดย่ออังกฤษ")
        _check_degree_abbr(soft(A.get("degree_abbr_th", "")), abs_th_idx, "บทคัดย่อไทย")

        if A.get("exam_date"):
            signature_location = ", ".join(page_ref(idx) for idx in sig_pages) or "หน้าไม่ระบุเลข"
            # หน้าลงนามเล่มไทยมักเขียน "วันที่ 11 พฤษภาคม พ.ศ. 2569" (มีคำระบุ
            # ศักราชคั่นระหว่างเดือนกับปี) แต่ข้อมูลอนุมัติเป็น "11 พฤษภาคม 2569"
            # จึงตัด พ.ศ./ค.ศ./B.E./A.D. ออกจากทั้งสองฝั่งก่อนเทียบ ไม่งั้นฟ้องผิด
            def _date_key(text):
                text = re.sub(r'พ\.?\s*ศ\.?|ค\.?\s*ศ\.?|B\.?\s*E\.?|A\.?\s*D\.?',
                              ' ', text, flags=re.I)
                return norm(re.sub(r'\b0([1-9])', r'\1', text))
            exam_found = _date_key(A["exam_date"]) in _date_key(sig_text)
            rep.add_verification("วันที่สอบผ่าน", f"หน้าลงนาม ({signature_location})",
                                 "pass" if exam_found else "fail")
            if not exam_found:
                rep.add("RED", "front_matter", f"หน้าลงนาม ({signature_location})", f"ไม่พบวันที่สอบ \"{A['exam_date']}\"",
                        "วันที่บนหน้าลงนาม = วันที่มีผลสอบผ่าน", "", "FORM.APPROVED_MATCH")
        if A.get("year"):
            year_found = str(A["year"]) in (pages[0] if pages else "")
            rep.add_verification("ปีบนหน้าปก", "หน้าปก", "pass" if year_found else "fail")
            if not year_found:
                rep.add("RED", "front_matter", "หน้าปก", f"ไม่พบปี {A['year']} บนหน้าปก",
                        "ปี = ปีที่มีผลสอบผ่าน", "", "FORM.APPROVED_MATCH")

        # human checklist (หน้าลงนาม — เจ้าหน้าที่ตรวจเอง)
        rep.add_human("รายชื่อกรรมการ ตำแหน่งวิชาการ และคุณวุฒิ บนหน้าลงนามทั้ง 2 หน้า",
                      "เทียบกับ บฑ.1 (หน้า 1) และ บฑ.2 (หน้า 2) ทีละคน รวมการสะกด")
        rep.add_human("ลำดับและตำแหน่งการวางชื่อในตารางลายเซ็น",
                      "ชื่อที่ 1 (Major Advisor/Chair) แถวเดียวกับนักศึกษา คอลัมน์ขวา, ชื่อ 2-5 ไล่ลงขวา, ชื่อ 6 แถวเดียวกับชื่อ 5 ฝั่งซ้าย, 7-9 ไล่ขึ้น, ช่องที่เหลือถมขาว")
        rep.add_human("หน้า 1 — ประธานหลักสูตร (ระบุชื่อหลักสูตรให้ถูกต้อง)",
                      "ข้อความมุมล่างขวาใต้ลายเซ็นต้องเป็นชื่อหลักสูตร เช่น ปรัชญาดุษฎีบัณฑิต สาขาวิชา...")
        rep.add_human("หน้า 2 — คณบดี/ผู้อำนวยการ (ระบุหัวหน้าส่วนงานให้ถูกต้อง)",
                      "ข้อความมุมล่างขวาใต้ลายเซ็นต้องเป็นคณะ/ส่วนงานที่นักศึกษาสังกัด เช่น คณะวิศวกรรมศาสตร์")

        prog = A.get("program_language", "")
        if prog == "international":
            if not has_en_abs:
                rep.add(FRONT_FAILURE_ZONE, "front_matter", "บทคัดย่อ",
                        "ไม่พบบทคัดย่อภาษาอังกฤษ",
                        "หลักสูตรนานาชาติต้องมีบทคัดย่อภาษาอังกฤษ",
                        "เพิ่มบทคัดย่อภาษาอังกฤษ", "FRONT.ABSTRACT")
            if has_th_abs:
                rep.add("RED", "front_matter", "บทคัดย่อ", "มีบทคัดย่อภาษาไทย",
                        "หลักสูตรนานาชาติใช้บทคัดย่ออังกฤษเท่านั้น",
                        "ลบบทคัดย่อไทย", "FRONT.ABSTRACT")
        if prog in ("thai", "thai_english") and not (has_en_abs and has_th_abs):
            rep.add("RED", "front_matter", "บทคัดย่อ",
                    f"พบบทคัดย่อ: EN={has_en_abs}, TH={has_th_abs}",
                    "หลักสูตรไทยต้องมีทั้ง 2 ภาษา", "", "FRONT.ABSTRACT")

        if toc_pages:
            actual_toc_sections = {}
            if ack_pages:
                actual_toc_sections["ack"] = ("กิตติกรรมประกาศ", ack_pages[0])
            if abs_en_idx is not None:
                actual_toc_sections["abstract_en"] = ("บทคัดย่อภาษาอังกฤษ", abs_en_idx)
            if abs_th_idx is not None:
                actual_toc_sections["abstract_th"] = ("บทคัดย่อภาษาไทย", abs_th_idx)
            for list_idx in list_pages:
                for heading in top_lines(pages[list_idx], 8):
                    list_kind = _toc_section_kind(heading)
                    if list_kind.startswith("list_"):
                        actual_toc_sections.setdefault(
                            list_kind, (_strip_toc_page_number(heading), list_idx)
                        )
                        break
            if ref_head:
                actual_toc_sections["references"] = ("รายการอ้างอิง/บรรณานุกรม", ref_head[1])
            if appendix_page is not None:
                actual_toc_sections["appendix"] = ("ภาคผนวก", appendix_page)
            if bio_page is not None:
                actual_toc_sections["biography"] = ("ประวัติผู้วิจัย", bio_page)

            toc_entries_by_kind = {}
            for entry in toc_entries:
                toc_entries_by_kind.setdefault(entry["kind"], []).append(entry)

            # เลขหน้าเริ่มของภาคผนวกทุกชุดที่มีอยู่จริงในเล่ม (ใช้แยกกรณีก้ำกึ่ง)
            appendix_labels = {page_labels.get(i, "") for i in appendix_pages}
            appendix_labels.discard("")

            for section_kind, (section_label, actual_page_idx) in actual_toc_sections.items():
                candidates = toc_entries_by_kind.get(section_kind, [])
                if not candidates:
                    rep.add(
                        "RED", "front_matter", f"สารบัญ ({page_ref(toc_pages[0])})",
                        f"ไม่พบหัวข้อ {section_label} ในสารบัญ",
                        f"สารบัญต้องมีหัวข้อ {section_label} พร้อมเลขหน้า",
                        f"เพิ่มหัวข้อ {section_label} และเลขหน้าจริงลงในสารบัญ",
                        "FRONT.TOC_CONTENT",
                    )
                    continue
                entry = candidates[0]
                if not entry["page_label"]:
                    rep.add(
                        "RED", "front_matter", f"สารบัญ ({page_ref(entry['source_page_idx'])})",
                        f"หัวข้อ {section_label} ไม่มีเลขหน้า",
                        f"หัวข้อ {section_label} ต้องระบุเลขหน้าที่เริ่มต้นจริง",
                        "เพิ่มเลขหน้าของหัวข้อนี้ในสารบัญ",
                        "FRONT.TOC_CONTENT",
                    )
                    continue
                actual_label = page_labels.get(actual_page_idx, "")
                if actual_label and entry["page_label"] != actual_label:
                    location = (f"สารบัญ ({page_ref(entry['source_page_idx'])}) ↔ "
                                f"{section_label} ({page_ref(actual_page_idx)})")
                    if toc_page_mismatch_zone(section_kind, entry["page_label"],
                                              appendix_labels) == "ORANGE":
                        # หัวข้อร่ม "APPENDIX" ชี้ไปหน้าเริ่มของภาคผนวกชุดอื่นแทนชุดแรก
                        # เลขหน้ายังมีอยู่จริงในเล่ม จึงให้เจ้าหน้าที่ตัดสิน
                        rep.add(
                            "ORANGE", "front_matter", location,
                            f"สารบัญระบุหน้า {entry['page_label']} ซึ่งเป็นหน้าเริ่มของภาคผนวกอีกชุดหนึ่ง "
                            f"(ภาคผนวกชุดแรกอยู่หน้า {actual_label})",
                            f"โดยทั่วไปหัวข้อ {section_label} ควรชี้หน้าเริ่มของภาคผนวกชุดแรก คือหน้า {actual_label}",
                            "เจ้าหน้าที่พิจารณาว่ายอมรับได้ หรือให้แก้เป็นหน้าแรกของภาคผนวก",
                            "FRONT.TOC_CONTENT",
                        )
                    else:
                        rep.add(
                            "RED", "front_matter", location,
                            f"สารบัญระบุหน้า {entry['page_label']} แต่หัวข้อเริ่มจริงหน้า {actual_label}",
                            f"เลขหน้า {section_label} ในสารบัญต้องเป็น {actual_label}",
                            f"แก้เลขหน้าในสารบัญจาก {entry['page_label']} เป็น {actual_label}",
                            "FRONT.TOC_CONTENT",
                        )

            for optional_kind in ("list_tables", "list_figures", "list_abbreviations"):
                if optional_kind in toc_entries_by_kind and optional_kind not in actual_toc_sections:
                    entry = toc_entries_by_kind[optional_kind][0]
                    rep.add(
                        "RED", "front_matter", f"สารบัญ ({page_ref(entry['source_page_idx'])})",
                        f"สารบัญระบุหัวข้อ \"{_strip_toc_page_number(entry['raw'])}\" แต่ไม่พบส่วนดังกล่าวในเล่ม",
                        "หัวข้อในสารบัญต้องตรงกับส่วนที่มีอยู่จริงในเล่ม",
                        "ลบหัวข้อออกจากสารบัญ หรือเพิ่มส่วนดังกล่าวในเล่ม",
                        "FRONT.TOC_CONTENT",
                    )

    _p("สรุปผล")
    part_order = {"front_matter": 0, "body": 1, "body/end": 2, "end_matter": 3, "-": 4}
    for z in rep.zones:
        rep.zones[z].sort(key=lambda x: part_order.get(x["part"], 9))
        for it in rep.zones[z]:
            it["category"] = classify(it)

    return {
        "context": {"document_type": doc_type, "option": option, "chapters_mode": chapters_mode,
                    "n_pages": n, "approved_data": bool(approved)},
        "verdict": rep.verdict(),
        "summary": {z.lower(): len(v) for z, v in rep.zones.items()},
        "issues_by_zone": rep.zones,
        "info": rep.info,
        "human_checklist": rep.human_checklist,
        "not_checked": NOT_CHECKED,
        "verification": rep.verification,
    }
