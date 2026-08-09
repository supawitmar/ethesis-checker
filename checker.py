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
    for c in _thai_chars(chars):
        rows.setdefault(round(c['top'] / 3.0), []).append(c)
    out_lines = []
    for key in sorted(rows):
        line = _compose_thai_line(rows[key])
        if line:
            out_lines.append(line)
    return '\n'.join(out_lines)


def _thai_chars(chars):
    """แปลง "ช่องว่างกว้างศูนย์" ให้เป็นนิคหิต ก่อนประกอบข้อความ

    ฟอนต์ไทยบางตัวใน PDF map นิคหิต (ํ ซึ่งเป็นครึ่งบนของสระ ำ) ไปเป็นอักขระเว้นวรรค
    ทำให้ "จำลองทำนาย" ถูกอ่านออกมาเป็น "จา ลองทา นาย" — เจ้าหน้าที่อ่านข้อความใน
    รายงานไม่รู้เรื่องว่าหมายถึงตรงไหนของเล่ม

    แยกจากช่องว่างจริงได้ชัดเจนด้วยความกว้าง: สำรวจเล่มจริง 9 เล่มพบช่องว่างจริง
    20,303 ตัวกว้าง >= 0.5 pt ทุกตัว ส่วนกว้างศูนย์มี 62 ตัวและตามด้วยสระ า ถึง 55 ตัว
    (จำ สำ คำ ทำ ดำ นำ กำ ซ้ำ) จึงไม่ใช่การเว้นวรรคของจริงแน่นอน

    เมื่อกลายเป็นนิคหิตแล้ว _compose_thai_line จะผูกกลับเข้าพยัญชนะฐานเอง
    ได้ "จํา" แล้วรวมเป็น "จำ" ตามปกติ
    """
    out = []
    for c in chars:
        if c.get('text') == ' ' and (float(c.get('x1', 0)) - float(c.get('x0', 0))) < 0.5:
            c = {**c, 'text': 'ํ'}     # NIKHAHIT
        out.append(c)
    return out


# ลำดับที่ถูกต้องของไทยคือ พยัญชนะ + สระ + วรรณยุกต์ + การันต์
# เรียงตามพิกัด x เฉย ๆ จะได้ "ท่ี" แทน "ที่" เพราะวรรณยุกต์วางเยื้องซ้ายกว่าสระ
_MARK_ORDER = {**{c: 0 for c in 'ัิีึืฺุู็ํ'},   # สระบน-ล่าง ไม้ไต่คู้ นิคหิต
               **{c: 1 for c in '่้๊๋'},  # วรรณยุกต์ เอก โท ตรี จัตวา
               **{c: 2 for c in '์๎'}}              # การันต์ ยามักการ


def _attach_thai_marks(chars):
    """ผูก combining mark ไทยกลับเข้าพยัญชนะฐาน คืน [(char ฐาน, ฐาน+mark)] เรียงตาม x

    mark ของไทยเป็นอักขระกว้างศูนย์ (x0 == x1) ที่วางไว้ตรงขอบขวาของพยัญชนะฐานพอดี
    จึงผูกกลับเข้าฐานที่ขอบขวาใกล้ที่สุดได้ แล้วเรียง สระ → วรรณยุกต์ → การันต์

    "ช่องว่าง" ต้องไม่นับเป็นฐาน — เล่มจริง (เล่มที่ 6) วางการันต์ของ "ทวีศักดิ์" ไว้
    ห่างจาก ด เล็กน้อยจนขอบขวาของช่องว่างที่ตามมาใกล้กว่า mark จึงไปเกาะช่องว่าง
    ได้ "ทวีศักดิ ์สมานชื่น" — เจ้าหน้าที่อ่านรายงานแล้วนึกว่าระบบอ่านชื่อผิดคน
    """
    bases = sorted((c for c in chars if not _TH_MARKS.match(c['text'])),
                   key=lambda c: float(c['x0']))
    if not bases:
        return []
    anchors = [b for b in bases if b['text'].strip()] or bases
    attached = {id(b): [] for b in bases}
    for m in chars:
        if _TH_MARKS.match(m['text']):
            base = min(anchors, key=lambda b: abs(float(b['x1']) - float(m['x0'])))
            attached[id(base)].append(m)
    out = []
    for b in bases:
        marks = ''.join(m['text'] for m in sorted(
            attached[id(b)],
            key=lambda m: (_MARK_ORDER.get(m['text'], 1), float(m['x0']))))
        out.append((b, b['text'] + marks))
    return out


def _compose_thai_line(chars):
    """ประกอบข้อความ 1 บรรทัดจาก chars โดยผูก combining mark ไทยกลับเข้าพยัญชนะฐาน"""
    pieces = _attach_thai_marks(chars)
    if not pieces:
        return ''
    parts, prev = [], None
    for b, text in pieces:
        if prev is not None and (float(b['x0']) - float(prev['x1'])) > 1.2:
            parts.append(' ')
        parts.append(text)
        prev = b
    line = re.sub(r' +', ' ', ''.join(parts))
    # นิคหิต + า = ำ  ส่วน นิคหิต + ำ เกิดจากไฟล์ที่มี ำ อยู่แล้วและยังใส่นิคหิตซ้ำมาให้
    # (ถ้าไม่ยุบจะได้ "คํำสํำคัญ" แทน "คำสำคัญ")
    return line.replace('ํำ', 'ำ').replace('ํา', 'ำ').strip()


def top_lines(page_text, k=10):
    return [l.strip() for l in page_text.split('\n') if l.strip()][:k]


def _is_blank_page_text(page_text):
    """Treat a page containing only its printed page label as blank content."""
    lines = [line.strip() for line in (page_text or '').splitlines() if line.strip()]
    return not any(
        not re.fullmatch(r'(?:\d{1,3}|[ivxlcdm]+|[ก-ฮ])', line, re.I)
        for line in lines
    )


# เศษที่ติดมากับบรรทัดเลขหน้า — เส้นคั่น/จุดไข่ปลา/หัวกระดาษที่เป็นสัญลักษณ์ล้วน
# เล่มจริงพบบรรทัดเลขหน้าเป็น ". 1" ทุกหน้าคี่ (จุดของเส้นตกแต่งติดมาด้วย)
# ถ้าไม่ปัดออก ระบบจะอ่านเลขหน้าไม่ได้ 119 จาก 177 หน้า แล้วฟ้อง "เลขหน้ากระโดด" 45 ข้อ
_PAGE_LABEL_NOISE = re.compile(r'^[\s.\-—–_•·|:]+|[\s.\-—–_•·|:]+$')


def _extract_page_label(page_text):
    """Read the page label printed at the top or bottom of a document page."""
    lines = [line.strip() for line in (page_text or '').splitlines() if line.strip()]
    candidates = (lines[:1] + lines[-1:]) if lines else []
    for raw in candidates:
        candidate = _PAGE_LABEL_NOISE.sub('', raw)
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


def _sig_words(pdf_page):
    """คำบนหน้าลงนาม โดยซ่อมนิคหิตที่ฟอนต์ map เป็น "ช่องว่างกว้างศูนย์" ก่อน

    ถ้าอ่านด้วย extract_words ตรง ๆ ตัวช่องว่างนั้นจะถูกนับเป็นการเว้นวรรค ชื่อ
    "จำเนียร จวงตระกูล" จึงถูกอ่านเป็น "จ าเนียร จวงตระกูล" แล้วเทียบกับข้อมูลอนุมัติ
    ไม่ตรง ระบบฟ้องผิดว่า "ไม่พบกรรมการ" ทั้งที่ชื่ออยู่บนหน้าจริง (พบในเล่มที่ 9)
    """
    chars = _thai_chars(getattr(pdf_page, 'chars', None) or [])
    words = []
    if chars:
        try:
            words = pdfplumber.utils.extract_words(
                chars, extra_attrs=["non_stroking_color"], return_chars=True) or []
        except Exception:
            words = []
    if not words:                       # หน้าที่อ่าน chars ไม่ได้ ใช้ทางเดิม
        try:
            words = pdf_page.extract_words(extra_attrs=["non_stroking_color"]) or []
        except Exception:
            words = pdf_page.extract_words() or []
    words = _rejoin_thai_marks(words)
    # นิคหิตที่ซ่อมแล้วยังลอยอยู่หน้า "า" ต้องรวมเป็น "ำ" ตัวเดียวเหมือน _compose_thai_line
    return [{**w, 'text': (w.get('text') or '').replace('ํา', 'ำ').replace('ํำ', 'ำ')}
            for w in words]


def _rejoin_thai_marks(words):
    """ซ่อม "ภาษาเลื่อน" ของคำที่ตัดมาจาก extract_words

    extract_words ตัดคำจากระยะห่างแกน x ล้วน ๆ ส่วน combining mark ของไทยเป็นอักขระ
    กว้างศูนย์ที่วางคร่อมขอบพยัญชนะ ผลคือ
      1. mark หลุดออกไปเป็น "คำ" ของตัวเอง — เล่มจริง (เล่มทดสอบ 3) อ่านชื่อกรรมการ
         "สุภาภรณ์ สงค์ประชา" ได้เป็น "สุภาภรณ ์ สงค์ประชา" การันต์กลายเป็นคำกลางชื่อ
      2. mark ที่อยู่ในคำเดียวกันเรียงตามพิกัด x จึงสลับที่ ได้ "ท่ี" แทน "ที่"
    ทั้งสองแบบทำให้เจ้าหน้าที่อ่านรายงานแล้วนึกว่าระบบอ่านชื่อผิดคน

    ต้องซ่อมหลัง extract_words ไม่ใช่แทนที่มัน เพราะขอบเขตคำ/พิกัดของ extract_words
    คือสิ่งที่ signature_committee_slots ใช้แบ่งช่องตาราง การตัดคำเองด้วยระยะห่าง
    จะไปเปลี่ยนผลการแบ่งช่องของทุกเล่มที่อ่านถูกอยู่แล้ว
    """
    if not any(w.get('chars') for w in words):
        return words                    # ทางเดิม (fixture ในเทส/หน้าที่ไม่มี chars)
    real, floating = [], []
    for w in words:
        cs = w.get('chars') or []
        if cs and all(_TH_MARKS.match(c['text']) for c in cs):
            floating.append(w)
        else:
            real.append(w)
    if not real:
        return words
    for w in floating:
        for c in (w.get('chars') or []):
            x, top = float(c['x0']), float(c['top'])
            same_line = [r for r in real if abs(float(r['top']) - top) <= 3] or real
            host = min(same_line,
                       key=lambda r: min(abs(float(r['x1']) - x), abs(float(r['x0']) - x)))
            host.setdefault('chars', []).append(c)
    out = []
    for w in real:
        text = ''.join(t for _, t in _attach_thai_marks(w['chars'])) or w.get('text', '')
        out.append({**w, 'text': text})
    return out


def signature_committee_slots(pdf_page):
    """อ่านตารางลายเซ็นตามกริดตายตัว

    คืน (members, member_quals, bottom_text):
      members = dict{ลำดับกรรมการ 1..9 → ชื่อ (str) หรือ None ถ้าช่องว่าง/placeholder}
      member_quals = dict{ลำดับกรรมการ 1..9 → ข้อความคุณวุฒิใต้ชื่อ ('' ถ้าไม่มี/placeholder)}
      bottom_text = ข้อความช่องล่างสุด (คณบดี + ประธานหลักสูตร) เรียงตาม "ลำดับการอ่าน"
                    บน→ล่าง ซ้าย→ขวา ไว้ตรวจชื่อคณะ/หลักสูตร
      member_raw = dict{ลำดับ → ข้อความดิบของช่อง (ยังมีตำแหน่งวิชาการ)} ไว้เทียบตำแหน่ง
    """
    words = _sig_words(pdf_page)
    # ข้อความที่ถมขาวไว้ (มองไม่เห็นบนหน้ากระดาษ) ต้องไม่นับเป็นเนื้อหาของช่อง
    # เล่มจริงพบว่ามีข้อความชั้นเก่าถมขาวทับซ้อนอยู่ ถ้าอ่านรวมจะได้ชื่อกรรมการ
    # ซ้ำหรือไปโผล่ผิดช่อง แล้วฟ้องผิดว่ามีคนเกิน/ชื่อซ้ำ
    words = [w for w in words if not _is_white_fill(w.get("non_stroking_color"))]
    if not words:
        return {}, {}, '', {}
    mid = float(getattr(pdf_page, 'width', 595) or 595) / 2
    lines = []
    for w in sorted(words, key=lambda w: (round(float(w['top'])), float(w['x0']))):
        top = float(w['top'])
        if lines and abs(lines[-1]['top'] - top) <= 6:
            lines[-1]['words'].append(w)
        else:
            lines.append({'top': top, 'words': [w]})
    line_dotted = [_sig_is_dotted(' '.join(w['text'] for w in ln['words'])) for ln in lines]

    # เส้นแบ่งสองคอลัมน์ต้องมาจาก "เส้นประของ template" ไม่ใช่กึ่งกลางหน้า
    # เล่มจริงพบว่าช่องขวาเริ่มที่ x0=297.53 ขณะที่กึ่งกลางหน้าคือ 297.66 ต่างกัน
    # แค่ 0.13 pt คำแรกของทุกช่องขวาจึงถูกโยนไปฝั่งซ้าย ชื่อกรรมการเลยขาดครึ่ง
    # ("มยุรี หอมสนิท" เหลือ "หอมสนิท" ส่วน "มยุรี" ไปโผล่เป็นกรรมการอีกคน)
    dot_starts = [float(w['x0']) for ln in lines for w in ln['words']
                  if _sig_is_dotted(w['text']) and float(w['x0']) > mid * 0.6]
    if dot_starts:
        mid = min(dot_starts) - 2.0     # เผื่อคำที่เริ่มชิดขอบซ้ายของช่องพอดี
    # แถวชื่อ = บรรทัดถัดจากเส้นประ; แถวคุณวุฒิ = บรรทัดถัดจากชื่อ (ถ้าไม่ใช่เส้นประ)
    name_rows, qual_rows = [], []
    for i in range(len(lines) - 1):
        if not line_dotted[i]:
            continue
        name_rows.append(lines[i + 1])
        qual_rows.append(lines[i + 2] if (i + 2 < len(lines) and not line_dotted[i + 2])
                         else None)

    def cell(row, left):
        """ข้อความในช่องหนึ่งของแถว — ประกอบจาก chars ไม่ใช่ต่อ text ของ extract_words

        extract_words ตัดคำจากระยะห่างแกน x ล้วน ๆ ฟอนต์ในเล่มจริงบางตัวเว้นช่องว่าง
        ระหว่างตัวอักษรกลางคำมากพอจนถูกตัดเป็นคนละคำ ต่อกลับด้วยช่องว่างแล้วได้
        "วันเพ็ญ แก้ว ปาน" / "แอนน์ จิร ะพงษ์สุวรรณ" / "วิจ ยัการศึกษา" (เล่มที่ 1)
        เจ้าหน้าที่อ่านรายงานแล้วนึกว่าระบบอ่านชื่อผิดคน

        _compose_thai_line คือทางเดียวกับที่ _page_text ใช้ ซึ่งอ่านหน้าเดียวกันนี้ถูก
        อยู่แล้ว — ตัดคำจากระยะห่างของ "พยัญชนะฐาน" และผูก mark ข้ามขอบเขตคำได้
        """
        if not row:
            return ''
        ws = [w for w in sorted(row['words'], key=lambda w: float(w['x0']))
              if (float(w['x0']) < mid) == left]
        chars = [c for w in ws for c in (w.get('chars') or [])]
        if chars:
            return _compose_thai_line(chars)
        return ' '.join(w['text'] for w in ws).strip()      # fixture ที่ไม่มี chars

    members, member_quals, member_raw = {}, {}, {}
    # แถวเส้นประสุดท้ายคือช่องสถาบัน (คณบดี / ประธานหลักสูตร) ไม่ใช่กรรมการ — ตัดทิ้งเสมอ
    # (เดิมตัดด้วย [:5] ซึ่งพึ่งว่าต้องอ่านเส้นประเจอครบ 6 แถวพอดี ถ้าเจอไม่ครบ
    #  แถวคณบดีจะเลื่อนเข้ามาเป็นกรรมการ แล้วฟ้องว่ามีชื่อนอกรายชื่ออนุมัติ)
    member_rows = list(zip(name_rows, qual_rows))[:-1][:5]
    for idx, (nrow, qrow) in enumerate(member_rows):     # 0..4 = ระดับกรรมการ
        right = cell(nrow, left=False)
        members[idx + 1] = _sig_clean_name(right)                    # ขวา → 1..5
        member_raw[idx + 1] = right
        member_quals[idx + 1] = _sig_qual_text(cell(qrow, left=False))
        if idx >= 1:
            left = cell(nrow, left=True)
            members[10 - idx] = _sig_clean_name(left)                # ซ้าย → 9,8,7,6
            member_raw[10 - idx] = left
            member_quals[10 - idx] = _sig_qual_text(cell(qrow, left=True))
    # ช่องล่างสุด (สถาบัน) = ทุกคำใต้แถวกรรมการสุดท้าย
    #
    # ไม่มีวิธีเรียงคำวิธีเดียวที่ถูกกับทุกเล่ม เพราะสองช่องนี้กว้างไม่เท่ากันและข้อความยาว
    # ไม่เท่ากัน จากเล่มจริง:
    #   - บางเล่มชื่อหลักสูตรไทยยาวจนขึ้นบรรทัดใหม่ "...ผู้ใหญ่และ" / "ผู้สูงอายุ"
    #     ถ้าแบ่งซ้าย-ขวาด้วยกึ่งกลางหน้า คำท้ายตกไปคนละฝั่ง ชื่อสาขาขาดกลาง
    #   - บางเล่มทั้งสองช่องมีข้อความหลายบรรทัด ถ้าเรียงตามลำดับการอ่านล้วน ๆ
    #     คำของสองช่องจะสลับกันเป็นบรรทัดต่อบรรทัด ชื่อสาขาก็ขาดกลางเหมือนกัน
    #
    # จึงคืนทั้งสองแบบต่อกัน แล้วให้ผู้เรียกค้นแบบ substring — เจอแบบใดแบบหนึ่งถือว่าผ่าน
    # กฎนี้เป็นสีส้ม "โปรดตรวจ" อยู่แล้ว การฟ้องเกินทั้งที่เล่มถูกเสียหายกว่าการไม่ฟ้อง
    floor = (name_rows[4]['top'] + 20) if len(name_rows) >= 5 else \
            (name_rows[-1]['top'] if name_rows else 0)
    band = [w for w in sorted(words, key=lambda w: (round(float(w['top'])), float(w['x0'])))
            if float(w['top']) >= floor]
    def band_text(ws):
        """ประกอบจาก chars ด้วยเหตุผลเดียวกับ cell() — ชื่อสาขาที่ถูกตัดคำผิดจะหาไม่เจอ"""
        rows = {}
        for w in ws:
            rows.setdefault(round(float(w['top'])), []).extend(w.get('chars') or [])
        if not any(rows.values()):
            return ' '.join(w['text'] for w in ws)
        return ' '.join(_compose_thai_line(cs) for _t, cs in sorted(rows.items()) if cs)

    reading = band_text(band)
    by_column = band_text([w for w in band if float(w['x0']) < mid]) + ' ' + \
                band_text([w for w in band if float(w['x0']) >= mid])
    return members, member_quals, (reading + '\n' + by_column).strip(), member_raw


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
    r'ศาสตราจารย์เกียรติคุณ|ศาสตราจารย์คลินิก|ศาสตราจารย์|\(?พิเศษ\)?|'
    r'รองศาสตราจารย์|ผู้ช่วยศาสตราจารย์|อาจารย์|'
    r'ว่าที่ร้อยตรี|นางสาว|นาง|นาย|'
    r'ผศ\.|รศ\.|ศ\.|ดร\.|'
    # คำนำหน้าแพทย์/ทหาร — พบในเล่มจริง eThesis เขียน "พันเอก ศักรินทร์ จิรพงศธร"
    # แต่เล่มเขียน "Col. Sakkarin Chirapongsathorn" ถ้าไม่ตัดออกทั้งสองฝั่ง
    # ชื่อจะเทียบไม่ตรงแล้วฟ้องผิดว่าไม่อยู่ในรายชื่อกรรมการอนุมัติ
    r'นายแพทย์|แพทย์หญิง|ทันตแพทย์|สัตวแพทย์|เภสัชกร|'
    r'นพ\.|พญ\.|ทพ\.|สพ\.|ภก\.|'
    r'พลเอก|พลโท|พลตรี|พันเอก|พันโท|พันตรี|ร้อยเอก|ร้อยโท|'
    r'พล\.อ\.|พล\.ท\.|พล\.ต\.|พ\.อ\.|พ\.ท\.|พ\.ต\.|ร\.อ\.|ร\.ท\.|'
    r'Clinical\s+Professor|Emeritus\s+Professor|'
    r'Associate\s+Professor|Assistant\s+Professor|Professor|'
    r'Assoc\.?\s*Prof\.?|Asst\.?\s*Prof\.?|Prof\.?|'
    r'Lt\.?\s*Gen\.?|Maj\.?\s*Gen\.?|Lt\.?\s*Col\.?|Gen\.?|Col\.?|Maj\.?|Capt\.?|'
    r'Lecturer|Lect\.?|Dr\.?|Mr\.?|Mrs\.?|Miss|Ms\.?'
    r')[\s. ]*', re.I)


# ตำแหน่งที่เขียนไว้ "ท้ายชื่อ" เช่น "ธเนศ เกษศิลป์, ผู้ช่วยศาสตราจารย์" — พบในเล่มจริง
# ถ้าไม่ตัดออกจะเทียบชื่อไม่ตรง แล้วฟ้องผิดว่าไม่อยู่ในรายชื่อกรรมการอนุมัติ
# ใช้รายชื่อคำเดียวกับ prefix (ตัดหัว ^[\s,]* ออกแล้วผูก $ ท้าย) เพื่อไม่ให้ลิสต์ 2 ชุดหลุดกัน
_COMMITTEE_TITLE_SUFFIX = re.compile(
    r'[\s,]+'
    + _COMMITTEE_TITLE_PREFIX.pattern[_COMMITTEE_TITLE_PREFIX.pattern.index('(?:'):]
    + r'$', re.I)


# คำนำหน้าที่แทรก "กลางชื่อ" ได้ — eThesis เขียน "ศาสตราจารย์ พิศิษฐ์ ดร. จำเนียร
# จวงตระกูล" ส่วนเล่มเขียน "ศาสตราจารย์พิศิษฐ์ จำเนียร จวงตระกูล" ถ้าตัดเฉพาะหัว
# จะเหลือ "ดร." ค้างอยู่ฝั่งเดียว เทียบไม่ตรง แล้วฟ้องผิดว่าไม่พบกรรมการคนนี้
# บังคับว่าต้องมีจุด จึงไม่ไปโดนชื่อคนจริง (เล่มที่ 9)
_COMMITTEE_TITLE_INNER = re.compile(
    r'(?:(?<=^)|(?<=[\s,(]))(?:ดร|นพ|พญ|ทพ|สพ|ภก|Dr)\s*\.\s*', re.I)


def _strip_committee_title(name):
    """ตัดคำนำหน้า/ตำแหน่งวิชาการทั้งหมดออก เหลือเฉพาะชื่อ-สกุล (วนจนไม่เหลือคำนำหน้า)"""
    s = (name or "").strip()
    prev = None
    while s and s != prev:
        prev = s
        s = _COMMITTEE_TITLE_PREFIX.sub('', s, count=1).strip()
        s = _COMMITTEE_TITLE_SUFFIX.sub('', s, count=1).strip()
        s = _COMMITTEE_TITLE_INNER.sub('', s).strip()
    return s.strip(' ,')


# คำนำหน้าชื่อ "นักศึกษา" — ตัดออกก่อนเทียบเสมอ ตามที่เจ้าหน้าที่กำหนด (ก.ค. 2569)
# "ชื่อนักศึกษา ให้ตรวจแบบไม่มีคำนำหน้า ถ้ามีให้เตือนส้ม"
#
# ยศทหาร/ตำรวจเขียนย่อได้หลายสิบแบบ (พ.จ.ต. จ.ส.อ. ร.ต.อ. น.ท. พล.ต.ต. ...)
# ไล่รายคำไม่มีวันครบ จึงจับ "อักษรไทย 1-4 ตัวคั่นด้วยจุด" เป็นรูปแบบเดียว —
# ชื่อคนไทยไม่มีจุดอยู่แล้ว จึงไม่ไปโดนชื่อจริง
# ฝั่งอังกฤษยศมักตามด้วยเลขชั้น (บฑ. ของเล่มที่ 9 เขียน "CPO 3 NUTCHANOP PETSUK")
# จึงเผื่อเลขท้ายคำนำหน้าไว้ด้วย
_STUDENT_TITLE_PREFIX = re.compile(
    r'^\s*(?:'
    # "หญิง" ต่อท้ายยศได้ เช่น "ร.ต.อ.หญิง" / "พันเอกหญิง" ต้องตัดไปด้วย
    r'(?:[ก-๙]{1,4}\.\s*){1,4}(?:หญิง)?'
    r'|ว่าที่\s*(?:ร้อยตรี|ร้อยโท|ร้อยเอก)|'
    r'นายแพทย์|แพทย์หญิง|ทันตแพทย์|สัตวแพทย์|เภสัชกร|'   # ต้องมาก่อน "นาย"
    r'(?:พล|พัน|ร้อย|พันจ่า|จ่าสิบ|สิบ|นาวา|เรือ)(?:เอก|โท|ตรี)(?:หญิง)?|'
    r'นางสาว|นาง|นาย(?=\s|[ก-๙])|'
    r'(?:[A-Z]\.){2,4}'
    r'|(?:Pol\.?\s*)?(?:Gen|Lt|Col|Maj|Capt|Sgt|Cpl|Pvt|CPO|PO|Cdr|Adm|Lieut|'
    r'Mr|Mrs|Miss|Ms|Dr)\b\.?'
    r')\s*\.?\s*\d*\s*', re.I)


def _strip_student_title(name):
    """ตัดคำนำหน้า/ยศออกจากชื่อนักศึกษา เหลือเฉพาะชื่อ-สกุล"""
    s = (name or "").strip()
    prev = None
    while s and s != prev:
        prev = s
        s = _STUDENT_TITLE_PREFIX.sub('', s, count=1).strip()
    return s or (name or "").strip()


def _student_title_in_page(page_text, core_name):
    """คำนำหน้าที่เล่มพิมพ์ไว้หน้าชื่อนักศึกษา ('' ถ้าไม่มี)

    ใช้เตือน (ส้ม) ว่า "ชื่อนักศึกษาไม่ควรมีคำนำหน้า" โดยไม่ตีตกเล่ม
    """
    key = norm(core_name)
    if not key:
        return ""
    for line in (page_text or "").splitlines():
        raw = line.strip()
        if not raw:
            continue
        stripped = _strip_student_title(raw)
        if stripped != raw and norm(stripped).startswith(key):
            return raw[:len(raw) - len(stripped)].strip()
    return ""


# รูปแบบการพิมพ์ชื่อนักศึกษา แยกตามหน้า (นโยบายเจ้าหน้าที่ ก.ค. 2569)
# "ชื่อนักศึกษาภาษาอังกฤษในหน้าลงนาม ต้องเป็น Capital case และจะมีหรือไม่มีคำนำหน้านามก็ได้
#  ส่วนชื่อในหน้าปก และหน้าบทคัดย่อ ต้องเป็น UPPERCASE และไม่มีคำนำหน้านาม"
_STUDENT_NAME_STYLE = {
    "cover":     ("upper", False),
    "abstract":  ("upper", False),
    "signature": ("title", True),
}

_STYLE_LABEL = {"upper": "ตัวพิมพ์ใหญ่ทั้งหมด (UPPERCASE)",
                "title": "ตัวพิมพ์ใหญ่ต้นคำ (Capital Case)"}


def _printed_student_name(page_text, core_name):
    """ข้อความชื่อนักศึกษา "ตามที่พิมพ์จริงในเล่ม" ('' ถ้าหาไม่เจอ)

    ต้องดูตัวสะกดจริง ไม่ใช่ค่าจากข้อมูลอนุมัติ เพราะกฎนี้ตรวจ "ตัวพิมพ์"
    """
    words = [w for w in (core_name or "").split() if w]
    if not words:
        return ""
    pat = re.compile(r'\s+'.join(re.escape(w) for w in words), re.I)
    for line in (page_text or "").splitlines():
        found = pat.search(line)
        if found:
            return found.group(0)
    return ""


# คำเชื่อมในนามสกุลที่เขียนตัวเล็กเป็นปกติ (van Beethoven, de la Cruz, bin Ahmad)
_NAME_PARTICLES = {"van", "von", "de", "del", "della", "da", "di", "du", "la", "le",
                   "bin", "binti", "al", "ibn", "of", "the"}


def _is_title_case(text):
    """ทุกคำขึ้นต้นด้วยตัวพิมพ์ใหญ่ และไม่ใช่ตัวพิมพ์ใหญ่ทั้งคำ

    แบ่งที่ "ช่องว่าง" อย่างเดียว ไม่แบ่งที่ขีดกลาง — นามสกุลไทยที่ถอดเป็นอังกฤษ
    เขียนได้ทั้ง "Pan-Ngum" และ "Pan-ngum" (เจ้าตัวเลือกเอง เจอทั้งสองแบบในเล่มจริง)
    ถ้าไปบังคับตัวหลังขีดกลางจะฟ้องผิดใส่ชื่อที่สะกดถูกตามเจ้าของ
    """
    words = [w for w in re.split(r"\s+", text or "") if re.search(r'[A-Za-z]', w)]
    if not words:
        return False
    for word in words:
        if word.lower().strip(".,") in _NAME_PARTICLES:
            continue
        letters = re.sub(r'[^A-Za-z]', '', word)
        first = re.search(r'[A-Za-z]', word).group(0)
        if not first.isupper() or (len(letters) > 1 and letters.isupper()):
            return False
    return True


def _report_student_name_style(rep, page_text, core_name, loc, label, kind, rule_id):
    """ตรวจ "รูปแบบการพิมพ์" ชื่อนักศึกษาบนหน้านั้น — คำนำหน้า + ตัวพิมพ์

    รวมเป็นข้อเดียวต่อหน้า (ทั้งสองเรื่องแก้ที่บรรทัดเดียวกัน จะแยกสองข้อก็ซ้ำซ้อน)
    ตัวพิมพ์ตรวจเฉพาะชื่อภาษาอังกฤษ — ภาษาไทยไม่มีตัวพิมพ์ใหญ่-เล็ก
    """
    want_case, allow_title = _STUDENT_NAME_STYLE[kind]
    found_title = "" if allow_title else _student_title_in_page(page_text, core_name)
    printed = _printed_student_name(page_text, core_name)
    english = bool(re.search(r'[A-Za-z]', core_name or ""))
    bad_case = bool(english and printed) and (
        not printed.isupper() if want_case == "upper" else not _is_title_case(printed))

    if not found_title and not bad_case:
        rep.add_verification("ชื่อนักศึกษา", loc, "pass")
        return

    want_text = core_name.upper() if want_case == "upper" else \
        person_name_sentence_case(core_name)
    reasons = []
    if bad_case:
        reasons.append(f"ต้องเป็น{_STYLE_LABEL[want_case]}")
    if found_title:
        reasons.append(f'มีคำนำหน้านาม "{found_title}" ซึ่งหน้านี้ต้องไม่มี')
    seen = printed or core_name
    if found_title:
        seen = f"{found_title} {seen}".strip()

    # ตัวพิมพ์ผิดบนหน้าปก/บทคัดย่อ = ชัดเจน ฟันธงแดงได้ (เทียบกับกฎชื่อกรรมการที่แดงอยู่แล้ว)
    # ส่วนหน้าลงนามและกรณีมีแต่คำนำหน้าเกิน = ส้ม ให้เจ้าหน้าที่ตัดสิน
    zone = "RED" if (bad_case and kind != "signature") else "ORANGE"
    rep.add_verification("ชื่อนักศึกษา", loc,
                         "fail" if zone == "RED" else "pending", "; ".join(reasons))
    rep.add(zone, "front_matter", loc,
            f'{label}บนหน้านี้พิมพ์ว่า "{seen}" — ' + " · ".join(reasons),
            f'{label}ในหน้านี้ต้องเป็น{_STYLE_LABEL[want_case]}'
            + ("" if allow_title else " และไม่มีคำนำหน้านาม")
            + f' คือ "{want_text}"',
            f'แก้{label}บนหน้านี้เป็น "{want_text}"', rule_id)


def _display_committee_name(name):
    """ชื่อกรรมการที่เอาไปแสดงในรายงาน — ตัดคำนำหน้าออกให้ตรงกับที่ระบบใช้เทียบ

    ข้อมูลจาก eThesis บางรายการมีเศษคำนำหน้าติดมา (เช่น "พิศิษฐ์ ดร. จำเนียร จวงตระกูล")
    ถ้าแสดงดิบ ๆ เจ้าหน้าที่จะงงว่าระบบเทียบอะไรกันแน่
    """
    return _strip_committee_title(name) or (name or "")


# หัวบทจริง vs บรรทัดบทในสารบัญ — ต่างกันที่ "มีชื่อบทและเลขหน้าอยู่บรรทัดเดียวกัน"
#   สารบัญ : "CHAPTER 4 RESULTS 23" / "บทที่ 4 ผลการวิจัย 23"
#   หัวบท  : "CHAPTER 4" (ชื่อบทอยู่บรรทัดถัดไป) หรือ "บทที่ 4 ผลการวิจัย"
_CHAPTER_HEAD_LINE = re.compile(r'^(?:CHAPTER|บทที่)\s*\d', re.I)
_TOC_CHAPTER_LINE = re.compile(r'^(?:CHAPTER|บทที่)\s*\d+\s+\S.*\s\d{1,3}\s*$', re.I)


def _looks_like_chapter_start(line):
    s = (line or "").strip()
    return bool(_CHAPTER_HEAD_LINE.match(s)) and not _TOC_CHAPTER_LINE.match(s)


def _toc_continuation_pages(pages, toc_start, hard_stop, limit=12):
    """หน้าทั้งหมดที่เป็น "สารบัญ" ต่อเนื่องจากหน้าแรก

    คำว่า "สารบัญ" พิมพ์เฉพาะหน้าแรก หน้าถัด ๆ ไปจึงหาจากลักษณะหน้าแทน:
    มีบรรทัดที่ลงท้ายด้วยเลขหน้าตั้งแต่ 3 บรรทัดขึ้นไป

    เดิมตัดไว้แค่ 4 หน้าตายตัว เล่มที่ 4 มีสารบัญ 5 หน้า (ซ ฌ ญ ฎ ฏ) หน้าสุดท้าย
    จึงหลุด — ซึ่งเป็นหน้าที่มี บรรณานุกรม / ภาคผนวก / ประวัติผู้วิจัย พอดี
    ระบบเลยฟ้องผิดว่า "ไม่พบหัวข้อ ... ในสารบัญ" ทั้งที่พิมพ์ไว้ครบ
    """
    out = [toc_start]
    for idx in range(toc_start + 1, min(hard_stop, toc_start + limit, len(pages))):
        lines = [ln.strip() for ln in pages[idx].split('\n') if ln.strip()]
        # ขึ้นบทแล้ว = พ้นสารบัญแน่นอน (บรรทัดแรกมักเป็นเลขหน้า หัวข้อจึงอยู่บรรทัด 2)
        # ต้องแยกจาก "บรรทัดบทในสารบัญ" ให้ออก — หน้าสารบัญหน้าที่ 2 ขึ้นต้นด้วย
        # "CHAPTER 4 RESULTS 23" ได้ตามปกติ ถ้าเหมารวมจะตัดหน้าสารบัญทิ้ง (เล่มที่ 1)
        if any(_looks_like_chapter_start(ln) for ln in lines[:2]):
            break
        if sum(1 for ln in lines if re.search(r'\d{1,3}\s*$', ln)) < 3:
            break
        out.append(idx)
    return out


def ethesis_matches_book(approved, pages):
    """ไฟล์ eThesis กับไฟล์เล่ม "น่าจะเป็นของนักศึกษาคนเดียวกัน" ไหม

    คืน (ตรงกันไหม, [สัญญาณที่ตรวจ], [สัญญาณที่พบ])

    ดู 3 สัญญาณจากส่วนนำ: รหัสนักศึกษา / ชื่อนักศึกษา / ชื่อเรื่อง
      - เจอ "อย่างน้อยหนึ่งอย่าง" = คนเดียวกัน · ที่เหลือไม่ตรงคือข้อผิดของเล่มจริง ๆ
      - ไม่เจอเลยสักอย่าง = น่าจะอัปโหลดไฟล์สลับคน เพราะเล่มที่พิมพ์ผิดจริง ๆ
        ยากมากที่จะผิดพร้อมกันทั้งรหัส ทั้งชื่อ และทั้งชื่อเรื่อง

    เกิดขึ้นจริงหลายครั้งตอนใช้งาน แล้วรายงานออกมาแดงยาวเป็นสิบข้อโดยไม่มีข้อไหน
    ช่วยอะไรเลย เจ้าหน้าที่ที่ไม่ทันสังเกตอาจส่งกลับให้นักศึกษาแก้ทั้งที่เล่มไม่ผิด
    """
    front = "\n".join(pages[:20])
    nfront, digits = norm(front), re.sub(r'\D', '', front)
    checked, found = [], []

    student_id = re.sub(r'\D', '', (approved.get("student_id") or ""))
    if len(student_id) >= 6:
        checked.append("รหัสนักศึกษา")
        if student_id in digits:
            found.append("รหัสนักศึกษา")

    names = [approved.get("student_name"), approved.get("student_name_th")]
    keys = [norm(_strip_student_title(nm)) for nm in names if soft(nm or "")]
    if keys:
        checked.append("ชื่อนักศึกษา")
        if any(k and k in nfront for k in keys):
            found.append("ชื่อนักศึกษา")

    titles = [t for t in (approved.get("title_en"), approved.get("title_th"))
              if len(norm(t or "")) >= 20]
    if titles:
        checked.append("ชื่อเรื่อง")
        # ชื่อเรื่องยาวและพิมพ์ผิดบางคำได้ จึงหาว่ามี "ท่อนยาว ๆ" ของชื่อเรื่องโผล่ไหม
        # (แบ่งเป็นคำไม่ได้ เพราะภาษาไทยไม่เว้นวรรคระหว่างคำ)
        for title in titles:
            nt = norm(title)
            chunks = [nt[i:i + 15] for i in range(0, len(nt) - 14, 5)]
            if any(c in nfront for c in chunks):
                found.append("ชื่อเรื่อง")
                break

    return (bool(found) or len(checked) < 2), checked, found


def _page_count_issue(count_wrong, last_arabic):
    """รวม "จำนวนหน้ารวมไม่ตรง" ของทุกหน้าบทคัดย่อเป็นข้อเดียว

    count_wrong = [(ชื่อตำแหน่ง, เลขที่เล่มระบุ), ...]
    คืน (zone, ตำแหน่ง, ข้อความ "พบ")

    จำนวนหน้ารวมเป็นค่าเดียวของทั้งเล่ม แต่พิมพ์ไว้ทั้งบทคัดย่อไทยและอังกฤษ
    เดิมฟ้องหน้าละข้อ = ข้อความเดียวกันสองข้อ · ยุบเป็นข้อเดียวได้ แต่ต้องบอก
    ให้ครบว่าเป็นหน้าไหนบ้าง (และถ้าสองหน้าระบุคนละเลข ต้องบอกว่าหน้าไหนระบุเท่าไร)
    """
    where = " · ".join(lbl for lbl, _num in count_wrong)
    stated = {num for _lbl, num in count_wrong}
    # นโยบายเจ้าหน้าที่ (ส.ค. 2569): ให้เป็น "สีส้ม" เสมอ ไม่ฟันธงแดง
    # เพราะ "เลขหน้าสุดท้าย" ที่ระบบอ่านได้ขึ้นกับว่าอ่านเลขหน้าท้ายเล่มออกครบไหม
    # (หน้าภาคผนวกที่เป็นภาพสแกน/หน้าที่พิมพ์เลขหน้าไม่ชัด ทำให้ระบบอ่านได้น้อยกว่าจริง)
    # ระบบยืนยันเองไม่ได้ว่าเป็นความผิดของเล่ม จึงส่งให้เจ้าหน้าที่ตัดสิน
    zone = "ORANGE"
    if len(stated) == 1:
        found = (f"ระบุจำนวนหน้า {stated.pop()} "
                 f"แต่เลขหน้าสุดท้ายที่ระบบอ่านได้คือ {last_arabic}")
    else:
        detail = " · ".join(f"{lbl} ระบุ {num}" for lbl, num in count_wrong)
        found = (f"ระบุจำนวนหน้าไม่ตรงกัน: {detail} "
                 f"— เลขหน้าสุดท้ายที่ระบบอ่านได้คือ {last_arabic}")
    return zone, where, found


def _report_committee_name_case(rep, members, loc):
    """ชื่อกรรมการบนหน้าลงนามต้องเป็นตัวพิมพ์ใหญ่ต้นคำ (Capital Case)

    กติกาเดียวกับชื่อนักศึกษาบนหน้าเดียวกัน (นโยบายเจ้าหน้าที่ ก.ค. 2569)
    เป็นกฎ "รูปแบบ" ของ template จึงตรวจได้แม้ไม่มีข้อมูลอนุมัติ
    ตรวจเฉพาะชื่อภาษาอังกฤษ — ภาษาไทยไม่มีตัวพิมพ์ใหญ่-เล็ก
    ลงส้มเพราะระบบอ่านชื่อจากตาราง อาจอ่านคร่อมคำได้ ให้เจ้าหน้าที่ยืนยัน
    """
    bad = [members[k] for k in sorted(members)
           if members.get(k) and re.search(r'[A-Za-z]', members[k])
           and not _is_title_case(members[k])]
    if not bad:
        return
    shown = ", ".join(f'"{n}"' for n in bad)
    rep.add("ORANGE", "front_matter", loc,
            f"ชื่อกรรมการบนหน้านี้ไม่ใช่ตัวพิมพ์ใหญ่ต้นคำ (Capital Case): {shown}",
            "ชื่อกรรมการบนหน้าลงนามต้องเป็นตัวพิมพ์ใหญ่ต้นคำ (Capital Case)",
            "แก้ชื่อกรรมการบนหน้านี้เป็นตัวพิมพ์ใหญ่ต้นคำ แล้วให้เจ้าหน้าที่ยืนยัน",
            "FRONT.COMMITTEE")


def _committee_names(expected):
    """รายชื่อจากข้อมูลอนุมัติ — รับได้ทั้ง list ของ dict {'name': ...} และ list ของ str"""
    return [m.get("name", "") if isinstance(m, dict) else (m or "") for m in expected]


def _note_committee_reference(rep, expected, loc, rule_id="FRONT.COMMITTEE"):
    """รายชื่อกรรมการตามข้อมูลอนุมัติ = รายการให้เจ้าหน้าที่ทานเอง (สีม่วง)

    นโยบายเจ้าหน้าที่ (ส.ค. 2569): **ระบบไม่เทียบชื่อและตัวสะกดให้แล้ว** พิมพ์รายชื่อ
    จาก บฑ. ไว้ให้กวาดตาทานเอง เพราะการเทียบตัวอักษรสร้างข้อฟ้องที่ต้องมานั่งปัดทิ้ง
    ทีละข้อจนเป็นอุปสรรคต่อการใช้งานจริง (ดู _report_committee_count)
    """
    names = "  ".join(f'{k}. {_display_committee_name(n)}'
                      for k, n in enumerate(_committee_names(expected), start=1))
    rep.add_human(loc, "ระบบนับจำนวนกรรมการให้แล้ว แต่ไม่ได้เทียบชื่อและตัวสะกด "
                       f"— โปรดทานรายชื่อกับข้อมูลอนุมัติ (บฑ.) คือ {names}", rule_id)


def _report_committee_count(rep, expected, found_names, loc,
                            rule_id="FRONT.COMMITTEE", label="กรรมการ"):
    """นับจำนวนกรรมการให้ครบ — ไม่เทียบชื่อ ไม่เทียบตัวสะกด

    นโยบายเจ้าหน้าที่ (ส.ค. 2569): *"ไม่ต้องตรวจสอบรายชื่อจำนวนอาจารย์ในหน้าลงนาม
    และหน้าบทคัดย่อ แค่นับจำนวนให้ครบพอ ไม่ต้องเทียบชื่อสะกดชื่อตรงไหม
    ส่วนนี้เป็นอุปสรรคต่อการใช้ระบบมาก"*

    เหตุผลเชิงเนื้อหา: ชื่อในเล่มกับใน บฑ. ต่างกันได้โดยไม่ผิด — ตำแหน่งวิชาการเปลี่ยน
    หลังยื่นเรื่อง ใช้ชื่อสกุลคนละแบบ หรือถอดเป็นอังกฤษคนละหลัก การเทียบตัวอักษรจึง
    ให้ข้อฟ้องที่เจ้าหน้าที่ต้องปัดทิ้งเองแทบทุกเล่ม

    จำนวนไม่ตรง = **ส้ม ไม่ใช่แดง** เพราะจำนวนที่นับได้ขึ้นกับว่าระบบอ่านหน้าออกครบไหม
    ระบบยืนยันเองไม่ได้ว่าเป็นความผิดของเล่ม
    """
    want = len(expected)
    if not want or len(found_names) == want:
        return
    # ใส่เครื่องหมายคำพูดรอบชื่อ — เป็น "ค่าที่อ่านได้จากเล่ม" ไม่ใช่ข้อความของระบบ
    # จึงต้องคงเป็นภาษาไทยในรายงานอังกฤษ (ด่าน check_i18n ใช้เครื่องหมายนี้แยก)
    listed = ("  ".join(f'{k}. "{n}"' for k, n in enumerate(found_names, start=1))
              or "ไม่พบชื่อเลย")
    rep.add("ORANGE", "front_matter", loc,
            f"นับรายชื่อ{label}บนหน้านี้ได้ {len(found_names)} คน "
            f"แต่ข้อมูลอนุมัติมี {want} คน — ระบบอ่านได้ว่า {listed}",
            f"ต้องมี{label} {want} คนตามข้อมูลอนุมัติ (บฑ.)",
            f"ตรวจว่าจำนวน{label}บนหน้านี้ครบหรือไม่ "
            "ถ้าครบแล้วแปลว่าระบบอ่านบางช่องไม่ออก ให้ผ่านได้",
            rule_id)


_SIG_LABEL_KIND = {'i': 'advisory', 'ii': 'exam',
                   norm('ก'): 'advisory', norm('ข'): 'exam'}


def signature_page_kind(page_label, page_text):
    """หน้าลงนามหน้านี้เป็นของใคร — ยึด "เลขหน้า" ก่อนตามกติกาเจ้าหน้าที่

    หน้า i (ไทย: ก) = คณะกรรมการที่ปรึกษา, หน้า ii (ไทย: ข) = คณะกรรมการสอบ
    ถ้าเลขหน้าอ่านไม่ได้หรือไม่ใช่ i/ii/ก/ข ค่อยดูจากหัวข้อบนหน้าแทน
    """
    label = (page_label or '').strip()
    return _SIG_LABEL_KIND.get(label.lower()) or _SIG_LABEL_KIND.get(norm(label)) \
        or _committee_page_kind(page_text)


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


def _closest_run(text, want, min_ratio=0.6):
    """ช่วงข้อความใน text ที่ใกล้เคียงกับ want ที่สุด ('' ถ้าไม่ใกล้พอ)

    ใช้บอกเจ้าหน้าที่ว่า "เล่มเขียนว่าอะไร" แทนการฟ้องลอย ๆ ว่า "ไม่พบ" ซึ่งทำให้
    เข้าใจผิดว่าระบบอ่านไม่เจอ ทั้งที่เห็นข้อความอยู่บนหน้ากระดาษ
    (เล่มจริงพิมพ์ "อาชีวนามัย" ตก อ จาก "อาชีวอนามัย" — เจ้าหน้าที่กวาดตาแล้วนึกว่าตรง)

    เลื่อนหน้าต่างทีละ "ตัวอักษร" ไม่ใช่ทีละคำ เพราะภาษาไทยไม่เว้นวรรคระหว่างคำ
    """
    flat = soft(text)
    n = len(soft(want))
    if not flat or n < 4:
        return ''
    best, best_ratio = '', min_ratio - 1e-9
    for size in range(max(4, n - 2), n + 3):
        for i in range(0, len(flat) - size + 1):
            run = flat[i:i + size]
            ratio = difflib.SequenceMatcher(None, norm(run), norm(want)).ratio()
            if ratio > best_ratio:
                best, best_ratio = run, ratio
    return best


def _institution_mismatch(rep, loc, label, want, bottom_text, box, rule_id):
    """ฟ้องช่องสถาบันที่ข้อความไม่ตรง — บอกด้วยว่าเล่มเขียนว่าอะไรและต่างตรงไหน"""
    near = _closest_run(bottom_text, want)
    if near:
        diff = describe_diff(near, want)
        found_msg = f'{box} เขียนว่า "{near}"'
        if diff:
            found_msg += f' — ต่างที่ {diff}'
    else:
        found_msg = f'ไม่พบ{label} "{want}" ใน{box}'
    rep.add("ORANGE", "front_matter", loc, found_msg,
            f'ข้อความใต้ลายเซ็นต้องมี{label} "{want}"',
            f"โปรดตรวจ{label}มุมล่างขวาให้ถูกต้อง", rule_id)


def _check_signature_institution(rep, kind, bottom_text, approved, english_book,
                                 loc_prefix="", loc_suffix=""):
    """ช่องสถาบันแถวล่างสุดของหน้าลงนาม — บทบาทต่างกันในสองหน้า (ยืนยันจาก template ทางการ)

    หน้าอาจารย์ที่ปรึกษา: มุมล่างขวา = "ประธานหลักสูตร ... สาขาวิชา ..." → ต้องมีชื่อสาขา
    หน้ากรรมการสอบ     : มุมล่างขวา = "คณบดี/ผู้อำนวยการคณะ/สถาบัน ..." → ต้องมีชื่อคณะ
    มุมล่างซ้ายเป็นคณบดีบัณฑิตวิทยาลัยทั้งสองหน้า จึงไม่ใช้ตรวจคณะของนักศึกษา

    รับ bottom_text ที่เรียงตามลำดับการอ่านมาแล้ว (ดู signature_committee_slots)
    จึงค้นจากข้อความทั้งแถวล่างรวมกัน — ช่องซ้ายเป็นบัณฑิตวิทยาลัยเสมอ จึงไม่ชนกัน
    """
    found_text = norm(bottom_text)
    if kind == "advisory":
        degree = approved.get("degree_cover_th" if not english_book else "degree_cover_en", "") \
            or approved.get("degree_cover_en", "")
        subject = _degree_subject(degree)
        if subject and norm(subject) not in found_text:
            _institution_mismatch(
                rep, f"{loc_prefix}ประธานหลักสูตร{loc_suffix}", "ชื่อสาขา", subject,
                bottom_text, "ช่องประธานหลักสูตร (มุมล่างขวา)", "FRONT.COMMITTEE")
        return
    # เล่มอังกฤษเทียบชื่อคณะไม่ได้ เพราะชื่อคณะจาก eThesis เป็นภาษาไทย
    faculty = approved.get("faculty", "")
    if faculty and not english_book and norm(faculty) not in found_text:
        _institution_mismatch(
            rep, f"{loc_prefix}คณบดีคณะ{loc_suffix}", "ชื่อคณะ", faculty,
            bottom_text, "ช่องคณบดีคณะ (มุมล่างขวา)", "FRONT.COMMITTEE")


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


def _report_missing_abstract_language(rep, has_en, has_th, en_loc="", th_loc=""):
    """เล่มหลักสูตรไทยต้องมีบทคัดย่อทั้งไทยและอังกฤษ — ขาดภาษาไหนต้องบอกให้ชัด

    นโยบายเจ้าหน้าที่ (ส.ค. 2569): *"เวลาตรวจสอบเล่มของหลักสูตรไทย แล้วไม่พบว่ามี
    บทคัดย่อเป็นไปตามที่กำหนด คือทั้งไทยและอังกฤษ ให้แจ้งว่าบทคัดย่อไม่ครบถ้วน
    ขาดอะไรไป แจ้ง"*

    เดิมพิมพ์สภาพภายในระบบดิบ ๆ ว่า "พบบทคัดย่อ: EN=False, TH=True" ซึ่งเจ้าหน้าที่
    ต้องมาแปลเองว่าขาดภาษาไหน และไม่ได้บอกด้วยว่าอันที่มีอยู่อยู่หน้าไหน
    """
    if has_en and has_th:
        return
    missing = ([] if has_th else ["ภาษาไทย"]) + ([] if has_en else ["ภาษาอังกฤษ"])
    found = []
    if has_th:
        found.append(f"ภาษาไทย ({th_loc})" if th_loc else "ภาษาไทย")
    if has_en:
        found.append(f"ภาษาอังกฤษ ({en_loc})" if en_loc else "ภาษาอังกฤษ")
    detail = "บทคัดย่อไม่ครบถ้วน — ขาดบทคัดย่อ" + "และ".join(missing)
    if found:
        detail += " (พบเฉพาะบทคัดย่อ" + "และ".join(found) + ")"
    rep.add("RED", "front_matter", "บทคัดย่อ", detail,
            "เล่มหลักสูตรไทยต้องมีบทคัดย่อทั้งภาษาไทยและภาษาอังกฤษ",
            "เพิ่มบทคัดย่อ" + "และ".join(missing), "FRONT.ABSTRACT")


def _report_missing_form_fields(rep, approved, required_fields):
    """ช่องข้อมูลอ้างอิงในฟอร์มที่ยังว่าง — สีส้ม ไม่ใช่สีแดง

    ช่องฟอร์มว่าง = ข้อมูลอ้างอิงไม่ครบ **ไม่ใช่ข้อบกพร่องของเล่ม** จึงต้องไม่ตัดสิน
    ว่าเล่ม "ไม่ผ่าน" และต้องไม่เข้ารายการที่นักศึกษาต้องแก้ (system_note) เพราะ
    นักศึกษาแก้เล่มยังไงข้อนี้ก็ไม่หาย — คนที่ทำให้หายได้คือเจ้าหน้าที่ที่กรอกฟอร์ม

    เจอจริงกับเล่มที่ 6: หน้า eThesis ไม่มีบรรทัดตัวย่อปริญญาภาษาอังกฤษให้อ่าน และ
    "DOCTOR OF NURSING SCIENCE" ยังไม่มีในตารางตัวย่อ ระบบจึงเว้นช่องว่างไว้
    แล้วฟ้องแดงใส่เล่มที่ถูกต้องทุกอย่าง
    """
    for field_name in required_fields:
        if soft(approved.get(field_name, "")):
            continue
        rep.add("ORANGE", "front_matter", "ข้อมูลอ้างอิงในแบบฟอร์ม",
                f"ไม่ได้กรอก{FORM_FIELD_LABELS[field_name]} ระบบจึงข้ามการเทียบข้อมูลนี้",
                "การตรวจอย่างเข้มต้องมีข้อมูลอ้างอิงครบทุกช่องที่กำหนด",
                "กรอกข้อมูลในฟอร์มให้ครบแล้วตรวจใหม่ หรือตรวจข้อมูลนี้ด้วยตาเทียบกับ บฑ.",
                "FORM.REQUIRED", system_note=True)


def _check_committees(rep, committees, sig_pages, pages, pdf_path, page_ref,
                      program_language, A, page_labels=None):
    """ตรวจกรรมการบนหน้าลงนามทั้งสองหน้า (ตามกริดตายตัวของ template)

    หน้าไหนเป็นของใครยึดเลขหน้าก่อน (i/ก = ที่ปรึกษา, ii/ข = กรรมการสอบ)

    **ไม่เทียบชื่อและตัวสะกดกับข้อมูลอนุมัติ** (นโยบายเจ้าหน้าที่ ส.ค. 2569)
    ตรวจแค่ "จำนวนครบไหม" ส่วนรายชื่อพิมพ์ไว้ในรายการสีม่วงให้เจ้าหน้าที่ทานเอง
    ภาษาของเล่มจึงไม่มีผลกับส่วนนี้ และไม่ต้องถอดชื่อไทยเป็นอังกฤษอีกต่อไป

    กฎรูปแบบยังตรวจตามเดิม (เป็นกฎของ template ไม่ใช่การเทียบชื่อ):
      ตัวพิมพ์ของชื่อ, ข้อความตัวอย่างที่ค้างอยู่, คุณวุฒิใต้ชื่อต้องมี, ชื่อสาขา/คณะ
    คืน True ถ้าตรวจได้ (อ่านตารางเจอ) — ไม่งั้น False (ให้เจ้าหน้าที่ตรวจเอง)
    """
    page_labels = page_labels or {}
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
        kind = signature_page_kind(page_labels.get(idx, ""), pages[idx])
        expected = committees.get(kind, []) if kind else []
        members, member_quals, bottom_text, member_raw = slots[idx]
        page_label = ("หน้าอาจารย์ที่ปรึกษา" if kind == "advisory" else
                      "หน้ากรรมการสอบ" if kind == "exam" else
                      f"หน้าลงนาม {sig_pages.index(idx) + 1}")
        loc = f"{page_label} ({page_ref(idx)})"
        # กฎรูปแบบของ template — ตรวจได้แม้ยังไม่มีข้อมูลอนุมัติของหน้านี้
        _report_committee_name_case(rep, members, loc)
        # บอกเจ้าหน้าที่ว่าระบบเอา "อะไร" ไปเทียบ — เวลาระบบอ่านหน้าเพี้ยนจะเห็นทันที
        # ว่าเพี้ยนตรงไหน แทนที่จะเห็นแต่ผลตัดสินแล้วเดาไม่ออกว่าทำไมถึงฟ้อง
        read_names = [members[k] for k in sorted(members) if members.get(k)]
        rep.add_info("front_matter", f"รายชื่อที่ระบบอ่านได้จาก{page_label}",
                     "  ".join(f'{k}. {n}' for k, n in enumerate(read_names, start=1))
                     or "ระบบอ่านรายชื่อบนหน้านี้ไม่ได้")
        if not expected:
            continue
        handled_any = True
        _report_sig_placeholders(rep, leftover.get(idx) or [], loc)

        # นับจำนวนอย่างเดียว ไม่เทียบชื่อ/ตัวสะกด (นโยบาย ส.ค. 2569)
        # ภาษาของเล่มจึงไม่มีผลกับการตรวจส่วนนี้อีกต่อไป — เล่มไทยและเล่มอังกฤษ
        # ใช้เกณฑ์เดียวกัน และไม่ต้องถอดชื่อไทยเป็นอังกฤษก่อนอีกแล้ว
        _report_committee_count(rep, expected, read_names, loc)
        _note_committee_reference(rep, expected, loc)

        # ---------- คุณวุฒิใต้ชื่อ: ไม่ตรวจเนื้อหา แต่ต้องมีทุกคน ----------
        # ตรวจเฉพาะช่องกรรมการจริง (1..N) — ช่องที่อ่านเพี้ยนถูกฟ้องเรื่องชื่อไปแล้ว
        for k in range(1, len(expected) + 1):
            if members.get(k) and not member_quals.get(k):
                rep.add("RED", "front_matter", loc,
                        f'ไม่พบคุณวุฒิใต้ชื่อกรรมการ "{members[k]}"',
                        "ใต้ชื่อกรรมการแต่ละคนต้องมีบรรทัดคุณวุฒิ (Degree)",
                        "เพิ่มบรรทัดคุณวุฒิใต้ชื่อกรรมการให้ครบทุกคน", "FRONT.COMMITTEE")

        _check_signature_institution(
            rep, kind, bottom_text, A, english_book,
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


# คุณวุฒิที่ขึ้นต้นก้อนข้อความ เช่น "ปร.ด." "วศ.ด." "Ph.D." "PhD." "Ed.D." "P.hD."
# ตามด้วยสาขาในวงเล็บได้ (ผิดรูปแบบ แต่มีในเล่มจริง และมีกฎฟ้องแยกอยู่แล้ว)
_ABS_DEGREE_HEAD = re.compile(
    r'^\s*(?:'
    # (?=[A-Za-z]*\.) บังคับว่าต้องมี "จุด" อยู่ในตัวย่อ ไม่งั้นคำขึ้นต้นของชื่อคน
    # จะถูกกินเป็นคุณวุฒิ เช่น "THIRAJIT BOONSAEN" เคยถูกอ่านเป็นคุณวุฒิ "THI"
    # แล้วเหลือ "RAJIT BOONSAEN" กลายเป็นชื่อคน
    r'(?=[A-Za-z]*\.)[A-Za-z]{1,4}(?:\.[A-Za-z]{1,4})*\.?'   # Ph.D. / PhD. / M.Sc. / Ph.D
    # ฝั่งไทยยอมให้มีช่องว่างคั่นระหว่างท่อน — เล่มจริงพิมพ์ "พย. ด." / "ปร. ด."
    # (ยอมเฉพาะฝั่งไทยเพราะชื่อคนไทยไม่มีจุด ตัวจับจึงวิ่งเลยเข้าไปในชื่อไม่ได้
    #  ต่างจากฝั่งอังกฤษที่ ". SOMCHAI" จะถูกกินเป็นท่อนคุณวุฒิได้)
    r'|[ก-๙]{1,4}\.(?:\s*[ก-๙]{1,4}\.)*'         # ปร.ด. / วศ.ด. / พย. ด. / ว.ว.
    r')\s*(?:\([^)]*\))?\s*\.?\s*')

# คุณวุฒิหลายใบของคนเดียวเขียนต่อกันด้วย "และ" ได้ เช่น "พ.บ., ว.ว. และ อ.ว."
_DEGREE_CONJUNCTION = re.compile(r'\s+(?:และ|and)\s+', re.I)


def _is_degree_only(text):
    """ก้อนนี้เป็น "คุณวุฒิล้วน" หรือไม่ (ไม่มีชื่อคนปนอยู่)

    ใช้จับกรณีคนหนึ่งมีคุณวุฒิหลายตัวคั่นจุลภาค เช่น "..., M.D., Ph.D., ..."
    ซึ่งทำให้การสลับ ชื่อ/คุณวุฒิ เลื่อนไปทั้งชุดถ้าไม่รู้จัก
    รวมถึงที่คั่นด้วย "และ" ในก้อนเดียวกัน ("ว.ว. และ อ.ว.")

    เช็คจากโครงสร้างล้วน ๆ — ทุกท่อนต้องเป็นคุณวุฒิเต็มท่อน จึงไม่ต้องเดาว่า
    หน้าตาเหมือนชื่อคนไหม (ชื่อคนไทยไม่มีจุด ชื่ออังกฤษก็แมตช์ไม่เต็มท่อน)
    """
    s = (text or "").strip()
    if not s:
        return False
    for part in _DEGREE_CONJUNCTION.split(s):
        part = part.strip()
        m = _ABS_DEGREE_HEAD.match(part)
        if not part or not m or m.end() != len(part):
            return False
    return True


def split_abstract_committee(block):
    """แยก 'ชื่อ, คุณวุฒิ, ชื่อ, คุณวุฒิ, ...' → (names, degrees) ตามลำดับ

    รูปแบบตาม template คือคั่นทุกช่องด้วยจุลภาค แต่เล่มจริงพบว่าบางเล่ม "ลืมจุลภาค"
    ระหว่างคุณวุฒิของคนก่อนกับชื่อของคนถัดไป เช่น
        "ศรัณยา โฆสิตะมงคล, ปร.ด.(การพยาบาล) อุษาวดี อัศดรวิเศษ, Ph.D. (NURSING)"
    ถ้าแบ่งด้วยจุลภาคสลับกันเฉย ๆ ชื่อคนที่ 2 จะกลายเป็น "Ph.D. (NURSING)" แล้วระบบ
    ฟ้องแดงว่า "ไม่พบกรรมการ" ทั้งที่ชื่อพิมพ์อยู่ครบ — จึงตัดคุณวุฒิที่หัวก้อนออกก่อน
    ส่วนที่เหลือในก้อนเดียวกันคือชื่อของคนถัดไป (ตัวขาดจุลภาคมีกฎฟ้องรูปแบบแยกต่างหาก)
    """
    names, degrees = [], []
    for kind, text, _ in _scan_abstract_committee(block):
        (names if kind == "name" else degrees).append(text)
    return names, degrees


def _looks_like_person_name(text):
    """ข้อความนี้หน้าตาเหมือน "ชื่อ นามสกุล" หรือไม่

    ใช้แยกว่าส่วนที่เหลือหลังคุณวุฒิเป็น "ชื่อคนถัดไปที่ลืมใส่จุลภาค" หรือเป็น
    "ส่วนท้ายของคุณวุฒิเอง" — คุณวุฒิบางแบบมีหลายท่อนคั่นด้วยช่องว่าง เช่น
    "Dr. rer. nat." (เยอรมัน), "Dr. med.", "Dr. phil." ซึ่งท่อนหลังขึ้นต้นด้วย
    ตัวพิมพ์เล็กเสมอ ต่างจากชื่อคนที่ขึ้นต้นด้วยตัวพิมพ์ใหญ่หรืออักษรไทย
    และตามรูปแบบที่กำหนดไว้ ชื่อต้องมีทั้งชื่อและนามสกุล = อย่างน้อย 2 คำ
    """
    s = (text or "").strip()
    if len(s.split()) < 2:
        return False
    return not s[:1].islower()


def _scan_abstract_committee(block):
    """ไล่อ่านก้อนรายชื่อกรรมการทีละช่อง — yield (kind, text, missing_comma)

    kind = 'name' | 'degree'
    missing_comma = True เมื่อชื่อนี้ติดมากับคุณวุฒิของคนก่อนหน้าโดยไม่มีจุลภาคคั่น
    """
    expect_name = True
    seen_name = False
    for tok in [t.strip() for t in (block or "").split(",") if t.strip()]:
        if expect_name:
            # คุณวุฒิตัวที่ 2 ของคนเดิม (เช่น "..., M.D., Ph.D., ชื่อคนถัดไป, ...")
            # ไม่ใช่ชื่อคนใหม่ ถ้านับผิดจะเลื่อนสลับ ชื่อ/คุณวุฒิ ไปทั้งชุด
            if seen_name and _is_degree_only(tok):
                yield "degree", tok, False
                continue
            yield "name", tok, False
            seen_name = True
            expect_name = False
            continue
        m = _ABS_DEGREE_HEAD.match(tok)
        rest = tok[m.end():].strip() if m else ""
        if not m or not _looks_like_person_name(rest):
            # ทั้งก้อนคือคุณวุฒิ (รวมคุณวุฒิหลายท่อนอย่าง "Dr. rer. nat.")
            yield "degree", tok, False
            expect_name = True
            continue
        yield "degree", tok[:m.end()].strip(), False
        yield "name", rest, True       # ขาดจุลภาคคั่น — ที่เหลือคือชื่อคนถัดไป


def abstract_committee_missing_commas(block):
    """คืนรายชื่อกรรมการที่ไม่มีจุลภาคคั่นจากคุณวุฒิของคนก่อนหน้า"""
    return [text for kind, text, missing in _scan_abstract_committee(block)
            if kind == "name" and missing]


def _check_abstract_committees(rep, committees, abs_en_pages, abs_th_pages, pages,
                               page_ref):
    """ตรวจคณะกรรมการที่ปรึกษาบนหน้าบทคัดย่อ (รูปแบบ + จำนวน)

    รูปแบบต่อคน = 'ชื่อ นามสกุล, คุณวุฒิ' — ไม่มีสาขาในวงเล็บ, ไม่มีตำแหน่งวิชาการ
    หน้าอังกฤษ: ชื่อต้องเป็นตัวพิมพ์ใหญ่ทั้งหมด

    **ไม่เทียบชื่อและตัวสะกดกับข้อมูลอนุมัติ** (นโยบายเจ้าหน้าที่ ส.ค. 2569)
    ตรวจแค่จำนวนครบไหม เหมือนหน้าลงนาม ส่วนรายชื่อให้เจ้าหน้าที่ทานเองจากรายการสีม่วง

    กฎ "รูปแบบ" เป็นกฎของ template ล้วน จึงตรวจได้แม้ไม่มีข้อมูลกรรมการจาก eThesis
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
                inside = ", ".join(f'"({s})"' for s in re.findall(r'\(([^)]*)\)', block))
                rep.add("RED", "front_matter", loc,
                        f"รายชื่อกรรมการที่ปรึกษามีสาขาวิชาในวงเล็บ: {inside}"
                        if inside else "รายชื่อกรรมการที่ปรึกษามีสาขาวิชาในวงเล็บ",
                        "รูปแบบต้องเป็น 'ชื่อ นามสกุล, คุณวุฒิ' โดยไม่มีสาขาวิชาในวงเล็บ",
                        f"ลบ {inside} ออก ให้เหลือเฉพาะชื่อและคุณวุฒิ"
                        if inside else "ลบสาขาวิชาในวงเล็บออกจากคุณวุฒิ", "FRONT.ABSTRACT")

            # รูปแบบ 1.1: ต้องมีจุลภาคคั่นระหว่างคุณวุฒิของคนก่อนกับชื่อคนถัดไป
            # (เจอในเล่มจริง ถ้าไม่ฟ้องตรงนี้ เจ้าหน้าที่จะไม่รู้ว่าต้องเติมจุลภาคตรงไหน)
            for nm in abstract_committee_missing_commas(block):
                rep.add("RED", "front_matter", loc,
                        f'ไม่มีจุลภาคคั่นหน้าชื่อ "{nm}"',
                        "ต้องคั่นด้วยจุลภาคทุกช่อง คือ 'ชื่อ นามสกุล, คุณวุฒิ, ชื่อ นามสกุล, คุณวุฒิ'",
                        f'เติมจุลภาคหน้าชื่อ "{nm}"', "FRONT.ABSTRACT")
            # รูปแบบ 2-3: รวมชื่อที่ผิดของหน้านั้นไว้ข้อเดียว ไม่ฟ้องรายคน
            # (เล่มที่ 4 พิมพ์ Capital Case ทั้ง 3 คน เดิมได้ 3 ข้อที่แก้เหมือนกันหมด)
            stripped = [nm for nm in (n.strip() for n in names)
                        if _strip_committee_title(nm) != nm]
            if stripped:
                shown = ", ".join(f'"{nm}"' for nm in stripped)
                rep.add("RED", "front_matter", loc,
                        f'ชื่อกรรมการมีตำแหน่งทางวิชาการนำหน้า: {shown}',
                        "รูปแบบต้องเป็นชื่อ-สกุลและคุณวุฒิเท่านั้น ไม่มีตำแหน่งทางวิชาการ",
                        "ลบตำแหน่งทางวิชาการนำหน้าชื่อออก", "FRONT.ABSTRACT")
            lower = [nm for nm in (n.strip() for n in names)
                     if heading_en and re.search(r'[a-z]', nm)]
            if lower:
                shown = ", ".join(f'"{nm}"' for nm in lower)
                rep.add("RED", "front_matter", loc,
                        f'ชื่อกรรมการไม่ได้เป็นตัวพิมพ์ใหญ่ทั้งหมด: {shown}',
                        "ชื่อกรรมการในบทคัดย่อภาษาอังกฤษต้องเป็นตัวพิมพ์ใหญ่ทั้งหมด",
                        "แก้ชื่อกรรมการเป็นตัวพิมพ์ใหญ่ทั้งหมด", "FRONT.ABSTRACT")

            # นับจำนวนอย่างเดียว ไม่เทียบชื่อ/ตัวสะกด (นโยบาย ส.ค. 2569)
            # ภาษาของหน้าจึงไม่มีผล — บทคัดย่อไทยและอังกฤษใช้เกณฑ์เดียวกัน
            if not advisory:
                continue
            _report_committee_count(rep, advisory, [n.strip() for n in names], loc,
                                    "FRONT.ABSTRACT", "กรรมการที่ปรึกษา")
            _note_committee_reference(rep, advisory, loc, "FRONT.ABSTRACT")


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

    ข้อที่ตั้ง system_note=True ไม่เข้าสรุปทุกกรณี เพราะเป็นข้อจำกัดของระบบเอง
    (เช่น ยังไม่ได้ตั้ง API key จึงถอดชื่อกรรมการเป็นอังกฤษไม่ได้) นักศึกษาแก้เล่ม
    ยังไงข้อนี้ก็ไม่หาย การใส่ไว้ในใบสั่งแก้ทำให้นักศึกษาสับสน

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
    return [it for it in items if not it.get("system_note")]


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
    # คั่นด้วย " · " ไม่ใช่ช่องว่างเปล่า ๆ — อ่านง่ายกว่า และทำให้แยก "สิ่งที่พบ" กับ
    # "สิ่งที่ต้องเป็น" ออกจากกันได้ (ตัวแปลอังกฤษต้องแปลทีละท่อน ถ้าต่อกันด้วย
    # ช่องว่างจะแยกไม่ออกว่าท่อนไหนจบตรงไหน แล้วต้องตกไปใช้การแทนที่แบบเศษคำ)
    directive = summary_tidy(issue.get("expected")) or summary_tidy(issue.get("fix"))
    return f"{sentence} · {directive}".strip() if directive else sentence.strip()


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
    # เขียนให้เหมือนคนพูด: บอกว่า "ในเล่มเขียนว่าอะไร" ก่อน แล้วค่อยบอกว่าต่างยังไง
    # (ของเดิมขึ้นต้นด้วยคำตัดสินแบบระบบ เช่น "ชื่อบทข้อความไม่ตรง:" และมีคะแนน
    #  ความใกล้เคียงซึ่งเจ้าหน้าที่เอาไปใช้อะไรไม่ได้)
    if compared['status'] == 'case':
        detail = f'{label}ในเล่มเขียนว่า "{compared["actual"]}" — ต่างกันแค่ตัวพิมพ์เล็ก-ใหญ่'
    elif compared['status'] == 'typo':
        detail = f'{label}ในเล่มเขียนว่า "{compared["actual"]}" — พิมพ์ผิดเล็กน้อย'
    else:
        detail = f'{label}ในเล่มเขียนว่า "{compared["actual"]}"'
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
    # ข้อความที่ประกอบจาก extract_words ยังมีสระ/วรรณยุกต์ไทยหลุดตำแหน่งและนิคหิตที่
    # ฟอนต์ map เป็นช่องว่าง ("จำลอง" -> "จา ลอง") ถ้าเอาไปแสดงในรายงานเจ้าหน้าที่จะ
    # อ่านไม่ออกว่าหมายถึงข้อความไหนของเล่ม จึงประกอบข้อความใหม่จาก chars ด้วยตัวเดียว
    # กับ _page_text ส่วนการนับตัวหนายังใช้ words ตามเดิม (chars ไม่มี fontname ที่เชื่อได้)
    page_chars = _thai_chars(getattr(pdf_page, 'chars', None) or [])

    results = []
    for group in grouped:
        line_words = sorted(group['words'], key=lambda word: float(word.get('x0', 0)))
        text = ' '.join(word.get('text', '') for word in line_words).strip()
        if not text:
            continue
        row_chars = [c for c in page_chars
                     if abs(float(c.get('top', 0)) - group['top']) <= tolerance]
        text = _compose_thai_line(row_chars) or text
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


# เลขหน้าในสารบัญเขียนได้ 3 แบบ: อารบิก / โรมัน / พยัญชนะไทย
_TOC_PAGE_TOKEN = r'(?:\d{1,4}|[ivxlcdm]+|[ก-ฮ])'


def _strip_toc_page_number(text):
    s = soft(text)
    # ตัดเลขหน้าท้ายบรรทัดออกก่อน (อารบิก/โรมัน/อักษรไทย)
    # รองรับ "ช่วงหน้า" ด้วย เช่น "LIST OF TABLES xi-xii" / "สารบัญตาราง ฎ-ฏ"
    # เล่มที่หัวข้อกินสองหน้าเขียนแบบนี้ ถ้าไม่ตัดจะจำแนกหัวข้อไม่ออก แล้วฟ้องผิดว่า
    # "ไม่พบหัวข้อ LIST OF TABLES ในสารบัญ" ทั้งที่มีอยู่ (เล่มที่ 3)
    s = re.sub(rf'\s+{_TOC_PAGE_TOKEN}(?:\s*[-–—]\s*{_TOC_PAGE_TOKEN})?\s*$', '', s,
               flags=re.I)
    # ตัด "จุดไข่ปลา" (dot leader) ที่ลากเชื่อมชื่อหัวข้อกับเลขหน้า เช่น
    #   "LIST OF TABLES ......................" หรือ "ABSTRACT ………… ."
    # มันคือเส้นประของ template ไม่ใช่การสะกด ถ้าไม่ตัดจะทำให้ compare_values
    # (rule toc_heading เป็น case_sensitive จึงข้ามการเทียบแบบ norm) มองว่า
    # หัวข้อสะกดผิดทุกบรรทัด ทั้งที่ถูกต้อง — ตัดชุดจุด/ellipsis ตั้งแต่ 2 ตัวขึ้นไป
    s = re.sub(r'\s*(?:[.…]\s*){2,}$', '', s)
    return s.strip()


def _toc_page_label(text):
    """Return the page label printed at the end of one TOC entry.

    ถ้าเขียนเป็นช่วง ("xi-xii") ให้ยึด "หน้าแรก" เพราะกฎที่ใช้ค่านี้ถามว่า
    หัวข้อเริ่มหน้าไหน
    """
    match = re.search(rf'\s({_TOC_PAGE_TOKEN})\s*[-–—]\s*{_TOC_PAGE_TOKEN}\s*$',
                      soft(text), re.I) or \
        re.search(r'\s(\d{1,4}|[ivxlcdm]+|[ก-ฮ])\s*$', soft(text), re.I)
    if not match:
        return ""
    label = match.group(1)
    return str(int(label)) if label.isdigit() else label.lower()


def _toc_misspelled_heading(toc_lines, want, min_ratio=0.7):
    """บรรทัดในสารบัญที่ "น่าจะใช่หัวข้อนี้แต่สะกดผิด" — คืน (หัวข้อที่พบ, ดัชนีหน้า)

    เล่มจริงพิมพ์ "ประวัติผู้จัย" ตก "วิ" จาก "ประวัติผู้วิจัย" ตัวจำแนกหัวข้อจึงไม่รู้จัก
    แล้วระบบฟ้องว่า "ไม่พบหัวข้อ ... ในสารบัญ" ทั้งที่บรรทัดพิมพ์อยู่ในสารบัญ
    เจ้าหน้าที่เห็นแล้วนึกว่าระบบอ่านไม่เจอ ทั้งที่ปัญหาจริงคือ "สะกดผิด"
    ซึ่งเป็นคนละวิธีแก้กัน (แก้ตัวสะกด ไม่ใช่เพิ่มบรรทัดใหม่)

    ข้ามบรรทัดที่จำแนกเป็นหัวข้ออื่นได้แล้ว เพื่อไม่ให้หัวข้อที่มีอยู่จริงถูกดึงมาตอบผิดที่
    """
    want_key = norm(want)
    best, best_ratio = None, min_ratio - 1e-9
    for page_idx, line in toc_lines:
        head = _strip_toc_page_number(line).strip()
        if not head or _toc_section_kind(line):
            continue
        ratio = difflib.SequenceMatcher(None, norm(head), want_key).ratio()
        if ratio > best_ratio:
            best, best_ratio = (head, page_idx), ratio
    return best


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
    # ชื่อบทต้องมาก่อน "พิมพ์ผิดเล็กน้อย" — ไม่งั้นชื่อบทที่ต่างจากประกาศเพียงตัวเดียว
    # จะถูกจัดเป็นหมวด "สะกดผิด" ส่วนบทที่ต่างมากถูกจัดเป็น "ชื่อบทไม่ตรงประกาศ"
    # กลายเป็นปัญหาเดียวกันแต่โผล่คนละหมวด เจ้าหน้าที่เห็นเป็นสองเรื่อง (ซ้ำซ้อน)
    if "ชื่อบท" in text:
        return "ชื่อบทไม่ตรงประกาศ" if "ประกาศ" in text else "สะกดผิด (typo)"
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

    def add(self, zone, part, loc, found, expected, fix="", rule_id=None,
            system_note=False):
        """system_note=True = ข้อจำกัดของระบบ ไม่ใช่จุดที่นักศึกษาแก้ได้

        ยังแสดงในรายงานฝั่งเจ้าหน้าที่ตามปกติ แต่ไม่นับเป็น "จุดที่ต้องแก้" ในข้อความ
        สรุปที่ส่งให้นักศึกษา (เช่น ระบบถอดชื่อกรรมการเป็นอังกฤษไม่ได้เพราะยังไม่ได้
        ตั้งค่า API key — เป็นเรื่องการติดตั้งเซิร์ฟเวอร์ นักศึกษาทำอะไรกับเล่มก็ไม่หาย)
        """
        rule_id = rule_id or DEFAULT_RULE_BY_PART.get(part, "FORM.REQUIRED")
        fix = fix or f"แก้ไขให้เป็นไปตามข้อกำหนด: {expected}"
        self.zones[zone].append({
            "part": part,
            "location": loc,
            "found": found,
            "expected": expected,
            "fix": fix,
            "system_note": system_note,
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


def run_check(pdf_path, approved, chapters_mode="strict", progress=None,
              skip_identity_check=False):
    """skip_identity_check=True ปิดด่าน "ไฟล์ eThesis กับเล่มคนละคน"

    ใช้เฉพาะเครื่องมือตรวจคำแปล (check_i18n --corpus) ที่จงใจจับคู่ข้อมูลอ้างอิง
    สมมติกับเล่มไหนก็ได้ เพื่อให้ทุกข้อความของระบบถูกสร้างออกมาให้ตรวจคำแปล
    """
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

    # ไฟล์ eThesis เป็นของนักศึกษาคนเดียวกับเล่มไหม — ต้องรู้ก่อนกฎอื่นที่ใช้ข้อมูลอนุมัติ
    # (รวมถึงกฎชนิดเลขหน้าส่วนนำ ที่อ่าน program_language จากข้อมูลอนุมัติ)
    same_student, sig_checked, _sig_found = (
        ethesis_matches_book(approved, pages)
        if approved and not skip_identity_check else (True, [], []))
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
        toc_page_indices = _toc_continuation_pages(
            pages, toc_start, min(after_toc) if after_toc else n)
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
    # ถ้าไฟล์ eThesis เป็นของคนอื่น ค่า "รูปแบบเล่ม" ในนั้นก็เป็นของคนอื่นด้วย
    # ปล่อยให้ระบบเดารูปแบบจากตัวเล่มเองแทน ไม่งั้นชื่อบทจะถูกเทียบกับผังบทผิดชุด
    # แล้วฟ้องแดงรัวทั้งเล่ม (เล่มที่ 3 คู่กับ eThesis คนอื่น: แดง 49 ข้อ)
    option = resolve_option(body_ch, approved if same_student else None, chapters_mode)
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
                            # ใส่เครื่องหมายคำพูดให้ชัดว่าเป็น "ข้อความที่คัดมาจากเล่ม"
                            # ไม่ใช่คำของระบบ (รายงานอังกฤษจะได้ไม่แปลชื่อหัวข้อของเล่ม)
                            "หัวข้อหลักไม่เป็นตัวหนา: "
                            + ", ".join(f'"{t}"' for t in nonbold),
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

    if chapters_mode == "strict" and body_ch and BODY_RULES['check_body_chapter_count']:
        if option == 1 and len(body_ch) != 6:
            rep.add("RED", "body", "ทั้งเล่ม", f"พบ {len(body_ch)} บท",
                    "ประกาศ 2569: รูปแบบดั้งเดิมต้องมี 6 บท", "ปรับโครงบทตามประกาศ", "BODY.OPTION1")
        if option == 2 and len(body_ch) not in (2, 3):
            rep.add("RED", "body", "ทั้งเล่ม", f"พบ {len(body_ch)} บท",
                    "รูปแบบตีพิมพ์ต้องมี 2-3 บท", "", "BODY.OPTION2")

    # ---------- ชื่อบทเทียบประกาศ (สารบัญ + เนื้อหา รวมเป็นข้อเดียวต่อบท) ----------
    #
    # ชื่อบทต้องตรงกันทั้ง 3 ทาง: ประกาศ ↔ สารบัญ ↔ เนื้อหา โดยยึดประกาศเป็นหลัก
    # จึงเทียบทั้งสองฝั่งกับประกาศเสมอ แม้สารบัญกับเนื้อหาจะต่างกันไปแล้ว
    #
    # เดิมแยกเป็นสองข้อ (ฝั่งสารบัญข้อหนึ่ง ฝั่งเนื้อหาอีกข้อหนึ่ง) ทั้งที่เป็น
    # "ชื่อบทเดียวกันผิดจากประกาศ" เรื่องเดียว เจ้าหน้าที่ต้องอ่านซ้ำสองรอบ
    # ยิ่งกว่านั้นสองข้อยังตกไปคนละหมวด (ฝั่งที่ต่างเล็กน้อยเข้าหมวด "สะกดผิด"
    # ฝั่งที่ต่างมากเข้าหมวด "ชื่อบทไม่ตรงประกาศ") จึงดูเหมือนเป็นคนละปัญหา
    # ตอนนี้รวมเป็นข้อเดียวต่อบท และบอกในข้อความว่าผิดที่ไหนบ้าง
    if chapters_mode == "strict":
        canon = CANONICAL_OPT1 if option == 1 else CANONICAL_OPT2
        rule_id = "BODY.OPTION1" if option == 1 else "BODY.OPTION2"
        toc_by_ch = {c[0]: (_toc_chapter_title(c[3]), c[4]) for c in toc_ch}
        body_by_ch = ({c[0]: (c[1], c[2]) for c in body_ch}
                      if body_ch and BODY_RULES['check_body_title_against_canonical'] else {})

        def _title_status(title, cn):
            """สถานะของชื่อบทหนึ่งฝั่ง — คืน None ถ้าถือว่าใช้ได้"""
            kind, compared, expected = canonical_title_status(title, cn, option)
            if kind == 'exact':
                return None
            # หัวบทยาวอาจถูกตัดขึ้นบรรทัดใหม่ — ยอมรับถ้าชื่อมาตรฐานขึ้นต้นด้วยข้อความที่พบ
            nb = norm(title)
            if len(nb) >= 8 and any(norm(cand).startswith(nb) for cand in canon[cn - 1]):
                return None
            return kind, compared, expected

        for cn in sorted(set(toc_by_ch) | set(body_by_ch)):
            if not (1 <= cn <= enforced_chapters):
                continue
            toc_title, toc_idx = toc_by_ch.get(cn, (None, None))
            body_title, body_idx = body_by_ch.get(cn, (None, None))
            toc_bad = _title_status(toc_title, cn) if toc_title is not None else None
            body_bad = _title_status(body_title, cn) if body_title is not None else None
            if not toc_bad and not body_bad:
                continue

            expected_title = (toc_bad or body_bad)[2]
            both = bool(toc_bad and body_bad)
            same = both and norm(toc_title) == norm(body_title)
            zone = ("ORANGE" if all(s[0] == 'variant' for s in (toc_bad, body_bad) if s)
                    else "RED")
            if both:
                where = f"บทที่ {cn} — สารบัญ ({page_ref(toc_idx)}) และในเนื้อหา ({page_ref(body_idx)})"
                part = "front_matter"
            elif toc_bad:
                where, part = f"สารบัญ ({page_ref(toc_idx)}) บทที่ {cn}", "front_matter"
            else:
                where, part = f"บทที่ {cn} ({page_ref(body_idx)})", "body"

            if zone == "ORANGE":
                seen = (f'"{toc_title}"' if same or not both else
                        f'สารบัญพิมพ์ "{toc_title}" ส่วนเนื้อหาพิมพ์ "{body_title}"')
                rep.add("ORANGE", part, where,
                        f'ชื่อบทสะกดตามคู่มือ: {seen}',
                        f'ประกาศใช้ "{expected_title}" แต่คู่มือแสดงแบบที่พบ — เจ้าหน้าที่ยืนยันได้',
                        "ยืนยันตามคู่มือ หรือแก้ให้ตรงประกาศ", rule_id)
                continue

            if both and not same:
                found = (f'ชื่อบทไม่ตรงประกาศ: สารบัญพิมพ์ "{toc_title}" '
                         f'ส่วนเนื้อหาพิมพ์ "{body_title}"')
            else:
                # ชื่อเดียวกันทั้งสองที่ (หรือผิดที่เดียว) — ชี้จุดต่างให้ด้วย
                bad = toc_bad or body_bad
                found = mismatch_detail("ชื่อบท", bad[1], expected_title)
            rep.add("RED", part, where, found,
                    f'ตามประกาศ 2569 ควรเป็น "{expected_title}"',
                    "แก้ชื่อบทให้ตรงประกาศ" + (" ทั้งในสารบัญและในเนื้อหา" if both else ""),
                    rule_id)

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
        _expected_front_label_style(
            (approved or {}).get("program_language", "") if same_student else ""))

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
    # "จำนวนหน้ารวม" เป็นค่าเดียวของทั้งเล่ม แต่พิมพ์ไว้ทั้งบทคัดย่อไทยและอังกฤษ
    # เดิมฟ้องหน้าละข้อ = ข้อความเดียวกันสองข้อ จึงเก็บผลไว้ก่อนแล้วรวมเป็นข้อเดียว
    # (ตามที่เจ้าหน้าที่สั่ง: ยุบได้ แต่ต้องบอกว่าเป็นหน้าไหนบ้าง)
    count_missing, count_wrong = [], []
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
            count_missing.append(lbl)
        elif last_arabic is not None and int(m2.group(1)) != last_arabic:
            count_wrong.append((lbl, int(m2.group(1))))
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

    if count_missing:
        rep.add(FRONT_FAILURE_ZONE, "front_matter", " · ".join(count_missing),
                "ระบบไม่พบการระบุจำนวนหน้า (เช่น 123 pages / 123 หน้า)",
                "ท้ายบทคัดย่อต้องระบุจำนวนหน้ารวมของเล่ม", "ตรวจด้วยตา", "FRONT.ABSTRACT")
    if count_wrong:
        zone, where, found_count = _page_count_issue(count_wrong, last_arabic)
        rep.add(zone, "front_matter", where, found_count,
                "จำนวนหน้าที่ระบุต้องเท่ากับเลขหน้าสุดท้ายของเล่ม",
                "เจ้าหน้าที่ยืนยันเลขหน้าสุดท้ายจากไฟล์จริง แล้วให้แก้ตัวเลขให้ตรงทุกหน้าที่ระบุไว้",
                "FRONT.ABSTRACT")

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
    if approved and not same_student:
        # ข้ามการเทียบข้อมูลอนุมัติทั้งชุด — ถ้าปล่อยให้เทียบต่อ รายงานจะแดงยาวเป็นสิบข้อ
        # โดยไม่มีข้อไหนช่วยอะไร และเสี่ยงที่เจ้าหน้าที่จะส่งกลับให้นักศึกษาแก้ทั้งที่เล่มไม่ผิด
        rep.add("ORANGE", "front_matter", "ไฟล์ที่อัปโหลด",
                "ข้อมูลอนุมัติกับเล่มไม่ตรงกันเลยสักอย่าง ("
                + " / ".join(sig_checked) + ") น่าจะเป็นคนละคนกัน",
                "ไฟล์ eThesis กับไฟล์เล่มต้องเป็นของนักศึกษาคนเดียวกัน",
                "ตรวจว่าเลือกไฟล์ eThesis ตรงกับเล่มหรือไม่ แล้วสั่งตรวจใหม่ "
                "(ระบบข้ามการเทียบข้อมูลอนุมัติทั้งหมดไว้ก่อน)",
                "FORM.REQUIRED", system_note=True)
    elif approved:
        A = approved
        program_language = A.get("program_language", "")
        required_fields = FRONT_MATTER_RULES["required_form_fields"].get(program_language, ())
        _report_missing_form_fields(rep, A, required_fields)

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
                # ไม่มีหน้าบทคัดย่อภาษานั้นในเล่ม = ถูกฟ้องเป็นสีแดงในกฎ "ภาษาครบตามหลักสูตร"
                # อยู่แล้ว จึงไม่ฟ้องซ้ำด้วยข้อความที่ฟังเหมือนระบบอ่านไม่ได้
                rep.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", alt_lbl, "pending",
                                     "เล่มไม่มีหน้าบทคัดย่อภาษานี้")

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
            # ชื่อผู้เขียนท้ายกิตติกรรมประกาศก็คือชื่อนักศึกษา จึงเทียบแบบ
            # "ไม่เอาคำนำหน้า" ตามกติกาเดียวกัน (บฑ. มียศ แต่เล่มมักพิมพ์แค่ชื่อ-สกุล)
            expected_ack_name = _strip_student_title(
                student_name_th if thai_book else person_name_sentence_case(student_name))
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
            # เทียบ "ชื่อ-สกุล" อย่างเดียว ไม่เอาคำนำหน้า/ยศ ตามที่เจ้าหน้าที่กำหนด
            # (บฑ. ของเล่มที่ 9 เขียน "พ.จ.ต. ณัชนพ เพชรสุข" แต่เล่มพิมพ์แค่ชื่อ-สกุล
            #  เดิมฟ้องแดง 5 ตำแหน่งจากสาเหตุเดียวกันหมด)
            core_name = _strip_student_title(primary_student_name)
            name_spots = [("หน้าปก", 0, "cover")] + [
                (f"หน้าลงนาม {k + 1} ({page_ref(idx)})", idx, "signature")
                for k, idx in enumerate(sig_pages)
            ]
            for spot_name, spot_idx, spot_kind in name_spots:
                compared = compare_reference_text(pages[spot_idx], core_name, 'student_name')
                if compared['status'] != 'exact':
                    rep.add_verification("ชื่อนักศึกษา", spot_name, "fail",
                                         compared['actual'])
                    rep.add("RED", "front_matter", spot_name,
                            mismatch_detail("ชื่อนักศึกษา", compared, core_name),
                            f"ต้องสะกดตรงข้อมูลอนุมัติทุกหน้า: \"{core_name}\"",
                            "แก้การสะกดชื่อ", "FORM.APPROVED_MATCH")
                    continue
                _report_student_name_style(rep, pages[spot_idx], core_name, spot_name,
                                           "ชื่อนักศึกษา", spot_kind,
                                           "FORM.APPROVED_MATCH")

        # ชื่อนักศึกษาในบทคัดย่อ: ไม่พบ = 🔴, มีคำนำหน้า = 🟠
        if A.get("program_language") in ("thai", "thai_english"):
            name_checks = [
                (student_name_th, abs_th_idx, "บทคัดย่อภาษาไทย", "ชื่อภาษาไทย", True),
                (student_name, abs_en_idx, "บทคัดย่อภาษาอังกฤษ", "ชื่อภาษาอังกฤษ", True),
            ]
        else:
            name_checks = [(student_name, abs_en_idx, "บทคัดย่อ", "ชื่อนักศึกษา", False)]
        for nm3, aidx, albl, nlbl, required in name_checks:
            if not nm3:
                if required:
                    rep.add(FRONT_FAILURE_ZONE, "front_matter", albl, f"ไม่ได้กรอก{nlbl}ของนักศึกษาในฟอร์ม",
                            f"หลักสูตรไทยต้องตรวจ{nlbl}ในหน้า{albl}",
                            "กรอกฟอร์มให้ครบแล้วตรวจใหม่", "FORM.REQUIRED")
                continue
            if aidx is None:
                # เล่มไม่มีหน้าบทคัดย่อภาษานี้ — กฎ "ภาษาครบตามหลักสูตร" ฟ้องแดงไปแล้ว
                rep.add_verification("ชื่อนักศึกษา", albl, "pending",
                                     f"เล่มไม่มีหน้า{albl}")
                continue
            core3 = _strip_student_title(nm3)
            compared = compare_reference_text(pages[aidx], core3, 'student_name')
            if compared['status'] != 'exact':
                rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})",
                                     "fail", compared['actual'])
                rep.add("RED", "front_matter", f"{albl} ({page_ref(aidx)})",
                        mismatch_detail(f"{nlbl}", compared, core3),
                        f"{nlbl}ของนักศึกษาในหน้า{albl}ต้องสะกดตรงข้อมูลอนุมัติ: \"{core3}\"",
                        "ตรวจการสะกด", "FORM.APPROVED_MATCH")
            else:
                _report_student_name_style(rep, pages[aidx], core3,
                                           f"{albl} ({page_ref(aidx)})", nlbl,
                                           "abstract", "FORM.APPROVED_MATCH")

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
        # ไม่ต้องถอดชื่อกรรมการเป็นอังกฤษอีกแล้ว — ระบบไม่เทียบชื่อ/ตัวสะกดตั้งแต่
        # นโยบาย ส.ค. 2569 (ดู _report_committee_count) เหลือแค่ตรวจว่าจำนวนครบ
        checked_committee = False
        if committees.get("advisory") or committees.get("exam"):
            checked_committee = _check_committees(
                rep, committees, sig_pages, pages, pdf_path, page_ref,
                prog_lang, A, page_labels)
        # หน้าบทคัดย่อ: รูปแบบรายชื่อกรรมการ (ตัวพิมพ์ใหญ่/วงเล็บ/ตำแหน่งวิชาการ) เป็นกฎ
        # ของ template ล้วน จึงตรวจเสมอ ส่วนการนับจำนวนทำเมื่อมีข้อมูล eThesis
        _check_abstract_committees(rep, committees, abs_en_pages, abs_th_pages,
                                   pages, page_ref)

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
        if prog in ("thai", "thai_english"):
            _report_missing_abstract_language(
                rep, has_en_abs, has_th_abs,
                page_ref(abs_en_idx) if abs_en_idx is not None else "",
                page_ref(abs_th_idx) if abs_th_idx is not None else "")

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
                    # หัวข้อที่ "มีอยู่แต่สะกดผิด" ต้องบอกว่าสะกดผิด ไม่ใช่บอกว่าไม่มี
                    # เพราะวิธีแก้คนละอย่างกัน (แก้ตัวสะกด ไม่ใช่เพิ่มบรรทัดใหม่)
                    typo = _toc_misspelled_heading(toc_lines, section_label)
                    if typo:
                        head, typo_idx = typo
                        found_msg = f'สารบัญสะกดหัวข้อนี้ผิด เขียนว่า "{head}"'
                        diff = describe_diff(head, section_label)
                        if diff:
                            found_msg += f" — ต่างที่ {diff}"
                        rep.add(
                            "RED", "front_matter", f"สารบัญ ({page_ref(typo_idx)})",
                            found_msg,
                            f'หัวข้อในสารบัญต้องสะกดว่า "{section_label}"',
                            f'แก้ตัวสะกดในสารบัญเป็น "{section_label}"',
                            "FRONT.TOC_CONTENT",
                        )
                        continue
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
