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
    SIGNATURE_TEMPLATE_EN,
    SIGNATURE_TEMPLATE_TH,
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


# พยัญชนะไทยที่ใช้เป็นเลขหน้าส่วนนำ เรียงตามลำดับ ก ข ค ง ...
_THAI_PAGE_LETTERS = "กขคงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ"

_PAGE_LABEL_STYLE_NAME = {
    "roman": "เลขโรมัน (i, ii, iii)",
    "thai": "พยัญชนะไทย (ก, ข, ค)",
    "arabic": "เลขอารบิก (1, 2, 3)",
}


def _page_label_order(label):
    """แปลงเลขหน้าเป็น (ชนิด, ลำดับ) เพื่อตรวจความต่อเนื่อง — (None, None) ถ้าอ่านไม่ออก"""
    label = (label or "").strip()
    if not label:
        return None, None
    if label.isdigit():
        return "arabic", int(label)
    if label in _THAI_PAGE_LETTERS:
        return "thai", _THAI_PAGE_LETTERS.index(label) + 1
    value = _roman_to_int(label)
    return ("roman", value) if value else (None, None)


def _is_page_number_token(token):
    token = (token or '').strip()
    return bool(re.fullmatch(r'\d{1,4}', token)
                or re.fullmatch(r'[ivxlcdm]{1,10}', token, re.I)
                or re.fullmatch(r'[ก-ฮ]', token))


def header_extra_text(pdf_page):
    """ข้อความในแถบหัวกระดาษ (บนสุดของหน้า) ที่ไม่ใช่เลขหน้า

    หัวกระดาษของส่วนเนื้อหา/ส่วนท้ายต้องมีเพียงเลขหน้าเท่านั้น (ห้ามมี running head
    หรือชื่อบท) เลขหน้าอยู่ในระยะขอบบน (~5-6% ของความสูง) ส่วนเนื้อความเริ่ม ~9%+
    จึงตัดที่ 8% เพื่อดูเฉพาะแถบหัวกระดาษ คืน '' ถ้าหัวกระดาษมีแต่เลขหน้า/ว่าง
    """
    height = float(getattr(pdf_page, 'height', 0) or 0)
    if not height:
        return ""
    cutoff = height * 0.08
    extras = []
    for word in (pdf_page.extract_words() or []):
        if float(word.get('top', height)) >= cutoff:
            continue
        # ตัดอักขระ PUA ของฟอนต์ไทย (F700-F70F) ที่ดึงมาเป็นกล่องออกก่อน
        raw = word.get('text', '') or ''
        token = ''.join(c for c in raw if not (0xF700 <= ord(c) <= 0xF70F)).strip()
        if token and not _is_page_number_token(token):
            extras.append(token)
    return ' '.join(extras).strip()


# หน้าลงนามเป็นตารางตายตัวตาม template ส่วนนำ (2 คอลัมน์ × 6 แถวกรรมการ)
#   r0(บนสุด): นักศึกษา(ซ้าย) | กรรมการ 1(ขวา)
#   r1..r4   : กรรมการ 9,8,7,6(ซ้าย) | กรรมการ 2,3,4,5(ขวา)
#   r5(ล่างสุด): คณบดี(ซ้าย) | ผู้อำนวยการหลักสูตร(ขวา)  [ช่องสถาบันคงที่]
# กรรมการเติมขวาบน→ล่าง(1–5) แล้วซ้ายล่าง→บน(6–9); ช่องว่างทิ้ง placeholder
_SIG_SKIP_MARKERS = (
    norm('ตำแหน่งทางวิชาการและชื่อ'), norm('นามสกุล'), 'ACADEMICRANK',
    'FIRSTNAME', 'LASTNAME', norm('คุณวุฒิ'), norm('ระบุสาขาวิชา'), 'DEGREESUBJECT',
    norm('ผู้วิจัย'), 'CANDIDATE', norm('คณบดี'), 'DEAN',
    norm('ประธานหลักสูตร'), 'PROGRAMDIRECTOR', 'DIRECTOR',
)
# ข้อความตัวอย่างของ template ที่ต้องลบ/ถมขาวก่อนส่งเล่ม — ถ้ายังดึงข้อความได้แปลว่า
# ยังอยู่ในไฟล์ (แต่ระบบอ่านข้อความที่ถมขาวไว้ได้ด้วย จึงยืนยันเองไม่ได้ว่ามองเห็นจริง)
_SIG_LEFTOVER_PLACEHOLDERS = (
    (norm('ตำแหน่งทางวิชาการและชื่อ'), 'ตำแหน่งทางวิชาการและชื่อ นามสกุล'),
    (norm('ระบุสาขาวิชา'), 'คุณวุฒิ (ระบุสาขาวิชา)'),
    ('ACADEMICRANK', 'Academic rank First Name Last name'),
    ('DEGREESUBJECT', 'Degree (Subject)'),
)


def _sig_is_dotted(text):
    t = (text or '').strip()
    return len(t) >= 4 and sum(c in '….' for c in t) >= len(t) * 0.6


def _sig_clean_name(text):
    """ตัดคำนำหน้าวิชาการ/คอมมาท้าย เหลือชื่อ-สกุล; คืน None ถ้าเป็น placeholder/ช่องคงที่"""
    n = norm(text)
    if not n or any(m and m in n for m in _SIG_SKIP_MARKERS):
        return None
    # ตัดตำแหน่งวิชาการทั้งหน้าและท้าย — ช่องที่เหลือแต่ตำแหน่ง ไม่มีชื่อคน ถือว่าว่าง
    # (เดิมคืน ", รองศาสตราจารย์" ออกไป แล้วถูกฟ้องว่าเป็นคนที่ไม่อยู่ในรายชื่ออนุมัติ)
    return _strip_committee_title(text) or None


# placeholder ของช่องคุณวุฒิที่ template ทิ้งไว้ = ถือว่ายัง "ไม่มี" คุณวุฒิจริง
_SIG_QUAL_PLACEHOLDERS = (
    'DEGREESUBJECT', norm('ระบุคุณวุฒิ'), norm('คุณวุฒิ'), norm('ระบุสาขาวิชา'),
)


def _sig_qual_text(text):
    """ข้อความคุณวุฒิใต้ชื่อกรรมการ — คืน '' ถ้าว่างหรือเป็น placeholder (ยังไม่กรอกจริง)"""
    n = norm(text)
    if not n or any(m and m in n for m in _SIG_QUAL_PLACEHOLDERS):
        return ''
    return (text or '').strip()


def signature_committee_slots(pdf_page):
    """อ่านตารางลายเซ็นตามกริดตายตัว

    คืน (members, member_quals, bottom_left, bottom_right):
      members = dict{ลำดับกรรมการ 1..9 → ชื่อ (str) หรือ None ถ้าช่องว่าง/placeholder}
      member_quals = dict{ลำดับกรรมการ 1..9 → ข้อความคุณวุฒิใต้ชื่อ ('' ถ้าไม่มี/placeholder)}
      bottom_left/right = ข้อความรวมช่องล่างสุด (คณบดี / ผู้อำนวยการหลักสูตร) ไว้ตรวจคณะ/หลักสูตร
    """
    try:
        words = pdf_page.extract_words(extra_attrs=["non_stroking_color"]) or []
    except Exception:
        words = pdf_page.extract_words() or []
    # ข้อความที่ถมขาวไว้ (มองไม่เห็นบนหน้ากระดาษ) ต้องไม่นับเป็นเนื้อหาของช่อง
    # เล่มจริงพบว่ามีข้อความชั้นเก่าถมขาวทับซ้อนอยู่ ถ้าอ่านรวมจะได้ชื่อกรรมการ
    # ซ้ำหรือไปโผล่ผิดช่อง แล้วฟ้องผิดว่ามีคนเกิน/ชื่อซ้ำ
    words = [w for w in words if not _is_white_fill(w.get("non_stroking_color"))]
    if not words:
        return {}, {}, '', ''
    mid = float(getattr(pdf_page, 'width', 595) or 595) / 2
    lines = []
    for w in sorted(words, key=lambda w: (round(float(w['top'])), float(w['x0']))):
        top = float(w['top'])
        if lines and abs(lines[-1]['top'] - top) <= 6:
            lines[-1]['words'].append(w)
        else:
            lines.append({'top': top, 'words': [w]})
    line_dotted = [_sig_is_dotted(' '.join(w['text'] for w in ln['words'])) for ln in lines]
    # แถวชื่อ = บรรทัดถัดจากเส้นประ; แถวคุณวุฒิ = บรรทัดถัดจากชื่อ (ถ้าไม่ใช่เส้นประ)
    name_rows, qual_rows = [], []
    for i in range(len(lines) - 1):
        if not line_dotted[i]:
            continue
        name_rows.append(lines[i + 1])
        qual_rows.append(lines[i + 2] if (i + 2 < len(lines) and not line_dotted[i + 2])
                         else None)

    def cell(row, left):
        if not row:
            return ''
        toks = [w['text'] for w in sorted(row['words'], key=lambda w: float(w['x0']))
                if (float(w['x0']) < mid) == left]
        return ' '.join(toks).strip()

    members, member_quals = {}, {}
    # แถวเส้นประสุดท้ายคือช่องสถาบัน (คณบดี / ประธานหลักสูตร) ไม่ใช่กรรมการ — ตัดทิ้งเสมอ
    # (เดิมตัดด้วย [:5] ซึ่งพึ่งว่าต้องอ่านเส้นประเจอครบ 6 แถวพอดี ถ้าเจอไม่ครบ
    #  แถวคณบดีจะเลื่อนเข้ามาเป็นกรรมการ แล้วฟ้องว่ามีชื่อนอกรายชื่ออนุมัติ)
    member_rows = list(zip(name_rows, qual_rows))[:-1][:5]
    for idx, (nrow, qrow) in enumerate(member_rows):     # 0..4 = ระดับกรรมการ
        members[idx + 1] = _sig_clean_name(cell(nrow, left=False))   # ขวา → 1..5
        member_quals[idx + 1] = _sig_qual_text(cell(qrow, left=False))
        if idx >= 1:
            members[10 - idx] = _sig_clean_name(cell(nrow, left=True))  # ซ้าย → 9,8,7,6
            member_quals[10 - idx] = _sig_qual_text(cell(qrow, left=True))
    # ช่องล่างสุด (สถาบัน) = ทุกคำใต้แถวกรรมการสุดท้าย เรียงตามบรรทัด (บน→ล่าง, ซ้าย→ขวา)
    # เพื่อไม่ให้ชื่อหลักสูตร/คณะที่อยู่คนละบรรทัดสลับกันจนเทียบไม่เจอ
    floor = (name_rows[4]['top'] + 20) if len(name_rows) >= 5 else \
            (name_rows[-1]['top'] if name_rows else 0)
    ordered = sorted(words, key=lambda w: (round(float(w['top'])), float(w['x0'])))
    bl = ' '.join(w['text'] for w in ordered
                  if float(w['top']) >= floor and float(w['x0']) < mid)
    br = ' '.join(w['text'] for w in ordered
                  if float(w['top']) >= floor and float(w['x0']) >= mid)
    return members, member_quals, bl.strip(), br.strip()


def _committee_page_kind(page_text):
    """หน้าลงนามนี้เป็นหน้าอาจารย์ที่ปรึกษา หรือหน้ากรรมการสอบ (คืน 'advisory'/'exam'/'')"""
    nl = norm(page_text)
    if "EXAMINATION" in nl or norm("กรรมการสอบ") in nl or "CHAIR" in nl:
        return "exam"
    if "ADVISORY" in nl or norm("ที่ปรึกษา") in nl or "MAJORADVISOR" in nl:
        return "advisory"
    return ""


def _degree_subject(degree):
    """ดึงชื่อสาขาในวงเล็บจากชื่อปริญญา เช่น 'Doctor of Philosophy (Tropical Medicine)'
    → 'Tropical Medicine'"""
    m = re.search(r'\(([^)]+)\)', degree or "")
    return m.group(1).strip() if m else ""


# คำนำหน้า/ตำแหน่งวิชาการที่ต้อง "ปล่อยผ่าน" — เทียบเฉพาะชื่อ-สกุล ไม่เทียบคำนำหน้า
_COMMITTEE_TITLE_PREFIX = re.compile(
    r'^[\s,]*(?:'
    r'ศาสตราจารย์เกียรติคุณ|ศาสตราจารย์คลินิก|ศาสตราจารย์|'
    r'รองศาสตราจารย์|ผู้ช่วยศาสตราจารย์|อาจารย์|'
    r'ว่าที่ร้อยตรี|นางสาว|นาง|นาย|'
    r'ผศ\.|รศ\.|ศ\.|ดร\.|'
    r'Clinical\s+Professor|Emeritus\s+Professor|'
    r'Associate\s+Professor|Assistant\s+Professor|Professor|'
    r'Assoc\.?\s*Prof\.?|Asst\.?\s*Prof\.?|Prof\.?|'
    r'Lecturer|Lect\.?|Dr\.?|Mr\.?|Mrs\.?|Miss|Ms\.?'
    r')[\s. ]*', re.I)


# ตำแหน่งที่เขียนไว้ "ท้ายชื่อ" เช่น "ธเนศ เกษศิลป์, ผู้ช่วยศาสตราจารย์" — พบในเล่มจริง
# ถ้าไม่ตัดออกจะเทียบชื่อไม่ตรง แล้วฟ้องผิดว่าไม่อยู่ในรายชื่อกรรมการอนุมัติ
# ใช้รายชื่อคำเดียวกับ prefix (ตัดหัว ^[\s,]* ออกแล้วผูก $ ท้าย) เพื่อไม่ให้ลิสต์ 2 ชุดหลุดกัน
_COMMITTEE_TITLE_SUFFIX = re.compile(
    r'[\s,]+'
    + _COMMITTEE_TITLE_PREFIX.pattern[_COMMITTEE_TITLE_PREFIX.pattern.index('(?:'):]
    + r'$', re.I)


def _strip_committee_title(name):
    """ตัดคำนำหน้า/ตำแหน่งวิชาการทั้งหมดออก เหลือเฉพาะชื่อ-สกุล (วนจนไม่เหลือคำนำหน้า)"""
    s = (name or "").strip()
    prev = None
    while s and s != prev:
        prev = s
        s = _COMMITTEE_TITLE_PREFIX.sub('', s, count=1).strip()
        s = _COMMITTEE_TITLE_SUFFIX.sub('', s, count=1).strip()
    return s.strip(' ,')


def _committee_keyname(name, fuzzy=False):
    """คีย์เทียบชื่อกรรมการ — ตัดคำนำหน้า/ตำแหน่งวิชาการก่อนเสมอ (เทียบเฉพาะชื่อ-สกุล)
    fuzzy=False (เล่มไทย): normalize เทียบตรง
    fuzzy=True (เล่มอังกฤษ, เทียบชื่อแปล): เก็บเฉพาะตัวอักษร/เลข เทียบด้วย ratio
    """
    base = _strip_committee_title(name)
    if fuzzy:
        return re.sub(r'[^a-z0-9ก-๙]', '', norm(base).lower())
    return norm(base)


def _assign_committee_slots(exp_keys, found_keys, fuzzy):
    """จับคู่ช่องกริด(slot)→ดัชนี expected แบบ greedy (best match)

    fuzzy=False: ต้องคีย์ตรงกันเป๊ะ; fuzzy=True: ratio ≥ 0.7
    คืน (slot_to_idx {slot: idx|None}, matched_exp set{idx})
    """
    thr = 0.7 if fuzzy else 1.0
    slot_to_idx, used = {}, set()
    for s in sorted(found_keys):
        fk = found_keys[s]
        best_i, best_r = None, thr - 1e-9
        for i, ek in enumerate(exp_keys):
            if i in used or not ek or not fk:
                continue
            r = (difflib.SequenceMatcher(None, fk, ek).ratio() if fuzzy
                 else (1.0 if fk == ek else 0.0))
            if r >= thr and r > best_r:
                best_i, best_r = i, r
        slot_to_idx[s] = best_i
        if best_i is not None:
            used.add(best_i)
    return slot_to_idx, used


def _report_committee_positions(rep, expected_names, members, loc, fuzzy):
    """เทียบชื่อกรรมการแบบ 'ชุดรายชื่อ' — ใช้ได้ทั้งไทย(เทียบตรง) และอังกฤษ(เทียบชื่อแปลหลวม)

    - ครบทุกคนแต่วางผิดตำแหน่ง = สลับ/เรียงผิด → รวมเป็นข้อความเดียว
    - ไม่ครบชุด = ระบุ ขาด/เกิน ตรง ๆ (กัน cascade ฟ้องเลื่อนทั้งแถว)
    - ถูกต้องทุกตำแหน่ง = เงียบ (ผ่าน)
    expected_names = ชื่อที่จะใช้เทียบ+แสดงผล (เล่มอังกฤษส่งชื่อที่แปลแล้วเข้ามา)
    """
    N = len(expected_names)
    exp_keys = [_committee_keyname(e, fuzzy) for e in expected_names]
    found_slots = {k: members[k] for k in range(1, 10) if members.get(k)}
    found_keys = {k: _committee_keyname(v, fuzzy) for k, v in found_slots.items()}

    slot_to_idx, matched_exp = _assign_committee_slots(exp_keys, found_keys, fuzzy)
    extra_slots = [s for s, i in slot_to_idx.items() if i is None]
    missing_idx = [i for i in range(N) if i not in matched_exp]

    if not extra_slots and not missing_idx:
        # ชื่อครบทุกคน — ต่างแค่ตำแหน่ง (slot ถูก เมื่อ slot_to_idx[s] == s-1)
        wrong = sorted(s for s, i in slot_to_idx.items() if i != s - 1)
        if wrong:
            _report_committee_reorder(rep, expected_names, slot_to_idx, wrong, loc)
        return

    # ชื่อไม่ครบชุด → ระบุ ขาด/เกิน ตรง ๆ
    for i in missing_idx:
        name = expected_names[i]
        rep.add("RED", "front_matter", loc,
                f'ไม่พบกรรมการ "{name}" ตามข้อมูลอนุมัติ',
                f'ต้องมีกรรมการชื่อ "{name}" ตามข้อมูลอนุมัติ (บฑ.)',
                "เพิ่มกรรมการที่ขาดให้ครบตามข้อมูลอนุมัติ", "FRONT.COMMITTEE")
    # ช่องที่ "เกิน" อาจเป็นชื่อคนนอกรายชื่อ (แดง) หรือชื่อกรรมการคนเดิมที่โผล่ซ้ำ
    # อีกช่อง (ส้ม) — กรณีหลังฟ้องว่า "ไม่อยู่ในรายชื่ออนุมัติ" ไม่ได้ เพราะเขาอยู่จริง
    # และระบบแยกไม่ออกว่าเล่มพิมพ์ซ้ำเองหรือระบบอ่านตารางซ้ำ
    for s in extra_slots:
        name = members.get(s) or ""
        dup = next((expected_names[i] for i, ek in enumerate(exp_keys)
                    if ek and ek == found_keys.get(s)), None)
        if dup:
            rep.add("ORANGE", "front_matter", loc,
                    f'พบชื่อ "{name}" ปรากฏซ้ำมากกว่าหนึ่งช่องในตารางลายเซ็น',
                    "กรรมการแต่ละคนต้องมีช่องลงนามช่องเดียว",
                    "ตรวจว่าชื่อนี้ถูกพิมพ์ซ้ำในช่องอื่นหรือไม่ ถ้าซ้ำให้ลบช่องที่เกินออก",
                    "FRONT.COMMITTEE")
        else:
            rep.add("RED", "front_matter", loc,
                    f'พบชื่อ "{name}" ที่ไม่อยู่ในรายชื่อกรรมการอนุมัติ',
                    "รายชื่อกรรมการต้องตรงกับข้อมูลอนุมัติ (บฑ.)",
                    "ตรวจชื่อกรรมการให้ตรงกับข้อมูลอนุมัติ", "FRONT.COMMITTEE")


def _report_thai_committee(rep, expected, members, loc):
    """wrapper: เล่มไทยเทียบชื่อไทยแบบตรง (expected = list ของ dict มี key 'name')"""
    _report_committee_positions(rep, [m["name"] for m in expected], members, loc,
                                fuzzy=False)


def _report_committee_reorder(rep, expected_names, slot_to_idx, wrong, loc):
    """ชื่อครบแต่วางผิดตำแหน่ง: จับคู่สลับตรง ๆ ก่อน ที่เหลือบอกลำดับที่ถูกครั้งเดียว

    slot_to_idx: {slot จริงบนกริด → ดัชนี expected ที่จับคู่ได้}
    ช่อง s ควรมี expected_names[s-1]; ชื่อในช่อง s จริง ๆ ควรไปอยู่ช่อง slot_to_idx[s]+1
    """
    N = len(expected_names)
    described = set()
    for s in wrong:
        if s in described:
            continue
        # ชื่อที่ 'ควร' อยู่ช่อง s (คือ expected[s-1]) ตอนนี้ไปโผล่ช่องไหน?
        home = next((p for p, i in slot_to_idx.items() if i == s - 1), None)
        belongs = slot_to_idx.get(s)
        # คู่สลับกันแท้ ๆ: ช่อง s มีชื่อของ home และช่อง home มีชื่อของ s
        if (home is not None and home != s and home not in described
                and belongs is not None and belongs + 1 == home):
            lo, hi = sorted((s, home))
            rep.add("RED", "front_matter", loc,
                    f'กรรมการครบทุกคน แต่คนที่ {lo} ("{expected_names[lo - 1]}") '
                    f'กับ คนที่ {hi} ("{expected_names[hi - 1]}") สลับตำแหน่งกัน',
                    f'ช่องที่ {lo} ต้องเป็น "{expected_names[lo - 1]}" '
                    f'และช่องที่ {hi} ต้องเป็น "{expected_names[hi - 1]}"',
                    "สลับตำแหน่งกรรมการสองคนนี้ให้ถูกต้อง", "FRONT.COMMITTEE")
            described.add(s)
            described.add(home)
    rest = [s for s in wrong if s not in described]
    if rest:
        order = "  ".join(f'{k}. {expected_names[k - 1]}' for k in range(1, N + 1))
        rep.add("RED", "front_matter", loc,
                "กรรมการครบทุกคน แต่เรียงผิดตำแหน่ง",
                f'ลำดับที่ถูกต้องตามข้อมูลอนุมัติ (บฑ.) คือ {order}',
                "จัดเรียงตำแหน่งกรรมการให้ตรงตามลำดับข้อมูลอนุมัติ", "FRONT.COMMITTEE")


def _committee_translation(committees):
    """แปลชื่อกรรมการไทย→อังกฤษครั้งเดียว คืน (name_en dict, translation_ok bool)
    ใช้ร่วมกันทั้งหน้าลงนามและหน้าบทคัดย่อ (แปลไม่ครบ = ไม่ใช้เทียบชื่อ)"""
    all_th = [m["name"] for key in ("advisory", "exam")
              for m in committees.get(key, [])]
    if not all_th:
        return {}, False
    try:
        import llm_assist
        translated = llm_assist.translate_names(all_th)
    except Exception:
        translated = []
    if len(translated) == len(all_th) and all(str(t).strip() for t in translated):
        return dict(zip(all_th, translated)), True
    return {}, False


def _is_white_fill(color):
    """สีตัวอักษรเป็นสีขาว (ถมขาว = มองไม่เห็นบนหน้ากระดาษ) หรือไม่"""
    if color is None:
        return False
    values = (color,) if isinstance(color, (int, float)) else tuple(color)
    try:
        nums = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    if not nums:
        return False
    if len(nums) == 4:                      # CMYK: ขาวคือ 0,0,0,0
        return all(v == 0 for v in nums)
    return all(v >= 0.99 for v in nums)     # gray / RGB: ขาวคือ 1


def sig_visible_placeholders(pdf_page):
    """ข้อความตัวอย่างของ template ที่ยัง "มองเห็นได้" บนหน้าลงนาม (ไม่ได้ถมขาว)

    เล่มจริงถมขาวช่องที่ไม่ได้ใช้ ข้อความจึงยังถูกดึงออกมาได้แม้มองไม่เห็น
    ถ้าเช็คจากข้อความอย่างเดียวจะฟ้องทุกเล่มจนกลายเป็น noise จึงต้องดูสีตัวอักษรด้วย
    """
    try:
        words = pdf_page.extract_words(extra_attrs=["non_stroking_color"])
    except Exception:
        return []
    visible = norm(" ".join(w["text"] for w in words
                            if not _is_white_fill(w.get("non_stroking_color"))))
    return [label for key, label in _SIG_LEFTOVER_PLACEHOLDERS if key and key in visible]


def _check_signature_institution(rep, kind, bottom_text, approved, english_book,
                                 loc_prefix="", loc_suffix=""):
    """ช่องสถาบันแถวล่างสุดของหน้าลงนาม — บทบาทต่างกันในสองหน้า (ยืนยันจาก template ทางการ)

    หน้าอาจารย์ที่ปรึกษา: มุมล่างขวา = "ประธานหลักสูตร ... สาขาวิชา ..." → ต้องมีชื่อสาขา
    หน้ากรรมการสอบ     : มุมล่างขวา = "คณบดี/ผู้อำนวยการคณะ/สถาบัน ..." → ต้องมีชื่อคณะ
    มุมล่างซ้ายเป็นคณบดีบัณฑิตวิทยาลัยทั้งสองหน้า จึงไม่ใช้ตรวจคณะของนักศึกษา

    ค้นจากข้อความ "ทั้งแถวล่าง" (ซ้าย+ขวา) เพราะการแบ่งคอลัมน์ด้วยพิกัด x คลาดเคลื่อน
    ได้เมื่อข้อความไทยยาวล้ำกึ่งกลางหน้า — ช่องซ้ายเป็นบัณฑิตวิทยาลัยเสมอ จึงไม่ชนกัน
    """
    found_text = norm(bottom_text)
    if kind == "advisory":
        degree = approved.get("degree_cover_th" if not english_book else "degree_cover_en", "") \
            or approved.get("degree_cover_en", "")
        subject = _degree_subject(degree)
        if subject and norm(subject) not in found_text:
            rep.add("ORANGE", "front_matter", f"{loc_prefix}ประธานหลักสูตร{loc_suffix}",
                    f'ไม่พบชื่อสาขา "{subject}" ในช่องประธานหลักสูตร (มุมล่างขวา)',
                    f'ข้อความใต้ลายเซ็นต้องเป็นชื่อหลักสูตรที่มีสาขา "{subject}"',
                    "โปรดตรวจชื่อหลักสูตรมุมล่างขวาให้ถูกต้อง", "FRONT.COMMITTEE")
        return
    # เล่มอังกฤษเทียบชื่อคณะไม่ได้ เพราะชื่อคณะจาก eThesis เป็นภาษาไทย
    faculty = approved.get("faculty", "")
    if faculty and not english_book and norm(faculty) not in found_text:
        rep.add("ORANGE", "front_matter", f"{loc_prefix}คณบดีคณะ{loc_suffix}",
                f'ไม่พบชื่อคณะ "{faculty}" ในช่องคณบดีคณะ (มุมล่างขวา)',
                f'ข้อความใต้ลายเซ็นควรเป็นคณะที่นักศึกษาสังกัด คือ "{faculty}"',
                "โปรดตรวจชื่อคณะมุมล่างขวาให้ถูกต้อง", "FRONT.COMMITTEE")


def _report_sig_placeholders(rep, found, loc):
    """ช่องกรรมการที่ไม่ได้ใช้ต้องลบ/ถมขาวข้อความตัวอย่างของ template

    ยังเป็นส้มเพราะข้อความที่ไม่ใช่สีขาวอาจถูกกล่องทึบทับไว้อีกชั้น ระบบยืนยันเองไม่ได้
    """
    if not found:
        return
    rep.add("ORANGE", "front_matter", loc,
            "พบข้อความตัวอย่างของ template ค้างอยู่ในตารางลายเซ็น: "
            + ", ".join(f'"{label}"' for label in found),
            "ช่องกรรมการที่ไม่ได้ใช้ต้องลบข้อความตัวอย่างออกจากไฟล์",
            "ตรวจว่าข้อความนี้มองเห็นบนหน้ากระดาษหรือไม่ ถ้าเห็นให้ลบออกจากช่องที่ไม่ได้ใช้",
            "FRONT.COMMITTEE")


def _check_committees(rep, committees, sig_pages, pages, pdf_path, page_ref,
                      program_language, A, name_en, translation_ok):
    """ตรวจรายชื่อ+คุณวุฒิกรรมการบนหน้าลงนามเทียบข้อมูลอนุมัติ (ตามกริดตายตัวของ template)

    เล่มไทย: เทียบชื่อไทยแบบชุด (สลับ/ขาด/เกิน = แดง)
    เล่มอังกฤษ/นานาชาติ: ถ้า AI แปลชื่อครบ → เทียบตามลำดับเหมือนเล่มไทย (แดง);
      ถ้าแปลไม่ได้ → ส้มให้เจ้าหน้าที่ตรวจเอง
    คุณวุฒิใต้ชื่อ: ไม่ตรวจเนื้อหา แต่ต้อง "มี" — ไม่มี = แดง
    คืน True ถ้าตรวจได้ (อ่านตารางเจอ) — ไม่งั้น False (ให้เจ้าหน้าที่ตรวจเอง)
    """
    english_book = program_language in ("international", "thai_english")

    # อ่านตารางลายเซ็นของหน้าลงนามด้วย geometry (เปิดไฟล์เฉพาะ 2 หน้า)
    slots, leftover = {}, {}
    try:
        with pdfplumber.open(pdf_path) as _pl:
            for idx in sig_pages[:2]:
                if 0 <= idx < len(_pl.pages):
                    slots[idx] = signature_committee_slots(_pl.pages[idx])
                    leftover[idx] = sig_visible_placeholders(_pl.pages[idx])
    except Exception:
        return False
    if not slots:
        return False

    handled_any = False
    for idx in sig_pages[:2]:
        if idx not in slots or idx >= len(pages):
            continue
        kind = _committee_page_kind(pages[idx])
        expected = committees.get(kind, []) if kind else []
        if not expected:
            continue
        handled_any = True
        members, member_quals, bottom_left, bottom_right = slots[idx]
        page_label = "หน้าอาจารย์ที่ปรึกษา" if kind == "advisory" else "หน้ากรรมการสอบ"
        loc = f"{page_label} ({page_ref(idx)})"
        _report_sig_placeholders(rep, leftover.get(idx) or [], loc)

        if not english_book:
            # เล่มไทย: เทียบชื่อไทยแบบชุด (สลับ/ขาด/เกิน) — กัน cascade
            _report_thai_committee(rep, expected, members, loc)
        elif translation_ok:
            # เล่มอังกฤษ + แปลชื่อครบ: เทียบตามลำดับแบบเดียวกับเล่มไทย (เทียบหลวมจากชื่อแปล)
            expected_en = [name_en[m["name"]] for m in expected]
            _report_committee_positions(rep, expected_en, members, loc, fuzzy=True)
        else:
            # เล่มอังกฤษ + แปลไม่ได้ (ไม่มี API key/แปลไม่สำเร็จ): ลงส้มให้เจ้าหน้าที่ตรวจเอง
            names_th = "  ".join(f'{k}. {m["name"]}'
                                 for k, m in enumerate(expected, start=1))
            rep.add("ORANGE", "front_matter", loc,
                    "ระบบแปลชื่อกรรมการเป็นอังกฤษไม่ได้ จึงเทียบชื่ออัตโนมัติไม่ได้",
                    f"ต้องมีกรรมการ {len(expected)} คนตามลำดับ บฑ. คือ {names_th}",
                    "โปรดตรวจรายชื่อและตำแหน่งกรรมการบนหน้านี้ด้วยตา", "FRONT.COMMITTEE")

        # ---------- คุณวุฒิใต้ชื่อ: ไม่ตรวจเนื้อหา แต่ต้องมีทุกคน ----------
        # ตรวจเฉพาะช่องกรรมการจริง (1..N) — ช่องที่อ่านเพี้ยนถูกฟ้องเรื่องชื่อไปแล้ว
        for k in range(1, len(expected) + 1):
            if members.get(k) and not member_quals.get(k):
                rep.add("RED", "front_matter", loc,
                        f'ไม่พบคุณวุฒิใต้ชื่อกรรมการคนที่ {k} ("{members[k]}")',
                        "ใต้ชื่อกรรมการแต่ละคนต้องมีบรรทัดคุณวุฒิ (Degree)",
                        "เพิ่มบรรทัดคุณวุฒิใต้ชื่อกรรมการให้ครบทุกคน", "FRONT.COMMITTEE")

        _check_signature_institution(
            rep, kind, bottom_left + " " + bottom_right, A, english_book,
            f"{page_label} — ", f" ({page_ref(idx)})")

    return handled_any


_ABS_COMMITTEE_HEADING = re.compile(
    r'(?:ADVISORY\s+COMMITTEE|คณะกรรมการที่ปรึกษา\S*)\s*:', re.I)


def abstract_committee_block(page_text):
    """ดึงบรรทัดรายชื่อคณะกรรมการที่ปรึกษาบนหน้าบทคัดย่อ (รวมบรรทัดที่ห่อคำ)

    คืน (is_english, block) หรือ None ถ้าไม่พบ
      is_english = หัวข้อเป็นภาษาอังกฤษ (ต้องเป็นตัวพิมพ์ใหญ่)
      block = ข้อความหลัง ':' ถึงก่อนหัวข้อ ABSTRACT/บทคัดย่อ (รวมเป็นบรรทัดเดียว)
    """
    m = _ABS_COMMITTEE_HEADING.search(page_text or "")
    if not m:
        return None
    is_english = "ADVISORY" in (page_text[m.start():m.end()].upper())
    tail = page_text[m.end():]
    stop = re.search(r'\n\s*(?:ABSTRACT|บทคัดย่อ)\b', tail)
    block = tail[:stop.start()] if stop else "\n".join(tail.split("\n")[:4])
    return is_english, re.sub(r'\s*\n\s*', ' ', block).strip()


def split_abstract_committee(block):
    """แยก 'ชื่อ, คุณวุฒิ, ชื่อ, คุณวุฒิ, ...' → (names, degrees) ตามลำดับ"""
    toks = [t.strip() for t in (block or "").split(",") if t.strip()]
    return toks[0::2], toks[1::2]


def _check_abstract_committees(rep, committees, abs_en_pages, abs_th_pages, pages,
                               page_ref, name_en, translation_ok):
    """ตรวจรายชื่อคณะกรรมการที่ปรึกษาบนหน้าบทคัดย่อ (ชื่อ + รูปแบบ)

    รูปแบบต่อคน = 'ชื่อ นามสกุล, คุณวุฒิ' — ไม่มีสาขาในวงเล็บ, ไม่มีตำแหน่งวิชาการ
    หน้าอังกฤษ: ชื่อต้องเป็นตัวพิมพ์ใหญ่ทั้งหมด และเทียบชื่อจากคำแปล (fuzzy)
    หน้าไทย: เทียบชื่อไทยตรง

    กฎ "รูปแบบ" เป็นกฎของ template ล้วน จึงตรวจได้แม้ไม่มีข้อมูลกรรมการจาก eThesis
    ส่วนการเทียบ "ชื่อและลำดับ" ทำเฉพาะเมื่อมีข้อมูลอนุมัติ
    """
    advisory = (committees or {}).get("advisory", [])
    for page_list, heading_en in ((abs_en_pages, True), (abs_th_pages, False)):
        for ai in page_list:
            if ai >= len(pages):
                continue
            parsed = abstract_committee_block(pages[ai])
            if not parsed:
                continue
            _, block = parsed
            names, degrees = split_abstract_committee(block)
            if not names:
                continue
            loc = f"บทคัดย่อ ({page_ref(ai)}) — คณะกรรมการที่ปรึกษา"

            # รูปแบบ 1: ห้ามมีสาขาวิชาในวงเล็บ
            if "(" in block or ")" in block:
                rep.add("RED", "front_matter", loc,
                        "รายชื่อกรรมการที่ปรึกษามีสาขาวิชาในวงเล็บ",
                        "รูปแบบต้องเป็น 'ชื่อ นามสกุล, คุณวุฒิ' โดยไม่มีสาขาวิชาในวงเล็บ",
                        "ลบสาขาวิชาในวงเล็บออกจากคุณวุฒิ", "FRONT.ABSTRACT")
            for i, name in enumerate(names, start=1):
                nm = name.strip()
                # รูปแบบ 2: ห้ามมีตำแหน่งทางวิชาการนำหน้าชื่อ
                if _strip_committee_title(nm) != nm:
                    rep.add("RED", "front_matter", loc,
                            f'ชื่อกรรมการคนที่ {i} มีตำแหน่งทางวิชาการนำหน้า: "{nm}"',
                            "รูปแบบต้องเป็นชื่อ-สกุลและคุณวุฒิเท่านั้น ไม่มีตำแหน่งทางวิชาการ",
                            "ลบตำแหน่งทางวิชาการนำหน้าชื่อออก", "FRONT.ABSTRACT")
                # รูปแบบ 3: หน้าอังกฤษ ชื่อต้องเป็นตัวพิมพ์ใหญ่ทั้งหมด
                if heading_en and re.search(r'[a-z]', nm):
                    rep.add("RED", "front_matter", loc,
                            f'ชื่อกรรมการคนที่ {i} ไม่ได้เป็นตัวพิมพ์ใหญ่ทั้งหมด: "{nm}"',
                            "ชื่อกรรมการในบทคัดย่อภาษาอังกฤษต้องเป็นตัวพิมพ์ใหญ่ทั้งหมด",
                            "แก้ชื่อกรรมการเป็นตัวพิมพ์ใหญ่ทั้งหมด", "FRONT.ABSTRACT")

            # เทียบชื่อกับข้อมูลอนุมัติ (advisory) แบบเดียวกับหน้าลงนาม
            if not advisory:
                continue
            members = {i: n.strip() for i, n in enumerate(names, start=1)}
            if heading_en:
                if translation_ok:
                    expected = [name_en[m["name"]] for m in advisory]
                    _report_committee_positions(rep, expected, members, loc, fuzzy=True)
            else:
                expected = [m["name"] for m in advisory]
                _report_committee_positions(rep, expected, members, loc, fuzzy=False)


_ERA_PREFIX = re.compile(r'พ\.?\s*ศ\.?|ค\.?\s*ศ\.?|B\.?\s*E\.?|A\.?\s*D\.?', re.I)


def _exam_date_key(text):
    """คีย์เทียบวันที่ — ตัดคำระบุศักราชและเลข 0 นำหน้าวันที่ออกก่อน

    หน้าลงนามเล่มไทยมักเขียน "วันที่ 11 พฤษภาคม พ.ศ. 2569" (มีคำระบุศักราชคั่นระหว่าง
    เดือนกับปี) แต่ข้อมูลอนุมัติเป็น "11 พฤษภาคม 2569" ถ้าไม่ตัดออกจะฟ้องผิด
    """
    return norm(re.sub(r'\b0([1-9])', r'\1', _ERA_PREFIX.sub(' ', text or "")))


def _check_exam_date(rep, exam_date, sig_pages, pages, page_ref):
    """วันที่สอบต้องตรงข้อมูลอนุมัติ "ทุกหน้าลงนาม"

    เดิมรวมข้อความสองหน้าลงนามแล้วค้นครั้งเดียว หน้าที่วันที่ผิดหรือหายจึงรอดไปได้
    ถ้าอีกหน้าหนึ่งถูก และตารางยืนยันก็ขึ้นเป็นแถวเดียวแทนที่จะแยกรายหน้า
    """
    if not sig_pages:
        rep.add_verification("วันที่สอบผ่าน", "หน้าลงนาม", "pending",
                             "ระบบหาหน้าลงนามไม่เจอ")
        return
    for k, idx in enumerate(sig_pages):
        loc = f"หน้าลงนาม {k + 1} ({page_ref(idx)})"
        if _exam_date_key(exam_date) in _exam_date_key(pages[idx]):
            rep.add_verification("วันที่สอบผ่าน", loc, "pass")
            continue
        found_date = find_signature_date(pages[idx])
        rep.add_verification("วันที่สอบผ่าน", loc, "fail", found_date)
        if found_date:
            # มีวันที่บนหน้าลงนามแต่ วัน/เดือน/ปี ไม่ตรงกับข้อมูลในระบบ
            rep.add("RED", "front_matter", loc,
                    f'พบวันที่สอบผ่านไม่ตรงกันกับในระบบ: "{found_date}"',
                    f'ที่ถูกต้องตามระบบคือ "{exam_date}"',
                    "แก้วันที่บนหน้าลงนามให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")
        else:
            rep.add("RED", "front_matter", loc, f'ไม่พบวันที่สอบ "{exam_date}"',
                    "วันที่บนหน้าลงนาม = วันที่มีผลสอบผ่าน", "", "FORM.APPROVED_MATCH")


def _check_cover_year(rep, year, cover_text):
    """ปีต้องอยู่ใน "บรรทัดปี" ของหน้าปก ไม่ใช่เจอเลขปีที่ไหนก็ได้บนหน้า

    ชื่อเรื่องบางเล่มมีปีอยู่ในชื่อ การค้นทั้งหน้าจึงผ่านได้ทั้งที่หน้าปกไม่มีบรรทัดปี
    บรรทัดปีอาจเขียน "2569" หรือ "พ.ศ. 2569" ก็ได้
    """
    lines = [soft(line) for line in (cover_text or "").splitlines() if soft(line)]
    year_lines = [line for line in lines if year in line]
    year_ok = any(soft(_ERA_PREFIX.sub(' ', line)) == year for line in year_lines)
    rep.add_verification("ปีบนหน้าปก", "หน้าปก", "pass" if year_ok else "fail",
                         "" if year_ok else (year_lines[0] if year_lines else ""))
    if year_ok:
        return
    if year_lines:
        rep.add("RED", "front_matter", "หน้าปก",
                f'พบปี {year} บนหน้าปกแต่ไม่ได้อยู่ในบรรทัดปีของตัวเอง: "{year_lines[0]}"',
                f'หน้าปกต้องมีบรรทัดที่เป็นปีเพียงอย่างเดียว เช่น "{year}" หรือ "พ.ศ. {year}"',
                "เพิ่มหรือแก้บรรทัดปีบนหน้าปกให้มีเฉพาะปี", "FRONT.COVER")
    else:
        rep.add("RED", "front_matter", "หน้าปก", f"ไม่พบปี {year} บนหน้าปก",
                "ปี = ปีที่มีผลสอบผ่าน", "", "FRONT.COVER")


def _expected_front_label_style(program_language):
    """ชนิดเลขหน้าส่วนนำตามภาษาของเล่ม — เล่มหลักสูตรไทยใช้พยัญชนะ นอกนั้นใช้โรมัน

    (เล่ม thai_english ใช้ปก/หน้าลงนามภาษาอังกฤษ จึงนับเป็นเล่มอังกฤษเหมือน international)
    คืน None เมื่อยังไม่รู้ภาษาเล่ม → ตรวจได้แค่ว่าชนิดต้องไม่ปนกันและไม่ใช่อารบิก
    """
    if not program_language:
        return None
    return "thai" if program_language == "thai" else "roman"


def _check_front_page_numbers(rep, page_labels, page_ref, start_idx, stop_idx,
                              expected_style=None):
    """เลขหน้าส่วนนำ: ชนิดต้องตรงภาษาเล่ม และเรียงต่อเนื่อง ไม่ซ้ำ ไม่ข้าม

    เดิมตรวจเฉพาะค่าเลขหน้าของหน้าลงนาม 2 หน้าแรก (i/ii หรือ ก/ข) หน้าอื่นของส่วนนำ
    จึงไม่ถูกตรวจเลย ฟังก์ชันนี้ตรวจทั้งช่วง จึงไม่ทับกับกฎเดิมที่ตรวจ "ค่าเริ่มต้น"
    ของหน้าลงนาม
    """
    if stop_idx is None or stop_idx <= start_idx:
        return
    entries, unread = [], []
    for i in range(start_idx, stop_idx):
        label = page_labels.get(i, "")
        style, value = _page_label_order(label)
        if style is None:
            unread.append(i)
        else:
            entries.append((i, label, style, value))

    if expected_style:
        main_style = expected_style
        want = _PAGE_LABEL_STYLE_NAME[expected_style]
        book = "เล่มหลักสูตรไทย" if expected_style == "thai" else "เล่มภาษาอังกฤษ"
        want_sentence = f"เลขหน้าส่วนนำของ{book}ต้องเป็น{want} ทั้งส่วน"
    else:
        # ไม่รู้ภาษาเล่ม → ยึดชนิดที่ใช้มากที่สุด (ไม่นับอารบิกซึ่งผิดแน่นอน)
        found = [s for _i, _lab, s, _v in entries if s != "arabic"]
        main_style = max(set(found), key=found.count) if found else None
        want = (_PAGE_LABEL_STYLE_NAME[main_style] if main_style
                else "เลขโรมัน (i, ii, iii) หรือพยัญชนะไทย (ก, ข, ค)")
        want_sentence = f"เลขหน้าส่วนนำต้องเป็น{want} ทั้งส่วน"

    off_style = [(i, lab, s) for i, lab, s, _v in entries if s != main_style]
    if off_style:
        found_names = " / ".join(sorted({_PAGE_LABEL_STYLE_NAME[s]
                                         for _i, _lab, s in off_style}))
        shown = ", ".join(f'{page_ref(i)} ("{lab}")' for i, lab, _s in off_style[:5])
        more = f" และอีก {len(off_style) - 5} หน้า" if len(off_style) > 5 else ""
        rep.add("RED", "front_matter", "ส่วนนำ",
                f"ส่วนนำใช้{found_names} {len(off_style)} หน้า: {shown}{more}",
                want_sentence, f"แก้เลขหน้าส่วนนำให้เป็น{want}", "PAGE.NUMBERING")

    seq = [e for e in entries if e[2] == main_style]
    if len(seq) > 1:
        # หน้าที่อ่านเลขไม่ได้/ใช้ชนิดผิด ถูกฟ้องแยกไปแล้ว และทำให้ยืนยันความต่อเนื่อง
        # ข้ามหน้านั้นไม่ได้ จึงไม่ฟ้อง "กระโดด" คร่อมหน้าเหล่านี้ (กันฟ้องซ้ำ/ฟ้องผิด)
        broken = set(unread) | {i for i, _lab, _s in off_style}
        problems, dup_run = [], 1
        for k in range(1, len(seq)):
            prev_i, prev_lab, _ps, prev_v = seq[k - 1]
            cur_i, cur_lab, _cs, cur_v = seq[k]
            if any(j in broken for j in range(prev_i + 1, cur_i)):
                dup_run = 1
                continue
            if cur_v != prev_v:
                dup_run = 1
                if cur_v != prev_v + 1:
                    problems.append(f'กระโดดจาก "{prev_lab}" ไป "{cur_lab}"')
                continue
            # หลายหน้าใช้เลขเดียวกัน — รวมเป็นข้อความเดียว ไม่ฟ้องทีละคู่
            dup_run += 1
            if k == len(seq) - 1 or seq[k + 1][3] != cur_v:
                problems.append(f'เลขหน้า "{cur_lab}" ถูกใช้ซ้ำ {dup_run} หน้า')
                dup_run = 1
        if problems:
            more = f" และอีก {len(problems) - 5} จุด" if len(problems) > 5 else ""
            observed = ", ".join(lab for _i, lab, _s, _v in seq)
            rep.add("RED", "front_matter", "ส่วนนำ",
                    "เลขหน้าส่วนนำไม่ต่อเนื่อง: " + "; ".join(problems[:5]) + more,
                    f"เลขหน้าส่วนนำต้องเรียงต่อเนื่องทีละหน้า ไม่ซ้ำ ไม่ข้าม (ที่พบ: {observed})",
                    "แก้เลขหน้าส่วนนำให้เรียงต่อเนื่องทีละหน้า", "PAGE.NUMBERING")

    if unread:
        def _after_ref(idx):
            for j in range(idx - 1, start_idx - 1, -1):
                if page_labels.get(j):
                    return f"หน้าถัดจาก{page_ref(j)}"
            return "หน้าไม่ระบุเลข"
        shown = ", ".join(_after_ref(i) for i in unread[:5])
        more = f" และอีก {len(unread) - 5} หน้า" if len(unread) > 5 else ""
        rep.add(UNCERTAIN_ZONE, "front_matter", "ส่วนนำ",
                f"ระบบอ่านเลขหน้าส่วนนำไม่ได้ {len(unread)} หน้า: {shown}{more}",
                f"ทุกหน้าของส่วนนำต้องมีเลขหน้าเป็น{want}",
                "ตรวจด้วยตาว่าหน้าเหล่านี้มีเลขหน้าถูกต้องและต่อเนื่อง", "UNCERTAIN.REVIEW")


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
    """Return the run of text closest to the approved value (may span lines).

    ชื่อเรื่องบนหน้าลงนาม/หน้าปกมักถูกตัดขึ้น 2-4 บรรทัด เช่น
      "An evaluation ... system using" / "ISO/IEC 25010 software quality model"
    ถ้าคืนแค่บรรทัดเดียวที่ใกล้ที่สุด ข้อความ "ที่พบ" ในรายงานจะไม่ครบ และ
    describe_diff จะฟ้องว่า "ขาด ..." ทั้งที่ข้อความอยู่ครบแค่คนละบรรทัด จึงลองรวม
    บรรทัดต่อเนื่อง 1-4 บรรทัดแล้วเลือกช่วงที่ใกล้เคียงข้อมูลอนุมัติที่สุด
    """
    lines = [soft(line) for line in (page_text or '').splitlines() if soft(line)]
    if not lines:
        return "(ไม่พบข้อความ)"
    target = norm(expected)
    best, best_ratio = lines[0], -1.0
    for start in range(len(lines)):
        for span in range(1, 5):
            if start + span > len(lines):
                break
            window = ' '.join(lines[start:start + span])
            ratio = difflib.SequenceMatcher(None, target, norm(window)).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best


# ---------- ข้อความสรุปสำหรับคัดลอก ----------
# ส่วนประกอบของเล่มเรียงตามลำดับที่ปรากฏจริง เพื่อให้เจ้าหน้าที่ไล่แก้จากหน้าแรกไปหน้าสุดท้าย
SUMMARY_SECTIONS = [
    ("หน้าปก", "หน้าปก"),
    ("หน้าลงนาม", "หน้าลงนาม"),
    ("กิตติกรรมประกาศ", "กิตติกรรม"),
    ("บทคัดย่อ", "บทคัดย่อ"),
    ("สารบัญ", "สารบัญ"),
    ("เนื้อหา (บท)", r"บทที่|เนื้อหา|ทั้งเล่ม"),
    ("ส่วนท้ายเล่ม", r"บรรณานุกรม|อ้างอิง|ภาคผนวก|ประวัติ"),
]
SUMMARY_SECTION_ORDER = [name for name, _ in SUMMARY_SECTIONS] + ["อื่น ๆ"]

# ตัดข้อความเชิงเทคนิค/คำต่อรองออกจากข้อความสรุป (รายละเอียดในรายงานยังคงเดิมทุกตัวอักษร)
_SUMMARY_NOISE = re.compile(
    r"\s*\(\s*typo[^)]*\)"
    r"|\s*แต่คู่มือแสดงแบบที่พบ\s*[—-]\s*เจ้าหน้าที่ยืนยันได้"
    r"|\s*[—-]\s*เจ้าหน้าที่ยืนยันได้", re.I)
_SUMMARY_LEAD = re.compile(r"^(ข้อความที่ถูกต้อง|ควรเป็น|ต้องเป็น|ที่ถูก)\s*[:：]?\s*")


def summary_tidy(text):
    text = _SUMMARY_NOISE.sub("", text or "")
    text = re.sub(r"\s+([:：])", r"\1", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def summary_section(issue):
    """ส่วนของเล่มที่ต้องไปแก้ — ใช้ส่วนที่ถูกเอ่ยถึงก่อนในตำแหน่ง

    เช่น "สารบัญ (หน้า viii) ↔ บทคัดย่อภาษาไทย" ต้องไปแก้ที่สารบัญ ไม่ใช่บทคัดย่อ
    """
    text = f"{issue.get('location', '')} {issue.get('part', '')}"
    best, best_at = None, len(text) + 1
    for name, pattern in SUMMARY_SECTIONS:
        found = re.search(pattern, text)
        if found and found.start() < best_at:
            best, best_at = name, found.start()
    return best or "อื่น ๆ"


def issues_to_fix(report, failed=None, passed=None):
    """รายการที่ต้องแก้ในสรุป

    - สีแดง: เข้าสรุปเสมอ
    - สีส้ม (รอยืนยัน): เข้าสรุป**โดยปริยาย** เพราะเป็นจุดที่ต่างจากข้อมูลอนุมัติ
      นักศึกษาควรรับรู้ เว้นแต่เจ้าหน้าที่กด "ผ่าน" (ยอมรับได้) จึงตัดออก
    - สีเหลือง (ข้อสังเกต): เข้าสรุปเฉพาะที่เจ้าหน้าที่กด "ไม่ผ่าน"

    failed/passed เป็นชุดคีย์รูปแบบ "ZONE:index" เช่น {"ORANGE:0", "YELLOW:2"}
    """
    failed = set(failed or ())
    passed = set(passed or ())
    items = list(report["issues_by_zone"].get("RED") or [])
    for index, issue in enumerate(report["issues_by_zone"].get("ORANGE") or []):
        if f"ORANGE:{index}" not in passed:
            items.append(issue)
    for index, issue in enumerate(report["issues_by_zone"].get("YELLOW") or []):
        if f"YELLOW:{index}" in failed:
            items.append(issue)
    return items


def _corrected_value(issue):
    """ค่าที่ควรเป็น (ข้อความในเครื่องหมายคำพูดท้าย expected/fix) ถ้ามี

    ใช้ทั้งจัดประโยคให้กระชับ และเป็นกุญแจรวมรายการซ้ำ — ตำแหน่งเดียวกันและค่าที่
    ต้องแก้เหมือนกัน ถือเป็น "จุดเดียว" (การครอสเช็ค 3 ทางอาจรายงานชื่อบทเดียวกัน
    ทั้งตอนเทียบสารบัญและเทียบประกาศ ซึ่งสำหรับนักศึกษาคือการแก้จุดเดียว)
    """
    raw = summary_tidy(issue.get("expected")) or summary_tidy(issue.get("fix"))
    raw = _SUMMARY_LEAD.sub("", raw)
    match = re.search(r'"([^"]+)"\s*$', raw)
    return match.group(1) if match else ""


def _prose_found(found):
    """แปลงข้อความ "ที่พบ" ให้เป็นสำนวนคน ไม่ใช้ลูกศร

    describe_diff ต่อท้ายว่า '— ต่างที่ "A" → "B"' ซึ่งมีเครื่องหมายลูกศร จึงตัดค่า
    ที่ถูกต้อง (→ "B") ออก เพราะจะบอกครบอีกทีในท่อน "ให้แก้ไขเป็น" และเปลี่ยน
    "— ต่างที่" เป็นคำเชื่อม "แตกต่างที่"
    """
    found = summary_tidy(found)
    found = re.sub(r'\s*→\s*"[^"]*"', '', found)
    found = re.sub(r'\s*→\s*\S+', '', found)
    found = re.sub(r'\s*[—–-]\s*ต่างที่', ' แตกต่างที่', found)
    return re.sub(r'\s{2,}', ' ', found).strip()


def _prose_location(location):
    """ทำตำแหน่งให้เป็นสำนวนคน ไม่ให้มีสัญลักษณ์ตกค้าง (↔ → ใช้คำเชื่อมแทน)"""
    loc = summary_tidy(location)
    loc = loc.replace("↔", "เทียบกับ").replace("→", "ถึง")
    return re.sub(r"\s{2,}", " ", loc).strip()


def _summary_sentence(issue):
    """ประโยคเดียวต่อหนึ่งจุด: "ใน<ตำแหน่ง>: <ที่พบ> ให้แก้ไขเป็น: <ค่าที่ควรเป็น>" """
    loc = _prose_location(issue.get("location"))
    found = _prose_found(issue.get("found"))
    if loc and found:
        sentence = f"ใน{loc}: {found}"
    else:
        sentence = f"ใน{loc}" if loc else found
    value = _corrected_value(issue)
    if value:
        return f'{sentence} ให้แก้ไขเป็น: "{value}"'.strip()
    # ไม่มีค่าเดี่ยวให้ดึง (เช่น มี 2 ตัวเลือก "ก/i") — ต่อท้าย expected/fix ตามเดิม
    # โดยไม่ตัดคำนำ "ควรเป็น/ต้องเป็น" ออก เพราะในกรณีนี้มันช่วยให้อ่านรู้เรื่อง
    directive = summary_tidy(issue.get("expected")) or summary_tidy(issue.get("fix"))
    return f"{sentence} {directive}".strip() if directive else sentence.strip()


def _dedupe_issues(items):
    """รวมรายการที่เป็นจุดเดียวกัน (ตำแหน่ง + ค่าที่ต้องแก้ ตรงกัน) ให้เหลือรายการเดียว

    เก็บรายการแรกที่พบ ยกเว้นถ้ารายการหลังอ้าง "ประกาศ" (แหล่งอำนาจสูงสุด) ให้ใช้แทน
    """
    kept = {}
    order = []
    for issue in items:
        value = _corrected_value(issue) or _prose_found(issue.get("found"))
        key = (summary_tidy(issue.get("location")), value)
        if key not in kept:
            kept[key] = issue
            order.append(key)
        elif "ประกาศ" in (issue.get("expected") or "") \
                and "ประกาศ" not in (kept[key].get("expected") or ""):
            kept[key] = issue
    return [kept[key] for key in order]


def plain_summary(report, failed=None, passed=None):
    """สรุปจุดที่ต้องแก้เป็นข้อความล้วน จัดกลุ่มตามส่วนของเล่ม (ไว้คัดลอก/ให้ AI เรียบเรียง)

    เขียนเป็นประโยคภาษาคน ใช้คำเชื่อม ไม่ใช้เครื่องหมาย - หรือ → และไล่เลขทุกจุด
    ไม่แยกระดับความรุนแรง — ทุกข้อในสรุปคือ "กรุณาแก้ไข" เหมือนกันหมด (รวมสีส้มด้วย)
    """
    items = _dedupe_issues(issues_to_fix(report, failed, passed))
    lines = [f"ผลการตรวจ: {report.get('verdict', '')}"]
    if not items:
        lines.append("\nไม่พบจุดที่ต้องแก้ไข")
        return "\n".join(lines).strip()

    lines.append(f"\nกรุณาแก้ไขทั้งหมด {len(items)} จุด ดังต่อไปนี้")
    grouped = {}
    for issue in items:
        grouped.setdefault(summary_section(issue), []).append(issue)
    number = 0
    for section in SUMMARY_SECTION_ORDER:
        section_items = grouped.get(section)
        if not section_items:
            continue
        lines.append(f"\n{section}")
        for issue in section_items:
            number += 1
            lines.append(f"{number}. {_summary_sentence(issue)}")
    return "\n".join(lines).strip()


def toc_page_mismatch_is_appendix_alt(section_kind, toc_label, appendix_labels):
    """เลขหน้าภาคผนวกในสารบัญชี้ไปหน้าเริ่มของภาคผนวก 'อีกชุด' ที่มีอยู่จริงในเล่ม

    ใช้เลือก 'ข้อความอธิบาย' เท่านั้น ไม่ได้ใช้ตัดสินสี — นโยบายใหม่: เลขหน้าของ
    หัวข้อหลักในสารบัญไม่ตรงหน้าจริง ให้เป็น 'ส้ม' (รอเจ้าหน้าที่ยืนยัน) ทุกกรณี
    ถ้าไม่มีจุดผิดที่สำคัญกว่า เจ้าหน้าที่ให้ผ่านได้ กรณีภาคผนวกหลายชุดนี้แค่
    ต้องอธิบายให้ชัดว่า 87 เป็นหน้าเริ่มของภาคผนวกอีกชุด ไม่ใช่เลขมั่ว
    """
    return section_kind == "appendix" and toc_label in appendix_labels


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


def title_mismatch_detail(label, compared, expected=''):
    """ข้อความชื่อเรื่องที่ไม่ตรงข้อมูลในระบบ — บอกกลาง ๆ ว่า "ไม่ตรงกับข้อมูลในระบบ"

    ชื่อเรื่องที่เก็บใน eThesis เป็นตัวพิมพ์ใหญ่ทั้งหมด แต่ในเล่มอาจใช้ Sentence case
    ซึ่งไม่ควรตีความว่าเป็น "ตัวพิมพ์เล็ก-ใหญ่ผิด" — ประเด็นคือข้อความไม่ตรงกับที่
    อนุมัติในระบบเฉย ๆ จึงบอกกลาง ๆ แล้วชี้จุดต่างเฉพาะเมื่อใกล้เคียงกันพอ (typo)
    """
    detail = f'{label}ไม่ตรงกับข้อมูลในระบบ: "{compared["actual"]}"'
    if expected and compared['status'] in ('typo', 'case'):
        diff = describe_diff(compared['actual'], expected)
        if diff:
            detail += f' — ต่างที่ {diff}'
    return detail


def find_signature_date(text):
    """ดึงวันที่สอบผ่านที่พิมพ์บนหน้าลงนามออกมา (ถ้ามี) เพื่อบอกว่าที่พบต่างจากระบบอย่างไร

    รูปแบบที่พบ: อังกฤษ "on 26 June 2026" / ไทย "วันที่ 11 พฤษภาคม พ.ศ. 2569"
    คืน '' ถ้าหาไม่เจอ (ถือว่าไม่มีวันที่บนหน้าลงนาม ไม่ใช่แค่ไม่ตรง)
    """
    patterns = (
        r'\bon\s+(\d{1,2}\s+[A-Za-z]+\.?\s+\d{4})',
        r'วันท\S*\s*(\d{1,2}\s+\S+\s+(?:พ\.?\s*ศ\.?\s*)?\d{3,4})',
        r'(\d{1,2}\s+[A-Za-zก-๙]+\.?\s+(?:พ\.?\s*ศ\.?\s*)?\d{4})',
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return re.sub(r'\s+', ' ', match.group(1)).strip()
    return ""


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
    s = soft(text)
    # ตัดเลขหน้าท้ายบรรทัดออกก่อน (อารบิก/โรมัน/อักษรไทย)
    s = re.sub(r'\s+(?:\d+|[ivxlcdm]+|[ก-ฮ])\s*$', '', s, flags=re.I)
    # ตัด "จุดไข่ปลา" (dot leader) ที่ลากเชื่อมชื่อหัวข้อกับเลขหน้า เช่น
    #   "LIST OF TABLES ......................" หรือ "ABSTRACT ………… ."
    # มันคือเส้นประของ template ไม่ใช่การสะกด ถ้าไม่ตัดจะทำให้ compare_values
    # (rule toc_heading เป็น case_sensitive จึงข้ามการเทียบแบบ norm) มองว่า
    # หัวข้อสะกดผิดทุกบรรทัด ทั้งที่ถูกต้อง — ตัดชุดจุด/ellipsis ตั้งแต่ 2 ตัวขึ้นไป
    s = re.sub(r'\s*(?:[.…]\s*){2,}$', '', s)
    return s.strip()


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

# คำเรียกส่วนอ้างอิง — ต้องเลือกใช้ "คำเดียว" และสารบัญต้องใช้คำเดียวกับหน้าจริง
_REF_TERM_GROUPS = (
    ("REFERENCES", ("REFERENCES", "REFERENCE")),
    ("BIBLIOGRAPHY", ("BIBLIOGRAPHY",)),
    ("รายการอ้างอิง", (norm("รายการอ้างอิง"),)),
    ("บรรณานุกรม", (norm("บรรณานุกรม"),)),
)


def reference_terms(heading):
    """คืนรายชื่อคำเรียกส่วนอ้างอิงที่ปรากฏในหัวข้อ (ตัดเลขหน้า/จุดไข่ปลาออกก่อน)

    ถ้าคืนมากกว่า 1 คำ แปลว่าเลือกหลายคำ (เช่น "REFERENCES/BIBLIOGRAPHY") ซึ่งผิด
    ใช้เทียบว่าคำในสารบัญตรงกับหัวข้อในหน้าจริงหรือไม่ด้วย
    """
    nl = norm(_strip_toc_page_number(heading))
    return [label for label, keys in _REF_TERM_GROUPS if any(k in nl for k in keys)]
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
    header_extras = []   # ข้อความอื่นในหัวกระดาษต่อหน้า (นอกจากเลขหน้า)
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
                header_extras.append(header_extra_text(_pg))
            except Exception:
                header_extras.append("")
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
                rep.add("RED", "body/end", f"ช่วงเลขหน้า {a} ถึง {b}", f"เลขหน้ากระโดดจาก {a} ไป {b}",
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
        lab_en, lab_th = expected_labels[k]
        # ตรวจทั้งหน้า ไม่ใช่แค่บรรทัดแรก/ท้าย — เลขหน้าของหน้าลงนามอาจไม่ได้อยู่
        # บรรทัดแรกเสมอ (เช่น มีหัวเรื่อง "วิทยานิพนธ์" นำหน้า) เทียบเฉพาะบรรทัดที่
        # เป็นเลขหน้าล้วน (สั้น) จึงไม่ชนกับข้อความในเนื้อหน้า
        page_lines = [l.strip() for l in pages[i2].split('\n') if l.strip()]
        matched = any(t.lower() == lab_en or norm(t) == norm(lab_th) for t in page_lines)
        if not matched:
            found_lab = _extract_page_label(pages[i2])
            what = f'พบเลขหน้า "{found_lab}"' if found_lab else "ไม่พบเลขหน้าบนหน้า"
            rep.add(FRONT_FAILURE_ZONE, "front_matter", f"หน้าลงนามหน้า {k+1} ({page_ref(i2)})",
                    what,
                    f'ต้องเป็นเลขหน้า "{lab_th}" (ไทย) หรือ "{lab_en}" (อังกฤษ)',
                    f"แก้เลขหน้าหน้าลงนามหน้า {k+1} ให้เป็น {lab_th} (ไทย) หรือ {lab_en} (อังกฤษ)",
                    "PAGE.NUMBERING")

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

    # แก้ก่อนตรวจสารบัญ↔เนื้อหา เพราะต้องรู้ว่าบทไหน "ประกาศบังคับชื่อ" — บทที่บังคับ
    # ให้ยึดประกาศเป็นหลัก (เทียบสารบัญกับประกาศ และเนื้อหากับประกาศ แยกกันด้านล่าง)
    # จึงไม่เทียบสารบัญ↔เนื้อหาซ้ำ ซึ่งจะแนะนำผิดทางเมื่อฝั่งสารบัญเป็นตัวสะกดผิด
    option = resolve_option(body_ch, approved, chapters_mode)
    enforced_chapters = CANONICAL_ENFORCED_COUNT.get(option, 0)

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
                # บทที่ประกาศบังคับชื่อ (โหมด strict) ยึดประกาศเป็นหลัก ไม่เทียบสารบัญ↔
                # เนื้อหา — บทที่ประกาศไม่บังคับ (รูปแบบ 2 บทที่ 3 / โหมดยกเว้นบท) ยังเทียบ
                enforced_title = chapters_mode == "strict" and 1 <= cn <= enforced_chapters
                if BODY_RULES['check_toc_title_against_body'] and t_title_n != nb \
                        and not enforced_title:
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
                    # เลขหน้าบทในสารบัญไม่ตรงหน้าจริง = ส้ม ให้เจ้าหน้าที่ตัดสิน
                    rep.add("ORANGE", "body", f"สารบัญ ({page_ref(toc_page_idx)}) ↔ บทที่ {cn} ({page_ref(ppage)})",
                            f"สารบัญระบุหน้า {t_pno} แต่บทอยู่จริงหน้า {pno}",
                            f"เลขหน้าบทในสารบัญควรเป็น {pno}",
                            "เจ้าหน้าที่พิจารณาว่ายอมรับได้ หรือให้แก้เลขหน้าในสารบัญ", "FRONT.TOC")
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

    # ชื่อบทตามประกาศ (option/enforced_chapters คำนวณไว้ก่อนหน้าแล้ว)
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

    # หัวกระดาษส่วนเนื้อหา/ส่วนท้าย ต้องมีเพียงเลขหน้าเท่านั้น (ไม่มี running head/ชื่อบท)
    # รวมทุกหน้าที่พบข้อความอื่นในหัวกระดาษเป็นรายการเดียว ให้เจ้าหน้าที่ยืนยัน (ส้ม)
    if body_ch:
        header_bad = [i for i in range(end_scan_start, len(pages))
                      if i < len(header_extras) and header_extras[i]]
        if header_bad:
            shown = ", ".join(page_ref(i) for i in header_bad[:5])
            more = f" และอีก {len(header_bad) - 5} หน้า" if len(header_bad) > 5 else ""
            sample = header_extras[header_bad[0]]
            rep.add("ORANGE", "body/end", f"หัวกระดาษ ({shown}{more})",
                    f'พบข้อความอื่นนอกจากเลขหน้าในหัวกระดาษ {len(header_bad)} หน้า '
                    f'เช่น "{sample[:60]}"',
                    "หัวกระดาษส่วนเนื้อหาและส่วนท้ายต้องมีเพียงเลขหน้าเท่านั้น",
                    "ลบข้อความอื่น (เช่น ชื่อบท/running head) ออกจากหัวกระดาษ ให้เหลือเฉพาะเลขหน้า",
                    "PAGE.HEADER")

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

    # เลขหน้าส่วนนำทุกหน้า (ไม่ใช่แค่ 2 หน้าลงนาม) — ตรวจได้เมื่อรู้ว่าเนื้อหาเริ่มหน้าไหน
    _check_front_page_numbers(
        rep, page_labels, page_ref,
        sig_pages[0] if sig_pages else 1,
        body_ch[0][2] if body_ch else None,
        _expected_front_label_style((approved or {}).get("program_language", "")))

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
                            title_mismatch_detail("ชื่อเรื่อง", compared, main_title),
                            f"ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: \"{main_title}\"",
                            "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")
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
                            title_mismatch_detail("ชื่อเรื่องอีกภาษา", compared, alt_title),
                            f"ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: \"{alt_title}\"",
                            "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")
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
                        "ลบรหัสนักศึกษาออกจากหน้าปก", "FRONT.COVER")
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

        # ประโยคตายตัวของ template หน้าลงนาม ต้องอยู่ครบ ไม่ใช่แค่ชื่อปริญญาถูก
        # เล่มไทยหน้าอาจารย์ที่ปรึกษาใช้ "นับเป็นส่วนหนึ่ง..." ส่วนหน้ากรรมการสอบ
        # ขึ้นต้น "ได้รับการพิจารณาให้นับเป็นส่วนหนึ่ง..." จึงเช็คท่อนร่วมท่อนเดียว
        sig_template = (SIGNATURE_TEMPLATE_TH if thai_book else SIGNATURE_TEMPLATE_EN)
        for k, idx in enumerate(sig_pages):
            spot = f"หน้าลงนาม {k + 1} ({page_ref(idx)})"
            if norm(sig_template) in norm(pages[idx]):
                rep.add_verification("ข้อความ template หน้าลงนาม", spot, "pass")
            else:
                rep.add_verification("ข้อความ template หน้าลงนาม", spot, "fail",
                                     "ไม่พบข้อความตาม template")
                rep.add("RED", "front_matter", spot,
                        f'ไม่พบข้อความตาม template: "{sig_template}"',
                        f'หน้าลงนามต้องมีข้อความ "{sig_template}" นำหน้าชื่อปริญญา',
                        "ใส่ข้อความตาม template ให้ครบ", "FRONT.APPROVAL")
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
            _check_exam_date(rep, A["exam_date"], sig_pages, pages, page_ref)
        if A.get("year"):
            _check_cover_year(rep, str(A["year"]), pages[0] if pages else "")

        # ---------- รายชื่อกรรมการบนหน้าลงนาม ----------
        # ถ้ามีข้อมูลกรรมการจาก eThesis → ตรวจชื่อ+ตำแหน่งตามกริดตายตัวของ template
        # (เล่มไทยเทียบตรง = แดง, เล่มอังกฤษ AI แปลชื่อ = ส้มให้เจ้าหน้าที่ยืนยัน)
        committees = A.get("committees") or {}
        prog_lang = A.get("program_language", "")
        english_book = prog_lang in ("international", "thai_english")
        # แปลชื่อกรรมการครั้งเดียว ใช้ทั้งหน้าลงนามและหน้าบทคัดย่อ (เล่ม/บทคัดย่ออังกฤษ)
        name_en, translation_ok = ({}, False)
        if committees and (english_book or abs_en_pages):
            name_en, translation_ok = _committee_translation(committees)
        checked_committee = False
        if committees.get("advisory") or committees.get("exam"):
            checked_committee = _check_committees(
                rep, committees, sig_pages, pages, pdf_path, page_ref,
                prog_lang, A, name_en, translation_ok)
        # หน้าบทคัดย่อ: รูปแบบรายชื่อกรรมการ (ตัวพิมพ์ใหญ่/วงเล็บ/ตำแหน่งวิชาการ) เป็นกฎ
        # ของ template ล้วน จึงตรวจเสมอ ส่วนการเทียบชื่อ-ลำดับทำเมื่อมีข้อมูล eThesis
        _check_abstract_committees(rep, committees, abs_en_pages, abs_th_pages,
                                   pages, page_ref, name_en, translation_ok)

        # ช่องคงที่/รายการที่ระบบยังตรวจไม่ได้ → ให้เจ้าหน้าที่ตรวจเอง
        if not checked_committee:
            rep.add_human("รายชื่อกรรมการ ตำแหน่งวิชาการ และคุณวุฒิ บนหน้าลงนามทั้ง 2 หน้า",
                          "เทียบกับ บฑ.1 (หน้า 1) และ บฑ.2 (หน้า 2) ทีละคน รวมการสะกด")
            rep.add_human("ลำดับและตำแหน่งการวางชื่อในตารางลายเซ็น",
                          "ชื่อที่ 1 (Major Advisor/Chair) แถวเดียวกับนักศึกษา คอลัมน์ขวา, ชื่อ 2-5 ไล่ลงขวา, ชื่อ 6 แถวเดียวกับชื่อ 5 ฝั่งซ้าย, 7-9 ไล่ขึ้น, ช่องที่เหลือถมขาว")
        # คุณวุฒิใต้ชื่อกรรมการ: ระบบตรวจว่า "มี" ครบทุกคนแล้ว แต่ไม่ตรวจเนื้อหาคุณวุฒิ
        rep.add_human("ความถูกต้องของเนื้อหาคุณวุฒิ (Degree/Subject) ใต้ชื่อกรรมการ",
                      "เทียบกับ บฑ.1/บฑ.2 — ระบบตรวจว่ามีบรรทัดคุณวุฒิครบทุกคน แต่ไม่ตรวจเนื้อหา")

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
                if section_kind == "references":
                    # (1) สารบัญต้องเลือกคำเดียว: REFERENCES หรือ BIBLIOGRAPHY (ไม่ใช่ทั้งคู่)
                    toc_terms = reference_terms(entry["raw"])
                    if len(toc_terms) > 1 or '/' in entry["raw"]:
                        rep.add(
                            "RED", "front_matter", f"สารบัญ ({page_ref(entry['source_page_idx'])})",
                            f'หัวข้ออ้างอิงในสารบัญเลือกหลายคำ: "{_strip_toc_page_number(entry["raw"])}"',
                            "ต้องเลือกใช้คำเดียว: REFERENCES หรือ BIBLIOGRAPHY อย่างใดอย่างหนึ่ง",
                            "ลบคำที่ไม่ใช้ออกจากสารบัญ ให้เหลือคำเดียว", "FRONT.TOC_CONTENT",
                        )
                    # (2) คำที่เลือกในสารบัญ ต้องตรงกับหัวข้อในหน้าอ้างอิงจริง
                    elif toc_terms and ref_head:
                        page_terms = reference_terms(ref_head[0])
                        if page_terms and set(toc_terms) != set(page_terms):
                            rep.add(
                                "RED", "front_matter",
                                f"สารบัญ ({page_ref(entry['source_page_idx'])}) ↔ "
                                f"{section_label} ({page_ref(actual_page_idx)})",
                                f'สารบัญใช้คำ "{toc_terms[0]}" แต่หน้าอ้างอิงจริงใช้ "{page_terms[0]}"',
                                f'คำในสารบัญต้องตรงกับหัวข้อในหน้าจริง คือ "{page_terms[0]}"',
                                f'แก้คำในสารบัญให้เป็น "{page_terms[0]}"', "FRONT.TOC_CONTENT",
                            )
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
                    # เลขหน้าหัวข้อหลักในสารบัญไม่ตรงหน้าจริง = ส้มทุกกรณี ให้เจ้าหน้าที่
                    # ตัดสิน (ถ้าไม่มีจุดผิดสำคัญกว่าก็ผ่านได้) กรณีภาคผนวกหลายชุดแค่ใช้
                    # ข้อความอธิบายต่างออกไปว่าเลขที่ระบุเป็นหน้าเริ่มของภาคผนวกอีกชุด
                    if toc_page_mismatch_is_appendix_alt(section_kind, entry["page_label"],
                                                         appendix_labels):
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
                            "ORANGE", "front_matter", location,
                            f"สารบัญระบุหน้า {entry['page_label']} แต่หัวข้อเริ่มจริงหน้า {actual_label}",
                            f"เลขหน้า {section_label} ในสารบัญควรเป็น {actual_label}",
                            "เจ้าหน้าที่พิจารณาว่ายอมรับได้ หรือให้แก้เลขหน้าในสารบัญ",
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

    result = {
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
    result["plain_summary"] = plain_summary(result)
    return result
