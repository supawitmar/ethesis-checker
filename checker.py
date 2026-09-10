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
TOC_PAGE_ZONE = rule_zone("FRONT.TOC_PAGE_REF", "YELLOW")
ABSTRACT_COMMA_ZONE = rule_zone("FRONT.ABSTRACT_COMMA", "YELLOW")
DEGREE_SPACING_ZONE = rule_zone("FORM.DEGREE_SPACING", "YELLOW")
SIG_LABEL_ZONE = rule_zone("PAGE.SIGNATURE_LABEL", "ORANGE")
# keyword เกิน 5 คำ = ข้อสังเกต ผ่านได้ (เจ้าหน้าที่สั่ง ก.ย. 2569) กฎอื่นของ
# FRONT.ABSTRACT (ภาษาครบ จำนวนหน้า ชื่อเรื่อง รายชื่อกรรมการ) ยังเป็นแดงเหมือนเดิม
KEYWORD_COUNT_ZONE = rule_zone("FRONT.KEYWORD_COUNT", "YELLOW")
# เลขหน้าที่เรียงไม่ต่อเนื่อง = ส้ม ตามที่เจ้าหน้าที่สั่ง (ก.ย. 2569) ทั้งส่วนนำและเนื้อหา
# กฎอื่นของ PAGE.NUMBERING (ชนิดเลขหน้าผิด / ไม่มีเลขหน้า / เลขหน้าอารบิกไม่เริ่มที่บทที่ 1)
# ยังเป็นแดงเหมือนเดิม จึงต้องแยกรหัสกฎ ไม่ใช่ใส่ failure_zone ให้ PAGE.NUMBERING ทั้งก้อน
PAGE_SEQUENCE_ZONE = rule_zone("PAGE.NUMBERING_SEQUENCE", "ORANGE")

# ความใกล้เคียงขั้นต่ำที่ยอมให้ยก "ช่วงข้อความในเล่ม" มาอ้างว่าเป็นประโยค template
# ของหน้าลงนาม — สูงกว่าค่าปกติของ _closest_run เพราะชื่อปริญญาอยู่ต่อท้ายประโยคนี้
# พอดี ถ้าตั้งหลวมจะยกชื่อปริญญามาปนแล้วสองประเด็นนี้ปนกันในรายงาน
SIGNATURE_TEMPLATE_MIN_RATIO = 0.8

# ตัวคั่นของ "ลำดับส่วนประกอบ" — ต้องเป็นคำ ไม่ใช่ลูกศร เพราะเจ้าหน้าที่คัดลอก
# ข้อความสรุปไปวางในอีเมล/Word ซึ่งฟอนต์ปลายทางแสดงสัญลักษณ์เพี้ยน
# (เคยเจอ "·" กลายเป็นรูปโทรศัพท์ — กติกาเดียวกับที่ห้ามสัญลักษณ์ทั้งหมดในสรุป)
_ORDER_JOIN = " แล้ว "


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
    out_lines = []
    for row in _group_into_lines(_thai_chars(chars)):
        line = _compose_thai_line(row)
        if line:
            out_lines.append(line)
    return '\n'.join(out_lines)


# ความสูงสูงสุดที่ถือว่าอักขระยังอยู่ "บรรทัดเดียวกัน" — ระยะบรรทัดจริงของเล่มห่างกัน
# 13-18 pt ส่วนสองคอลัมน์ในแถวเดียวกันเยื้องกันไม่ถึง 2 pt ค่านี้จึงแยกสองอย่างได้ชัด
_LINE_TOLERANCE = 3.0


def _group_into_lines(chars):
    """จัดอักขระเป็นบรรทัดด้วย "ระยะห่างจริง" ไม่ใช่ช่องตายตัว

    ของเดิมใช้ round(top / 3.0) เป็นกุญแจ ซึ่งมีขอบช่องตายตัว อักขระที่ห่างกันแค่
    0.5 pt จึงตกคนละช่องได้ถ้าบังเอิญคร่อมขอบพอดี — หน้าลงนามเล่มจริง (ก.ย. 2569)
    ชื่อสองคอลัมน์อยู่แถวเดียวกัน (top 283.4 กับ 283.9) แต่ถูกแยกเป็นสองบรรทัด

        First Name Last name,          <- ช่องชื่อนักศึกษา (คอลัมน์ซ้าย)
        Chayanan Sittibusaya,          <- ประธานกรรมการ (คอลัมน์ขวา)
        Candidate MD.                  <- ป้ายสองคอลัมน์ รวมกันถูกต้อง

    บรรทัดเหนือคำว่า Candidate จึงกลายเป็นชื่อของคอลัมน์ขวา ระบบเลยรายงานว่า
    "ชื่อนักศึกษาในเล่มเขียนว่า Chayanan Sittibusaya" ทั้งที่ช่องนั้นเป็น placeholder
    ที่ยังไม่ได้กรอก ("First Name Last name")

    เทียบระยะกับ "ตัวแรกของบรรทัด" ไม่ใช่ตัวก่อนหน้า เพื่อไม่ให้บรรทัดยาวไหลไปเรื่อย ๆ
    ทีละ 3 pt จนกลืนบรรทัดถัดไป
    """
    lines, start = [], None
    for c in sorted(chars, key=lambda c: (float(c['top']), float(c['x0']))):
        top = float(c['top'])
        if start is None or top - start > _LINE_TOLERANCE:
            lines.append([])
            start = top
        lines[-1].append(c)
    return lines


_CID_GLYPH = re.compile(r'\(cid:\d+\)')


def font_damage_score(page, text=None):
    """หน้านี้มีร่องรอยว่า "ฟอนต์ในไฟล์บอกอักขระผิด" กี่ตัว — 0 = ปกติ

    สองสัญญาณที่ฟันธงได้ ไม่ใช่การเดา

    1. อักขระกว้างศูนย์ที่ไม่ใช่สระ/วรรณยุกต์ — ฟอนต์ map วรรณยุกต์ไปเป็นตัวอักษรอื่น
       (ดูเหตุผลเต็มใน _thai_chars ซึ่งทิ้งอักขระพวกนี้ก่อนประกอบข้อความอยู่แล้ว)
    2. (cid:N) — pdfminer หาคำแปลของ glyph นั้นไม่ได้เลย

    วัดกับเล่มจริง 6 เล่ม: เล่มที่ถูกต้อง 4 เล่มได้ 0 ทุกหน้า ไม่มี false positive เลย
    ส่วนเล่มที่ฟอนต์เพี้ยนจับได้ทั้งสองเล่ม (เล่มหนึ่ง 38 หน้า อีกเล่ม 9 หน้า) ตรงกับ
    หน้าที่ตรวจด้วยตาแล้วพบว่าเพี้ยนจริง
    """
    bad = 0
    for c in (getattr(page, 'chars', None) or []):
        t = c.get('text') or ''
        if not t or t == ' ' or _TH_MARKS.match(t):
            continue
        if float(c.get('x1', 0)) - float(c.get('x0', 0)) < 0.5:
            bad += 1
    return bad + len(_CID_GLYPH.findall(text or ''))


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
        text = c.get('text') or ''
        zero_width = (float(c.get('x1', 0)) - float(c.get('x0', 0))) < 0.5
        if zero_width and text == ' ':
            out.append({**c, 'text': 'ํ'})     # NIKHAHIT
            continue
        # กว้างศูนย์แต่ไม่ใช่สระ/วรรณยุกต์ = ฟอนต์ map วรรณยุกต์ผิดเป็นตัวอักษรอื่น
        # เล่มจริงเล่มหนึ่งได้ "พรีซีซั่น" ออกมาเป็น "พรีซีซั8น" และ "ที่ปรึกษา" เป็น
        # "ที8ปรึกษา" (่ กลายเป็น 8, 4, K แล้วแต่ฟอนต์ย่อย) เจ้าหน้าที่อ่านแล้วนึกว่า
        # เล่มพิมพ์ผิด ทั้งที่เล่มถูก — ทิ้งไปเพราะเดาไม่ได้ว่าเป็นวรรณยุกต์ตัวไหน
        # และ norm() ตัดวรรณยุกต์ทิ้งก่อนเทียบอยู่แล้ว ผลตัดสินจึงถูกต้องกว่าเดิมด้วย
        if zero_width and text and not _TH_MARKS.match(text):
            continue
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
# ข้อความตัวอย่างของ template ที่ต้องถมขาวก่อนส่งเล่ม (ห้ามลบ กรอบตารางต้องคงตาม
# template) — ถ้ายังดึงข้อความได้แปลว่ายังอยู่ในไฟล์ แต่ระบบอ่านข้อความที่ถมขาวไว้ได้ด้วย
# จึงยืนยันเองไม่ได้ว่ามองเห็นจริง
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
            f'{label}บนหน้านี้พิมพ์ว่า "{seen}" ' + " และ ".join(reasons),
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

    หน้าที่พิมพ์หัวข้อ "สารบัญ (ต่อ)" / "TABLE OF CONTENTS (Cont.)" ไว้ ให้นับเป็นหน้า
    สารบัญทันที ไม่ต้องผ่านเกณฑ์จำนวนบรรทัด — หน้าต่อหน้าสุดท้ายมักเหลือแค่ 1-2 รายการ
    (ภาคผนวกกับประวัติผู้วิจัย) ซึ่งไม่ถึงเกณฑ์ 3 บรรทัด แล้วถูกตัดทิ้งทั้งหน้า
    วัดกับเล่มจริง: ย้ายท้ายสารบัญ 2 บรรทัดไปหน้าถัดไป ระบบฟ้องผิดว่า
    "ไม่พบหัวข้อ ประวัติผู้วิจัย ในสารบัญ" ทั้งที่หน้านั้นเขียน TABLE OF CONTENTS (Cont.) ไว้
    """
    out = [toc_start]
    for idx in range(toc_start + 1, min(hard_stop, toc_start + limit, len(pages))):
        lines = [ln.strip() for ln in pages[idx].split('\n') if ln.strip()]
        if any(is_toc_heading(ln) for ln in lines[:3]):
            out.append(idx)
            continue
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
    where = " และ ".join(lbl for lbl, _num in count_wrong)
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
        detail = " และ ".join(f"{lbl} ระบุ {num}" for lbl, num in count_wrong)
        found = (f"ระบุจำนวนหน้าไม่ตรงกัน: {detail} "
                 f"ส่วนเลขหน้าสุดท้ายที่ระบบอ่านได้คือ {last_arabic}")
    return zone, where, found


def committee_name_for_case(text):
    """ชื่อคนล้วน ๆ สำหรับตรวจตัวพิมพ์ — ตัดคุณวุฒิท้ายชื่อและตำแหน่งวิชาการออก

    ระบบอ่านชื่อจากตารางลายเซ็นมาทั้งช่อง คุณวุฒิที่พิมพ์ต่อท้ายจึงติดมาด้วย
    กฎนี้เคยถูกถอดออกเพราะเหตุนี้ (ก.ย. 2569) — เล่มจริงได้ข้อส้มซ้ำทั้งสองหน้า

        "Weerawat Limroonreungrat, PT"     ติด ", PT" แล้วถูกตัดสินว่าไม่ใช่ Capital Case
        "Assist.Prof. Hoon Kim, ATC"       ติด ", ATC" เหมือนกัน

    ทั้งสองชื่อถูกต้องอยู่แล้ว ตัดคุณวุฒิออกก่อนแล้วผ่านทั้งคู่ เจ้าหน้าที่จึงสั่งให้เอา
    กฎกลับมาพร้อมการตัดคุณวุฒิ (ก.ย. 2569)
    """
    return _strip_committee_title((text or "").split(",")[0]).strip()


def _report_committee_name_case(rep, members, loc):
    """ชื่อกรรมการบนหน้าลงนามต้องไม่เป็นตัวพิมพ์ใหญ่ทั้งหมด

    หน้าลงนามใช้ตัวพิมพ์แบบชื่อคน ("Mathuros Tipayamongkholgul") ต่างจากหน้าบทคัดย่อ
    ที่ต้องเป็น UPPERCASE ทั้งหมด — นักศึกษาที่คัดรายชื่อจากหน้าบทคัดย่อมาวางจะได้
    ตัวพิมพ์ใหญ่ทั้งบรรทัด ซึ่งเป็นอาการที่กฎนี้จับ

    ตรวจเฉพาะชื่อภาษาอังกฤษ — ภาษาไทยไม่มีตัวพิมพ์ใหญ่-เล็ก

    **สีส้ม ไม่ใช่แดง** เพราะระบบอ่านชื่อจากตารางลายเซ็น ยังอ่านคร่อมช่องได้
    (เหตุผลเดียวกับที่กฎนี้เคยถูกถอดออกทั้งกฎ) ให้เจ้าหน้าที่เปิดหน้านั้นยืนยัน
    """
    bad = []
    for key in sorted(members):
        core = committee_name_for_case(members.get(key))
        if core and re.search(r'[A-Za-z]', core) and not _is_title_case(core):
            bad.append(core)
    if not bad:
        return
    shown = ", ".join(f'"{n}"' for n in bad)
    rep.add("ORANGE", "front_matter", loc,
            f"ชื่อกรรมการบนหน้านี้ไม่ใช่ตัวพิมพ์ใหญ่ต้นคำ (Capital Case): {shown}",
            "ชื่อกรรมการบนหน้าลงนามต้องเป็นตัวพิมพ์ใหญ่ต้นคำ (Capital Case) "
            "ไม่ใช่ตัวพิมพ์ใหญ่ทั้งหมดแบบหน้าบทคัดย่อ",
            "แก้ชื่อกรรมการบนหน้านี้เป็นตัวพิมพ์ใหญ่ต้นคำ แล้วให้เจ้าหน้าที่ยืนยัน",
            "FRONT.COMMITTEE")


def _committee_names(expected):
    """รายชื่อจากข้อมูลอนุมัติ — รับได้ทั้ง list ของ dict {'name': ...} และ list ของ str"""
    return [m.get("name", "") if isinstance(m, dict) else (m or "") for m in expected]


# เอกสารต้นทางของรายชื่อแต่ละชุด (ชื่อแบบฟอร์มของบัณฑิตวิทยาลัย) ตามที่เจ้าหน้าที่
# กำหนด ส.ค. 2569 — รายงานภาษาอังกฤษเรียกฟอร์มเดียวกันว่า GR.1 / GR.2
#   หน้าลงนาม 1 (คณะกรรมการที่ปรึกษา) = บฑ.1
#   หน้าลงนาม 2 (คณะกรรมการสอบ)       = บฑ.2
#   หน้าบทคัดย่อ (คณะกรรมการที่ปรึกษา) = บฑ.1
COMMITTEE_SOURCE_FORM = {"advisory": "บฑ.1", "exam": "บฑ.2"}


# ชุดรายชื่อที่ต้องปรากฏบนหน้าลงนามแต่ละหน้า เรียงตามลำดับหน้า
# เจ้าหน้าที่กำหนด ส.ค. 2569: "บฑ.1 หน้าลงนาม 1 และบทคัดย่อ · บฑ.2 หน้าลงนาม 2"
SIGNATURE_PAGE_COMMITTEE = ("advisory", "exam")


def signature_page_committee(sig_pages, page_index):
    """ชุดรายชื่อที่หน้านี้ต้องเทียบด้วย — ยึด "ลำดับหน้า" ไม่ใช่หัวข้อบนหน้า

    หน้าลงนามหน้าแรกคือคณะกรรมการที่ปรึกษา (บฑ.1) หน้าที่สองคือคณะกรรมการสอบ (บฑ.2)
    ตายตัวตาม template ไม่ต้องอ่านหัวข้อบนหน้าเพื่อเดา

    ยึดลำดับดีกว่ายึดหัวข้อสองเหตุผล
      1. ลำดับเชื่อได้เสมอ ส่วนหัวข้ออ่านพลาดได้ (ฟอนต์เพี้ยน/หน้าสแกน)
      2. เล่มที่สลับสองหน้ากันคือเล่มที่ผิด การยึดหัวข้อจะ "ตามน้ำ" ไปเทียบให้ถูกชุด
         แล้วความผิดนั้นหายไปจากรายงาน

    คืน '' ถ้าหน้านี้ไม่ได้อยู่ในสองหน้าแรก
    """
    try:
        return SIGNATURE_PAGE_COMMITTEE[list(sig_pages).index(page_index)]
    except (ValueError, IndexError):
        return ""


def committee_source_form(kind):
    """ชื่อฟอร์มต้นทางที่ใช้เทียบรายชื่อชุดนี้ — ชนิดหน้าที่อ่านไม่ออกถือเป็น บฑ.1"""
    return COMMITTEE_SOURCE_FORM.get(kind) or "บฑ.1"


# ช่องในตารางลายเซ็นที่ "ไม่มีตัวอักษรเลย" ไม่ใช่ชื่อคน (เศษเส้น/ตัวเลข/สัญลักษณ์)
_NAME_HAS_LETTER = re.compile(r'[A-Za-zก-๙]')


def committee_name_list(members):
    """เฉพาะ "ชื่ออาจารย์" ในตารางลายเซ็น เรียงตามลำดับช่อง

    เจ้าหน้าที่สั่ง (ส.ค. 2569) ว่าให้นับเฉพาะชื่ออาจารย์ ช่องคงที่ของ template
    (คณบดี / ประธานหลักสูตร / ข้อความตัวอย่าง) ถูกคัดออกตั้งแต่ _sig_clean_name แล้ว
    เหลือกันเศษที่ไม่มีตัวอักษรเลยออกอีกชั้น เพื่อไม่ให้ถูกนับเป็นคนหนึ่งคน
    """
    return [members[k] for k in sorted(members)
            if members.get(k) and _NAME_HAS_LETTER.search(members[k])]


def committee_read_is_trustworthy(names):
    """รายชื่อที่อ่านมาหน้าตาน่าเชื่อพอจะเอาไปเทียบหรือไม่

    ใช้ตัดสินว่า "นับจำนวนได้ไหม" — ตารางลายเซ็นเป็นตาราง 2 คอลัมน์คั่นด้วยเส้นประ
    ซึ่งอ่านพลาดได้หลายแบบและเคยพลาดมาแล้วทุกแบบในเล่มจริง:
      - เส้นแบ่งคอลัมน์คลาดไป 0.13 pt  ชื่อขาดครึ่ง ("มยุรี หอมสนิท" -> "หอมสนิท")
      - ฟอนต์เว้นช่องกลางคำ            ชื่อถูกตัดเป็นหลายคำ ("วันเพ็ญ แก้ว ปาน")
      - วรรณยุกต์หลุดเป็นคำของตัวเอง    ("สุภาภรณ ์ สงค์ประชา")
      - ข้อความชั้นเก่าที่ถมขาวไว้        ชื่อซ้ำหรือไปโผล่ผิดช่อง
      - อ่านเส้นประไม่ครบ               แถวคณบดีเลื่อนขึ้นมาเป็นกรรมการ

    ทุกแบบให้ผลเป็น "ชื่อที่ไม่ใช่ชื่อคน" — ท่อนเดียวโดด ๆ หรือเศษข้อความ และทำให้
    **จำนวนที่นับได้ผิดไปด้วย** (ชื่อแตกครึ่งกลายเป็นสองคน) ถ้าฟ้องดื้อ ๆ จะกลายเป็น
    บอกว่าเล่มที่ถูกอยู่แล้วมีรายชื่อไม่ครบ จึงต้องเช็คก่อนว่าทุกช่องหน้าตาเป็น
    "ชื่อ นามสกุล" จริง
    """
    return bool(names) and all(
        _looks_like_person_name(_strip_committee_title(n) or n) for n in names)


# เหตุผลที่เขียนในรายการสีม่วง ตามว่าระบบนับจำนวนให้ได้หรือไม่
_COMMITTEE_NOTE_LEAD = {
    "counted": "ระบบนับจำนวนอาจารย์เทียบกับ {form} ให้แล้ว แต่ไม่ได้เทียบชื่อ "
               "โปรดทานรายชื่อเอง",
    "unclear": "ระบบอ่านรายชื่อบนหน้านี้ได้ไม่ชัด จึงนับจำนวนเทียบกับ {form} ไม่ได้ "
               "โปรดทานรายชื่อเอง และดูรายชื่อที่ระบบอ่านได้ในข้อมูลประกอบ",
}


def _note_committee_reference(rep, expected, loc, rule_id="FRONT.COMMITTEE",
                              form="บฑ.1", status="unclear"):
    """รายชื่ออาจารย์ตามข้อมูลต้นทาง = รายการให้เจ้าหน้าที่ทานเอง (สีม่วง)

    ระบบไม่เทียบชื่อให้ (ดู _report_committee_count) รายการนี้จึงเป็นที่เดียวที่
    เจ้าหน้าที่จะเห็นรายชื่อตามฟอร์ม เหตุผลต้องตรงกับสิ่งที่ระบบทำได้จริง เจ้าหน้าที่
    จะได้รู้ว่าเมื่อไรที่แม้แต่ "จำนวน" ก็เชื่อไม่ได้ (committee_read_is_trustworthy)
    """
    names = "  ".join(f'{k}. {_display_committee_name(n)}'
                      for k, n in enumerate(_committee_names(expected), start=1))
    lead = _COMMITTEE_NOTE_LEAD.get(status, _COMMITTEE_NOTE_LEAD["unclear"])
    rep.add_human(loc, f"{lead.format(form=form)} รายชื่อตาม {form} คือ {names}", rule_id)


def _report_committee_count(rep, expected, found_names, loc, form="บฑ.1",
                            rule_id="FRONT.COMMITTEE"):
    """จำนวนรายชื่อบนหน้าต้องเท่ากับที่ได้รับอนุมัติในฟอร์มต้นทาง (บฑ.1 / บฑ.2)

    เจ้าหน้าที่สั่ง (ส.ค. 2569): *"ทั้งไทยและอังกฤษ ไม่ต้องเทียบชื่อ สมมุติว่าใน บฑ.1
    หรือ บฑ.2 เป็น 3 ชื่อ ในเล่มมี 2 หรือ 4 ชื่อ ... แจ้งว่ารายชื่อไม่ครบตามที่ได้รับ
    อนุมัติใน บฑ.1 หรือ 2 แล้วแต่หน้า และก็บอกว่าควรมีชื่ออะไรบ้าง"*

    **ไม่เทียบชื่อเลย** เพราะชื่อในเล่มกับในฟอร์มต่างกันได้โดยเล่มไม่ผิด — เล่มหลักสูตร
    นานาชาติพิมพ์ชื่ออังกฤษขณะที่ฟอร์มเก็บชื่อไทย และคนไทยสะกดชื่อตัวเองตามพาสปอร์ต
    ไม่ได้ตามหลักถอดเสียง (วัดจากคู่ชื่อจริง 9 คนในเล่มทดสอบ: ถอดเสียงตามหลัก
    ราชบัณฑิตฯ แล้วตรงกับที่เล่มพิมพ์ 0 คน — "จันทราทิตย์" ถอดได้ "Chanthrathit"
    แต่เจ้าตัวสะกด "Chantratita")

    "ควรมีชื่ออะไรบ้าง" อยู่ในบรรทัดที่ควรเป็น ดึงจากฟอร์มต้นทางโดยตรง

    **จำนวนไม่ตรง = แดงทั้งสองทาง** (เจ้าหน้าที่สั่ง ก.ย. 2569 — เดิมขาดเป็นส้ม)

    ทั้งขาดและเกินต้องแจ้งสีแดง และต้องอ้างถึง บฑ.1 / บฑ.2 ให้ชัดว่าเทียบกับอะไร
    ข้อความจึงยกรายชื่อตามฟอร์มมาไว้ในบรรทัด "ควรเป็น" ทุกครั้ง

    **ข้อควรระวังที่ยอมรับแล้ว:** จำนวนที่นับได้ขึ้นกับว่าระบบอ่านตารางลายเซ็นออกครบไหม
    ถ้าอ่านไม่ครบจะได้ชื่อ *น้อยกว่า* ความจริง แล้วฟ้องแดงทั้งที่เล่มถูก — เจ้าหน้าที่
    รับความเสี่ยงนี้แล้ว จึงต้องเปิดหน้านั้นดูก่อนส่งกลับให้นักศึกษา

    เล่มจริงที่เจอ (ก.ย. 2569) หน้าลงนาม 2 พิมพ์กรรมการซ้ำสองคน คนละสองครั้ง
    (Tran Kiem Hao กับ Karl Peltzer บรรทัดคุณวุฒิเขียน "Ph.D." กับ "PhD" ต่างกัน)
    จึงได้ 7 ชื่อจากที่อนุมัติไว้ 5 — ของเดิมเป็นส้มพร้อมคำแนะนำว่า "ถ้าครบแล้วแปลว่า
    ระบบอ่านบางช่องไม่ออก ให้ผ่านได้" ซึ่งชี้ผิดทางสำหรับกรณีเกิน
    """
    want = _committee_names(expected)
    if not want or len(found_names) == len(want):
        return
    listed = "  ".join(f'{k}. {_display_committee_name(n)}'
                       for k, n in enumerate(want, start=1))
    short = len(found_names) < len(want)
    lead = (f"รายชื่อไม่ครบตามที่ได้รับอนุมัติใน {form}" if short else
            f"รายชื่อเกินจากที่ได้รับอนุมัติใน {form}")
    rep.add("RED", "front_matter", loc,
            f"{lead}: หน้านี้มี {len(found_names)} ชื่อ แต่อนุมัติไว้ {len(want)} ชื่อ",
            f"ต้องมีรายชื่อครบตาม {form} คือ {listed}",
            (f"เพิ่มรายชื่อที่ขาดให้ครบตาม {form}" if short else
             f"ลบรายชื่อที่เกินออก ให้เหลือเฉพาะรายชื่อตาม {form}"),
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


# เลขหน้าที่หน้าลงนามแต่ละหน้าต้องพิมพ์ไว้ ตามลำดับหน้า (อังกฤษ, ไทย)
SIGNATURE_PAGE_LABELS = (("i", "ก"), ("ii", "ข"))


def signature_page_position(sig_pages, page_index):
    """ชื่อตำแหน่งของหน้าลงนาม — ใช้คำเดียวกันทุกกฎ

    เจ้าหน้าที่กำหนด ส.ค. 2569: หัวกลุ่มในสรุปเป็น "หน้าลงนาม" ส่วนตำแหน่งของแต่ละข้อ
    เป็น "หน้าลงนาม 1" / "หน้าลงนาม 2"

    เดิมกฎแต่ละตัวเรียกหน้าเดียวกันคนละแบบ — บ้างตามบทบาทของหน้า
    ("หน้าอาจารย์ที่ปรึกษา" / "หน้ากรรมการสอบ") บ้างตามลำดับ ("หน้าลงนามหน้า 1")
    บ้าง "หน้าลงนาม 1" เจ้าหน้าที่อ่านรายงานแล้วนึกว่าเป็นคนละหน้ากัน ทั้งที่หน้าเดียวกัน

    ยึด "ลำดับที่เจอในไฟล์" ไม่ใช่บทบาทของหน้า เพราะลำดับเชื่อได้เสมอ ส่วนบทบาท
    อาจแยกไม่ออกเมื่อเลขหน้าผิดและหัวข้ออ่านไม่ได้ (ดู signature_page_kind)
    """
    try:
        return f"หน้าลงนาม {list(sig_pages).index(page_index) + 1}"
    except ValueError:
        return "หน้าลงนาม"


def _report_signature_page_labels(rep, sig_pages, pages, page_ref, zone=None):
    """หน้าลงนามหน้าแรกต้องเป็นหน้า i (ไทย: ก) หน้าที่สองต้องเป็น ii (ไทย: ข)

    เลขหน้าสองหน้านี้ไม่ใช่แค่การเรียงเลข — เป็นตัวบอกว่าหน้าไหนเป็นของคณะกรรมการ
    ที่ปรึกษา หน้าไหนเป็นของคณะกรรมการสอบ (ดู signature_page_kind) ซึ่งกำหนดว่า
    หน้านั้นจะถูกเทียบกับ บฑ.1 หรือ บฑ.2 ถ้าเลขหน้าผิด การตรวจทั้งหน้าเพี้ยนตาม

    **สีส้ม ไม่ใช่แดง** ตามที่เจ้าหน้าที่กำหนด ส.ค. 2569: เลขหน้าลงนามที่ผิดจะพ่วงไป
    ทำให้เลขหน้าส่วนนำหน้าอื่นผิดตามไปด้วย (เล่มจริงพิมพ์ "ค" ทั้งสองหน้า กฎ
    "เลขหน้าไม่ต่อเนื่อง" จึงฟ้องซ้ำอีกข้อจากต้นเหตุเดียวกัน) ถ้าฟ้องแดงทั้งคู่
    เจ้าหน้าที่จะเห็นสองข้อจากความผิดเดียว จึงให้ข้อนี้เป็นส้มแล้วบอกผลพ่วงไว้ด้วย

    ตรวจทั้งหน้า ไม่ใช่แค่บรรทัดแรก/ท้าย — เลขหน้าของหน้าลงนามอาจไม่ได้อยู่บรรทัดแรก
    เสมอ (เช่น มีหัวเรื่อง "วิทยานิพนธ์" นำหน้า) เทียบเฉพาะบรรทัดที่เป็นเลขหน้าล้วน
    จึงไม่ชนกับข้อความในเนื้อหน้า
    """
    zone = zone or SIG_LABEL_ZONE
    for k, idx in enumerate(sig_pages[:2]):
        lab_en, lab_th = SIGNATURE_PAGE_LABELS[k]
        page_lines = [l.strip() for l in pages[idx].split('\n') if l.strip()]
        if any(t.lower() == lab_en or norm(t) == norm(lab_th) for t in page_lines):
            continue
        # ตำแหน่งบอกเลขที่พิมพ์ไว้แล้ว ("หน้าลงนามหน้า 1 (หน้า ค)") ถ้าบรรทัด
        # "สิ่งที่พบ" พูดซ้ำอีกรอบ เจ้าหน้าที่จะเห็นคำเดียวกันสองครั้งในข้อเดียว
        # (เจ้าหน้าที่สั่ง ส.ค. 2569: ตำแหน่งกับสิ่งที่พบต้องไม่พูดเรื่องเดียวกันซ้ำ)
        # ส่วนหน้าที่ไม่มีเลขหน้าเลย ตำแหน่งบอกแค่แผ่นที่ของไฟล์ จึงไม่ซ้ำอยู่แล้ว
        found_lab = _extract_page_label(pages[idx])
        what = ("เลขหน้าของหน้านี้ไม่ถูกต้อง" if found_lab
                else "ไม่พบเลขหน้าบนหน้า")
        rep.add(zone, "front_matter",
                f"{signature_page_position(sig_pages, idx)} ({page_ref(idx)})",
                what,
                f'ต้องเป็นเลขหน้า "{lab_th}" (ไทย) หรือ "{lab_en}" (อังกฤษ)',
                "แก้เลขหน้านี้ก่อน แล้วไล่เลขหน้าส่วนนำที่เหลือใหม่ทั้งชุด "
                "เพราะเลขหน้าหน้านี้ผิดทำให้หน้าถัดไปผิดตามไปด้วย",
                "PAGE.SIGNATURE_LABEL")


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

    เลื่อนหน้าต่างทีละ "ตัวอักษรที่คนมองเห็น" ไม่ใช่ทีละคำ เพราะภาษาไทยไม่เว้นวรรค
    ระหว่างคำ และไม่ใช่ทีละ code point เพราะขอบหน้าต่างจะตัดกลางพยางค์ ได้ข้อความ
    ขึ้นต้นด้วยวรรณยุกต์ลอย ๆ อย่าง "้เป็นส่วนหนึ่งของ..." ซึ่งอ่านไม่ออก
    """
    cells = _graphemes(soft(text))
    n = len(_graphemes(soft(want)))
    if not cells or n < 4:
        return ''
    best, best_ratio = '', min_ratio - 1e-9
    for size in range(max(4, n - 2), n + 3):
        for i in range(0, len(cells) - size + 1):
            run = ''.join(cells[i:i + size])
            ratio = difflib.SequenceMatcher(None, norm(run), norm(want)).ratio()
            if ratio > best_ratio:
                best, best_ratio = run, ratio
    return best


def signature_template_zone(page_text, degree):
    """ส่วนของหน้าลงนามที่ต้องเป็น "ข้อความ template" ล้วน — ตัดชื่อปริญญาออกแล้ว

    template ทางการวางหน้าลงนามไว้แบบนี้ (Electronic File template-2026)

        entitled / เรื่อง
        <ชื่อเรื่อง>
        <ชื่อนักศึกษา>
        was submitted to the Faculty of Graduate Studies, Mahidol University
        for the degree of  <Degree (Field of Study)>
        on <วันที่>

    ภาษาไทยก็โครงเดียวกัน "...ตามหลักสูตรปริญญา <ชื่อปริญญา>"

    **ชื่อปริญญาเป็นช่องเติมช่องเดียวที่อยู่กลางประโยค ที่เหลือเป็นข้อความตายตัวทั้งหมด**
    ถ้าไม่ตัดชื่อปริญญาออกก่อน ตัวหาช่วงที่ใกล้เคียงจะคร่อมชื่อปริญญาเข้ามาเป็นส่วนหนึ่ง
    ของประโยค แล้วรายงานยกชื่อปริญญามาอ้างว่าเป็นข้อความ template ทำให้สองประเด็น
    ปนกัน (เจ้าหน้าที่สั่ง ส.ค. 2569: "อ่าน template สิ และแยกชื่อปริญญาออกมา
    นอกนั้นก็เป็นข้อความ template")

    ตัดที่ "จุดเริ่มของชื่อปริญญาตามที่พิมพ์จริงในเล่ม" ไม่ใช่ตามข้อมูลอนุมัติตรง ๆ
    เพราะเล่มอาจสะกดชื่อปริญญาต่างไปเล็กน้อย ซึ่งเป็นคนละข้อฟ้องกัน
    """
    text = soft(page_text or "")
    if not (degree or "").strip():
        return text
    printed = _closest_run(text, degree, min_ratio=0.7)
    if not printed:
        return text
    cut = text.find(printed)
    return text[:cut] if cut > 0 else text


def _institution_mismatch(rep, loc, label, want, bottom_text, box, rule_id):
    """ฟ้องช่องสถาบันที่ข้อความไม่ตรง — บอกด้วยว่าเล่มเขียนว่าอะไรและต่างตรงไหน"""
    near = _closest_run(bottom_text, want)
    if near:
        diff = describe_diff(near, want)
        found_msg = f'{box} เขียนว่า "{near}"'
        if diff:
            found_msg += f' {diff}'
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
    """ช่องกรรมการที่ไม่ได้ใช้ต้อง "เปลี่ยนสีตัวอักษรเป็นสีขาว" ไม่ใช่ลบทิ้ง

    เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้แจ้งแบบนี้ — ของเดิมบอกให้ "ลบข้อความตัวอย่างออกจาก
    ไฟล์" ซึ่งสวนทางกับคู่มือของบัณฑิตวิทยาลัยเอง ที่กำหนดว่ากรอบของ template ต้องคงไว้
    ตามที่ set มา (ใส่รายชื่อได้ฝั่งละ 6 รายชื่อ) แล้วช่องที่ไม่มีชื่อให้ถมด้วยสีขาว
    ถ้อยคำชุดเดียวกันอยู่ใน STAFF_CHECKS หัวข้อ "โครงสร้างหน้าลงนาม" อยู่แล้ว
    นักศึกษาที่ทำตามคำว่า "ลบ" จะได้กรอบตารางผิดจาก template แล้วโดนตีกลับอีกรอบ

    ข้อความที่ตรวจทั้งสี่แบบเป็นแถวชื่อกับแถวคุณวุฒิของช่องกรรมการที่ไม่ได้ใช้ทั้งหมด
    (ดู _SIG_LEFTOVER_PLACEHOLDERS) จึงใช้ถ้อยคำเดียวกันได้ทุกแบบ — เล่มไทยเจอ
    "ตำแหน่งทางวิชาการและชื่อ นามสกุล" / "คุณวุฒิ (ระบุสาขาวิชา)" ส่วนเล่มอังกฤษเจอ
    "Academic rank First Name Last name" / "Degree (Subject)" วิธีแก้อันเดียวกัน

    ถ้อยคำจบแค่ "เปลี่ยนสีตัวอักษรเป็นสีขาว" ตามที่เจ้าหน้าที่สั่ง (ก.ย. 2569) — เคยต่อท้าย
    ว่า "ไม่ใช่ลบบรรทัดออก" แล้วถูกสั่งให้ตัดออก บรรทัดนี้อยู่ในข้อความที่คัดลอกส่งนักศึกษา
    ยิ่งสั้นยิ่งอ่านจบ ส่วนเหตุผลเรื่องกรอบ template อยู่ในถ้อยคำของหัวข้อโครงสร้างหน้าลงนาม

    ยังเป็นส้มเพราะข้อความที่ไม่ใช่สีขาวอาจถูกกล่องทึบทับไว้อีกชั้น ระบบยืนยันเองไม่ได้
    """
    if not found:
        return
    rep.add("ORANGE", "front_matter", loc,
            "พบข้อความตัวอย่างของ template ค้างอยู่ในตารางลายเซ็น: "
            + ", ".join(f'"{label}"' for label in found),
            "ช่องกรรมการที่ไม่ได้ใช้ต้องเปลี่ยนสีตัวอักษรเป็นสีขาว",
            "ตรวจว่าข้อความนี้มองเห็นบนหน้ากระดาษหรือไม่ "
            "ถ้าเห็นให้เปลี่ยนสีตัวอักษรของช่องที่ไม่ได้ใช้เป็นสีขาว",
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
    detail = "บทคัดย่อไม่ครบถ้วน ขาดบทคัดย่อ" + "และ".join(missing)
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

    ตรวจ **จำนวน** รายชื่อเทียบกับฟอร์มต้นทางของหน้านั้น (บฑ.1 / บฑ.2) เท่านั้น
    ไม่เทียบชื่อทั้งเล่มไทยและเล่มอังกฤษ (ดู _report_committee_count) รายชื่อตามฟอร์ม
    พิมพ์ไว้ในรายการสีม่วงให้เจ้าหน้าที่ทานเอง

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
        # ชุดรายชื่อ (และฟอร์มต้นทาง) มาจากลำดับหน้า ไม่ใช่หัวข้อบนหน้า
        kind = signature_page_committee(sig_pages, idx)
        expected = committees.get(kind, []) if kind else []
        # หัวข้อบนหน้ายังอ่านไว้ เพื่อเตือนเมื่อ "ขัดกับลำดับหน้า" (เช่น สลับสองหน้ากัน)
        # ต้องอ่านจาก "หัวข้อบนหน้า" ล้วน ๆ ไม่ใช่ signature_page_kind ซึ่งดูเลขหน้าก่อน
        # (เลขหน้ามักถูกอยู่แล้ว จึงกลบความขัดแย้งของหัวข้อจนตรวจไม่เจอ)
        heading_kind = _committee_page_kind(pages[idx])
        members, member_quals, bottom_text, member_raw = slots[idx]
        # เรียกตามลำดับหน้าเสมอ ไม่เรียกตามบทบาท ("หน้าอาจารย์ที่ปรึกษา") เพราะ
        # กฎอื่นบนหน้าเดียวกันเรียกว่า "หน้าลงนาม 1" อยู่แล้ว ถ้าเรียกคนละแบบ
        # เจ้าหน้าที่จะนึกว่าเป็นคนละหน้า — บทบาทของหน้าอยู่ในข้อความอยู่แล้ว
        # (ข้อฟ้องบอกว่าเทียบกับ บฑ.1 หรือ บฑ.2 ซึ่งบอกบทบาทในตัว)
        page_label = signature_page_position(sig_pages, idx)
        loc = f"{page_label} ({page_ref(idx)})"
        # ตัวพิมพ์ของชื่อกรรมการ — เจ้าหน้าที่สั่งให้เอากลับมา (ก.ย. 2569) พร้อมกับ
        # ตัดคุณวุฒิท้ายชื่อออกก่อน ซึ่งเป็นสาเหตุที่กฎนี้เคยถูกถอดออกทั้งกฎ
        _report_committee_name_case(rep, members, loc)
        # บอกเจ้าหน้าที่ว่าระบบเอา "อะไร" ไปเทียบ — เวลาระบบอ่านหน้าเพี้ยนจะเห็นทันที
        # ว่าเพี้ยนตรงไหน แทนที่จะเห็นแต่ผลตัดสินแล้วเดาไม่ออกว่าทำไมถึงฟ้อง
        read_names = committee_name_list(members)
        rep.add_info("front_matter", f"รายชื่อที่ระบบอ่านได้จาก{page_label}",
                     "  ".join(f'{k}. {n}' for k, n in enumerate(read_names, start=1))
                     or "ระบบอ่านรายชื่อบนหน้านี้ไม่ได้")
        # หัวข้อบนหน้าขัดกับลำดับหน้า = อาจสลับสองหน้ากัน ระบบยังเทียบตามลำดับ
        # (ตามกติกา) แต่ต้องบอกออกไป ไม่งั้นข้อฟ้อง "รายชื่อไม่ครบ" ที่ตามมาจะดู
        # เหมือนเล่มขาดคน ทั้งที่ของจริงคือสลับหน้ากัน ซึ่งแก้คนละอย่างกัน
        #
        # ลงเป็น "รายการสีม่วง" ไม่ใช่ข้อฟ้องสีส้ม ตามที่เจ้าหน้าที่กำหนด ส.ค. 2569
        # ("เอาเป็นสีม่วงไหม เพราะแบบนั้นต้องตรวจตาอยู่แล้ว") — และเพราะสีส้มจะหลุด
        # เข้าใบสั่งแก้ที่ส่งให้นักศึกษาโดยปริยาย
        if kind and heading_kind and heading_kind != kind:
            rep.add_human(loc,
                          "หัวข้อบนหน้านี้ไม่ตรงกับลำดับหน้า อาจสลับหน้ากัน "
                          "ระบบเทียบตามลำดับหน้าไว้ก่อน โปรดตรวจว่าหน้าลงนาม 1 "
                          "เป็นคณะกรรมการที่ปรึกษา และหน้าลงนาม 2 เป็นคณะกรรมการสอบ",
                          "UNCERTAIN.REVIEW")
        if not expected:
            continue
        handled_any = True
        _report_sig_placeholders(rep, leftover.get(idx) or [], loc)

        # นับจำนวนเทียบกับฟอร์มต้นทางของหน้านั้น (บฑ.1 / บฑ.2) ไม่เทียบชื่อ
        # นับได้ก็ต่อเมื่ออ่านตารางออกเป็นชื่อคนจริงทุกช่อง ไม่งั้นจำนวนก็เชื่อไม่ได้
        # (ตารางลายเซ็นอ่านพลาดได้หลายแบบ ดู committee_read_is_trustworthy)
        form = committee_source_form(kind)
        countable = committee_read_is_trustworthy(read_names)
        if countable:
            _report_committee_count(rep, expected, read_names, loc, form)
        _note_committee_reference(rep, expected, loc, form=form,
                                  status="counted" if countable else "unclear")

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
            f"{page_label} ", f" ({page_ref(idx)})")

    return handled_any


_ABS_COMMITTEE_HEADING = re.compile(
    r'(?:ADVISORY\s+COMMITTEE|คณะกรรมการที่ปรึกษา\S*)\s*:', re.I)


# รหัสนักศึกษาตามรูปแบบของบัณฑิตวิทยาลัย: เลข 7 หลัก + รหัสหลักสูตร + / + ระดับ
# เช่น "6526627 NSMY/M" — ยอมให้ช่องว่างเพี้ยนได้ เพราะการดึงข้อความจาก PDF
# แทรก/ตัดช่องว่างรอบเครื่องหมายทับได้
_STUDENT_ID_SHAPE = re.compile(
    r'\d{6,9}\s*[A-Z]{2,8}\s*/\s*[A-Z0-9]{1,3}', re.I)


def student_id_line(page_text):
    """บรรทัดที่พิมพ์รหัสนักศึกษาไว้ — คืน '' ถ้าหน้านี้ไม่มี

    template กำหนดให้ชื่อกับรหัสอยู่บรรทัดเดียวกัน ("ชื่อ นามสกุล  รหัส  รหัสหลักสูตร/ระดับ")
    จึงใช้บรรทัดนี้เป็นหน่วยเดียวในการตรวจ ไม่ใช่ค้นชื่อกับรหัสแยกกันทั้งหน้า
    """
    for line in (page_text or "").splitlines():
        if _STUDENT_ID_SHAPE.search(line):
            return soft(line)
    return ""


def abstract_printed_name(page_text):
    """ชื่อนักศึกษา "ตามที่พิมพ์จริง" บนหน้าบทคัดย่อ — คืน "" ถ้าหน้านี้ไม่มีบรรทัดนั้น

    template วางชื่อกับรหัสไว้บรรทัดเดียวกัน ("FIRSTNAME LASTNAME 6200000 XXXX / X")
    ช่องของชื่อจึงคือ "ข้อความที่อยู่หน้ารหัส" ซึ่งชี้ตำแหน่งได้จากโครงสร้างของหน้า
    ไม่ใช่ "บรรทัดไหนก็ได้ทั้งหน้าที่คล้ายชื่อที่สุด"

    เล่มจริง (ก.ย. 2569) ที่ลืมแก้ placeholder จึงพิมพ์ว่า
    "FIRSTNAME LASTNAME 6636201 PHEH/M" เคยถูกรายงานว่า "ชื่อภาษาอังกฤษในเล่ม
    เขียนว่า ABSTRACT" เพราะการวัดความคล้ายทีละบรรทัดให้หัวข้อ "ABSTRACT" 0.385
    แต่ให้บรรทัดชื่อจริง 0.213 (ชื่อที่ถูกคือ "NUTRADA WICHANUCHIT") เจ้าหน้าที่จึง
    ได้คำสั่งให้ไปแก้ข้อความที่ไม่ได้อยู่ในช่องนั้นเลย
    """
    line = student_id_line(page_text)
    m = _STUDENT_ID_SHAPE.search(line) if line else None
    return soft(line[:m.start()]) if m else ""


def _check_student_line_pairs_name_with_id(rep, page_text, core_name, student_id,
                                           loc, name_label):
    """ชื่อกับรหัสต้องอยู่บรรทัดเดียวกันตาม template

    เรียกเฉพาะตอนที่ "ชื่อสะกดถูกแล้ว" และ "รหัสถูกแล้ว" — ที่เหลือคือเรื่องการจัดวาง
    เท่านั้น จึงเป็นสีส้มให้เจ้าหน้าที่ตัดสิน ไม่ฟันธงแดง (การขึ้นบรรทัดใหม่อาจเกิดจาก
    การห่อคำของ PDF ได้ ระบบแยกไม่ออกจากการพิมพ์แยกบรรทัดจริง)
    """
    if not core_name or not student_id:
        return
    line = student_id_line(page_text)
    # ไม่มีบรรทัดรหัสเลย = กฎรหัสนักศึกษาฟ้องไปแล้ว ไม่ต้องฟ้องซ้ำ
    if not line or norm(student_id) not in norm(line):
        return
    if norm(core_name) in norm(line):
        return
    rep.add("ORANGE", "front_matter", loc,
            f'บรรทัดที่พิมพ์รหัสนักศึกษาไม่มี{name_label}อยู่ด้วย คือ "{line}"',
            f'{name_label}กับรหัสนักศึกษาต้องอยู่บรรทัดเดียวกัน คือ "{core_name} {student_id}"',
            "", "FORM.APPROVED_MATCH")


# อักษรไทย (ไม่รวมเลขไทยกับวรรณยุกต์) — ใช้แยก "ช่องรหัสที่ฟอนต์ทำเพี้ยน" ออกจาก
# "นามสกุลไทยที่ค้างอยู่ตรงนั้นเพราะเล่มลืมพิมพ์รหัส"
_THAI_LETTER = re.compile('[ก-ฮ]')


def is_page_count_line(line):
    """บรรทัด "97 pages" / "106 หน้า" ที่ปิดท้ายบทคัดย่อ

    ใช้เป็นจุดหยุดของรายการ keyword — สำรวจเล่มจริง 5 เล่ม (ทั้งไทยและอังกฤษ)
    พบว่ารายการ keyword จบด้วยบรรทัดนี้เสมอ ไม่มีเล่มไหนต่างออกไป
    """
    return bool(re.search(r'(\d{1,4})\s*PAGES?', line or "", re.I)
                or re.search(r'(\d{1,4})(หนา)', norm(line)))


def unreadable_id_digits(page_text, student_id, names=()):
    """ข้อความที่ยืนอยู่ตรงตำแหน่งตัวเลขรหัสนักศึกษา แต่ไม่ใช่ตัวเลข (คืน "" ถ้าปกติ)

    เจอกับเล่มจริง: หน้าบทคัดย่อไทยฝังฟอนต์ย่อย (subset) ที่ตาราง ToUnicode ผิด
    เลข "6437028" จึงถูกดึงออกมาเป็น "JKLMNOP" — ตัวอักษรอังกฤษไล่เรียงตามลำดับ
    glyph ส่วนหน้าบทคัดย่ออังกฤษของเล่มเดียวกันใช้อีกฟอนต์ อ่านได้ "6437028 PHPH/M"
    ถูกต้อง เล่มไม่ได้พิมพ์ผิด ระบบอ่านไม่ออกเอง

    อีกเล่มหนึ่ง (ก.ย. 2569) ฟอนต์เดียวกันแต่เพี้ยนคนละแบบ เลข "6636480" ออกมาเป็น
    ",,-,./0" คือกลายเป็น **เครื่องหมายวรรคตอน** ไม่ใช่ตัวอักษร และรหัสหลักสูตร
    "PHIE/M" ถูกแทรกช่องว่างเป็น "PHIE / M" จนกลายเป็นสามคำ ของเดิมจับได้เฉพาะแบบ
    ตัวอักษรอังกฤษล้วนและรหัสหลักสูตรที่เป็นคำเดียว เล่มนี้จึงหลุด แล้วโดนฟ้องแดงว่า
    "ไม่พบรหัสนักศึกษาบนหน้านี้" ทั้งที่พิมพ์อยู่ครบ

    ตัวชี้ขาดคือ "รหัสหลักสูตรอ่านได้ แต่ตัวเลขที่ต้องอยู่ข้างหน้ามันไม่ใช่ตัวเลข"
    ไม่ใช่การนับสถิติตัวอักษรทั้งหน้า เพราะกฎนี้ใช้ยกเลิกการฟ้อง จึงต้องแคบไว้ก่อน

    แยกจาก "เล่มลืมพิมพ์รหัส" ด้วยข้อมูลระบบสามอย่าง: ต้องยาวเท่าจำนวนหลักของรหัส
    · ต้องไม่มีอักษรไทยปน (เล่มที่ลืมรหัสบนหน้าไทยจะเหลือนามสกุลไทยติดอยู่ตรงนั้น)
    · และต้องไม่ใช่ท่อนหนึ่งของชื่อนักศึกษา (หน้าอังกฤษจะเหลือนามสกุลอังกฤษ)
    """
    student_id = soft(student_id)
    digits = re.sub(r'\D', '', student_id)
    tail = soft(re.sub(r'^\s*\d+', '', student_id))
    if not digits or not tail:
        return ""
    if digits in re.sub(r'\D', '', page_text or ""):
        return ""                       # อ่านเลขได้ ไม่ใช่ปัญหาฟอนต์
    want_tail = norm(tail)
    known = [norm(n) for n in names if soft(n or "")]
    for line in (page_text or "").splitlines():
        tokens = soft(line).split()
        for k in range(1, len(tokens)):
            # รหัสหลักสูตรอาจถูกแทรกช่องว่างจนแตกเป็นหลายคำ ("PHIE / M") จึงต่อคำ
            # ไปข้างหน้าทีละคำแล้วเทียบด้วย norm() ซึ่งตัดช่องว่างกับ "/" ทิ้งอยู่แล้ว
            if not any(norm("".join(tokens[k:k + span])) == want_tail
                       for span in range(1, min(4, len(tokens) - k + 1))):
                continue
            slot = tokens[k - 1]
            if len(slot) != len(digits) or _THAI_LETTER.search(slot):
                continue
            if any(norm(slot) in name for name in known if name):
                continue
            return slot
    return ""


def _closest_student_id(page_text, expected):
    """รหัสนักศึกษาที่ "พิมพ์อยู่จริง" บนหน้านี้ — คืน '' ถ้าหน้านี้ไม่มีรหัสเลย

    ใช้แยก "เล่มพิมพ์รหัสผิดตัวเลข" ออกจาก "เล่มไม่มีรหัส" ซึ่งวิธีแก้คนละอย่าง
    และทำให้รายงานบอกได้ว่าเล่มพิมพ์ว่าอะไร แทนที่จะบอกลอย ๆ ว่า "ไม่พบรหัสนักศึกษา"
    """
    found = [soft(m.group(0)) for m in _STUDENT_ID_SHAPE.finditer(page_text or "")]
    if not found:
        return ""
    target = norm(expected)
    return max(found,
               key=lambda s: difflib.SequenceMatcher(None, target, norm(s)).ratio())


def _committee_entry_complete(line):
    """บรรทัดนี้จบ "ชื่อ, คุณวุฒิ" ของคนล่าสุดครบแล้วหรือยัง

    ใช้ตัดสินว่าบรรทัดถัดไปเป็น "ชื่อที่ห่อคำมา" หรือเป็นเนื้อความที่ไม่เกี่ยวแล้ว
    โดยดูจากตัวข้อความเอง ไม่ใช่นับบรรทัด — ท่อนสุดท้ายเป็นคุณวุฒิ = ครบ,
    เป็นชื่อคน = ยังค้าง (คุณวุฒิของคนนั้นถูกห่อไปบรรทัดถัดไป)
    """
    tail = soft(line).rstrip()
    if tail.endswith(","):
        return False
    return _is_degree_only(tail.split(",")[-1].strip())


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
    lines = page_text[m.end():].split("\n")
    # รายชื่อกรรมการห่อคำกี่บรรทัดก็ได้ ขึ้นกับความยาวชื่อของแต่ละเล่ม จึงห้ามนับบรรทัด
    # ตัดสิน — ต้องดูว่า "ข้อความในบรรทัดนั้นคืออะไร" แล้วหยุดเมื่อมันไม่ใช่รายชื่อแล้ว
    # (ของเดิมหาหัวข้อ "บทคัดย่อ" ไม่เจอเมื่อไร ก็เดาเอาดื้อ ๆ ว่าเป็น 4 บรรทัดถัดไป
    #  เล่มจริงที่วรรณยุกต์หายตอนดึงข้อความจึงลากย่อหน้าแรกของบทคัดย่อเข้ามาทั้งย่อหน้า
    #  แล้วกฎรูปแบบไปทำงานกับเนื้อความบทคัดย่อ ฟ้องผิดสองข้อบนเล่มที่พิมพ์ถูกต้อง)
    block_lines = [lines[0]]
    for line in lines[1:]:
        if _is_abstract_heading(line):
            break
        # ยังอยู่ในรายชื่อได้สองแบบ: บรรทัดนี้มีคุณวุฒิอยู่ในตัว หรือบรรทัดก่อนหน้า
        # ยังค้างอยู่กลางรายการ (ยังไม่จบด้วยคุณวุฒิ) แปลว่าบรรทัดนี้คือส่วนที่ห่อคำมา
        continues = (_DEGREE_ABBR_TOKEN.search(soft(line))
                     or not _committee_entry_complete(block_lines[-1]))
        if not continues:
            break
        block_lines.append(line)
    return is_english, re.sub(r'\s*\n\s*', ' ', "\n".join(block_lines)).strip()


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


# คุณวุฒิที่ห้อยท้ายก้อนเดียวกับชื่อ (ลืมใส่จุลภาคคั่น) เช่น "SOMCHAI JAIDEE D. Eng."
# ยอมให้มีช่องว่างระหว่างท่อนได้ เพราะเล่มจริงพิมพ์ "D. Eng." แยกช่องว่าง
_ABS_DEGREE_TAIL = re.compile(
    r'\s+(?:'
    r'(?=[A-Za-z]*\.)[A-Za-z]{1,4}(?:\.\s*[A-Za-z]{1,4})*\.?'
    r'|[ก-๙]{1,4}\.(?:\s*[ก-๙]{1,4}\.)*'
    r')\s*(?:\([^)]*\))?\s*$')


def _drop_separator_dot(name):
    """ตัด "จุด" ที่เล่มใช้แทนจุลภาคท้ายชื่อออก (เช่น "ADISORN LEELASANTITHAM.")

    ตัดเฉพาะเมื่อคำสุดท้ายไม่ใช่อักษรย่อ (ยาวเกิน 2 ตัว) เพราะชื่อที่ลงท้ายด้วย
    อักษรย่อจริง ๆ จุดนั้นเป็นส่วนหนึ่งของชื่อ ไม่ใช่ตัวคั่นที่พิมพ์ผิด
    """
    text = (name or "").strip()
    if not text.endswith("."):
        return text
    last = text[:-1].split()[-1] if text[:-1].split() else ""
    return text[:-1].strip() if len(last) > 2 else text


def _scan_abstract_committee(block):
    """ไล่อ่านก้อนรายชื่อกรรมการทีละช่อง — yield (kind, text, missing_comma)

    kind = 'name' | 'degree'
    missing_comma บอกว่าท่อนนี้ติดมากับท่อนก่อนหน้าโดยไม่มีจุลภาคคั่น
      kind='name'   -> True ถ้าชื่อติดมากับคุณวุฒิของคนก่อนหน้า
      kind='degree' -> ข้อความก้อนเต็ม "ชื่อ+คุณวุฒิ" ตามที่พิมพ์จริง ถ้าคุณวุฒิติดมา
                       กับชื่อของตัวเอง (ต้องคืนก้อนเต็มเพราะรายงานต้องบอกว่าเป็นของใคร
                       และเล่มคั่นด้วยอะไรอยู่ — เล่มจริงใช้จุดแทนจุลภาค)
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
            # ลืมจุลภาคระหว่าง "ชื่อ" กับ "คุณวุฒิของตัวเอง" เช่น
            # "WATCHARAPONG CHOOKAEW D. Eng." — ถ้าไม่แยก คุณวุฒิจะถูกนับเป็นส่วน
            # หนึ่งของชื่อ แล้วกฎตัวพิมพ์ใหญ่ฟ้องผิดว่า "ชื่อไม่ได้เป็นตัวพิมพ์ใหญ่"
            # ทั้งที่ชื่อพิมพ์ใหญ่ครบ ของจริงคือขาดจุลภาค ซึ่งแก้คนละอย่างกัน
            tail = _ABS_DEGREE_TAIL.search(tok)
            if tail and _looks_like_person_name(tok[:tail.start()].strip()):
                nm = tok[:tail.start()].strip()
                dg = tok[tail.start():].strip()
                yield "name", nm, False
                yield "degree", dg, (tok, f"{_drop_separator_dot(nm)}, {dg}")
                seen_name = True
                expect_name = True
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


def abstract_committee_missing_degree_commas(block):
    """คืน [(ที่พิมพ์จริง, ที่ควรเป็น), ...] ของคนที่ชื่อกับคุณวุฒิไม่ได้คั่นด้วยจุลภาค

    คืนทั้งก้อน (เช่น "ADISORN LEELASANTITHAM. Ph.D.") ไม่ใช่เฉพาะคุณวุฒิ เพราะ
    เจ้าหน้าที่ต้องรู้ว่าเป็นของใคร และต้องเห็นว่าเล่มคั่นด้วยอะไรอยู่ (เล่มจริงใช้จุด)
    พร้อมข้อความที่แก้แล้วของคนนั้นจริง ๆ ("ADISORN LEELASANTITHAM, Ph.D.")
    ไม่ใช่รูปแบบกลาง ๆ นักศึกษาจะได้ก๊อปไปแก้ได้เลย
    """
    return [missing for kind, _text, missing in _scan_abstract_committee(block)
            if kind == "degree" and missing]


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
            abs_label = "บทคัดย่ออังกฤษ" if heading_en else "บทคัดย่อไทย"
            loc = f"{abs_label} ({page_ref(ai)}) รายชื่อคณะกรรมการที่ปรึกษา"

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
            # เจ้าหน้าที่สั่ง ส.ค. 2569 ว่าเรื่องจุลภาคให้แจ้งเป็นสีเหลือง — ยังบอกให้
            # ครบว่าขาดตรงไหนและต้องเป็นอะไร แต่ไม่ทำให้เล่มไม่ผ่าน
            for nm in abstract_committee_missing_commas(block):
                rep.add(ABSTRACT_COMMA_ZONE, "front_matter", loc,
                        f'ไม่มีจุลภาคคั่นหน้าชื่อ "{nm}"',
                        "ต้องคั่นด้วยจุลภาคทุกช่อง คือ 'ชื่อ นามสกุล, คุณวุฒิ, ชื่อ นามสกุล, คุณวุฒิ'",
                        f'เติมจุลภาคหน้าชื่อ "{nm}"', "FRONT.ABSTRACT_COMMA")
            for printed, correct in abstract_committee_missing_degree_commas(block):
                rep.add(ABSTRACT_COMMA_ZONE, "front_matter", loc,
                        f'ชื่อกรรมการกับคุณวุฒิไม่ได้คั่นด้วยจุลภาค คือ "{printed}"',
                        f'ต้องเป็น "{correct}"', "", "FRONT.ABSTRACT_COMMA")
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
            # ตรวจตัวพิมพ์จาก "ชื่อล้วน" หลังตัดตำแหน่งวิชาการออกแล้ว เพราะตำแหน่ง
            # วิชาการมีตัวพิมพ์เล็กเป็นปกติ ("Asst. Prof.") และถูกฟ้องเป็นข้อของตัวเอง
            # ไปแล้วข้างบน ถ้าไม่ตัดออกจะได้สองข้อจากความผิดเดียว แถมข้อหลังยังบอกผิด
            # ว่า "ชื่อไม่เป็นตัวพิมพ์ใหญ่" ทั้งที่ชื่อพิมพ์ใหญ่ครบ
            lower = [nm for nm in (_strip_committee_title(n.strip()) for n in names)
                     if heading_en and re.search(r'[a-z]', nm)]
            if lower:
                shown = ", ".join(f'"{nm}"' for nm in lower)
                rep.add("RED", "front_matter", loc,
                        f'ชื่อกรรมการไม่ได้เป็นตัวพิมพ์ใหญ่ทั้งหมด: {shown}',
                        "ชื่อกรรมการในบทคัดย่อภาษาอังกฤษต้องเป็นตัวพิมพ์ใหญ่ทั้งหมด",
                        "แก้ชื่อกรรมการเป็นตัวพิมพ์ใหญ่ทั้งหมด", "FRONT.ABSTRACT")

            # รายชื่อบนหน้าบทคัดย่อคือคณะกรรมการที่ปรึกษา จึงเทียบกับ บฑ.1 เสมอ
            # หน้าบทคัดย่ออังกฤษพิมพ์ชื่ออังกฤษ เทียบตัวอักษรกับต้นทางที่เป็นไทยไม่ได้
            if not advisory:
                continue
            read_names = [n.strip() for n in names]
            form = committee_source_form("advisory")
            countable = committee_read_is_trustworthy(read_names)
            if countable:
                _report_committee_count(rep, advisory, read_names, loc, form,
                                        "FRONT.ABSTRACT")
            _note_committee_reference(rep, advisory, loc, "FRONT.ABSTRACT",
                                      form=form,
                                      status="counted" if countable else "unclear")


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
                              expected_style=None, page_texts=None):
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
                f"มีเลขหน้าเป็น{found_names} {len(off_style)} หน้า: {shown}{more}",
                want_sentence, f"แก้เลขหน้าให้เป็น{want}", "PAGE.NUMBERING")

    seq = [e for e in entries if e[2] == main_style]
    if len(seq) > 1:
        # หน้าที่คั่นอยู่แต่อ่านเลขไม่ได้/ใช้ชนิดผิด ยังนับเป็นหน้าของเล่ม
        # เดิมเจอหน้าพวกนี้แล้ว "ข้ามไปเลย" (continue) การตรวจความต่อเนื่องจึงเงียบ
        # ทั้งช่วง เล่มที่เลขหน้าผิดจริงเลยรอดไปได้ — ตอนนี้นับจำนวนหน้าที่คั่นแทน
        # จึงยังฟันธงได้ว่าต่อเนื่องหรือไม่ โดยไม่ต้องเดาว่าหน้าที่อ่านไม่ออกพิมพ์เลขอะไร
        problems, dup_run = [], 1
        for k in range(1, len(seq)):
            prev_i, prev_lab, _ps, prev_v = seq[k - 1]
            cur_i, cur_lab, _cs, cur_v = seq[k]
            gap = cur_i - prev_i - 1
            if cur_v != prev_v:
                dup_run = 1
                if cur_v != prev_v + gap + 1:
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
            rep.add(PAGE_SEQUENCE_ZONE, "front_matter", "ส่วนนำ",
                    "เลขหน้าไม่ต่อเนื่อง: " + " และ ".join(problems[:5]) + more,
                    f"เลขหน้าต้องเรียงต่อเนื่องทีละหน้า ไม่ซ้ำ ไม่ข้าม (ที่พบ: {observed})",
                    "", "PAGE.NUMBERING_SEQUENCE")

    if unread:
        # แยกสองแบบ เพราะวิธีแก้คนละอย่าง
        #   หน้าที่ดึงข้อความได้ แต่ไม่มีบรรทัดเลขหน้า = เล่มไม่ได้ใส่เลขหน้ามาจริง
        #     ฟันธงได้ และบอกได้ว่าต้องเพิ่มเลขหน้า
        #   หน้าที่ดึงข้อความไม่ได้เลย = หน้าภาพ/สแกน ระบบไม่มีทางรู้ว่าพิมพ์เลขไว้ไหม
        #     จึงยังส่งให้เจ้าหน้าที่ดู ไม่ฟันธง
        # ทั้งสองแบบต้องบอก "แผ่นที่เท่าไรของไฟล์" เจ้าหน้าที่จะได้เปิดไปดูหน้านั้นได้
        # ไม่ใช่รู้แค่ว่า "มีหน้าที่อ่านไม่ออกอยู่ที่ไหนสักแห่ง"
        # ในรายการนี้ไม่ใช้ page_ref เพราะทุกหน้าในกลุ่ม "ไม่มีเลขหน้า" อยู่แล้ว
        # การขึ้นต้นทุกตัวด้วยคำว่า "หน้าไม่ระบุเลข" จึงเป็นการพูดซ้ำประโยคหลัก
        def _has_text(idx):
            if page_texts is None or idx >= len(page_texts):
                return False
            return bool((page_texts[idx] or "").strip())

        no_number = [i for i in unread if _has_text(i)]
        unreadable = [i for i in unread if i not in no_number]
        for group, zone, detail, rule_id, fix in (
            (no_number, "RED", "ไม่ได้พิมพ์เลขหน้าไว้", "PAGE.NUMBERING",
             "เพิ่มเลขหน้าให้ครบทุกหน้า"),
            (unreadable, UNCERTAIN_ZONE, "ระบบอ่านเลขหน้าไม่ได้ (อาจเป็นหน้าภาพ/สแกน)",
             "UNCERTAIN.REVIEW", "ตรวจด้วยตาว่าหน้าเหล่านี้มีเลขหน้าถูกต้องและต่อเนื่อง"),
        ):
            if not group:
                continue
            sheets = [str(i + 1) for i in group[:5]]
            shown = " และ ".join([", ".join(sheets[:-1]), sheets[-1]]) if len(sheets) > 1 \
                else sheets[0]
            more = f" และอีก {len(group) - 5} หน้า" if len(group) > 5 else ""
            rep.add(zone, "front_matter", "ส่วนนำ",
                    f"{len(group)} หน้า{detail} คือแผ่นที่ {shown} ของไฟล์{more}",
                    f"ทุกหน้าต้องมีเลขหน้าเป็น{want}", fix, rule_id)


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


# "สิ่งที่ควรอยู่บนหน้าปก" — ให้คะแนนจากเนื้อความทั้งหน้า ไม่ผูกกับบรรทัดใดบรรทัดหนึ่ง
#
# จับข้อความเดียวแบบเป๊ะ ๆ ไม่พอ: พิมพ์ผิดตัวเดียวในบรรทัดลิขสิทธิ์ (ซึ่งเป็นความผิด
# ที่ระบบมีไว้จับพอดี) ก็ทำให้หาหน้าปกไม่เจอทั้งหน้า วัดกับเล่มจริงแล้วยืนยัน
# ให้นับ "สิ่งที่ควรมี" หลายอย่างแทน พิมพ์ผิดหนึ่งจุดจึงเหลืออีกสามอย่างให้ยึด
# เก็บทั้งคำไทยและคำอังกฤษ เพื่อให้หาเจอโดยไม่ต้องรู้ภาษาของหลักสูตรก่อน
_COVER_MARKERS = (
    ("ลิขสิทธิ์", ("COPYRIGHT", norm("ลิขสิทธิ์"))),
    ("บัณฑิตวิทยาลัย", (norm("FACULTY OF GRADUATE STUDIES"), norm("บัณฑิตวิทยาลัย"))),
    ("มหาวิทยาลัยมหิดล", (norm("MAHIDOL UNIVERSITY"), norm("มหาวิทยาลัยมหิดล"))),
    ("ข้อความประเภทงาน", (norm("SUBMITTED IN PARTIAL FULFILLMENT"),
                          norm("เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร"))),
)

# "สิ่งที่หน้าปกต้องไม่มี" — ชื่อบัณฑิตวิทยาลัยกับชื่อมหาวิทยาลัยไปโผล่บนหน้าลงนาม
# และหน้าบทคัดย่อด้วย (2 จาก 4 ข้อ) คำกลุ่มนี้จึงเป็นตัวแยกสองหน้านั้นออกจากหน้าปก
# วัดกับเล่มจริงสามเล่ม: หน้าปกไม่มีคำกลุ่มนี้เลยสักเล่ม ส่วนหน้าลงนาม/บทคัดย่อมีทุกหน้า
_NOT_COVER_MARKERS = tuple(norm(w) for w in (
    "ENTITLED", "ABSTRACT", "บทคัดย่อ", "คณะกรรมการ",
    "ADVISORY COMMITTEE", "EXAMINATION COMMITTEE", "อาจารย์ที่ปรึกษา", "คณบดี",
))


def cover_page_score(text):
    """คะแนนความเป็นหน้าปกของหน้าหนึ่ง = สิ่งที่ควรมี ลบ สิ่งที่ต้องไม่มี

    ใช้หักลบแทนการตัดทิ้ง เพราะชื่อเรื่องบางเล่มอาจมีคำอย่าง ABSTRACT อยู่จริง
    ถ้าตัดทิ้งทันทีจะหาหน้าปกของเล่มนั้นไม่เจอเลย
    """
    nt = norm(text)
    want = sum(1 for _label, alts in _COVER_MARKERS if any(a in nt for a in alts))
    avoid = sum(1 for w in _NOT_COVER_MARKERS if w in nt)
    return want - avoid


# ---------- "เล่มนี้ทำเป็นภาษาอะไร" ----------
# เล่มไทยกับเล่มอังกฤษใช้ template คนละชุด "ข้อความตายตัวของ template" จึงเป็นตัวบอก
# ภาษาของเล่มที่ตรงที่สุด ไม่ต้องเดาจากสัดส่วนตัวอักษรบนหน้า — เล่มไทยที่มีศัพท์อังกฤษ
# เยอะจะหลอกวิธีนับสัดส่วนได้ (ปัญหาเดียวกับที่ title_script เจอ)
#
# ไม่ใช้ชนิดเลขหน้าส่วนนำ (ก ข ค / i ii iii) เป็นสัญญาณ เพราะ PAGE.NUMBERING ตรวจ
# เรื่องนั้นอยู่แล้ว ถ้าเอามาปนกันจะกลายเป็นกฎเดียวที่ตัดสินสองเรื่องพร้อมกัน
BOOK_LANGUAGE_PARTS = {
    "cover": "หน้าปก",
    "signature": "หน้าลงนาม",
    "chapter": "หัวบทในเนื้อหา",
}
LANGUAGE_NAME = {"thai": "ภาษาไทย", "en": "ภาษาอังกฤษ"}
# บอกให้ชัดว่าที่เหลือ "ไม่ได้ตรวจ" ไม่ใช่ "ตรวจแล้วผ่าน" — แต่ไม่นับเป็นจุดผิด
# เพราะเจ้าหน้าที่สั่งว่ากรณีนี้ต้องมีจุดผิดข้อเดียว
BOOK_LANGUAGE_STOPPED = ("ส่วนอื่นทั้งหมดของเล่ม "
                         "(ระบบหยุดตรวจเมื่อภาษาของเล่มไม่ตรงกับที่ได้รับอนุมัติ)")


def _join_and(names):
    """ต่อรายการด้วยคำว่า "และ" ไม่ใช้สัญลักษณ์ ตามกติกาข้อความสรุป"""
    names = [n for n in names if n]
    if len(names) <= 1:
        return names[0] if names else ""
    return " และ ".join([", ".join(names[:-1]), names[-1]])


def _cover_language_markers(doc_type, language):
    return [norm(text) for _label, text in cover_required_items(doc_type, language) if text]


def _one_sided(thai_hits, en_hits, need=1):
    """ภาษาที่สัญญาณหนึ่งตัวชี้ — ต้องเจอข้างเดียวเท่านั้น เจอทั้งสองข้างถือว่าบอกไม่ได้"""
    if thai_hits >= need and en_hits == 0:
        return "thai"
    if en_hits >= need and thai_hits == 0:
        return "en"
    return ""


def book_language_signals(pages, cover_idx=0, doc_type=""):
    """สัญญาณภาษาของเล่มสามตัว แต่ละตัวคืน "thai" / "en" / "" (บอกไม่ได้)

    หน้าปก      ข้อความบังคับตาม template ของแต่ละภาษา (cover_required_items)
    หน้าลงนาม   ประโยค template ที่ตามหลังชื่อเรื่อง (SIGNATURE_TEMPLATE_TH/_EN)
    หัวบท       คำนำหน้าหัวบท "บทที่ N" หรือ "CHAPTER N"
    """
    pages = list(pages or [])
    blank = {"cover": "", "signature": "", "chapter": ""}
    if not pages:
        return blank
    cover_idx = min(max(cover_idx, 0), len(pages) - 1)

    cover = norm(pages[cover_idx])
    cover_lang = _one_sided(
        sum(1 for m in _cover_language_markers(doc_type, "thai") if m in cover),
        sum(1 for m in _cover_language_markers(doc_type, "international") if m in cover),
        need=2,
    )

    # หน้าลงนามอยู่ถัดจากหน้าปกเสมอตามลำดับที่ประกาศกำหนด
    after_cover = norm("\n".join(pages[cover_idx + 1:cover_idx + 4]))
    sig_lang = _one_sided(int(norm(SIGNATURE_TEMPLATE_TH) in after_cover),
                          int(norm(SIGNATURE_TEMPLATE_EN) in after_cover))

    thai_ch = en_ch = 0
    for text in pages:
        for line in top_lines(text, 6):
            if _chapter_match(line) is None:
                continue
            if norm(line).startswith("CHAPTER"):
                en_ch += 1
            else:
                thai_ch += 1
    # "script" ไม่ใช่ส่วนหนึ่งของเล่ม จึงไม่ถูกนับใน wrong_parts (ซึ่งวนเฉพาะสามคีย์แรก)
    # เป็นตัวสำรองที่ book_language หยิบใช้ต่อเมื่อสามคีย์แรกเงียบหมด
    return {"cover": cover_lang, "signature": sig_lang,
            "chapter": _one_sided(thai_ch, en_ch),
            "script": book_language_by_script(pages)}


# เกณฑ์ของตัวสำรอง "สัดส่วนตัวอักษร" — เว้นช่องว่างตรงกลางไว้กว้างมากโดยตั้งใจ
# วัดกับเล่มจริง: เล่มอังกฤษได้ไทย 0.1% กับ 3.7% (เล่ม 3.7% มีบทคัดย่อไทยเต็ม ๆ อยู่ด้วย)
# ส่วนเล่มไทยได้ไทย 96.1% ช่องว่างระหว่าง 3.7% กับ 96.1% กว้างพอให้เล่มที่มีภาคผนวก
# ภาษาไทยยาว ๆ ในเล่มอังกฤษก็ยังไม่หลุดมาถึงเกณฑ์
_SCRIPT_THAI_MIN = 0.60
_SCRIPT_THAI_MAX = 0.10
# ข้อความน้อยกว่านี้เชื่อสัดส่วนไม่ได้ (ไฟล์ที่ดึงข้อความได้แค่หยิบมือ)
_SCRIPT_MIN_LETTERS = 200


def book_language_by_script(pages):
    """ภาษาของเล่มจากสัดส่วนตัวอักษรทั้งไฟล์ — **ตัวสำรองท้ายสุดเท่านั้น**

    ห้ามใช้เป็นสัญญาณหลัก เพราะเล่มไทยที่มีศัพท์อังกฤษเยอะจะหลอกได้ (ปัญหาเดียวกับ
    ที่ title_script เจอ) ใช้ต่อเมื่อข้อความตายตัวของ template เงียบหมดทั้งสามจุด
    ซึ่งแปลว่าเล่มไม่ได้ทำตาม template เลย หรือดึงข้อความได้ไม่ครบ

    ที่ต้องมีตัวสำรอง: การตอบว่า "ระบบอ่านภาษาจากไฟล์ไม่ได้" แทบไม่มีทางเกิดกับไฟล์ที่
    ระบบรับเข้ามาตรวจอยู่แล้ว (ไฟล์ที่ดึงข้อความไม่ได้ถูกปฏิเสธตั้งแต่ตอนอัปโหลด ดู
    main._pdf_readability_issue) ถ้ายังตอบแบบนั้นได้อยู่ เจ้าหน้าที่จะเจอช่องที่ไม่มี
    คำตอบทั้งที่ไฟล์อ่านออก
    """
    text = " ".join(pages or ())
    thai = len(_THAI_LETTER.findall(text))
    latin = len(_LATIN_LETTER.findall(text))
    total = thai + latin
    if total < _SCRIPT_MIN_LETTERS:
        return ""
    ratio = thai / total
    if ratio >= _SCRIPT_THAI_MIN:
        return "thai"
    if ratio <= _SCRIPT_THAI_MAX:
        return "en"
    return ""


def book_language(signals):
    """ภาษาที่เล่มทำมาจริง — "" เมื่อสัญญาณขัดกันเองหรือไม่พอตัดสิน

    ต้องมีสัญญาณชี้ทางเดียวกันอย่างน้อย 2 ตัว และห้ามมีตัวไหนชี้สวนทาง เพราะผลของ
    ฟังก์ชันนี้ใช้ "หยุดตรวจทั้งเล่ม" การฟันธงผิดจึงเสียหายกว่าการไม่ฟันธง
    """
    signals = signals or {}
    votes = [signals.get(key) for key in ("cover", "signature", "chapter")]
    votes = [v for v in votes if v]
    if len(votes) >= 2 and len(set(votes)) == 1:
        return votes[0]
    if votes:
        # มีข้อความ template อ่านได้อยู่บ้าง แต่ยังไม่พอหรือขัดกันเอง — ห้ามให้ตัวสำรอง
        # มาชี้ขาดทับ เพราะสัญญาณ template แม่นกว่าสัดส่วนตัวอักษรมาก
        return ""
    # เงียบหมดทั้งสามจุด (เล่มไม่ทำตาม template หรือดึงข้อความได้ไม่ครบ) จึงค่อยใช้
    # สัดส่วนตัวอักษรเป็นตัวสำรอง ดีกว่าตอบว่าอ่านภาษาไม่ได้ทั้งที่ไฟล์อ่านออก
    return signals.get("script", "")


def book_language_rows(want_language, signals):
    """สองแถวของหัวข้อ "ภาษาที่เขียน" ในตารางผลเทียบข้อมูลอนุมัติรายตำแหน่ง

    คืน [(ตำแหน่ง, สถานะ, รายละเอียด), ...] — เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้เห็น
    **ภาษาของทั้งสองฝั่ง** ไม่ใช่บอกแค่ว่าตรงหรือไม่ตรง จะได้รู้ทันทีว่าต้องไปแก้ที่
    ช่องหลักสูตรบนหน้าอัปโหลด หรือส่งกลับให้นักศึกษาทำเล่มใหม่

    ต้องลงตารางทุกครั้งที่มีข้อมูลให้เทียบ ไม่ใช่เฉพาะตอนผิด ไม่งั้นเวลาเล่มถูกต้อง
    หัวข้อนี้จะหายไปทั้งหัวข้อ แล้วแยกไม่ออกว่า "ตรวจแล้วผ่าน" กับ "ระบบไม่ได้ตรวจ"

    อ่านภาษาจากไฟล์ไม่ได้ = "รอยืนยัน" ไม่ใช่ "ตรง" — ถ้านับเป็นตรง เจ้าหน้าที่จะเชื่อว่า
    ระบบยืนยันให้แล้วทั้งที่ไม่ได้ยืนยัน
    """
    want_name = LANGUAGE_NAME[want_language]
    other_name = LANGUAGE_NAME["en" if want_language == "thai" else "thai"]
    found = book_language(signals)
    wrong_parts = [BOOK_LANGUAGE_PARTS[key]
                   for key in ("cover", "signature", "chapter")
                   if signals[key] and signals[key] != want_language]
    if found == want_language:
        status, in_file = "pass", want_name
    elif found:
        status, in_file = "fail", LANGUAGE_NAME[found]
    elif wrong_parts:
        # สัญญาณขัดกันเอง ตัดสินภาษาทั้งเล่มไม่ได้ แต่รู้แน่ว่าส่วนไหนคนละภาษา
        status, in_file = "fail", f"{other_name} ในส่วน {_join_and(wrong_parts)}"
    else:
        status, in_file = "pending", "ระบบอ่านภาษาจากไฟล์ไม่ได้"
    return [("ข้อมูลอนุมัติ", status, want_name),
            ("ในไฟล์รูปเล่ม", status, in_file)]


def approved_book_language(approved):
    """ภาษาที่เล่มต้องทำ ตามข้อมูลอนุมัติ — "thai" / "en" / "" (ไม่ได้ระบุหลักสูตร)

    thai_english กับ international ทำเล่มเป็นภาษาอังกฤษเหมือนกัน ต่างกันแค่
    thai_english ต้องมีหน้าบทคัดย่อภาษาไทยเพิ่มมาด้วย
    """
    language = soft((approved or {}).get("program_language", "") or "")
    if language == "thai":
        return "thai"
    return "en" if language in ("thai_english", "international") else ""


def find_cover_page(pages, limit=10):
    """แผ่นไหนของไฟล์คือหน้าปก (ปกติต้องเป็นแผ่นที่ 1)

    เดิมโค้ดถือว่า pages[0] คือหน้าปกเสมอ เล่มที่มีใบปะหน้า ใบรับรอง หรือหน้าว่าง
    มาก่อนจึงถูกตรวจผิดจุดทั้งชุด (ไม่พบข้อความบังคับ ไม่พบปี ไม่พบชื่อปริญญา)
    โดยไม่มีข้อไหนบอกสาเหตุจริงว่าหน้าปกไม่ได้อยู่แผ่นแรก

    เสมอกันให้แผ่นแรกสุดชนะ และถ้าไม่มีหน้าไหนดูเป็นหน้าปกพอ คืน 0 เท่าเดิม
    เพราะการเดาหน้าปกผิดอันตรายกว่าการไม่เดา
    """
    best_idx, best_score = 0, 0
    for i, text in enumerate(pages[:limit]):
        score = cover_page_score(text)
        if score > best_score:
            best_idx, best_score = i, score
    return best_idx if best_score >= 2 else 0


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
    # ภาษาของเล่มมาก่อนทุกอย่าง — ถ้าเล่มทำผิดภาษา ข้ออื่นไม่มีความหมาย
    # ไม่มีตำแหน่งไหนเขียนคำนี้ กลุ่มนี้จึงถูกเติมจาก "รหัสกฎ" ใน summary_section
    ("ภาษาของเล่ม", "ภาษาของเล่ม"),
    ("หน้าปก", "หน้าปก"),
    # หน้าลงนามถูกตั้งชื่อตามบทบาทของหน้า ไม่ได้เขียนคำว่า "หน้าลงนาม" ตรง ๆ เสมอ
    # (หน้าอาจารย์ที่ปรึกษา / หน้ากรรมการสอบ / ช่องประธานหลักสูตร / ช่องคณบดีคณะ)
    # ถ้าจับแค่คำว่า "หน้าลงนาม" ข้อของสองหน้านี้จะตกไปอยู่หมวด "อื่น ๆ"
    # ทั้งที่เจ้าหน้าที่ต้องไปแก้ที่หน้าลงนามเหมือนกัน
    ("หน้าลงนาม",
     r"หน้าลงนาม|อาจารย์ที่ปรึกษา|กรรมการสอบ|ประธานหลักสูตร|คณบดี"),
    ("กิตติกรรมประกาศ", "กิตติกรรม"),
    ("บทคัดย่อ", "บทคัดย่อ"),
    ("สารบัญ", "สารบัญ"),
    # ข้อที่พูดถึงส่วนนำทั้งก้อน (เช่น ลำดับเลขหน้า ก ข ค) ไม่ได้เจาะจงหน้าใดหน้าหนึ่ง
    # จึงมาท้ายกลุ่มส่วนนำ หลังหน้าที่เจาะจงได้ทั้งหมด
    ("ส่วนนำ", "ส่วนนำ"),
    ("เนื้อหา (บท)", r"บทที่|เนื้อหา|ทั้งเล่ม|โครงบท"),
    ("ส่วนท้ายเล่ม", r"บรรณานุกรม|อ้างอิง|ภาคผนวก|ประวัติ"),
]
SUMMARY_SECTION_ORDER = [name for name, _ in SUMMARY_SECTIONS] + ["อื่น ๆ"]
SUMMARY_SECTION_INDEX = {name: i for i, name in enumerate(SUMMARY_SECTION_ORDER)}

# ถ้าตำแหน่งไม่ได้เอ่ยชื่อส่วนใดเลย ยังรู้จาก part ได้ว่าอยู่ช่วงไหนของเล่ม
# (เดิมข้อพวกนี้ตกไปกอง "อื่น ๆ" ทั้งที่รู้อยู่แล้วว่าต้องไปแก้ตรงไหน)
_PART_SECTION = {
    "front_matter": "ส่วนนำ",
    "body": "เนื้อหา (บท)",
    "body/end": "เนื้อหา (บท)",
    "end_matter": "ส่วนท้ายเล่ม",
}

# ---------- ลำดับของข้อในรายงาน ----------
# เจ้าหน้าที่ไล่แก้เล่มจากหน้าแรกไปหน้าสุดท้าย ข้อในรายงานจึงต้องเรียงแบบเดียวกัน
# เลขหน้าอยู่ในข้อความตำแหน่งอยู่แล้ว (page_ref เขียนเป็น "(หน้า X)") จึงอ่านกลับออกมา
# แทนที่จะต้องไปเติมพารามิเตอร์เลขหน้าให้ rep.add ทุกจุดเรียกทั่วทั้งไฟล์
_PAGE_BUCKET_FRONT = 1
_PAGE_BUCKET_BODY = 2
_PAGE_TOKEN = r"\d{1,4}|[ivxlcdm]{1,10}|[ก-ฮ]"
# แบบมีวงเล็บมาก่อนเสมอ — เป็นรูปแบบที่ page_ref สร้าง และเป็นหน้าที่ต้องไปแก้จริง
_LOC_PAGE_PAREN = re.compile(rf"\(หน้า\s*({_PAGE_TOKEN})\)", re.I)
# ไม่มีวงเล็บ: ต้องไม่ใช่ "หน้า" ที่เป็นส่วนท้ายของคำอื่น (เช่น "หน้าลงนามหน้า 1")
# มิฉะนั้นเลข 1 ของ "หน้าลงนามหน้า 1" จะถูกอ่านเป็นเลขหน้าเนื้อหา
_LOC_PAGE_PLAIN = re.compile(rf"(?:^|[\s(:])หน้า\s*({_PAGE_TOKEN})(?![ก-๙\w])", re.I)
_LOC_PAGE_RANGE = re.compile(r"ช่วงเลขหน้า\s*(\d{1,4})")


def _issue_page_position(location):
    """(กลุ่มเลขหน้า, ลำดับหน้า) ของข้อ — None ถ้าตำแหน่งไม่ได้อ้างเลขหน้า

    กลุ่มแยกส่วนนำ (ก ข ค / i ii iii) ออกจากเนื้อหา (1 2 3) เพราะเลขซ้ำกันได้
    """
    text = location or ""
    found = (_LOC_PAGE_PAREN.search(text) or _LOC_PAGE_PLAIN.search(text)
             or _LOC_PAGE_RANGE.search(text))
    if not found:
        return None
    style, value = _page_label_order(found.group(1))
    if not style:
        return None
    bucket = _PAGE_BUCKET_BODY if style == "arabic" else _PAGE_BUCKET_FRONT
    return bucket, value


def issue_sort_key(issue):
    """เรียงตามส่วนประกอบของเล่มก่อน แล้วจึงตามเลขหน้าภายในส่วนนั้น

    เรียงด้วยเลขหน้าล้วนไม่ได้ เพราะบางข้ออ้างสองหน้าพร้อมกัน (เช่น ชื่อบทที่ผิด
    ทั้งในสารบัญและในเนื้อหา) ถ้ายึดเลขหน้าอย่างเดียว ข้อของส่วนเดียวกันจะถูกแทรก
    สลับกันจนรวมเป็นกลุ่มไม่ได้ — ยึดส่วนประกอบก่อนจึงได้ทั้ง "ตามลำดับส่วน" และ
    "ตามลำดับหน้า" พร้อมกัน ข้อที่ไม่มีเลขหน้าให้อยู่ท้ายกลุ่มของตัวเอง
    """
    position = _issue_page_position(issue.get("location"))
    section = SUMMARY_SECTION_INDEX.get(summary_section(issue), len(SUMMARY_SECTION_ORDER))
    if position is None:
        return (section, 9, 10 ** 6)
    return (section, position[0], position[1])

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

    เช่น "สารบัญ (หน้า viii) กับบทคัดย่อภาษาไทย" ต้องไปแก้ที่สารบัญ ไม่ใช่บทคัดย่อ
    ถ้าตำแหน่งไม่เอ่ยชื่อส่วนไหนเลย (เช่น "โครงบท", "หน้า 40") ใช้ part แล้วค่อยใช้
    ชนิดเลขหน้าเป็นตัวบอก — เลขอารบิกแปลว่าอยู่ในเนื้อหา
    """
    # ข้อ "เล่มผิดภาษา" พูดถึงทั้งเล่ม ไม่มีชื่อส่วนไหนในตำแหน่งให้จับ ถ้าปล่อยให้เดา
    # จากคำ ตำแหน่ง "ทั้งเล่ม" จะตกกลุ่ม "เนื้อหา (บท)" ซึ่งอยู่กลางรายงาน
    if issue.get("rule_id") == "FORM.BOOK_LANGUAGE":
        return "ภาษาของเล่ม"
    text = issue.get('location', '') or ''
    best, best_at = None, len(text) + 1
    for name, pattern in SUMMARY_SECTIONS:
        found = re.search(pattern, text)
        if found and found.start() < best_at:
            best, best_at = name, found.start()
    if best:
        return best
    by_part = _PART_SECTION.get(issue.get('part') or '')
    if by_part:
        return by_part
    position = _issue_page_position(text)
    return "เนื้อหา (บท)" if position and position[0] == _PAGE_BUCKET_BODY else "อื่น ๆ"


# ---------- สิ่งที่เจ้าหน้าที่ต้องตัดสินเอง แล้วกดเพิ่มถ้อยคำเข้าข้อความสรุป ----------
# บางเรื่องระบบอ่านจากไฟล์ไม่ได้เลย (โครงสร้างหน้าลงนาม) บางเรื่องไม่ได้อยู่ในไฟล์
# ตั้งแต่แรก (ค่าปรับส่งล่าช้า) แต่ถ้อยคำที่ส่งให้นักศึกษาเป็นชุดเดียวกันทุกเล่ม
# เจ้าหน้าที่จึงต้องพิมพ์ย่อหน้าเดิมซ้ำเองทุกครั้ง กติกาปฏิบัติงาน (ก.ย. 2569) กำหนดให้
# มีปุ่มกด แล้วถ้อยคำชุดนั้นเข้าข้อความคัดลอกทันที
#
# หนึ่งหัวข้อมีได้หลายตัวเลือก (choices) กดได้ทีละอัน กดซ้ำที่เดิมคือยกเลิก ตัวเลือกที่
# text ว่างคือ "ไม่ต้องเพิ่มอะไร" (เช่น ปุ่ม "ถูกต้อง")
#
# placement บอกว่าถ้อยคำไปอยู่ตรงไหนของข้อความสรุป
#   section = เป็นจุดที่นักศึกษาต้องแก้ นับรวมใน "กรุณาแก้ไขทั้งหมด N จุด"
#             และไปอยู่ในกลุ่มของส่วนนั้นของเล่ม (ต้องมีชื่อกลุ่มใน SUMMARY_SECTIONS)
#   closing = ข้อความปิดท้าย ไม่ใช่จุดผิด ต่อท้ายสุดและไม่นับเป็นจุดที่ต้องแก้
#
# ถ้อยคำทั้งไทยและอังกฤษเจ้าหน้าที่เขียนมาเอง ระบบห้ามเรียบเรียงใหม่หรือย่อ (หลักการ
# เดียวกับ FORM.BOOK_LANGUAGE) **รวมถึงห้ามแก้คำที่ดูเหมือนพิมพ์ตกด้วย** — เคยแก้ให้
# ("ทำดำเนินจัดทำ" "ทื่กำหนด" "Line Offical" "(Pages i-ii )") แล้วเจ้าหน้าที่สั่งให้คืน
# ต้นฉบับทุกตัวอักษร มีเทสต์ล็อกคำเหล่านี้ไว้ ถ้าจะแก้ต้องได้คำสั่งจากเจ้าหน้าที่ก่อน
# เก็บคู่กันไว้ตรงนี้ที่เดียว หน้ารายงานรับไปทั้งก้อน
# แล้วใช้ "เทียบทั้งบรรทัด" ตอนสลับเป็นอังกฤษ คำแปลจึงหลุดจากต้นฉบับไม่ได้ และไม่ต้อง
# พึ่ง TR ซึ่งเป็นการแทนที่เศษคำ (ถ้อยคำยาวขนาดนี้ผ่าน TR แล้วเพี้ยนแน่)
# ลิงก์แบบสอบถามความพึงพอใจต่อกระบวนการตรวจรูปเล่ม (ระบบของงานบริการการศึกษาเอง)
# ใช้ลิงก์ย่อ ไม่ใช่ URL ของ Apps Script ตรง ๆ เพราะ URL นั้นเปลี่ยนทุกครั้งที่ deploy
# รุ่นใหม่ ข้อความที่ส่งให้นักศึกษาไปแล้วจะพาไปหน้าที่ไม่มีอยู่
SURVEY_URL = "https://bit.ly/4cwqxAd"


# ---------- ถ้อยคำปิดท้ายของเล่มที่ตรวจแล้ว "ผ่าน" ----------
# เล่มที่ผ่านไม่มีอะไรให้แก้ ถ้อยคำชุด "แก้แล้วส่งกลับเข้าระบบ" จึงใช้ไม่ได้ — เคยส่ง
# "หากดำเนินการแก้ไขตามรายละเอียดที่เจ้าหน้าที่แจ้งใน Remarks เสร็จสิ้นแล้ว กรุณาส่งกลับ
# เข้าสู่ระบบอีกครั้ง" ไปหาคนที่ไม่มีจุดต้องแก้สักจุด เจ้าหน้าที่จึงเขียนถ้อยคำอีกชุดไว้
# (ไฟล์ note V.2.txt) แยกตามมีค่าปรับ/ไม่มีค่าปรับ เหมือนกับปุ่มที่มีอยู่แล้ว
#
# ถ้อยคำเป็นของเจ้าหน้าที่ ห้ามเรียบเรียงใหม่เอง แก้ได้เมื่อเจ้าหน้าที่สั่งเท่านั้น แล้ว
# ต้องแก้เทสต์ที่ล็อกคำนั้นไว้พร้อมกัน (หลักการเดียวกับถ้อยคำชุดอื่นในแฟ้มนี้) — ดอกจัน
# คร่อม "*ทั้งสองขั้นตอน...*" เป็นต้นฉบับ ยังคงไว้ มีเทสต์ล็อก
#
# รอบเกลาคำ (ก.ย. 2569) เจ้าหน้าที่อ่านถ้อยคำทั้งสี่ชุดแล้วสั่งให้แก้ห้าจุดที่ต้นฉบับ
# ไม่สม่ำเสมอกันเอง จุดอื่นคงไว้ทุกตัวอักษร
#   "1 .ชำระผ่าน" เว้นวรรคหน้าจุด        ->  "1. ชำระผ่าน" ให้เหมือนข้อ 2 กับ 3
#   ชุดมีค่าปรับอังกฤษเปิดเรื่องซ้ำสองรอบ  ->  ตัด "Your E-Thesis submission has been
#                                            successfully completed in the system."
#                                            ที่พูดซ้ำบรรทัดแรกออก
#   "If you meet all graduation requirements" ->  เติมจุลภาค และ "You" -> "you"
#   "GR.5 (REQUESTING DEGREE)"          ->  "(Requesting Degree)" ให้ตรงกับอีกชุด
#   "Mr.Supawit"                        ->  "Mr. Supawit"
#
# อีกจุดหนึ่ง ฝั่งอังกฤษของชุดมีค่าปรับ "ตก" หมายเหตุ 48 ชั่วโมงที่ฝั่งไทยมี เหลือ
# แต่กรณีบัตรเครดิต 3 ชั่วโมง นักศึกษาต่างชาติจึงไม่รู้กำหนดของกรณีทั่วไป
# เจ้าหน้าที่สั่งให้เติม (ก.ย. 2569) บรรทัดนี้จึงไม่มีในต้นฉบับอังกฤษ แต่แปลตาม
# ฝั่งไทยที่มีอยู่แล้ว
#
# ดอกจันคู่นี้ต่างจากกรณีหน้าลงนาม ซึ่งเจ้าหน้าที่สั่งให้ถอดออกเพราะขึ้นเป็นดาวลอยใน
# อีเมลกับ Word — ตรงนี้เจ้าหน้าที่ยืนยันให้คงไว้ (ก.ย. 2569) อย่าถอดตามกรณีนั้น
#
# บล็อกเชิญตอบแบบสอบถามเป็นชุดใหม่ (ก.ย. 2569) แทนบล็อกเดิมที่ชี้ไป forms.gle ซึ่งเป็น
# แบบประเมินงานบริการภาพรวม คนละตัวกับแบบสอบถามกระบวนการตรวจเล่มที่ใช้อยู่ตอนนี้
#
# ไทยกับอังกฤษของถ้อยคำชุดนี้ขึ้นบรรทัดคนละจุดและจำนวนบรรทัดไม่เท่ากัน จึงจับคู่แปล
# "ทั้งก้อน" ไม่ใช่ทีละบรรทัด ดู STAFF_BLOCK_EN ใน report.html

_PASS_NO_FEE_TH = "\n".join([
    'การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว',
    'แต่ยังมีขั้นตอนที่ต้องดำเนินการต่อ กรุณาอ่านรายละเอียดด้านล่าง',
    '',
    'ขั้นตอนถัดไป: การส่งเอกสารหน้าลงนาม',
    'เมื่อระบบแสดงสถานะการส่ง E-Thesis ของนักศึกษาเป็น “เสร็จสิ้น” แล้ว',
    'ขอให้นักศึกษาดำเนินการ นำส่งเอกสารหน้าลงนาม (เฉพาะหน้า ก - ข หรือ i - ii)',
    'ซึ่งต้องมีลายเซ็นของกรรมการทุกท่านครบถ้วนเรียบร้อย',
    'โดยส่งผ่านระบบ ใน tab Sign off page submission',
    'ผ่านลิงก์ https://graduate.mahidol.ac.th/ethesis/stu/login.php',
    'ภายใน 30 วัน นับจากวันที่สถานะในระบบแสดงว่า “เสร็จสิ้น”',
    'เพื่อเสนอเอกสารให้ คณบดีบัณฑิตวิทยาลัย ลงนามต่อไป',
    '',
    'กรณีที่นักศึกษามีคุณสมบัติครบถ้วนในการขออนุมัติปริญญา',
    'สามารถดำเนินการส่งแบบฟอร์ม GR.5 (Requesting Degree)',
    'ผ่านระบบออนไลน์ได้ที่ลิงก์ด้านล่างนี้',
    'https://graduate.mahidol.ac.th/e-graduate/main/formlogin.php',
    '',
    '*ทั้งสองขั้นตอนสามารถดำเนินการควบคู่กันได้*',
    '',
    'งานบริการการศึกษา',
    'ขอเชิญนักศึกษาตอบแบบสอบถามความพึงพอใจต่อกระบวนการตรวจรูปเล่มอิเล็กทรอนิกส์',
    'เพื่อนำความคิดเห็นไปปรับปรุงคุณภาพการให้บริการ โดยไม่เปิดเผยชื่อผู้ตอบรายบุคคล',
    'ลิงก์ตอบแบบสอบถาม  ' + SURVEY_URL,
    '',
    'สอบถามข้อมูลเพิ่มเติม',
    'supawit.mar@mahidol.ac.th',
])


_PASS_NO_FEE_EN = "\n".join([
    'Your E-thesis has been completed.',
    'However, there are still steps you need to complete. '
    'Please read the details below.',
    '',
    'Next step: submitting the signature pages',
    'Once your E-Thesis status is shown as “Completed” in the system,',
    'please submit the signature pages (pages i - ii only),',
    'with all committee members’ signatures completed',
    'through the system, in the Sign off page submission tab,',
    'at https://graduate.mahidol.ac.th/ethesis/stu/login.php within 30 days',
    'from the date your E-Thesis status is shown as "Completed" in the system,',
    'so that they may be forwarded to the Dean of the FGS for signature.',
    '',
    'If you have fulfilled all graduation requirements,',
    'you may also proceed to submit the GR.5 form (Requesting Degree)',
    'via the online system at the link below:',
    'https://graduate.mahidol.ac.th/e-graduate/main/formlogin.php',
    '',
    'Both processes can be carried out simultaneously.',
    '',
    'Academic Service Section',
    'We invite you to complete the satisfaction survey on the electronic file format review process,',
    'so that your feedback can help us improve our service. No individual respondent will be identified.',
    'Survey link: ' + SURVEY_URL,
    '',
    'If you have any further questions or need clarification,',
    'please feel free to contact Mr. Supawit at supawit.mar@mahidol.ac.th.',
])


_PASS_FEE_TH = "\n".join([
    'การส่ง E-thesis ในระบบเสร็จสิ้นแล้ว',
    'แต่ยังมีขั้นตอนที่ต้องดำเนินการต่อ กรุณาอ่านรายละเอียดด้านล่าง',
    '',
    'เนื่องจากนักศึกษามีค่าปรับกรณีส่ง E-thesis ล่าช้า',
    'นักศึกษาสามารถพิมพ์ใบแจ้งค่าปรับ (invoice) จากระบบเพื่อชำระเงินได้',
    'โดยสามารถเลือกช่องทางการชำระเงินได้ 3 ช่องทาง ได้แก่',
    '1. ชำระผ่าน QR Code payment',
    '2. ชำระผ่านใบแจ้งหนี้ (Invoice)',
    '3. ชำระผ่านบัตรเครดิต',
    '',
    'หมายเหตุ: ระบบจะดำเนินการตรวจสอบการชำระเงินภายใน 48 ชั่วโมง',
    'หากนักศึกษาชำระค่าปรับผ่านบัตรเครดิต ระบบจะใช้เวลาตรวจสอบการชำระเงินภายใน 3 ชั่วโมง',
    '',
    'ขั้นตอนถัดไป: การส่งเอกสารหน้าลงนาม',
    'เมื่อระบบแสดงสถานะการส่ง E-Thesis ของนักศึกษาเป็น “เสร็จสิ้น” แล้ว',
    'ขอให้นักศึกษาดำเนินการ นำส่งเอกสารหน้าลงนาม (เฉพาะหน้า ก - ข หรือ i - ii)',
    'ซึ่งต้องมีลายเซ็นของกรรมการทุกท่านครบถ้วนเรียบร้อย',
    'โดยส่งผ่านระบบ ใน tab Sign off page submission',
    'ผ่านลิงก์ https://graduate.mahidol.ac.th/ethesis/stu/login.php',
    'ภายใน 30 วัน นับจากวันที่สถานะในระบบแสดงว่า “เสร็จสิ้น”',
    'เพื่อเสนอเอกสารให้ คณบดีบัณฑิตวิทยาลัย ลงนามต่อไป',
    # ต้นฉบับเว้นสองบรรทัดตรงนี้ที่เดียว ที่อื่นเว้นบรรทัดเดียว เจ้าหน้าที่ให้ลดลง
    # ให้เท่ากัน (ก.ย. 2569) — เป็นการจัดย่อหน้า ไม่ได้แก้ถ้อยคำ
    '',
    'ในกรณีที่นักศึกษามีคุณสมบัติครบถ้วนในการเสนอขออนุมัติปริญญา',
    'สามารถดำเนินการส่งแบบฟอร์ม GR.5 (Requesting Degree)',
    'ผ่านระบบออนไลน์ที่ลิงก์ด้านล่างนี้',
    'https://graduate.mahidol.ac.th/e-graduate/main/formlogin.php',
    '',
    '*ทั้งสองขั้นตอนสามารถดำเนินการควบคู่กันได้*',
    '',
    'งานบริการการศึกษา',
    'ขอเชิญนักศึกษาตอบแบบสอบถามความพึงพอใจต่อกระบวนการตรวจรูปเล่มอิเล็กทรอนิกส์',
    'เพื่อนำความคิดเห็นไปปรับปรุงคุณภาพการให้บริการ โดยไม่เปิดเผยชื่อผู้ตอบรายบุคคล',
    'ลิงก์ตอบแบบสอบถาม  ' + SURVEY_URL,
    '',
    'สอบถามข้อมูลเพิ่มเติม',
    'supawit.mar@mahidol.ac.th',
])


_PASS_FEE_EN = "\n".join([
    'Your E-thesis has been completed.',
    'However, there are still steps you need to complete. '
    'Please read the details below.',
    '',
    'As there is a late submission fine, you are required to settle the payment.',
    'The invoice for the fine can be printed from the system, and payment can be made through one of the following three methods:',
    '1. QR Code',
    '2. Invoice',
    '3. Credit Card',
    '',
    'Note: The system will verify your payment within 48 hours.',
    'If you choose to pay by credit card, the system will take up to 3 hours to verify your payment.',
    '',
    'This will allow our staff to verify the payment and update the status of your E-Thesis in the system accordingly.',
    '',
    'Next step: submitting the signature pages',
    'Once your E-Thesis status is shown as “Completed” in the system,',
    'please submit the signature pages (pages i - ii only),',
    'with all committee members’ signatures completed',
    'through the system, in the Sign off page submission tab,',
    'at https://graduate.mahidol.ac.th/ethesis/stu/login.php within 30 days',
    'from the date your E-Thesis status is shown as "Completed" in the system,',
    'so that they may be forwarded to the Dean of the FGS for signature.',
    '',
    'If you meet all graduation requirements,',
    'you may proceed to submit the GR.5 form (Requesting Degree)',
    'via the online system at the following link:',
    'https://graduate.mahidol.ac.th/e-graduate/main/formlogin.php',
    '',
    'Both processes can be carried out simultaneously.',
    '',
    'Academic Service Section',
    'We invite you to complete the satisfaction survey on the electronic file format review process,',
    'so that your feedback can help us improve our service. No individual respondent will be identified.',
    'Survey link: ' + SURVEY_URL,
    '',
    'If you have any further questions or need clarification,',
    'please feel free to contact Mr. Supawit at supawit.mar@mahidol.ac.th.',
])


STAFF_CHECKS = [
    {
        "id": "SIGNATURE_LAYOUT",
        "item": "โครงสร้างหน้าลงนาม",
        "item_en": "Signature page layout",
        "why": "ระบบตรวจโครงสร้างของหน้านี้ไม่ได้ กรุณาเทียบกับ template และคู่มือ "
               "ทั้งลำดับที่ชื่อกรรมการวางลงในช่อง กรอบตาราง ฟอนต์ "
               "และช่องที่เหลือซึ่งต้องถมด้วยตัวอักษรสีขาว",
        "why_en": "The system cannot check this page's layout. Compare it with the "
                  "template and the manual: the order the committee names are placed "
                  "in the table, the frames, the font, and the leftover slots that "
                  "must be filled with white text.",
        "rule_id": "FRONT.SIGNATURE_LAYOUT",
        "placement": "section",
        # ถ้อยคำบอกตำแหน่งไว้ในประโยคแรกอยู่แล้ว จึงไม่พิมพ์บรรทัดตำแหน่งซ้ำ
        # ชื่อนี้ใช้จัดกลุ่มในข้อความสรุปเท่านั้น
        "section": "หน้าลงนาม",
        "choices": [
            {
                "id": "SIGNATURE_LAYOUT_OK",
                "label": "ถูกต้อง",
                "label_en": "Correct",
                "tone": "pass",
                "text": "",
                "text_en": "",
            },
            {
                "id": "SIGNATURE_LAYOUT_WRONG",
                "label": "โครงสร้างหน้าลงนามผิด",
                "label_en": "Layout is wrong",
                "tone": "fail",
                # บรรทัดแรกคือ "ตำแหน่ง" แยกออกมาให้เหมือนข้ออื่นในสรุป แล้วตามด้วย
                # สามย่อหน้าที่ย่อหน้าเข้ามา — เจ้าหน้าที่กำหนดรูปนี้เอง (ก.ย. 2569)
                # คำทุกคำเป็นต้นฉบับ เว้นวรรครอบขีดเขียนเหมือนกันทั้งสองภาษา
                # ("i - ii หรือ ก - ข" กับ "Pages i - ii")
                #
                # จำนวนบรรทัดสองภาษาต้องเท่ากันเสมอ (สี่ต่อสี่) เพราะหน้ารายงานแปล
                # ด้วยการเทียบทีละบรรทัด บรรทัดตำแหน่งก็อยู่ในคู่แปลด้วย
                #
                # ย่อหน้า 2 กับ 4 เคยอ่านแล้วขัดกันเอง — "ไม่ต้องเลื่อนหรือปรับกรอบ"
                # กับ "ปรับกรอบของ template ให้ตรงกันกับที่ set ไว้" เจ้าหน้าที่สั่งให้
                # ปรับให้สอดคล้องกัน (ก.ย. 2569) ทั้งสองย่อหน้าพูดคนละเรื่อง
                #   ย่อหน้า 2  ห้ามขยับ "เพื่อให้พอดีกับจำนวนชื่อของตัวเอง"
                #   ย่อหน้า 4  กรอบต้องเป็นค่ามาตรฐานของ template (ฝั่งละ 6 รายชื่อ)
                "text": ("ในหน้าลงนาม (หน้า i - ii หรือ ก - ข)"
                         "\n"
                         "ปรับโครงสร้างของหน้า "
                         "และกรุณาให้ปรับตำแหน่งรายชื่อของคณะกรรมการแต่ละชุด "
                         "โดยให้เรียงตามรายชื่อที่ได้รับอนุมัติในเอกสาร ทั้งนี้ "
                         "ให้เรียงชื่อลงมาตามลำดับที่ปรากฏในเอกสาร "
                         "ไม่ต้องเลื่อนชื่อหรือย่อขยายกรอบเพื่อให้พอดีกับจำนวนชื่อ"
                         "\n"
                         "สำหรับ ส่วนรายชื่อที่ว่างตามไฟล์ตัวอย่าง"
                         "ให้เปลี่ยนสีตัวอักษรเป็นสีขาว "
                         "และต้องใช้ font และ template ที่กำหนดด้วย "
                         "ซึ่งนักศึกษาจะต้องทำดำเนินจัดทำรูปเล่มตามโครงสร้างทื่กำหนด "
                         "ดูวิธีการเรียงลำดับชื่อจากคู่มือการจัดฯ "
                         "(จัดตามลูกศรสีเหลืองในคู่มือ) "
                         "https://www.canva.com/design/DAHA0Rwyb84/1Jxw_MXS0fDa__P-58UvKA/view?utm_content=DAHA0Rwyb84&utm_campaign=designshare&utm_medium=link2&utm_source=uniquelinks&utlId=h21893564e5"
                         "\n"
                         "ปรับกรอบของ template ให้ตรงกันกับที่ set ไว้ คือ "
                         "จะใส่รายชื่อได้ฝั่ง ละ 6 รายชื่อ ส่วนตรงไหนที่ไม่มีชื่อ "
                         "ให้ใส่สีขาวไว้ และลบตำแหน่งที่อยู่ใต้คุณวุฒิออก"),
                "text_en": ("Approval pages (Pages i - ii)"
                            "\n"
                            "please restructure the page and realign each committee "
                            "list to strictly follow the top-to-bottom sequence "
                            "approved in the official document without shifting the "
                            "names or resizing the frames to fit them."
                            "\n"
                            "Students must format the file using the designated font "
                            "and template, following the exact ordering sequence "
                            "indicated by the arrows in the formatting manual: "
                            "https://www.canva.com/design/DAHA0Rwyb84/1Jxw_MXS0fDa__P-58UvKA/view?utm_content=DAHA0Rwyb84&utm_campaign=designshare&utm_medium=link2&utm_source=uniquelinks&utlId=h21893564e5"
                            "\n"
                            "Additionally, the template frames must be adjusted to "
                            "match the default settings, which accommodate up to 6 "
                            "names per side; any remaining blank slots must be "
                            "changed to white font color and remove the position "
                            "below the degree"),
            },
        ],
    },
    {
        "id": "LATE_FEE",
        "item": "เล่มไม่ผ่าน: มีค่าปรับไหม",
        "item_en": "Not passed: is there a late fine?",
        "why": "ระบบไม่รู้เรื่องค่าปรับ เลือกให้ตรงกับข้อมูลของนักศึกษา "
               "ข้อความปิดท้าย (ค่าปรับ วิธีส่งกลับ และช่องทางติดต่อ) "
               "จะถูกต่อท้ายข้อความสรุป",
        "why_en": "The system knows nothing about fines. Pick the one that matches "
                  "the student's record. The closing text (the fine, how to "
                  "resubmit, and who to contact) is appended to the summary.",
        "rule_id": "FORM.LATE_FEE_NOTE",
        "placement": "closing",
        "applies_to": "not_pass",
        "choices": [
            {
                "id": "LATE_FEE_NONE",
                "label": "ไม่มีค่าปรับ",
                "label_en": "No fine",
                "tone": "pass",
                "text": ("นักศึกษาไม่มีค่าปรับในการส่งเล่มล่าช้า "
                         "และระหว่างการแก้ไขไฟล์จะไม่มีการคำนวณค่าปรับเพิ่มเติม"
                         "\n"
                         "\n"
                         "หากดำเนินการแก้ไขตามรายละเอียดที่เจ้าหน้าที่แจ้งใน Remarks "
                         "เสร็จสิ้นแล้ว กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง "
                         "ในกรณีนักศึกษามีข้อสงสัยเกี่ยวกับการแก้ไขไฟล์ e-Thesis "
                         "หรือประสงค์จะสอบถามข้อมูลเพิ่มเติมเกี่ยวกับกระบวนการส่งไฟล์ "
                         "e-Thesis กรุณาติดต่อเจ้าหน้าที่งานบริการการศึกษา ผ่านช่องทาง "
                         "Line Official Account ID: @600qubzh "
                         "เพื่อให้เจ้าหน้าที่ดำเนินการตรวจสอบและให้ข้อมูลแก่นักศึกษาต่อไป"
                         "\n"
                         "\n"
                         "หากนักศึกษาไม่สามารถ Resubmit ผ่านระบบได้ "
                         "ขอให้นักศึกษาติดต่อเจ้าหน้าที่งานเทคโนโลยีสารสนเทศ ผ่าน Line "
                         "Offical Account ID @322wjrbo หรือผ่านลิ้งค์ "
                         "https://line.me/R/ti/p/@322wjrbo "
                         "เพื่อให้เจ้าหน้าที่ดำเนินการตรวจสอบต่อไป"
                         "\n"
                         "\n"
                         "สอบถามข้อมูลเพิ่มเติม"
                         "\n"
                         "supawit.mar@mahidol.ac.th"),
                "text_en": ("You have no fine for late submission, and no additional fees "
                            "are charged during the checking process."
                            "\n"
                            "\n"
                            "Please resubmit the document to the system once you have "
                            "made the corrections listed in the Remarks provided by the "
                            "staff. If students have any inquiries regarding the "
                            "correction of e-Thesis files or wish to obtain further "
                            "information about the e-Thesis submission process, please "
                            "contact the Academic Services staff via the Line Official "
                            "Account ID: @600qubzh. The staff will review the matter and "
                            "provide appropriate assistance accordingly."
                            "\n"
                            "\n"
                            "If you are unable to submit the revised file through the "
                            "system, please contact the IT staff via Line Offical Account "
                            "ID @322wjrbo or link: https://line.me/R/ti/p/@322wjrbo"
                            "\n"
                            "\n"
                            "For more information"
                            "\n"
                            "supawit.mar@mahidol.ac.th"),
            },
            {
                "id": "LATE_FEE_YES",
                "label": "มีค่าปรับ",
                "label_en": "Has a fine",
                "tone": "fail",
                # ตัด "กรณีที่นักศึกษามีค่าปรับ" ออก (เจ้าหน้าที่สั่ง ก.ย. 2569 ว่าให้
                # สองภาษาเท่ากัน) ประโยคแรกยืนยันไปแล้วว่ามีค่าปรับ จะพูดเป็นเงื่อนไข
                # ซ้ำอีกไม่ได้ — ฝั่งอังกฤษเขียนตรงแบบนี้อยู่แล้วตั้งแต่แรก
                "text": ("นักศึกษามีค่าปรับในการส่งเล่มล่าช้า "
                         "และระหว่างการแก้ไขไฟล์จะไม่มีการคำนวณค่าปรับเพิ่มเติม "
                         "จะได้รับเอกสารแจ้งค่าปรับ(Invoice)ผ่านระบบเมื่อ "
                         "กระบวนการตรวจสอบเสร็จสิ้นแล้ว"
                         "\n"
                         "\n"
                         "หากดำเนินการแก้ไขตามรายละเอียดที่เจ้าหน้าที่แจ้งใน Remarks "
                         "เสร็จสิ้นแล้ว กรุณาส่งกลับเข้าสู่ระบบอีกครั้ง "
                         "ในกรณีนักศึกษามีข้อสงสัยเกี่ยวกับการแก้ไขไฟล์ e-Thesis "
                         "หรือประสงค์จะสอบถามข้อมูลเพิ่มเติมเกี่ยวกับกระบวนการส่งไฟล์ "
                         "e-Thesis กรุณาติดต่อเจ้าหน้าที่งานบริการการศึกษา ผ่านช่องทาง "
                         "Line Official Account ID: @600qubzh "
                         "เพื่อให้เจ้าหน้าที่ดำเนินการตรวจสอบและให้ข้อมูลแก่นักศึกษาต่อไป"
                         "\n"
                         "\n"
                         "หากนักศึกษาไม่สามารถ Resubmit ผ่านระบบได้ "
                         "ขอให้นักศึกษาติดต่อเจ้าหน้าที่งานเทคโนโลยีสารสนเทศ ผ่าน Line "
                         "Offical Account ID @322wjrbo หรือผ่านลิ้งค์ "
                         "https://line.me/R/ti/p/@322wjrbo "
                         "เพื่อให้เจ้าหน้าที่ดำเนินการตรวจสอบต่อไป"
                         "\n"
                         "\n"
                         "สอบถามข้อมูลเพิ่มเติม"
                         "\n"
                         "supawit.mar@mahidol.ac.th"),
                "text_en": ("You have a fine for late submission, and no additional fees "
                            "are charged during the checking process. The invoice will be "
                            "issued through the system after the checking process is "
                            "completed."
                            "\n"
                            "\n"
                            "Please resubmit the document to the system once you have "
                            "made the corrections listed in the Remarks provided by the "
                            "staff. If students have any inquiries regarding the "
                            "correction of e-Thesis files or wish to obtain further "
                            "information about the e-Thesis submission process, please "
                            "contact the Academic Services staff via the Line Official "
                            "Account ID: @600qubzh. The staff will review the matter and "
                            "provide appropriate assistance accordingly."
                            "\n"
                            "\n"
                            "If you are unable to submit the revised file through the "
                            "system, please contact the IT staff via Line Offical Account "
                            "ID @322wjrbo or link: https://line.me/R/ti/p/@322wjrbo"
                            "\n"
                            "\n"
                            "For more information"
                            "\n"
                            "supawit.mar@mahidol.ac.th"),
            },
        ],
    },
    {
        # หัวข้อนี้ขึ้นเฉพาะเล่มที่ตรวจแล้วผ่าน — คนละคำถามกับ LATE_FEE ข้างบน ซึ่งถาม
        # เรื่องค่าปรับของเล่มที่ยังต้องแก้ ถ้อยคำสองชุดนี้ขัดกันเอง (ชุดหนึ่งบอกให้
        # ส่งกลับมาแก้ อีกชุดบอกว่าเสร็จสิ้นแล้ว) จึงต้องแยกหัวข้อ ไม่ใช่แค่แยกปุ่ม
        #
        # ยังไม่กด = ข้อความสรุปมีแค่ "ผลการตรวจ: ผ่าน" กับ "ไม่พบจุดที่ต้องแก้ไข"
        # เจ้าหน้าที่กำหนดไว้แบบนี้ (ก.ย. 2569) ขั้นตอนถัดไปทั้งชุดผูกกับการกดปุ่ม
        "id": "PASS_FEE",
        "item": "เล่มผ่าน: ผ่านแบบมีค่าปรับหรือไม่",
        "item_en": "Passed: with or without a late fine?",
        "why": "เลือกให้ตรงกับข้อมูลของนักศึกษา แล้วข้อความขั้นตอนถัดไป "
               "(ส่งหน้าลงนามภายใน 30 วัน ขออนุมัติปริญญา และแบบสอบถาม) "
               "จะถูกต่อท้ายข้อความสรุป ถ้าไม่กด นักศึกษาจะไม่ได้ข้อความชุดนี้เลย",
        "why_en": "Pick the one that matches the student's record. The next-step "
                  "text (submitting the signature pages within 30 days, requesting "
                  "the degree, and the survey) is then appended to the summary. "
                  "Without pressing, the student gets none of it.",
        "rule_id": "FORM.LATE_FEE_NOTE",
        "placement": "closing",
        "applies_to": "pass",
        "choices": [
            {
                "id": "PASS_FEE_NONE",
                "label": "ผ่าน ไม่มีค่าปรับ",
                "label_en": "Passed, no fine",
                "tone": "pass",
                "text": _PASS_NO_FEE_TH,
                "text_en": _PASS_NO_FEE_EN,
            },
            {
                "id": "PASS_FEE_YES",
                "label": "ผ่าน มีค่าปรับ",
                "label_en": "Passed, with a fine",
                "tone": "fail",
                "text": _PASS_FEE_TH,
                "text_en": _PASS_FEE_EN,
            },
        ],
    },
]
STAFF_CHECK_BY_ID = {check["id"]: check for check in STAFF_CHECKS}
# ตัวเลือกที่ "มีถ้อยคำจริง" เท่านั้นที่หาเจอจากตรงนี้ ปุ่มอย่าง "ถูกต้อง" จึงไม่เพิ่มอะไร
STAFF_CHOICE_BY_ID = {
    choice["id"]: (check, choice)
    for check in STAFF_CHECKS for choice in check["choices"] if choice["text"]
}


def check_applies(check, verdict):
    """หัวข้อนี้ใช้กับผลตรวจนี้หรือไม่

    เรื่องค่าปรับถูกถามสองที่ ด้วยถ้อยคำที่ขัดกันเอง — ชุดของเล่มที่ยังต้องแก้บอกให้
    ส่งกลับเข้าระบบ ส่วนชุดของเล่มที่ผ่านบอกว่าเสร็จสิ้นแล้วให้ส่งหน้าลงนามต่อ ถ้าไม่กัน
    ตามผลตรวจ นักศึกษาจะได้ทั้งสองย่อหน้าพร้อมกันเมื่อหน้าเว็บส่ง id มาทั้งคู่
    (หน้าเก่าค้างไว้ หรือเจ้าหน้าที่กดตอนผลตรวจยังเป็นอีกอย่าง)
    "รอยืนยัน" ไม่ใช่ผ่าน จึงนับเป็น not_pass
    """
    scope = check.get("applies_to")
    if scope == "pass":
        return verdict == "ผ่าน"
    if scope == "not_pass":
        return verdict != "ผ่าน"
    return True


def staff_choices(keys, placement, verdict=None):
    """ตัวเลือกที่เจ้าหน้าที่กด เรียงตามลำดับในทะเบียน ไม่ใช่ตามลำดับที่กด

    ลำดับที่กดเป็นเรื่องบังเอิญของแต่ละคน ข้อความที่ส่งให้นักศึกษาต้องเรียงเหมือนกันทุกครั้ง
    id ที่ไม่รู้จักถูกทิ้งเงียบ ๆ เพราะค่านี้มาจากหน้าเว็บ เชื่อไม่ได้
    verdict = None คือไม่กรองตามผลตรวจ (ใช้ตอนอยากได้ทั้งทะเบียน)
    """
    picked = {str(key) for key in (keys or ())}
    out = []
    for check in STAFF_CHECKS:
        if check.get("placement") != placement:
            continue
        if verdict is not None and not check_applies(check, verdict):
            continue
        for choice in check["choices"]:
            if choice["text"] and choice["id"] in picked:
                # หนึ่งหัวข้อตอบได้คำตอบเดียว หน้าเว็บคุมให้อยู่แล้ว แต่ค่าที่ส่งมาเชื่อไม่ได้
                # (หน้าเก่าค้างไว้ กดรัวจนคำขอสวนกัน หรือคำขอถูกส่งซ้ำ) ถ้าไม่คุมตรงนี้
                # นักศึกษาจะได้ข้อความที่ขัดกันเอง "นักศึกษาไม่มีค่าปรับ" แล้วตามด้วย
                # "นักศึกษามีค่าปรับ" ในย่อหน้าถัดไป
                out.append((check, choice))
                break
    return out


def staff_issue(check, choice):
    """แปลงตัวเลือกที่เจ้าหน้าที่กด ให้อยู่ในรูปเดียวกับข้ออื่นในข้อความสรุป

    summary_text = ถ้อยคำที่เจ้าหน้าที่กำหนดมาทั้งย่อหน้า ให้พิมพ์ตรงตัว ไม่ผ่านการ
    ประกอบประโยคแบบ "ตำแหน่ง / ที่พบ / ต้องแก้เป็น" เพราะถ้อยคำชุดนี้บอกตำแหน่งไว้
    ในประโยคแรกอยู่แล้ว และเป็นคำสั่งที่เจ้าหน้าที่เขียนมาเอง
    """
    return {
        "part": "front_matter",
        "location": check["section"],
        "found": "",
        "expected": "",
        "fix": "",
        "system_note": False,
        "summary_text": choice["text"],
        "staff_choice": choice["id"],
        **rule_reference(check["rule_id"]),
    }


def issues_to_fix(report, failed=None, passed=None, staff=None):
    """รายการที่ต้องแก้ในสรุป

    - สีแดง: เข้าสรุปเสมอ
    - สีส้ม (รอยืนยัน): เข้าสรุป**โดยปริยาย** เพราะเป็นจุดที่ต่างจากข้อมูลอนุมัติ
      นักศึกษาควรรับรู้ เว้นแต่เจ้าหน้าที่กด "ผ่าน" (ยอมรับได้) จึงตัดออก
    - สีเหลือง (ข้อสังเกต): เข้าสรุปเฉพาะที่เจ้าหน้าที่กด "ไม่ผ่าน"

    ข้อที่ตั้ง system_note=True ไม่เข้าสรุปทุกกรณี เพราะเป็นข้อจำกัดของระบบเอง
    (เช่น เจ้าหน้าที่ยังไม่ได้กรอกข้อมูลอนุมัติ ระบบจึงข้ามการเทียบข้อมูลนั้น)
    นักศึกษาแก้เล่มยังไงข้อนี้ก็ไม่หาย การใส่ไว้ในใบสั่งแก้ทำให้นักศึกษาสับสน

    failed/passed เป็นชุดคีย์รูปแบบ "ZONE:index" เช่น {"ORANGE:0", "YELLOW:2"}
    staff เป็นชุด id ของ "ตัวเลือก" ใน STAFF_CHECKS ที่เจ้าหน้าที่กด (เช่น
    {"SIGNATURE_LAYOUT_WRONG"}) — เรื่องพวกนี้ระบบตรวจเองไม่ได้ จึงไม่มีใน
    issues_by_zone ตัวเลือกที่ placement เป็น closing ไม่ใช่จุดผิด จึงไม่เข้ารายการนี้
    """
    failed = set(failed or ())
    passed = set(passed or ())
    items = list(report["issues_by_zone"].get("RED") or [])
    # จุดที่เจ้าหน้าที่กด "ผิด" เองมาก่อนข้ออื่น เพราะเป็นคำตัดสินของคน ไม่ใช่ของระบบ
    # (ลำดับในข้อความสรุปยังจัดตามส่วนของเล่มอยู่ดี ตรงนี้แค่กันไม่ให้ตกท้ายกลุ่ม)
    for check, choice in staff_choices(staff, "section"):
        items.append(staff_issue(check, choice))
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
    # "เล่มผิดภาษา" ไม่มีค่าเดี่ยวให้พิมพ์แก้ในเล่ม สิ่งที่ต้องทำคือจัดทำเล่มใหม่ทั้งเล่ม
    # ถ้าดึงค่าท้ายประโยคไป สรุปจะเหลือแค่ 'ต้องแก้เป็น "ภาษาอังกฤษ"' ซึ่งกลืนถ้อยคำ
    # ที่เจ้าหน้าที่กำหนดไว้ทั้งประโยคหายไป — ปล่อยให้ตกไปใช้ประโยค expected เต็ม ๆ
    if issue.get("rule_id") == "FORM.BOOK_LANGUAGE":
        return ""
    raw = summary_tidy(issue.get("expected")) or summary_tidy(issue.get("fix"))
    raw = _SUMMARY_LEAD.sub("", raw)
    match = re.search(r'"([^"]+)"\s*$', raw)
    return match.group(1) if match else ""


def _nonbold_heading_text(nonbold, limit=3):
    """ข้อความ "หัวข้อหลักไม่เป็นตัวหนา" — ไม่ไล่ชื่อทุกหัวข้อเมื่อทั้งหน้าไม่หนา

    เล่มที่ไม่ได้ตั้งตัวหนาเลย จะได้รายชื่อยาว 14 หัวข้อต่อกันเป็นพืด แล้วตามด้วย
    ประโยคกฎที่พูดเรื่องเดียวกันอีกรอบ เจ้าหน้าที่อ่านแล้ววนไปวนมา ถ้าไม่หนาแค่
    ไม่กี่หัวข้อ การบอกชื่อยังช่วยให้หาเจอ จึงบอกครบเมื่อไม่เกิน limit หัวข้อ

    ใส่เครื่องหมายคำพูดรอบชื่อหัวข้อ = "ข้อความที่คัดมาจากเล่ม" ไม่ใช่คำของระบบ
    (รายงานอังกฤษจะได้ไม่แปลชื่อหัวข้อของเล่ม)
    """
    if len(nonbold) > limit:
        return f"หัวข้อหลักในสารบัญไม่เป็นตัวหนา {len(nonbold)} หัวข้อ"
    return "หัวข้อหลักไม่เป็นตัวหนา: " + ", ".join(f'"{t}"' for t in nonbold)


def _prose_found(found):
    """ข้อความ "ที่พบ" สำหรับข้อความสรุป — ตัดค่าที่ถูกต้องที่ถูกบอกซ้ำอยู่แล้วออก

    describe_diff ต่อท้ายว่า 'ต่างที่ "A" ต้องเป็น "B"' ซึ่ง "B" คือค่าเดียวกับที่
    บรรทัด "ต้องแก้เป็น" บอกอยู่แล้ว จึงตัดออกให้เหลือแค่จุดที่ผิด
    """
    found = summary_tidy(found)
    found = re.sub(r'\s*ต้องเป็น\s*"[^"]*"', '', found)
    return re.sub(r'\s{2,}', ' ', found).strip()


def _prose_location(location):
    """ทำตำแหน่งให้เป็นสำนวนคน ไม่ให้มีสัญลักษณ์ตกค้าง (↔ → ใช้คำเชื่อมแทน)"""
    loc = summary_tidy(location)
    loc = loc.replace("↔", "เทียบกับ").replace("→", "ถึง")
    return re.sub(r"\s{2,}", " ", loc).strip()


SUMMARY_INDENT = "   "


def _summary_sentence(issue, skip_location=False):
    """หนึ่งจุด = สามบรรทัด ตามที่เจ้าหน้าที่สั่ง: อยู่หน้าไหน / อะไรผิด / ต้องแก้เป็นอะไร

        3. สารบัญ (หน้า ฉ) บทที่ 3
           ชื่อบทในเล่มเขียนว่า "ระเบียบวิธีวิจัย"
           ต้องแก้เป็น "วิธีการดำเนินการวิจัย"

    ที่ต้องแยกบรรทัด ไม่ใช่แค่ความสวยงาม — ตัวแปลอังกฤษเทียบ "ทั้งข้อความ" กับกฎ
    ก่อนเสมอ ถ้ายัดสามท่อนไว้ในบรรทัดเดียว จะไม่มีกฎไหนตรงทั้งประโยค แล้วตกไปใช้
    การแทนที่แบบเศษคำซึ่งให้ผลเพี้ยน (เคยได้ "page after page 93 Change to:Page 94")
    พอแยกบรรทัด แต่ละบรรทัดกลายเป็นข้อความเดี่ยวที่มีกฎเต็มประโยครองรับอยู่แล้ว
    """
    # ถ้อยคำที่เจ้าหน้าที่กำหนดมาทั้งย่อหน้า (จุดที่กดเพิ่มเอง) พิมพ์ตรงตัวทุกคำ
    # ห้ามประกอบใหม่หรือย่อ และย่อหน้าบรรทัดต่อ ๆ ไปให้ตรงกับข้ออื่นในสรุป
    dictated = issue.get("summary_text")
    if dictated:
        return f"\n{SUMMARY_INDENT}".join(
            line.strip() for line in dictated.split("\n") if line.strip())
    lines = [] if skip_location else [_prose_location(issue.get("location"))]
    found = _prose_found(issue.get("found"))
    if found:
        lines.append(found)
    value = _corrected_value(issue)
    if value:
        lines.append(f'ต้องแก้เป็น "{value}"')
    else:
        # ไม่มีค่าเดี่ยวให้ดึง (เช่น มี 2 ตัวเลือก "ก/i") — ใช้ประโยค expected/fix เต็ม ๆ
        # ซึ่งเขียนไว้ในรูป "ต้องเป็น ..." อยู่แล้ว จึงไม่ต้องเติมคำนำอะไรอีก
        directive = summary_tidy(issue.get("expected")) or summary_tidy(issue.get("fix"))
        if directive:
            lines.append(directive)
    kept = [line for line in lines if line]
    return f"\n{SUMMARY_INDENT}".join(kept)


def _dedupe_issues(items):
    """รวมรายการที่เป็นจุดเดียวกัน (ตำแหน่ง + ค่าที่ต้องแก้ ตรงกัน) ให้เหลือรายการเดียว

    เก็บรายการแรกที่พบ ยกเว้นถ้ารายการหลังอ้าง "ประกาศ" (แหล่งอำนาจสูงสุด) ให้ใช้แทน
    """
    kept = {}
    order = []
    for issue in items:
        # จุดที่เจ้าหน้าที่กดเพิ่มไม่มีทั้ง "ค่าที่ต้องแก้" และ "สิ่งที่พบ" ถ้าไม่ใช้ถ้อยคำ
        # เป็นกุญแจ ทุกจุดที่อยู่หน้าเดียวกันจะกลายเป็นกุญแจ ("หน้าลงนาม", "") เหมือนกัน
        # แล้วถูกยุบเหลือจุดเดียว
        value = (issue.get("summary_text") or _corrected_value(issue)
                 or _prose_found(issue.get("found")))
        key = (summary_tidy(issue.get("location")), value)
        if key not in kept:
            kept[key] = issue
            order.append(key)
        elif "ประกาศ" in (issue.get("expected") or "") \
                and "ประกาศ" not in (kept[key].get("expected") or ""):
            kept[key] = issue
    return [kept[key] for key in order]


def zone_counts(report, failed=None, passed=None, staff=None):
    """ตัวเลขสามกล่องบนหัวรายงาน หลังรวมผลพิจารณาของเจ้าหน้าที่แล้ว

    เจ้าหน้าที่แจ้ง (ก.ย. 2569) ว่ากด "ไม่ผ่าน" ที่ข้อสีส้ม/เหลือง แล้วตัวเลขต้องขยับ
    เพราะการกดไม่ผ่านคือตัดสินว่าข้อนั้น "เป็นจุดที่นักศึกษาต้องแก้" เหมือนสีแดง
    ของเดิมตัวเลขค้างที่ "สิ่งที่ระบบตรวจพบ" ไม่ว่าจะกดอะไร หัวรายงานจึงเขียน
    ต้องแก้ 0 ทั้งที่ข้อความสรุปข้างล่างเขียนว่า "กรุณาแก้ไขทั้งหมด 4 จุด"

    กติกาเดียวกับ issues_to_fix ทุกประการ ตัวเลขกล่องแรกจึงเท่ากับจำนวนจุดใน
    ข้อความสรุปเสมอ ห้ามคิดคนละทาง ไม่งั้นเจ้าหน้าที่เห็นสองตัวเลขที่ขัดกันเอง
        กด "ไม่ผ่าน"   ย้ายไปนับกับสีแดง
        กด "ผ่าน"      หายไปจากทุกกล่อง (เจ้าหน้าที่รับได้แล้ว)
        ยังไม่กด       อยู่กล่องเดิม

    ข้อ system_note (ข้อจำกัดของระบบ เช่น อ่านหน้านั้นไม่ออก) ไม่เคยเข้าข้อความสรุป
    เพราะนักศึกษาแก้เล่มยังไงก็ไม่หาย จึงห้ามนับเป็น "ต้องแก้" แม้กดไม่ผ่าน —
    ค้างไว้ที่ "รอยืนยัน" ตามความจริงว่ายังไม่มีข้อสรุป ส่วนกด "ผ่าน" ถือว่าดูแล้ว
    """
    failed, passed = set(failed or ()), set(passed or ())
    zones = report.get("issues_by_zone") or {}
    # จุดที่เจ้าหน้าที่กดเพิ่มเองเป็นคำตัดสินว่าเล่มผิด นับรวมกับสีแดงเหมือนใน
    # issues_to_fix (ตัวเลือก placement=closing เป็นข้อความปิดท้าย ไม่ใช่จุดผิด)
    red = len(zones.get("RED") or []) + len(staff_choices(staff, "section"))
    pending = notice = 0
    for index, issue in enumerate(zones.get("ORANGE") or []):
        key = f"ORANGE:{index}"
        if issue.get("system_note"):
            pending += key not in passed
        elif key in failed:
            red += 1
        elif key not in passed:
            pending += 1
    for index, issue in enumerate(zones.get("YELLOW") or []):
        key = f"YELLOW:{index}"
        if key in failed:
            red += 1
        elif key not in passed:
            notice += 1
    return {"RED": red, "ORANGE": pending, "YELLOW": notice}


def summary_verdict(report, failed=None, passed=None, staff=None):
    """ผลตรวจที่ใช้จริงในข้อความสรุป หลังรวมคำตัดสินของเจ้าหน้าที่แล้ว

    ผลตรวจของระบบเป็น "ผ่าน" ได้ทั้งที่มีจุดต้องแก้ เมื่อจุดนั้นมาจากคำตัดสินของ
    เจ้าหน้าที่ (กดไม่ผ่านข้อสังเกต หรือกดเพิ่มจุดที่ระบบตรวจเองไม่ได้) ข้อความที่
    ส่งให้นักศึกษาจะขัดกันเองทันที — "ผลการตรวจ: ผ่าน" แล้วตามด้วย "กรุณาแก้ไข 1 จุด"

    และของเดิมค้างที่ "รอยืนยัน" ตลอดไป เพราะปรับเฉพาะขา ผ่าน -> ไม่ผ่าน เท่านั้น
    เล่มที่ระบบว่ารอยืนยัน แล้วเจ้าหน้าที่กดไม่ผ่านครบทุกข้อ ยังได้ข้อความสรุปว่า
    "ผลการตรวจ: รอยืนยัน" ส่งไปถึงนักศึกษา ทั้งที่ตัดสินไปแล้วว่าไม่ผ่าน และเล่มที่
    กดผ่านครบทุกข้อก็ไม่เคยขึ้นเป็น "ผ่าน" — คิดจากตัวเลขสามกล่องหลังคำตัดสินแทน
    ซึ่งเป็นกติกาเดียวกับ Report.verdict() เมื่อยังไม่มีใครกดอะไร

    หน้ารายงานเรียกฟังก์ชันนี้ผ่าน /summary ด้วย เพื่อรู้ว่าควรโชว์หัวข้อค่าปรับอันไหน
    """
    verdict = report.get("verdict", "")
    counts = zone_counts(report, failed, passed, staff)
    if counts["RED"]:
        return "ไม่ผ่าน"
    # เล่มที่ระบบว่า "รอยืนยัน" ค้างอยู่อย่างนั้นตลอดไปในของเดิม เพราะปรับเฉพาะขา
    # ผ่าน -> ไม่ผ่าน เท่านั้น เจ้าหน้าที่กดครบทุกข้อแล้วนักศึกษายังได้ข้อความว่า
    # "ผลการตรวจ: รอยืนยัน" ทั้งที่ตัดสินไปแล้ว — ขึ้นเป็น "ผ่าน" เมื่อข้อรอยืนยัน
    # ถูกตัดสินครบทุกข้อและไม่มีข้อไหนกลายเป็นจุดต้องแก้
    #
    # ต้องเห็นว่า "เคยมีข้อรอยืนยันแล้วหมดไป" ไม่ใช่แค่ "ตอนนี้ไม่มี" เพราะผลตรวจ
    # ของระบบเป็นตัวตั้งเสมอ ผู้เรียกที่ส่ง report มาโดยไม่มีรายการในโซน (เช่นเทสต์
    # หรือทางออกก่อนกำหนดของ run_check) ต้องได้ผลตรวจเดิมกลับไปไม่ถูกยกระดับเอง
    if verdict == "รอยืนยัน" and not counts["ORANGE"]:
        pending_found = len((report.get("issues_by_zone") or {}).get("ORANGE") or [])
        if pending_found:
            return "ผ่าน"
    return verdict


def plain_summary(report, failed=None, passed=None, staff=None):
    """สรุปจุดที่ต้องแก้เป็นข้อความล้วน จัดกลุ่มตามส่วนของเล่ม (ไว้คัดลอก/ให้ AI เรียบเรียง)

    เขียนเป็นประโยคภาษาคน ใช้คำเชื่อม ไม่ใช้เครื่องหมาย - หรือ → และไล่เลขทุกจุด
    ไม่แยกระดับความรุนแรง — ทุกข้อในสรุปคือ "กรุณาแก้ไข" เหมือนกันหมด (รวมสีส้มด้วย)
    """
    items = _dedupe_issues(issues_to_fix(report, failed, passed, staff))
    verdict = summary_verdict(report, failed, passed, staff)
    # ข้อความปิดท้าย (เช่น เรื่องค่าปรับและช่องทางติดต่อ) ไม่ใช่จุดที่ต้องแก้ จึงไม่ถูกนับ
    # และต้องตามไปด้วยเสมอ แม้เล่มจะไม่มีจุดต้องแก้เลย
    # ต้องอ่านค่า verdict ที่ปรับแล้วข้างบน ไม่ใช่ค่าดิบจาก report — เล่มที่ระบบว่าผ่าน
    # แต่เจ้าหน้าที่กดเพิ่มจุด ยังต้องได้ถ้อยคำ "แก้แล้วส่งกลับ" ไม่ใช่ "เสร็จสิ้นแล้ว"
    picked = staff_choices(staff, "closing", verdict)
    closing = [choice["text"] for _check, choice in picked]
    # ถ้อยคำชุด "ผ่าน" ขึ้นต้นด้วย "การส่ง E-thesis ... เสร็จสิ้นแล้ว" อยู่แล้ว
    finished = any(check.get("applies_to") == "pass" for check, _choice in picked)
    lines = [f"ผลการตรวจ: {verdict}"]
    if not items:
        # "ผลการตรวจ: ผ่าน" ตามด้วย "ไม่พบจุดที่ต้องแก้ไข" อ่านรวมกันว่า "จบแล้ว
        # ไม่ต้องทำอะไร" นักศึกษาหยุดอ่านตรงนั้น แล้วพลาดกำหนดส่งหน้าลงนามภายใน
        # 30 วันที่อยู่ข้างล่าง (เจ้าหน้าที่รายงานพฤติกรรมนี้ ก.ย. 2569) เล่มที่มี
        # ถ้อยคำชุด "ผ่าน" ต่อท้ายอยู่แล้วจึงไม่ต้องพิมพ์บรรทัดนี้ซ้ำ
        if not finished:
            lines.append("\nไม่พบจุดที่ต้องแก้ไข")
    else:
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
                # ตำแหน่งที่เป็นชื่อส่วนเปล่า ๆ (เช่น "ส่วนนำ") ซ้ำกับหัวข้อกลุ่มบรรทัดบน
                # จึงไม่ต้องพิมพ์อีก ให้ขึ้นต้นด้วยสิ่งที่พบเลย
                skip_loc = _prose_location(issue.get("location")) == section
                lines.append(
                    f"{number}. {_summary_sentence(issue, skip_location=skip_loc)}")
    for text in closing:
        lines.append("\n" + text.strip())
    return "\n".join(lines).strip()


def toc_page_mismatch_is_appendix_alt(section_kind, toc_label, appendix_labels):
    """เลขหน้าภาคผนวกในสารบัญชี้ไปหน้าเริ่มของภาคผนวก 'อีกชุด' ที่มีอยู่จริงในเล่ม

    ใช้เลือก 'ข้อความอธิบาย' เท่านั้น ไม่ได้ใช้ตัดสินสี — กติกา ส.ค. 2569 เลิกตรวจ
    เลขหน้าที่สารบัญอ้างถึงแล้ว ทุกกรณีเป็น 'เหลือง' (ข้อสังเกต ผ่านได้) กรณี
    ภาคผนวกหลายชุดนี้แค่ต้องอธิบายให้ชัดว่า 87 เป็นหน้าเริ่มของภาคผนวกอีกชุด
    ไม่ใช่เลขมั่ว
    """
    return section_kind == "appendix" and toc_label in appendix_labels


# จุดเริ่มของ "โซนรายชื่อกรรมการ" บนหน้าลงนามและหน้าบทคัดย่อ
# ใต้จุดนี้มีแต่ชื่อคนกับคุณวุฒิ ซึ่งเป็นตัวย่อชุดเดียวกับชื่อปริญญา (Ph.D. / ปร.ด.)
# ตำแหน่งนี้ตายตัวตาม template ทุกเล่ม จึงใช้เป็นขอบเขตได้โดยไม่ต้องเดา
_DEGREE_SEARCH_STOP = re.compile(
    r'^[ \t]*(?:'
    r'[…]{3,}|\.{6,}'                                    # เส้นประสำหรับลงนาม
    r'|(?:THESIS\s+|THEMATIC\s+PAPER\s+)?(?:ADVISORY|EXAMINATION)\s+COMMITTEE'
    r'|คณะกรรมการที่ปรึกษา|คณะกรรมการสอบ'
    r')', re.I | re.M)


# ตัวย่อคุณวุฒิที่มีจุดคั่นตั้งแต่สองท่อน เช่น Ph.D. / M.Sc. / ปร.ด. / วท.ม.
_DEGREE_ABBR_TOKEN = re.compile(r'(?:[A-Za-z]{1,4}\.){2,}|(?:[ก-๙]{1,4}\.){2,}')


# คำนำหน้าที่ template เขียนไว้เอง ไม่ใช่คำที่นักศึกษาเติมเกิน — หน้าปกเล่มไทยขึ้นบรรทัด
# ว่า "ปริญญาศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)" ส่วนข้อมูลอนุมัติเก็บไว้แค่
# "ศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)" ถ้าไม่ตัดคำนี้ก่อน เล่มไทยที่ถูกต้อง
# จะโดนฟ้องว่ามีข้อความเกินทุกเล่ม (เจอตอนวัดกับเล่มทดสอบ 3)
_DEGREE_LINE_TEMPLATE_PREFIXES = (norm("ปริญญา"),)


def degree_line_extras(page_text, expected):
    """ข้อความบนบรรทัดชื่อปริญญาที่ "เกิน" จากข้อมูลอนุมัติ — คืน "" ถ้าบรรทัดตรงพอดี

    เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าชื่อปริญญาต้องตรงเป๊ะและห้ามมีคำเกิน ส่วนการเว้นวรรค
    จะเว้นหรือไม่เว้นยอมรับได้ จึงเทียบด้วย norm() ซึ่งตัดวรรคตอนกับช่องว่างทิ้งก่อน

    เล่มจริงที่เจอ (ก.ย. 2569)
        หน้าปก      "DOCTOR OF PUBLIC HEALTH (INTERNATIONAL PROGRAM)"
                    ข้อมูลอนุมัติคือ "DOCTOR OF PUBLIC HEALTH"
        บทคัดย่อ    "Dr.PH (PUBLIC HEALTH)"  ข้อมูลอนุมัติคือ "Dr. P.H."
    ของเดิมปล่อยผ่านทั้งคู่ เพราะถามแค่ว่า "ข้อความที่อนุมัติอยู่บนหน้านี้ไหม"
    ไม่ได้ถามว่า "บรรทัดนั้นเท่ากับที่อนุมัติไหม"

    เลือก **บรรทัดที่สั้นที่สุด** ที่มีข้อความนั้นอยู่ — หน้าบทคัดย่อมีชื่อปริญญาโผล่ใน
    บรรทัดรายชื่อกรรมการด้วย ("... SUPA PENGPID, Dr.PH, ...") ถ้าเลือกบรรทัดแรกที่เจอ
    อาจไปยกบรรทัดกรรมการมาอ้างว่าเป็นบรรทัดชื่อปริญญา

    ใช้เฉพาะช่องที่ template วางชื่อปริญญาไว้ "บรรทัดของมันเอง" (หน้าปก และบรรทัด
    ชื่อย่อในบทคัดย่อ) — หน้าลงนามไม่ใช้ เพราะชื่อปริญญาอยู่กลางประโยค template
    ("for the degree of ...") ซึ่งมีคำอื่นล้อมรอบโดยชอบอยู่แล้ว
    """
    want = norm(expected)
    if not want:
        return ""
    hits = []
    for line in (page_text or "").splitlines():
        nl = norm(line)
        for prefix in _DEGREE_LINE_TEMPLATE_PREFIXES:
            if prefix and nl.startswith(prefix):
                nl = nl[len(prefix):]
                break
        if want in nl and nl != want:
            hits.append(soft(line))
    return min(hits, key=len) if hits else ""


def _looks_like_degree_line(line):
    """บรรทัดนี้หน้าตาเป็น "ชื่อปริญญาแบบย่อ" หรือไม่

    ใช้แยก "เล่มพิมพ์ชื่อปริญญาผิด" ออกจาก "เล่มไม่มีบรรทัดชื่อปริญญาเลย" ซึ่งวิธีแก้
    คนละอย่างกัน — ถ้าไม่แยก ระบบจะยกบรรทัดที่ใกล้เคียงที่สุดบนหน้ามาอ้างว่าเป็น
    ชื่อปริญญา (เคยได้บรรทัดชื่อ-รหัสนักศึกษา) แล้วเจ้าหน้าที่นึกว่าระบบอ่านเพี้ยน
    """
    text = soft(line)
    return bool(text) and bool(_ABS_DEGREE_HEAD.match(text)
                               or _DEGREE_ABBR_TOKEN.search(text))


def _degree_search_text(page_text):
    """ตัดโซนรายชื่อกรรมการทิ้งก่อนหาบรรทัดชื่อปริญญา

    คุณวุฒิใต้ชื่อกรรมการเขียนด้วยตัวย่อชุดเดียวกับชื่อปริญญา และมักมีสาขาในวงเล็บครบ
    ตัวกรอง "วงเล็บครบ" ข้างล่างจึงเลือกบรรทัดของกรรมการแทนบรรทัดปริญญาจริง โดยเฉพาะ
    เล่มที่บรรทัดปริญญาลืมปิดวงเล็บ (เจอในเล่มจริง: "for the degree of Doctor of
    Philosophy (Logistics and Engineering Management" ไม่มีวงเล็บปิด) ผลคือรายงาน
    ฟ้องว่าชื่อปริญญาในเล่มเขียนว่า "LIANGROKAPART, Ph.D., THANANYA WASUSRI, Ph.D."
    ซึ่งเป็นชื่ออาจารย์ ไม่ใช่ชื่อปริญญา
    """
    text = page_text or ""
    stop = _DEGREE_SEARCH_STOP.search(text)
    head = text[:stop.start()] if stop else text
    return head if soft(head) else text


def closest_degree_line(page_text, expected):
    """หาข้อความชื่อปริญญาบนหน้านั้น รองรับกรณีถูกตัดขึ้นหลายบรรทัด

    ชื่อปริญญาบนหน้าปกมักถูกตัดเป็น 2-3 บรรทัด ได้หลายแบบ เช่น
      "MASTER OF SCIENCE" / "(INFORMATION TECHNOLOGY MANAGEMENT)"   (ขึ้นบรรทัดตรงวงเล็บ)
      "MASTER OF SCIENCE (WELL-BEING AND" / "SUSTAINABILITY)"        (วงเล็บเปิดค้าง)
    จึงสร้างตัวเลือกจาก "หน้าต่างบรรทัดต่อเนื่อง 1-3 บรรทัด" รอบบรรทัดที่มีคำบ่งชี้
    แล้วเลือกอันที่ใกล้เคียงข้อมูลอนุมัติที่สุด (เล่มไทยต้องมีคำบ่งชี้ไทยด้วย)
    """
    page_text = _degree_search_text(page_text)
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
    best = max(candidates,
               key=lambda line: difflib.SequenceMatcher(None, target, norm(line)).ratio())
    return _strip_degree_lead_in(best)


# คำนำหน้าชื่อปริญญาในประโยค template ของหน้าลงนาม — ไม่ใช่ส่วนหนึ่งของชื่อปริญญา
# ถ้าไม่ตัดออก ข้อความที่ยกมาจะเป็น "for the degree of Doctor of Philosophy (..."
# แล้วเจ้าหน้าที่ต้องไล่เทียบเองว่าต่างจากข้อมูลอนุมัติตรงไหน
_DEGREE_LEAD_IN = re.compile(
    r'^\s*(?:for\s+the\s+degree\s+of|เพื่อรับปริญญา|ปริญญา)\s*', re.I)


def _strip_degree_lead_in(line):
    return _DEGREE_LEAD_IN.sub('', soft(line)).strip()


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


# "ตัวอักษรที่คนมองเห็นหนึ่งตัว" ของภาษาไทย = พยัญชนะพร้อมสระบน/ล่างและวรรณยุกต์ของมัน
# (สระหน้าอย่าง เ แ โ ใ ไ พิมพ์ก่อนพยัญชนะ จึงนับรวมไปข้างหน้าด้วย)
_TH_ABOVE_BELOW = 'ัิ-ฺ็-๎'
_TH_LEAD_VOWEL = 'เ-ไ'
_GRAPHEME = re.compile(
    rf'[{_TH_LEAD_VOWEL}]?[^{_TH_ABOVE_BELOW}][{_TH_ABOVE_BELOW}]*'
    rf'|[{_TH_ABOVE_BELOW}]+')


def _graphemes(text):
    """แยกข้อความเป็นตัวอักษรที่คนมองเห็น ไม่ใช่ code point ทีละตัว

    ถ้าไล่ทีละ code point ตัวชี้จุดต่างจะตัดกลางพยางค์ไทย เล่มจริงที่ชื่อเรื่องขาดคำว่า
    "กีฬา" เคยรายงานว่า ขาด "ีฬาก" (สระอียกไปไว้หน้า และลาก ก ของคำถัดไปมาด้วย)
    ซึ่งอ่านไม่ออกว่าต้องเติมอะไร
    """
    return _GRAPHEME.findall(text or "")


def describe_diff(found, expected):
    """ชี้ว่า 'ข้อความที่พบ' ต่างจาก 'ข้อความที่ถูกต้อง' ตรงไหน อย่างไร

    - อังกฤษที่มีช่องว่าง: เทียบระดับคำ (เช่น ต่างที่ "REQUIREMENT" ต้องเป็น "REQUIREMENTS")
    - ไทย/คำเดียว: เทียบระดับตัวอักษร (เช่น ขาด "อ")
    คืน '' ถ้าต่างกันมากจนการชี้จุดไม่ช่วย (ให้ผู้ใช้ดูข้อความเต็มที่ให้ไว้แทน)

    เขียนเป็นคำพูด ไม่ใช้ลูกศร — เจ้าหน้าที่สั่งว่าคำอธิบายไม่ต้องใช้สัญลักษณ์เยอะ
    ข้อความที่คืนมา "ต่อท้ายประโยคได้เลย" คือมีคำเชื่อมของตัวเองมาพร้อม เพราะคำเชื่อม
    ที่เหมาะกับแต่ละกรณีไม่เหมือนกัน ("ต่างที่ ก ต้องเป็น ข" แต่ "ขาด ก" / "มี ก เกินมา"
    ซึ่งถ้าเอา "ต่างที่" ไปนำหน้าจะกลายเป็น "ต่างที่ มี S เกินมา" ที่อ่านไม่เป็นภาษาคน)
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
                parts.append(f'มี "{got}" เกินมา')
            elif not got:
                parts.append(f'ขาด "{want}"')
            else:
                parts.append(f'ต่างที่ "{got}" ต้องเป็น "{want}"')
        return " และ ".join(parts)

    # อังกฤษหลายคำ: ลองเทียบระดับคำก่อน (อ่านง่าย เห็นเป็นคำ) ถ้าทุกคำต่างกัน
    # จนเทียบไม่ได้ ค่อยตกไปเทียบระดับตัวอักษร (เช่น "LITTERATURE" ต่าง T กับ S)
    if re.search(r'[A-Za-z]', expected_s) and ' ' in expected_s.strip():
        by_word = _diff(found_s.split(), expected_s.split(),
                        lambda xs: [x.upper() for x in xs], ' '.join)
        if by_word:
            return by_word
    return _diff(_graphemes(found_s), _graphemes(expected_s), lambda xs: xs, ''.join)


def mismatch_detail(label, compared, expected=''):
    """Make small differences visible instead of silently accepting fuzzy matches.

    ถ้าส่ง expected มาด้วย จะต่อท้ายว่า "ต่างที่ ..." ชี้ตำแหน่ง/วิธีที่ผิด
    """
    # เขียนให้เหมือนคนพูด: บอกว่า "ในเล่มเขียนว่าอะไร" ก่อน แล้วค่อยบอกว่าต่างยังไง
    # (ของเดิมขึ้นต้นด้วยคำตัดสินแบบระบบ เช่น "ชื่อบทข้อความไม่ตรง:" และมีคะแนน
    #  ความใกล้เคียงซึ่งเจ้าหน้าที่เอาไปใช้อะไรไม่ได้)
    detail = f'{label}ในเล่มเขียนว่า "{compared["actual"]}"'
    # ชี้จุดต่างเฉพาะเมื่อใกล้เคียงกัน (typo/ตัวพิมพ์) — ถ้าเป็นคนละข้อความ
    # (mismatch) การไล่ทีละตัวอักษรจะรกและสับสน ให้ดูข้อความที่ถูกต้องแทน
    diff = describe_diff(compared['actual'], expected) \
        if expected and compared['status'] in ('typo', 'case') else ''
    # ถ้าชี้จุดต่างได้แล้ว ไม่ต้องบอกซ้ำว่า "พิมพ์ผิดเล็กน้อย" — จุดต่างบอกอยู่ในตัว
    if diff:
        detail += f' {diff}'
    elif compared['status'] == 'case':
        detail += ' ต่างกันแค่ตัวพิมพ์เล็ก-ใหญ่'
    elif compared['status'] == 'typo':
        detail += ' พิมพ์ผิดเล็กน้อย'
    return detail


# ---------- "ชื่อเรื่องนี้เป็นภาษาอะไร" ----------
# ชื่อเรื่องภาษาไทยจำนวนมากมีอักษรอังกฤษปนอยู่จริง — ชื่อเทคโนโลยี ตัวย่อ ชื่อสารเคมี
# เช่น "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ" ซึ่งอักษรอังกฤษเกินครึ่ง
# บรรทัด ถ้าตัดสินด้วย "มีอักษรอังกฤษ = เป็นชื่อเรื่องภาษาอังกฤษ" หรือด้วยสัดส่วน
# ตัวอักษร จะตัดสินผิดทันที ส่วนชื่อเรื่องภาษาอังกฤษไม่มีอักษรไทยปนเลยสักตัว
# "การมีอักษรไทย" จึงเป็นตัวแยกสองภาษาที่เชื่อถือได้ทางเดียว
_THAI_LETTER = re.compile(r'[ก-๙]')
_LATIN_LETTER = re.compile(r'[A-Za-z]')


def title_script(text):
    """ภาษาของข้อความหนึ่งช่วง: "thai" / "en" / "" (ไม่มีตัวอักษรของภาษาใดเลย)"""
    if _THAI_LETTER.search(text or ""):
        return "thai"
    if _LATIN_LETTER.search(text or ""):
        return "en"
    return ""


def approved_title(approved, script):
    """ชื่อเรื่องภาษาที่ต้องการ เอาจากข้อมูลระบบ (eThesis/บฑ.1) เท่านั้น

    ไม่เดาชื่อเรื่องจากหน้ากระดาษเอง เพราะกฎนี้ตัดสินว่า "หน้านี้มีชื่อเรื่องอีกภาษา
    อยู่ด้วยหรือไม่" ถ้าเดาเองแล้วเดาผิด จะสั่งให้นักศึกษาลบข้อความที่ถูกต้องออก

    คืน "" เมื่อช่องนั้นกรอกเป็นภาษาอื่นหรือไม่ได้กรอก — หลักสูตรนานาชาติกรอก
    ช่องชื่อเรื่องภาษาไทยเป็น "-" ซึ่งไม่ใช่ชื่อเรื่องภาษาไทย จึงไม่มีอะไรให้เทียบ
    ถ้าข้อมูลระบบสลับช่องกันไว้ก็คืน "" เช่นกัน ดีกว่าเทียบผิดช่องแล้วฟ้องมั่ว
    """
    value = soft((approved or {}).get("title_th" if script == "thai" else "title_en", "") or "")
    return value if title_script(value) == script else ""


# เกณฑ์ตัดสินว่า "ชื่อเรื่องนี้ถูกพิมพ์อยู่บนหน้านี้จริง"
#
# วัดกับเล่มจริงสามเล่มและกับชื่อเรื่องไทยที่มีอักษรอังกฤษปน:
#   หน้าที่ไม่มีชื่อเรื่องอีกภาษาจริง ๆ ได้สูงสุด 0.21
#   หน้าที่พิมพ์ชื่อเรื่องอีกภาษาไว้จริง ได้ 0.69 ขึ้นไป (ครบทั้งชื่อได้ 1.0)
# ตั้งไว้กลางช่องว่างนั้น กฎนี้สั่งให้ "ลบข้อความออก" การฟ้องผิดจึงเสียหายกว่าการปล่อย
TITLE_ON_PAGE_MIN = 0.6


def title_printed_on_page(page_text, title, min_ratio=TITLE_ON_PAGE_MIN):
    """ข้อความบนหน้านี้ที่เป็นชื่อเรื่องดังกล่าว (คืน "" ถ้าไม่มี)

    เทียบเฉพาะช่วงข้อความที่เป็น "ภาษาเดียวกับชื่อเรื่องที่กำลังหา" — ชื่อเรื่องไทยที่มี
    อักษรอังกฤษปนจะไปคล้ายชื่อเรื่องภาษาอังกฤษของตัวเองเสมอ (วัดได้ 0.59 ในเล่มที่
    ชื่อไทยเป็น "การศึกษา COVID-19 mRNA vaccine booster ในผู้สูงอายุ") ถ้าไม่กรองภาษา
    ก่อน หน้าปกไทยที่ถูกต้องจะโดนฟ้องว่ามีชื่อเรื่องภาษาอังกฤษ ช่วงที่มีอักษรไทย
    ถือเป็นข้อความภาษาไทยเสมอ ต่อให้มีอักษรอังกฤษปนอยู่มากแค่ไหน
    """
    want = title_script(title)
    target = norm(title)
    if not want or not target:
        return ""
    lines = [soft(line) for line in (page_text or "").splitlines() if soft(line)]
    best, best_ratio = "", 0.0
    for start in range(len(lines)):
        for span in range(1, 5):
            if start + span > len(lines):
                break
            window = " ".join(lines[start:start + span])
            if title_script(window) != want:
                continue
            ratio = difflib.SequenceMatcher(None, target, norm(window)).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best if best_ratio >= min_ratio else ""


# คำนำหน้าบล็อกชื่อเรื่องบนหน้าลงนาม — ชื่อเรื่องเริ่มบรรทัดถัดจากนี้
_TITLE_LEAD_IN = re.compile(r'^(?:entitled|เรื่อง)$', re.I)
# บรรทัดที่บอกว่าบล็อกชื่อเรื่องจบแล้ว (ข้อความ template ที่ตามหลังชื่อเรื่องเสมอ)
_TITLE_STOP = re.compile(
    r'^(?:was\s+submitted\s+to'
    r'|A\s+(?:THESIS|THEMATIC\s+PAPER|DISSERTATION|MASTER|DOCTOR)'
    r'|ได้รับการพิจารณา|วิทยานิพนธ์นี้เป็นส่วนหนึ่ง|สารนิพนธ์นี้เป็นส่วนหนึ่ง'
    r'|ABSTRACT|บทคัดย่อ|FACULTY\s+OF|บัณฑิตวิทยาลัย)', re.I)


def printed_title(page_text, student_name=""):
    """ชื่อเรื่อง "ตามที่พิมพ์จริง" บนหน้านั้น รวมบรรทัดที่ห่อคำมาให้ครบ

    ต้องหาจากโครงสร้างของหน้า ไม่ใช่หาช่วงที่ "ใกล้เคียงข้อมูลอนุมัติที่สุด" เพราะถ้า
    ชื่อในเล่มกับในระบบเป็นคนละเรื่องกันจริง ๆ การหาช่วงที่ใกล้เคียงจะได้เศษข้อความมั่ว
    เล่มจริงเคยได้บรรทัดเนื้อความบทคัดย่อ ("suitable for future implementation of
    Robotic Process Automation (RPA), and to establish a") มาอ้างว่าเป็นชื่อเรื่อง

    ขอบเขต: เริ่มหลังคำว่า "entitled"/"เรื่อง" (หน้าลงนาม) หรือบรรทัดแรกที่ยาวพอ
    (หน้าปก/หน้าบทคัดย่อ) จบเมื่อเจอข้อความ template ที่ตามหลังชื่อเรื่อง ชื่อนักศึกษา
    หรือบรรทัดชื่อ-รหัสนักศึกษา
    """
    lines = [soft(line) for line in (page_text or '').splitlines() if soft(line)]
    start = None
    for i, line in enumerate(lines):
        if _TITLE_LEAD_IN.match(line.strip()):
            start = i + 1
            break
    if start is None:
        start = next((i for i, line in enumerate(lines)
                      if len(norm(line)) >= 8 and not _ABS_RUNNING_HEAD.search(line)), None)
    if start is None:
        return ""
    want_name = norm(_strip_student_title(student_name)) if student_name else ""
    out = []
    for line in lines[start:start + 6]:
        if _TITLE_STOP.match(line.strip()) or _ABS_STUDENT_LINE.search(line):
            break
        if want_name and want_name in norm(line):
            break
        out.append(line)
    return ' '.join(out).strip()


# ข้อความ template ที่พิมพ์ต่อจากชื่อนักศึกษาบนหน้าปกเสมอ ทุกภาษาและทุกประเภทเล่ม
# ใช้ชี้ "ช่องของชื่อ" บนหน้าปก ซึ่งไม่มีรหัสนักศึกษาให้ยึดแบบหน้าบทคัดย่อ
#
# ไล่ดูจาก template ครบทั้ง 18 ใบ (นานาชาติ / เล่มอังกฤษของหลักสูตรไทย / เล่มไทย
# คูณสามประเภทเล่ม คูณสองรูปแบบ) ได้ถ้อยคำ 7 แบบ — เล่มการค้นคว้าอิสระภาษาอังกฤษ
# ใช้ "AN INDEPENDENT STUDY" ไม่ใช่ "A ..." จึงต้องรับ "AN" ด้วย
_COVER_NAME_STOP = re.compile(
    r'^(?:AN?\s+(?:THESIS|THEMATIC\s+PAPER|DISSERTATION|INDEPENDENT\s+STUDY)'
    r'\s+SUBMITTED'
    r'|(?:วิทยานิพนธ์|สารนิพนธ์|การค้นคว้าอิสระ)นี้เป็นส่วนหนึ่ง)', re.I)


def cover_printed_name(page_text, title=""):
    """ชื่อนักศึกษา "ตามที่พิมพ์จริง" บนหน้าปก — คืน "" ถ้าชี้ช่องไม่ได้

    template วางชื่อนักศึกษาไว้บรรทัดก่อน "A THESIS SUBMITTED IN PARTIAL FULFILLMENT"
    (หรือ "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร") ช่องของชื่อจึงชี้ได้จาก
    โครงสร้างของหน้า ไม่ใช่จาก "บรรทัดไหนก็ได้ทั้งหน้าที่คล้ายชื่อที่สุด"

    เล่มจริง (ก.ย. 2569) ที่ลืมแก้ placeholder เป็น "FIRSTNAME LASTNAME" ถูกรายงานว่า
    "ชื่อนักศึกษาในเล่มเขียนว่า OF PM2.5 IN NORTHERN THAILAND" ซึ่งเป็นบรรทัดสุดท้าย
    ของชื่อเรื่อง เพราะการวัดความคล้ายให้เศษชื่อเรื่องคะแนนสูงกว่าบรรทัดชื่อจริง
    """
    lines = [soft(line) for line in (page_text or "").splitlines() if soft(line)]
    stop = next((i for i, line in enumerate(lines)
                 if _COVER_NAME_STOP.match(line.strip())), 0)
    if stop < 1:
        return ""
    candidate = lines[stop - 1]
    # เล่มที่ไม่ได้พิมพ์ชื่อไว้เลย จะเหลือบรรทัดสุดท้ายของชื่อเรื่องยืนอยู่ตรงช่องนั้น
    # อย่ารายงานเศษชื่อเรื่องว่าเป็นชื่อนักศึกษา ให้ถอยไปใช้วิธีเดิมแทน
    if title and norm(candidate) and norm(candidate) in norm(title):
        return ""
    return candidate


# ป้ายบทบาทที่ template พิมพ์ไว้ "ใต้ชื่อนักศึกษา" บนหน้าลงนามเสมอ
_SIGNATURE_CANDIDATE_LABEL = re.compile(r'^(?:Candidate|ผู้วิจัย|ผู้เขียน)', re.I)


def signature_printed_name(page_text):
    """ชื่อนักศึกษา "ตามที่พิมพ์จริง" บนหน้าลงนาม — คืน "" ถ้าชี้ช่องไม่ได้

    template พิมพ์ป้ายบทบาทไว้ใต้ชื่อนักศึกษาเสมอ ("Candidate" / "ผู้วิจัย") ช่องของชื่อ
    จึงคือบรรทัดเหนือป้ายนั้น การดึงข้อความรวมสองคอลัมน์เป็นบรรทัดเดียว ชื่อของ
    นักศึกษาอยู่คอลัมน์ซ้ายจึงเป็นท่อนแรกก่อนจุลภาค

    ถ้าปล่อยให้วัดความคล้ายทั้งหน้าแทน เล่มที่ชื่อไม่ตรงจะถูกรายงานว่าเล่มเขียนชื่อ
    นักศึกษาว่า "Mahidol University" หรือ "Management)" (วัดจากเล่มจริงสองเล่ม)
    """
    lines = [soft(line) for line in (page_text or "").splitlines() if soft(line)]
    for i, line in enumerate(lines):
        if i and _SIGNATURE_CANDIDATE_LABEL.match(line.strip()):
            return _strip_student_title(lines[i - 1].split(",")[0].strip())
    return ""


def _title_as_printed(compared, page_text, expected, student_name=""):
    """แทน "ช่วงที่ใกล้เคียงที่สุด" ด้วยชื่อเรื่องตามที่พิมพ์จริง เมื่อหาบล็อกชื่อเรื่องเจอ

    ใช้เฉพาะตอนที่ยังไม่ตรง — ถ้าตรงอยู่แล้วไม่ต้องแตะ
    ถ้าหาบล็อกไม่เจอ คงค่าเดิมไว้ ดีกว่าไม่มีอะไรให้เจ้าหน้าที่ดูเลย
    """
    printed = printed_title(page_text, student_name)
    if not printed:
        return compared
    return compare_values(printed, expected, 'title')


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
            detail += f' {diff}'
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


# ชื่อฟอนต์ที่ไม่ได้บอกอะไรเลยว่าเป็นน้ำหนักไหน — โปรแกรมแปลง PDF บางตัวตั้งชื่อฟอนต์
# ย่อยเป็น "CIDFont+F1", "F2" ไล่ตามลำดับที่พบ "ในหน้านั้น ๆ" ไม่ใช่ชื่อฟอนต์จริง
# (เล่มจริงเจอ CIDFont+F1 บนหน้าสารบัญเป็นตัวหนา แต่ CIDFont+F1 บนหน้าเนื้อหาเป็น
#  ตัวธรรมดา คือชื่อเดียวกันคนละฟอนต์) จึงดูจากชื่อไม่ได้เลยว่าหนาหรือไม่
_ANONYMOUS_FONT = re.compile(r'^(?:CIDFont\+)?F\d+$', re.I)


def bold_is_undetectable(pdf_page):
    """หน้านี้บอก "ตัวหนา" จากชื่อฟอนต์ไม่ได้เลยหรือไม่

    ถ้าบอกไม่ได้ ห้ามสรุปว่า "ไม่เป็นตัวหนา" — เล่มจริงที่หัวข้อสารบัญหนาครบทุกหัวข้อ
    เคยถูกฟ้องว่า "หัวข้อหลักในสารบัญไม่เป็นตัวหนา 13 หัวข้อ" ด้วยเหตุนี้
    """
    names = {(c.get('fontname') or '') for c in (pdf_page.chars or [])
             if (c.get('text') or '').strip()}
    if not names:
        return False
    if any(_is_bold_font(name) for name in names):
        return False        # มีฟอนต์ที่บอกน้ำหนักในชื่อ = เทียบได้ตามปกติ
    return all(_ANONYMOUS_FONT.match(name.split('+')[-1]) for name in names)


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
        results.append({'text': text, 'bold_ratio': (bold / total if total else 0.0),
                        'x0': min(float(w.get('x0', 0)) for w in line_words)})
    return results


_ABS_STUDENT_LINE = re.compile(r'\d{7}\s*\S*\s*/\s*\S')
# หัวกระดาษที่บางเล่มใส่ไว้เหนือชื่อเรื่องบนหน้าบทคัดย่อ (ไม่ใช่ส่วนของชื่อเรื่อง)
_ABS_RUNNING_HEAD = re.compile(
    r'มหาวิทยาลัยมหิดล|บัณฑิตวิทยาลัย|FACULTY OF GRADUATE|MAHIDOL UNIVERSITY', re.I)


def _report_abstract_title_format(rep, lines, loc):
    """ชื่อเรื่องบนหน้าบทคัดย่อต้องจัด "ชิดซ้าย"

    นโยบายเจ้าหน้าที่ (ส.ค. 2569): *"ไม่ควรชิดขวาหรือกึ่งกลาง ควรจัดชิดซ้าย"*
    ยืนยันด้วยเล่มจริง 10 เล่ม: 8 เล่มพิมพ์ชิดซ้าย · เล่มที่ 7 และ 9 ไม่ชิดซ้าย
    (สองเล่มนี้คือที่เจ้าหน้าที่ทักมา) · template ตั้ง `right` ไว้ก็จริง แต่เป็น
    บรรทัดจุดไข่ปลาเต็มความกว้าง ซึ่งจัดชิดไหนก็เห็นเหมือนกัน จึงไม่ใช่ข้อกำหนดจริง

    **ตัวหนาไม่ตรวจที่นี่** — กฎ `FORMAT.ABSTRACT_BOLD` ฟ้องตัวหนาทั้งหน้าเป็น
    สีเหลืองอยู่แล้ว และเจ้าหน้าที่สั่งว่า "หน้าบทคัดย่อไม่มีตัวหนา แต่ก็พอรับได้
    เลยให้เป็นสีเหลือง" ถ้าตรวจซ้ำที่นี่จะได้สองข้อที่แก้จุดเดียวกัน

    ระบบตรวจ PDF ไม่ใช่ไฟล์ Word จึงไม่มีค่า "การจัดย่อหน้า" ให้อ่านตรง ๆ ต้องอนุมาน
    จากพิกัด: เทียบขอบซ้ายของบรรทัดชื่อเรื่องกับขอบซ้ายของ "เนื้อความ" ในหน้าเดียวกัน
    (ใช้บรรทัดยาวเป็นตัวอ้างอิง เพราะบรรทัดยาวย่อมเริ่มที่ขอบซ้ายจริงเสมอ)
    ลงเป็นสีส้ม ไม่ฟันธงแดง เพราะเป็นการอนุมานจากพิกัด ไม่ใช่ค่าที่อ่านได้ตรง ๆ
    """
    # ชื่อเรื่อง = บรรทัดที่อยู่ "ก่อนบรรทัดชื่อ-รหัสนักศึกษา" ตามโครงสร้างของ template
    # หาแบบนี้แทนการเทียบกับข้อมูลอนุมัติ เพราะเป็นกฎรูปแบบของ template ล้วน
    # ต้องตรวจได้แม้ยังไม่มีข้อมูล eThesis (หลักการเดียวกับกฎรูปแบบข้ออื่น)
    stop = next((i for i, l in enumerate(lines)
                 if _ABS_STUDENT_LINE.search(l['text'])), None)
    if stop is None:
        return
    # ไล่ขึ้นจากบรรทัดชื่อ-รหัสนักศึกษา เก็บเฉพาะบรรทัดที่ติดกันขึ้นไป (สูงสุด 4 บรรทัด)
    # แล้วหยุดเมื่อเจอหัวกระดาษ/เลขหน้า — บางเล่มมี running head ที่ไม่ใช่ชื่อเรื่อง
    # (เล่มที่ 4: "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล วิทยานิพนธ์ / ง")
    title_lines = []
    for l in reversed(lines[:stop][-4:]):
        if len(norm(l['text'])) < 8 or _ABS_RUNNING_HEAD.search(l['text']):
            break
        title_lines.append(l)
    body = [l for l in lines[stop:] if len(l['text']) > 60]
    if not title_lines or not body:
        return
    margin = min(l['x0'] for l in body)
    off = [l['text'] for l in title_lines if l['x0'] - margin > 6]
    if not off:
        return
    shown = ", ".join(f'"{t}"' for t in dict.fromkeys(off))
    rep.add("ORANGE", "front_matter", loc,
            f"ชื่อเรื่องบนหน้าบทคัดย่อไม่ได้จัดชิดซ้าย: {shown}",
            "ชื่อเรื่องบนหน้าบทคัดย่อต้องจัดชิดซ้าย ไม่ใช่กึ่งกลางหรือชิดขวา",
            "แก้การจัดวางชื่อเรื่องบนหน้าบทคัดย่อให้ชิดซ้าย",
            "FORMAT.ABSTRACT_LAYOUT")


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


_TOC_ENTRY_TAIL = re.compile(
    rf'\s+{_TOC_PAGE_TOKEN}(?:\s*[-–—]\s*{_TOC_PAGE_TOKEN})?\s*$', re.I)


# "จุดไข่ปลา" (dot leader) ที่ลากเชื่อมชื่อหัวข้อกับเลขหน้า เป็นเส้นประของ template
# ไม่ใช่ตัวอักษรของหัวข้อ บางเล่มลากชนเลขหน้าโดยไม่มีช่องว่างคั่น
# ("CHAPTER 4 RESULTS.........45") ตัวอ่านบรรทัดสารบัญทุกตัวจึงต้องเห็นเป็นช่องว่าง
# เหมือนกันหมดตั้งแต่ต้นทาง ไม่งั้นพลาดเป็นลูกโซ่: อ่านชื่อบท/ชื่อหัวข้อติดจุดและ
# เลขหน้ามาด้วยแล้วฟ้องว่าสะกดผิด · หาเลขหน้าของรายการไม่เจอแล้วฟ้องว่าไม่ระบุเลขหน้า ·
# นับบรรทัดที่มีเลขหน้าไม่ถึงเกณฑ์แล้วมองไม่ออกว่าหน้านี้คือหน้าสารบัญ
#
# แทนที่เฉพาะชุดจุดที่ตามด้วยเลขหน้า (หรือไม่มีอะไรต่อ) แล้วจบบรรทัด จุดที่อยู่กลาง
# ชื่อหัวข้อและจุดในตัวย่อ ("U.S.") จึงไม่ถูกแตะ
_TOC_DOT_LEADER = re.compile(
    r'\s*(?:[.…]\s*){2,}'
    rf'(?=\s*(?:{_TOC_PAGE_TOKEN}(?:\s*[-–—]\s*{_TOC_PAGE_TOKEN})?)?\s*$)',
    re.I)


def space_dot_leader(text):
    """แทนจุดไข่ปลาด้วยช่องว่างเดียว เพื่อให้เลขหน้าแยกออกจากชื่อหัวข้อเสมอ"""
    return _TOC_DOT_LEADER.sub(' ', soft(text)).strip()


def looks_like_contents_page(text, min_entries=5):
    """หน้านี้เป็น "รายการสารบัญ" หรือไม่ — ดูจากสิ่งที่ควรอยู่บนหน้าสารบัญ

    ใช้แยกหน้าสารบัญออกจากหน้าหัวข้อจริง เพราะบรรทัดในสารบัญมีรูปเดียวกับหัวข้อที่มี
    เลขหน้าติดมาท้ายบรรทัดเป๊ะ ๆ ("ACKNOWLEDGEMENTS iii") ถ้าไม่แยก การยอมตัดเลขหน้า
    ท้ายบรรทัดจะทำให้หน้าสารบัญถูกนับเป็นหน้ากิตติกรรมประกาศ

    วัดกับเล่มจริงสามเล่ม: หน้าสารบัญได้ 13-20 บรรทัด หน้าสารบัญตาราง/รูปได้ 3-6
    ส่วนหน้าปก หน้าลงนาม หน้ากิตติกรรมประกาศ และหน้าบทคัดย่อได้ 0-1 บรรทัด
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    return sum(1 for line in lines
               if _TOC_ENTRY_TAIL.search(space_dot_leader(line))) >= min_entries


def _strip_toc_page_number(text):
    s = space_dot_leader(text)
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
    spaced = space_dot_leader(text)
    match = re.search(rf'\s({_TOC_PAGE_TOKEN})\s*[-–—]\s*{_TOC_PAGE_TOKEN}\s*$',
                      spaced, re.I) or \
        re.search(r'\s(\d{1,4}|[ivxlcdm]+|[ก-ฮ])\s*$', spaced, re.I)
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


# คำที่ใช้บอกว่า "บรรทัดนี้คือหัวข้อเดิมที่ยกมาต่อ" ไม่ใช่หัวข้อใหม่
# เทียบด้วย norm() เพราะ PDF ทำวรรณยุกต์ของ "ต่อ" หลุดเป็นประจำ
_CONTINUATION_WORDS = {norm("ต่อ"), "CONT", "CONTD", "CONTINUED", "CONTINUE"}
_TRAILING_PAREN = re.compile(r"\(([^()]*)\)\s*$")


def is_continuation_heading(line):
    """บรรทัดสารบัญ/หัวบทนี้เป็นบรรทัด "(ต่อ)" ของหัวข้อเดิมหรือไม่

    เจ้าหน้าที่กำหนด (ส.ค. 2569): บางเล่มเขียนสารบัญเป็น

        บทที่ 3 วิธีดำเนินการวิจัย        21
        บทที่ 3 วิธีดำเนินการวิจัย (ต่อ)   25

    สองบรรทัดนี้คือ **บทที่ 3 บทเดียว** ไม่ผิด ถ้านับเป็นสองบทจะพังสองทาง:
    จำนวนบทในสารบัญเกินจากเนื้อหา (ฟ้องผิด) และ toc_map เก็บบรรทัดหลังไว้
    ทำให้ชื่อบทที่เอาไปเทียบกลายเป็น "วิธีดำเนินการวิจัย (ต่อ)" (ฟ้องผิดอีกข้อ)

    ตัดเลขหน้า/จุดไข่ปลาท้ายบรรทัดออกก่อน เพราะคำว่า (ต่อ) ไม่ได้อยู่ท้ายบรรทัดเสมอ
    ("บทที่ 3 วิธีดำเนินการวิจัย (ต่อ) 25")
    """
    head = _strip_toc_page_number(line or "")
    found = _TRAILING_PAREN.search(head)
    return bool(found and norm(found.group(1)) in _CONTINUATION_WORDS)


def without_continuation(line):
    """ตัดคำว่า "(ต่อ)" / "(Cont.)" / "(CONT)" ท้ายหัวข้อออก

    เจ้าหน้าที่กำหนด (ก.ย. 2569): "สารบัญ" กับ "สารบัญ (ต่อ)" และ
    "TABLE OF CONTENTS" กับ "TABLE OF CONTENTS (Cont.)" คือหัวข้อเดียวกัน
    """
    head = _strip_toc_page_number(line or "")
    found = _TRAILING_PAREN.search(head)
    if found and norm(found.group(1)) in _CONTINUATION_WORDS:
        return head[:found.start()].strip()
    return head


def is_toc_heading(line):
    """บรรทัดนี้เป็นหัวข้อหน้าสารบัญไหม (นับหน้าต่อด้วย)

    ต้องนับหน้าต่อเป็นหน้าสารบัญ ไม่งั้นรายการที่ตกไปอยู่หน้าถัดไป (มักเป็น
    ภาคผนวกกับประวัติผู้วิจัย) จะถูกฟ้องว่า "ไม่พบหัวข้อ ... ในสารบัญ" ทั้งที่พิมพ์ไว้ครบ
    """
    return norm(without_continuation(line)) in N_TOC


def _toc_section_kind(text):
    """Classify one non-chapter TOC entry using its visible heading."""
    normalized = norm(_strip_toc_page_number(text))
    if normalized in N_ACK:
        return "ack"
    if normalized.startswith(norm("บทคัดย่อภาษาอังกฤษ")):
        return "abstract_en"
    if normalized == N_ABSTRACT_TH or normalized.startswith(norm("บทคัดย่อภาษาไทย")):
        return "abstract_th"
    if any(normalized.startswith(term) for term in N_ABSTRACT_TH_EN):
        return "abstract_th"
    if normalized == "ABSTRACT" or any(normalized.startswith(term)
                                       for term in N_ABSTRACT_EN_EN):
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


def abstract_page_label(start_idx, abs_en_pages, abs_th_pages):
    """ชื่อหน้าบทคัดย่อพร้อมภาษา เช่น "บทคัดย่อภาษาไทย"

    เล่มหลักสูตรไทยมีบทคัดย่อสองหน้า (ไทยและอังกฤษ) ถ้าตำแหน่งเขียนแค่ "บทคัดย่อ"
    เจ้าหน้าที่แยกไม่ออกว่าข้อไหนเป็นของหน้าไหน โดยเฉพาะเวลาสองหน้าฟ้องข้อความ
    เหมือนกัน (เล่มทดสอบ 3 ได้ "บทคัดย่อ (หน้า ง)" กับ "บทคัดย่อ (หน้า จ)")

    ใช้คำว่า "บทคัดย่อไทย" ไม่ใช่ "บทคัดย่อภาษาไทย" — สองเหตุผล: เป็นคำเดียวกับที่
    ระบบใช้อยู่แล้วใน _check_degree_abbr และเลี่ยงชนกับ classify() ที่จัดข้อความซึ่งมี
    คำว่า "ภาษาไทย"/"ภาษาอังกฤษ" เข้าหมวด "ภาษาไม่ครบตามหลักสูตร" ซึ่งจะทำให้
    ข้อของหน้าบทคัดย่อทุกข้อติดหมวดผิด

    รับ "หน้าเริ่ม" ของบทคัดย่อ ไม่ใช่หน้าใดก็ได้ในช่วง เพราะสองรายการนี้เก็บเฉพาะ
    หน้าเริ่ม — ผู้เรียกที่วนทีละหน้าในช่วงต้องส่งหน้าเริ่มของช่วงนั้นมา
    """
    if start_idx in set(abs_en_pages or ()):
        return "บทคัดย่ออังกฤษ"
    if start_idx in set(abs_th_pages or ()):
        return "บทคัดย่อไทย"
    return "บทคัดย่อ"


def _is_abstract_heading(text):
    """หัวเรื่อง 'บทคัดย่อ'/'ABSTRACT' เป็นตัวหนาตาม template อยู่แล้ว ไม่ใช่ข้อสังเกต"""
    nl = norm(_strip_toc_page_number(text))
    return (nl == 'ABSTRACT'
            or nl in N_ABSTRACT_TH_EN or nl in N_ABSTRACT_EN_EN
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


# บรรทัดสารบัญที่มีแต่ "CHAPTER 2" ไม่มีชื่อบท — _strip_toc_page_number อ่านเลขบท
# เป็นเลขหน้าแล้วตัดทิ้ง เหลือคำว่า CHAPTER ลอย ๆ ซึ่งไม่ใช่ชื่อบท ห้ามเอาไปแสดง
_TOC_CHAPTER_WORD_ONLY = re.compile(r'^(?:CHAPTER|บทท)\d*$', re.I)


def _toc_chapter_label(number, entries):
    """ป้ายบทที่ซ้ำ พร้อมชื่อบทตามที่เล่มพิมพ์ไว้ในสารบัญ

    เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าต้องบอกชื่อบทด้วย ไม่ใช่บอกแค่เลขบท เพราะสารบัญ
    ที่พิมพ์ซ้ำต้องเปิดไล่หาว่าบรรทัดไหน — ชื่อบทอ่านจากบรรทัดในสารบัญของเล่มเอง
    เล่มไทยจึงได้ชื่อไทย เล่มอังกฤษได้ชื่ออังกฤษ โดยไม่ต้องเดาภาษาของเล่ม
    ครอบด้วยเครื่องหมายคำพูดเพราะเป็น "ค่าที่อ่านได้จากเล่ม" ห้ามแปลเป็นอังกฤษ
    ถ้ารายการซ้ำใช้ชื่อคนละชื่อ ให้ยกมาทุกชื่อ เจ้าหน้าที่จะได้รู้ว่าซ้ำแบบไหน
    """
    titles = []
    for entry in entries:
        if entry[0] != number:
            continue
        title = soft(_toc_chapter_title(entry[3]))
        if _TOC_CHAPTER_WORD_ONLY.match(norm(title)):
            continue
        if title and title not in titles:
            titles.append(title)
    if not titles:
        return f"บทที่ {number}"
    return f'บทที่ {number} "' + '" / "'.join(titles) + '"'


# ---------- normalized heading keys ----------
N_ABSTRACT_TH = norm('บทคัดย่อ')
# หัวข้อบทคัดย่อ "ภาษาอังกฤษ" ของเล่มสองภาษา — template เขียน ABSTRACT (THAI)
# แต่เล่มจริงเขียน ABSTRACT IN THAI ด้วย (สำรวจ 11 เล่ม พบทั้งสองแบบ) ต้องรู้จักทั้งคู่
# ไม่งั้นสารบัญที่มีรายการนี้อยู่จริงจะถูกฟ้องว่า "ไม่พบหัวข้อบทคัดย่อภาษาไทยในสารบัญ"
# และหัวเรื่องบนหน้าถูกฟ้องเป็น "ข้อความตัวหนาที่ไม่ใช่หัวข้อ" ทั้งที่เป็นหัวข้อจริง
#
# ต่างจาก N_TOC_WRONG ตรงที่ "ไม่มีข้อฟ้องตามหลัง" — เจ้าหน้าที่ตัดสิน (ก.ย. 2569) ว่า
# ABSTRACT IN THAI ถือเป็นเรื่องเดียวกับ ABSTRACT (THAI) ปล่อยผ่านได้ ส่วน CONTENTS
# ยังต้องฟ้องให้แก้เป็น TABLE OF CONTENTS ความต่างนี้ตั้งใจ ห้ามไล่ให้เหมือนกัน
N_ABSTRACT_TH_EN = ('ABSTRACTTHAI', 'ABSTRACTINTHAI')
N_ABSTRACT_EN_EN = ('ABSTRACTENGLISH', 'ABSTRACTINENGLISH')
N_ACK = [norm('กิตติกรรมประกาศ'), 'ACKNOWLEDGEMENT', 'ACKNOWLEDGEMENTS']
# หัวข้อสารบัญตาม template คือ "TABLE OF CONTENTS" / "สารบัญ" เท่านั้น
# ส่วน CONTENT / CONTENTS เป็นคำที่เล่มจริงพิมพ์ผิดมา ต้องรู้จักไว้เพื่อ "หาหน้าสารบัญเจอ"
# (ไม่งั้นการตรวจสารบัญทั้งชุดเงียบไปทั้งเล่ม แล้วยังฟ้องผิดว่า "ไม่พบหน้าสารบัญ")
# แล้วค่อยฟ้องแยกว่าให้แก้หัวข้อเป็น TABLE OF CONTENTS
TOC_HEADING_CANONICAL = 'TABLE OF CONTENTS'
N_TOC_WRONG = ['CONTENTS', 'CONTENT']
N_TOC = [norm('สารบัญ'), 'TABLEOFCONTENTS'] + N_TOC_WRONG
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


def canonical_title_wrapped(title, next_line, candidates):
    """ชื่อบทถูกตัดขึ้นบรรทัดใหม่จริงไหม — บรรทัดถัดไปต่อให้ครบชื่อในประกาศพอดีหรือเปล่า

    หัวบทยาวถูกตัดขึ้นบรรทัดใหม่ได้จริง จึงต้องผ่อนให้ แต่ต้องผ่อนโดยดูของจริงว่า
    "บรรทัดถัดไปต่อให้ครบไหม" ไม่ใช่ยอมรับทุกชื่อที่เป็นต้นของชื่อในประกาศ

    ของเดิมยอมรับทุก prefix เล่มที่พิมพ์บทที่ 6 ว่า "CONCLUSION" เฉย ๆ จึงหลุด
    (ประกาศให้เป็น "CONCLUSION AND RECOMMENDATIONS") ทั้งที่บรรทัดถัดไปคือ
    "6.1 Conclusions" ซึ่งเป็นหัวข้อย่อย ไม่ใช่ส่วนที่เหลือของชื่อบท
    วัดกับเล่มจริง 11 เล่ม ข้อผ่อนผันแบบเดิมทำงานครั้งเดียว คือครั้งที่ปล่อยเล่มผิดให้ผ่าน
    """
    base = norm(title)
    joined = norm(f"{title} {_strip_toc_page_number(next_line)}")
    if not base or joined == base:
        return False
    return any(norm(candidate) == joined for candidate in candidates)


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


# ระดับความตรงกับประกาศ — ตรงเป๊ะ > ตัวสะกดที่คู่มือยอมรับ > ไม่ตรงเลย
_TITLE_RANK = {'exact': 2, 'variant': 1, 'wrong': 0}


def _correctly_spelled_side(body_title, toc_title, chapter_no, option):
    """ฝั่งไหนสะกดชื่อบทถูกตามประกาศ — คืน 'body' | 'toc' | None ถ้าบอกไม่ได้

    ใช้ตอนสารบัญกับเนื้อหาไม่ตรงกัน เพื่อไม่ให้ระบบสั่งแก้ฝั่งที่ถูกอยู่แล้ว
    เล่มจริงพิมพ์สารบัญว่า "LITURATURE REVIEW" ส่วนเนื้อหาว่า "LITERATURE REVIEW"
    ของเดิมยึดสารบัญเป็นหลักเสมอ จึงบอกให้แก้เนื้อหาเป็นคำที่สะกดผิด

    ใช้ได้แม้ในโหมดยกเว้นบท เพราะโหมดนั้นแค่ไม่บังคับว่าต้อง "ใช้ชื่อตามประกาศ"
    ไม่ได้แปลว่าปล่อยให้สะกดผิดได้ — ถ้าฝั่งหนึ่งตรงประกาศมากกว่า อีกฝั่งคือฝั่งที่ผิด

    เทียบกันด้วย "ระดับความตรงกับประกาศ" ไม่ใช่แค่ผ่าน/ไม่ผ่าน จึงตัดสินได้ด้วยว่า
    ฝั่งที่ตรงประกาศเป๊ะ ชนะฝั่งที่เป็นเพียงตัวสะกดที่คู่มือยอมรับ
    ถ้าทั้งสองฝั่งอยู่ระดับเดียวกัน = ประกาศชี้ขาดไม่ได้ จึงไม่ชี้ว่าฝั่งไหนผิด
    """
    try:
        body_rank = _TITLE_RANK[canonical_title_status(body_title, chapter_no, option)[0]]
        toc_rank = _TITLE_RANK[canonical_title_status(toc_title, chapter_no, option)[0]]
    except (IndexError, KeyError):
        return None
    if body_rank == toc_rank:
        return None
    return 'body' if body_rank > toc_rank else 'toc'


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


# หัวบทที่เขียนเลขเป็นอารบิกล้วน — norm() ตัดช่องว่างกับจุดทิ้งไปแล้ว
# เลขไทย (๑-๙) รอดจาก norm() เพราะอยู่ในช่วง ก-๙ จึงต้องระบุ 0-9 ให้ชัด
_ARABIC_CHAPTER_HEAD = re.compile(r'(?:CHAPTER|บทท)[0-9]{1,2}')


def chapter_number_is_arabic(line):
    """หัวบทบรรทัดนี้ใช้เลขอารบิกไหม — "CHAPTER II" กับ "บทที่ ๒" คืน False

    เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าหัวบทต้องใช้เลขอารบิกเสมอ **ทั้งเล่มไทยและเล่มอังกฤษ**
    เล่มไทยก็ใช้เลขอารบิก ไม่ใช่เลขไทย

    ระบบรู้จักเลขโรมันกับเลขไทยไว้เพื่อ "หาบทให้เจอ" อยู่แล้ว (ดู _chapter_match)
    แต่เดิมรู้จักแล้วเงียบ ไม่เคยฟ้อง — เล่มจริงเล่มหนึ่งพิมพ์
    CHAPTER I, II, III, IV, 5, VI คือปนกันเองด้วยซ้ำ แล้วรายงานไม่มีข้อนี้เลย
    (กติกาเดียวกับ N_TOC_WRONG: รู้จักไว้ให้การตรวจเดินต่อได้ แล้วค่อยฟ้องแยก)
    """
    return bool(_ARABIC_CHAPTER_HEAD.fullmatch(norm(line)))


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
    # กฎ "ลำดับ/ตำแหน่งของส่วนประกอบ" จัดหมวดจากรหัสกฎ ไม่ใช่จากคำในข้อความ
    #
    # ข้อความของกฎพวกนี้เป็น "รายการชื่อส่วนทั้งเล่ม" จึงมีคำที่กฎข้างล่างจับได้เต็มไปหมด
    # (เช่น "บทคัดย่อภาษาไทย" ทำให้ลำดับส่วนนำตกไปหมวดภาษาไม่ครบตามหลักสูตร) และการที่
    # หน้าปกไม่ได้อยู่แผ่นแรกก็ไม่มีคำไหนตรงเลย เลยตกหมวด "อื่นๆ" ทั้งที่เป็นเรื่อง
    # โครงสร้างเล่มชัด ๆ
    if issue.get("rule_id") in ("FRONT.COVER_FIRST", "FRONT.ORDER", "END.STRUCTURE"):
        return "โครงสร้างเล่ม"
    # "หน้านี้มีชื่อเรื่องอีกภาษาอยู่ด้วย" ไม่ใช่ "ชื่อเรื่องไม่ตรง บฑ.1" — ชื่อเรื่องตรงทุกตัว
    # แต่มีชื่อเรื่องเกินมาอีกอัน ถ้าปล่อยให้ตกหมวดเดิมตามคำว่า "ชื่อเรื่อง" เจ้าหน้าที่จะ
    # ไปไล่เทียบตัวอักษรกับ บฑ.1 ทั้งที่วิธีแก้คือลบข้อความออก
    if issue.get("rule_id") == "FRONT.TITLE_ONE_LANGUAGE":
        return "ภาษาของชื่อเรื่อง"
    # "เล่มทำผิดภาษา" ไม่ใช่ "ภาษาไม่ครบตามหลักสูตร" (ซึ่งแปลว่าเล่มขาดบทคัดย่ออีกภาษา)
    if issue.get("rule_id") == "FORM.BOOK_LANGUAGE":
        return "ภาษาของเล่ม"
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
    # จำนวน/รายชื่ออาจารย์ที่ไม่ตรงฟอร์มต้นทาง เป็นเรื่อง "ข้อมูลไม่ตรงที่อนุมัติไว้"
    # ไม่ใช่โครงสร้างเล่ม ต้องมาก่อนกฎโครงสร้างข้างล่าง
    if "รายชื่อ" in text and "อนุมัติ" in text:
        return "ไม่ตรงข้อมูลอนุมัติ"
    # หน้าลงนามถูกตั้งชื่อตามบทบาทของหน้า ไม่ได้เขียนคำว่า "หน้าลงนาม" ตรง ๆ เสมอ
    # (หน้าอาจารย์ที่ปรึกษา / หน้ากรรมการสอบ / ช่องประธานหลักสูตร / ช่องคณบดีคณะ)
    # ถ้าจับแค่คำว่า "หน้าลงนาม" ข้อของสองหน้านี้จะตกไปหมวด "อื่นๆ" ทั้งที่เป็น
    # เรื่องโครงสร้างเล่มเหมือนกัน — ปัญหาเดียวกับที่ SUMMARY_SECTIONS เคยเจอ
    if ("สารบัญ" in text or "บท" in text or "BIOGRAPHY" in text
            or re.search(r"หน้าลงนาม|อาจารย์ที่ปรึกษา|กรรมการสอบ|ประธานหลักสูตร|คณบดี",
                         text)):
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
        สรุปที่ส่งให้นักศึกษา (เช่น ช่องข้อมูลอนุมัติที่เจ้าหน้าที่ยังไม่ได้กรอก ระบบจึง
        ข้ามการเทียบ — เป็นเรื่องของการกรอกฟอร์ม นักศึกษาทำอะไรกับเล่มก็ไม่หาย)
        """
        rule_id = rule_id or DEFAULT_RULE_BY_PART.get(part, "FORM.REQUIRED")
        # ไม่เติม "แก้ไขให้เป็นไปตามข้อกำหนด: <expected>" อัตโนมัติอีกแล้ว — มันคือการ
        # พูดซ้ำบรรทัด "ควรเป็น" ที่อยู่เหนือมันคำต่อคำ ทำให้การ์ดยาวขึ้นโดยไม่ได้ข้อมูล
        # เพิ่ม ข้อที่มีวิธีแก้จริงจะส่ง fix มาเอง ข้อที่ไม่ส่งก็ไม่ต้องมีบรรทัดนี้
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


def front_section_kind(page_text):
    """หน้าส่วนนำหน้านี้เป็นชนิดไหน — ตัดสินจากเนื้อความบนหน้า ไม่ใช่บรรทัดเป๊ะบรรทัดเดียว

    คืน (ชนิด, บรรทัดหัวข้อที่พิมพ์จริง) ชนิดที่เป็นไปได้คือ
    signature / abstract_th / abstract_en / ack / toc / list และ "" เมื่อไม่ใช่ทั้งหมด

    รองรับหัวข้อที่มีเลขหน้าติดมาท้ายบรรทัด ("ACKNOWLEDGEMENTS iii") เพราะ PDF
    บางเล่มดึงหัวกระดาษมารวมกับบรรทัดหัวข้อ วัดกับเล่มจริงแล้วพบว่าถ้าไม่รองรับ
    เล่มที่ผ่านสะอาดจะกลายเป็นไม่ผ่านทันที 4 ข้อ (ไม่พบกิตติกรรมประกาศ ไม่พบสารบัญ
    ไม่พบบทคัดย่อ และภาคผนวกไม่อยู่ในสารบัญ) ทั้งที่เล่มถูกทุกอย่าง

    แต่ห้ามใช้รูปแบบที่ตัดเลขหน้าแล้วกับ "หน้าที่เป็นรายการสารบัญ" เพราะทุกบรรทัด
    ในสารบัญมีรูปเดียวกันเป๊ะ หน้าสารบัญจะถูกนับเป็นหน้ากิตติกรรมประกาศแทน
    """
    tls = top_lines(page_text, 12)
    nls = [norm(l) for l in tls]
    bare = [norm(_strip_toc_page_number(l)) for l in tls]
    alt = nls if looks_like_contents_page(page_text) else bare
    if any(x in N_ENTITLED for x in nls) or any(x in N_ENTITLED for x in alt):
        return "signature", soft(tls[0]) if tls else ""
    # สแกนหัวเรื่องให้ลึกพอ — เล่มที่ชื่อเรื่องยาว 3-4 บรรทัด คำว่า ABSTRACT
    # จะไปอยู่บรรทัดที่ 9-10 ของหน้า ถ้าสแกนตื้นจะหาหน้าบทคัดย่อไม่เจอ
    for j, nl in enumerate(nls[:12]):
        al = alt[j]
        if N_ABSTRACT_TH in (nl, al) or any(x in N_ABSTRACT_TH_EN for x in (nl, al)):
            return "abstract_th", soft(tls[j])
        if 'ABSTRACT' in (nl, al) or any(re.match(r'^ABSTRACT\(', x) for x in (nl, al)):
            return "abstract_en", soft(tls[j])
        if nl in N_ACK or al in N_ACK:
            return "ack", soft(tls[j])
        # หัวข้อสารบัญยอมให้ตัดเลขหน้าได้เสมอ แม้บนหน้าที่เป็นรายการสารบัญเอง
        # เพราะสารบัญไม่เคยมีบรรทัดที่ชื่อว่า "สารบัญ" อยู่ในรายการของตัวเอง
        # หัวข้อสารบัญยอมให้ตัดเลขหน้าได้เสมอ และยอมให้มีคำว่า "(ต่อ)" ต่อท้ายด้วย
        if nl in N_TOC or bare[j] in N_TOC or is_toc_heading(tls[j]):
            return "toc", soft(tls[j])
        if nl in N_LISTS or al in N_LISTS:
            return "list", soft(tls[j])
    return "", ""


def check_result(rep, context=None, not_checked=NOT_CHECKED):
    """ผลตรวจหนึ่งชุด — **ทุกทางออกของ run_check ต้องผ่านฟังก์ชันนี้**

    หน้ารายงานอ่านคีย์ชุดนี้ครบทุกตัวเสมอ ทางออกไหนตกคีย์ไปหน้ารายงานพัง 500
    โดยตัวตรวจเองไม่มีอะไรฟ้องเลย เจอจริงตอนใช้งาน: ด่าน "ภาษาของเล่มไม่ตรงกับที่
    อนุมัติ" คืน dict ที่คัดลอกคีย์มาจาก result ตัวหลัก แต่ result ตัวหลักเติม
    plain_summary ทีหลัง (นอก dict literal) คีย์นั้นจึงหายไป แล้ว report.html พังที่
    {{ report.plain_summary | tojson }} — เล่มที่ภาษาไม่ตรงเปิดรายงานไม่ได้เลย

    ต้องเรียงข้อและจัดหมวดที่นี่ด้วย ไม่ใช่ให้ผู้เรียกทำเอง ด้วยเหตุผลเดียวกัน
    """
    for zone in rep.zones:
        for issue in rep.zones[zone]:
            issue["category"] = classify(issue)
            issue["section"] = summary_section(issue)
        # เรียงตามลำดับที่เจ้าหน้าที่ไล่แก้เล่มจริง (ส่วนประกอบตามลำดับ แล้วเลขหน้า)
        rep.zones[zone].sort(key=issue_sort_key)
    result = {
        "context": dict(context or {}),
        "verdict": rep.verdict(),
        "summary": {z.lower(): len(v) for z, v in rep.zones.items()},
        "issues_by_zone": rep.zones,
        "info": rep.info,
        "human_checklist": rep.human_checklist,
        "not_checked": not_checked,
        "verification": rep.verification,
        # หน้ารายงานจัดกลุ่มการ์ดตามลำดับนี้ (Jinja groupby เรียงตามตัวอักษร ใช้ไม่ได้)
        "section_order": SUMMARY_SECTION_ORDER,
        # ส่งไปทั้งก้อนเพื่อให้หน้ารายงานสร้างปุ่มเอง และใช้ถ้อยคำอังกฤษชุดเดียวกัน
        # ตอนสลับภาษา — ไม่ใช่ให้หน้าเว็บเก็บถ้อยคำของตัวเองแล้วหลุดจากฝั่งเซิร์ฟเวอร์
        "staff_findings": list(STAFF_CHECKS),
    }
    result["plain_summary"] = plain_summary(result)
    return result


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
        return check_result(rep)

    _p("เปิดไฟล์ PDF")
    pages = []
    header_extras = []   # ข้อความอื่นในหัวกระดาษต่อหน้า (นอกจากเลขหน้า)
    font_damaged = []          # ดัชนีหน้าที่ฟอนต์ในไฟล์ทำให้อ่านข้อความเพี้ยน
    with pdfplumber.open(pdf_path) as _pdf:
        n = len(_pdf.pages)
        if n == 0:
            rep.add("ORANGE", "-", Path(pdf_path).name, "ไฟล์ PDF ไม่มีหน้าเอกสาร",
                    "ต้องเป็น PDF ที่มีเนื้อหาอย่างน้อย 1 หน้า", "สร้างไฟล์ PDF ใหม่แล้วลองอีกครั้ง")
            return check_result(rep, {"n_pages": 0})
        for _i, _pg in enumerate(_pdf.pages):
            if _i % 5 == 0 or _i == n - 1:
                _p(f"อ่านข้อความแบบละเอียด (หน้า {_i+1}/{n})")
            pages.append(_page_text(_pg))
            try:
                if font_damage_score(_pg, pages[-1]):
                    font_damaged.append(_i)
            except Exception:
                pass
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
    toc_heading_wrong = []      # (ดัชนีหน้า, หัวข้อที่เล่มพิมพ์) เมื่อไม่ใช่ TABLE OF CONTENTS
    for i in range(front_limit):
        kind, heading = front_section_kind(pages[i])
        if kind == "signature":
            sig_pages.append(i)
        elif kind == "abstract_th":
            abs_th_pages.append(i)
        elif kind == "abstract_en":
            abs_en_pages.append(i)
        elif kind == "ack":
            ack_pages.append(i)
        elif kind == "toc":
            toc_pages.append(i)
            # ถ้อยคำผิดคือปัญหาเดียว ไม่ใช่ปัญหาละหน้า — หน้าต่อของสารบัญที่เขียน
            # "CONTENTS (ต่อ)" ต้องไม่ถูกฟ้องซ้ำอีกข้อ จึงฟ้องเฉพาะหน้าแรกของสารบัญ
            if norm(without_continuation(heading)) in N_TOC_WRONG and not toc_pages[:-1]:
                toc_heading_wrong.append((i, heading))
        elif kind == "list":
            list_pages.append(i)

    abs_th_idx = abs_th_pages[0] if abs_th_pages else None
    abs_en_idx = abs_en_pages[0] if abs_en_pages else None
    has_th_abs, has_en_abs = abs_th_idx is not None, abs_en_idx is not None

    # ไฟล์ eThesis เป็นของนักศึกษาคนเดียวกับเล่มไหม — ต้องรู้ก่อนกฎอื่นที่ใช้ข้อมูลอนุมัติ
    # (รวมถึงกฎชนิดเลขหน้าส่วนนำ ที่อ่าน program_language จากข้อมูลอนุมัติ)
    same_student, sig_checked, _sig_found = (
        ethesis_matches_book(approved, pages)
        if approved and not skip_identity_check else (True, [], []))
    # ---------- ภาษาของเล่มต้องตรงกับที่ได้รับอนุมัติ ----------
    # ต้องมาก่อน rep.add ตัวแรก เพราะเมื่อเล่มทำผิดภาษา กฎอื่นแทบทุกข้อจะฟ้องพร้อมกัน
    # หมด (ข้อความบังคับบนปก ประโยค template หน้าลงนาม ชนิดเลขหน้า ชื่อบททุกบท)
    # วัดกับเล่มจริง: เล่มไทยที่จับคู่กับข้อมูล "เล่มอังกฤษ" ได้แดง 24 ข้อ โดยไม่มีข้อไหน
    # บอกสาเหตุจริงเลย และบางข้ออ่านแล้วสับสน เช่นยกชื่อเรื่องมาอ้างว่าเป็นชื่อนักศึกษา
    cover_idx = find_cover_page(pages)
    cover_text = pages[cover_idx] if pages else ""
    want_language = (approved_book_language(approved)
                     if approved and same_student and not skip_identity_check else "")
    if want_language:
        language_signals = book_language_signals(pages, cover_idx, doc_type)
        found_language = book_language(language_signals)
        want_name = LANGUAGE_NAME[want_language]
        other_name = LANGUAGE_NAME["en" if want_language == "thai" else "thai"]
        must_be = (f'ภาษาที่ได้รับอนุมัติคือ "{want_name}" '
                   f'ต้องดำเนินการจัดทำเล่มเป็น "{want_name}"')
        wrong_parts = [BOOK_LANGUAGE_PARTS[key]
                       for key in ("cover", "signature", "chapter")
                       if language_signals[key] and language_signals[key] != want_language]
        for where, status, detail in book_language_rows(want_language, language_signals):
            rep.add_verification("ภาษาที่เขียน", where, status, detail)
        if found_language and found_language != want_language:
            # ทั้งเล่มผิดภาษา — เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้หยุดตรวจส่วนอื่นทั้งหมด
            # แล้วแจ้งจุดผิดข้อเดียว
            rep.add("RED", "front_matter", "ทั้งเล่ม",
                    "ภาษาในไฟล์รูปเล่มไม่ตรงกับที่ได้รับอนุมัติ "
                    f"เล่มที่ส่งมาจัดทำเป็น{LANGUAGE_NAME[found_language]}",
                    must_be,
                    "ตรวจว่าช่องหลักสูตรบนหน้าอัปโหลดถูกต้องหรือไม่ "
                    "ถ้าถูกต้องแล้วจึงส่งกลับให้นักศึกษาจัดทำเล่มใหม่",
                    "FORM.BOOK_LANGUAGE")
            return check_result(
                rep,
                {"document_type": doc_type, "option": None,
                 "chapters_mode": chapters_mode, "n_pages": n,
                 "approved_data": bool(approved)},
                (BOOK_LANGUAGE_STOPPED,) + tuple(NOT_CHECKED))
        if wrong_parts:
            # สัญญาณขัดกันเอง (เช่น ปกอังกฤษ แต่หัวบทเป็น "บทที่ 1") ตัดสินภาษาทั้งเล่ม
            # ไม่ได้ จึงไม่หยุดตรวจ แต่บางส่วนคนละภาษากับที่อนุมัติแน่นอน ต้องฟ้อง
            rep.add("RED", "front_matter", "ทั้งเล่ม",
                    "ภาษาในไฟล์รูปเล่มไม่ตรงกับที่ได้รับอนุมัติ "
                    f"ส่วนที่จัดทำเป็น{other_name}คือ {_join_and(wrong_parts)}",
                    must_be,
                    "ตรวจว่าช่องหลักสูตรบนหน้าอัปโหลดถูกต้องหรือไม่ "
                    "ถ้าถูกต้องแล้วจึงส่งกลับให้นักศึกษาจัดทำเล่มใหม่",
                    "FORM.BOOK_LANGUAGE")

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
        """ตำแหน่งที่เจ้าหน้าที่เปิดไปดูได้จริง

        ถ้าอ่านเลขหน้าที่พิมพ์ในเล่มไม่ได้ ห้ามเดาเลขจากลำดับหน้าใน PDF เพราะจะกลาย
        เป็นการรายงานเลขที่ไม่มีอยู่จริง แต่ต้องบอก "แผ่นที่เท่าไรของไฟล์" กำกับไว้
        ไม่งั้นเจ้าหน้าที่หาหน้านั้นไม่เจอ แล้วข้อนั้นก็ตรวจต่อไม่ได้ทั้งข้อ
        """
        label = page_labels.get(page_index, "")
        if label:
            return f"หน้า {label}"
        return f"หน้าไม่ระบุเลข (แผ่นที่ {page_index + 1} ของไฟล์)"

    def order_ref(page_index):
        """ตำแหน่งสำหรับกฎ "ลำดับ" — ต้องเป็นแผ่นในไฟล์ ไม่ใช่เลขหน้าที่พิมพ์

        กฎลำดับพูดถึงลำดับจริงในไฟล์ ส่วนเลขหน้าที่พิมพ์เป็นสิ่งที่เล่มผิดลำดับมัก
        พิมพ์มาผิดอยู่แล้ว ถ้ารายงานด้วยเลขที่พิมพ์ เล่มที่รวมไฟล์สลับกันโดยไม่ได้
        ใส่เลขใหม่จะได้ข้อความที่ดูขัดกับตัวเอง เช่น
            "ภาคผนวก (หน้า 60) แล้ว รายการอ้างอิง (หน้า 52)"
        ซึ่งอ่านแล้วเหมือนระบบเรียงผิดเอง ทั้งที่ระบบเรียงตามไฟล์ถูกแล้ว
        แผ่นที่ของไฟล์มีเสมอ ไม่ซ้ำ และเป็นตัวที่เจ้าหน้าที่ใช้เปิดไปดูใน PDF จริง
        """
        return f"แผ่นที่ {page_index + 1} ของไฟล์"

    # ---------- หน้าปกต้องเป็นแผ่นแรก ----------
    # (cover_idx / cover_text หาไว้ตั้งแต่ก่อนด่านภาษาของเล่มแล้ว)
    if cover_idx > 0:
        rep.add("RED", "front_matter", "หน้าปก",
                f"หน้าปกอยู่{order_ref(cover_idx)} ไม่ใช่แผ่นแรก",
                "หน้าปกต้องเป็นแผ่นแรกของไฟล์",
                "ลบหน้าที่อยู่ก่อนหน้าปกออก หรือย้ายหน้าปกขึ้นเป็นแผ่นแรก",
                "FRONT.COVER_FIRST")

    seq = sorted(printed.items())
    arabic_sequence_ok = bool(seq) and seq[0][1] == 1 and all(
        seq[k][1] == seq[k - 1][1] + 1 for k in range(1, len(seq))
    )
    if BODY_RULES['check_page_sequence']:
        if seq and seq[0][1] != 1:
            rep.add("RED", "body", page_ref(seq[0][0]), f"เลขหน้าอารบิกแรกที่พบคือ {seq[0][1]}",
                    "เลขหน้าอารบิกต้องเริ่มที่ 1 ณ บทที่ 1", "แก้การตั้งเลขหน้า", "PAGE.NUMBERING")
        for k in range(1, len(seq)):
            prev_idx, a = seq[k - 1]
            cur_idx, b = seq[k]
            # หน้าที่คั่นอยู่แต่อ่านเลขไม่ได้ ยังนับเป็นหน้าของเล่ม — ถ้าไม่นับ
            # เล่มที่พิมพ์ 71, (อ่านไม่ออก), 73 จะถูกฟ้องผิดว่า "กระโดด 71 ไป 73"
            # ทั้งที่หน้าที่คั่นคือ 72 พอดี (การตรวจเพี้ยนเพราะหาเลขหน้าไม่เจอ)
            gap = cur_idx - prev_idx - 1
            want = a + gap + 1
            if b != want:
                # ตำแหน่งคือหน้าที่พิมพ์เลขผิด ส่วน "พบ" บอกว่าหน้าก่อนหน้าเป็นเลขอะไร
                # (เดิมตำแหน่งเขียนว่า "ช่วงเลขหน้า 71 ถึง 70" ซึ่งพูดเรื่องเดียวกับ
                #  ข้อความ "เลขหน้ากระโดดจาก 71 ไป 70" ซ้ำสองรอบ)
                rep.add(PAGE_SEQUENCE_ZONE, "body/end", page_ref(cur_idx),
                        f"เลขหน้าไม่ต่อเนื่อง หน้าก่อนหน้านี้พิมพ์เลข {a}",
                        f"หน้านี้ต้องเป็นหน้า {want}", "",
                        "PAGE.NUMBERING_SEQUENCE")
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
            f"{page_ref(run[0])} ถึง {page_ref(run[-1])} ({len(run)} หน้า)"
        image_like = any(not (pages[i] or '').strip() for i in run)
        kind = ("ไม่มีข้อความให้ดึงเลย อาจเป็นหน้ารูปภาพ/สแกน เช่น ภาคผนวก"
                if image_like else "มีเฉพาะเลขหน้า อาจเป็นหน้าว่างที่ตั้งใจเว้น")
        if all(i in printed for i in run) and arabic_sequence_ok:
            rep.add(BLANK_PAGE_ZONE, "body/end", run_ref,
                    f"ระบบดึงข้อความจากหน้านี้ไม่ได้ ({kind}) แต่เลขหน้าเรียงต่อเนื่องถูกต้อง",
                    "หน้าลักษณะนี้ที่การเรียงเลขหน้ายังคงถูกต้องเป็นข้อสังเกตและผ่านได้",
                    "ตรวจว่าเป็นหน้าภาพหรือหน้าว่างที่ตั้งใจเว้นไว้", "PAGE.BLANK")
        else:
            rep.add(UNCERTAIN_ZONE, "-", run_ref,
                    f"ระบบดึงข้อความจากหน้านี้ไม่ได้ ({kind}) และยืนยันลำดับเลขหน้าไม่ได้",
                    "เจ้าหน้าที่ตรวจสอบว่าเป็นหน้าภาพ/หน้าว่าง และเลขหน้ายังเรียงถูกต้อง",
                    "ตรวจด้วยตา", "UNCERTAIN.REVIEW")

    # เลขหน้าลงนาม i/ii หรือ ก/ข
    if len(sig_pages) != 2:
        rep.add(FRONT_FAILURE_ZONE, "front_matter", "หน้าลงนาม",
                f"พบหน้าลงนาม {len(sig_pages)} หน้า", "ต้องมี 2 หน้า (Advisory + Examination)",
                "ตรวจด้วยตา", "FRONT.APPROVAL")
    _report_signature_page_labels(rep, sig_pages, pages, page_ref)

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
    # บรรทัดถัดไปเก็บไว้ด้วย เพราะชื่อบทยาวถูกตัดขึ้นบรรทัดใหม่ได้ ต้องดูของจริง
    # ไม่ใช่ยอมรับทุกชื่อที่ "เป็นต้นของชื่อมาตรฐาน" (ดู _title_status)
    toc_ch = []   # (chap_no, title_norm, page_no, raw_line, source_page_idx, next_line)
    for _k, (source_page_idx, line) in enumerate(toc_lines):
        next_toc_line = toc_lines[_k + 1][1].strip() if _k + 1 < len(toc_lines) else ""
        raw = line.strip()
        if not raw:
            continue
        # "บทที่ 3 ... (ต่อ)" คือบทเดิมที่ยกมาต่อ ไม่ใช่บทใหม่ (ดู is_continuation_heading)
        if is_continuation_heading(raw):
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
                       source_page_idx, next_toc_line))

    body_ch = []  # (chap_no, title_raw, pdf_idx, printed_no, next_line)
    non_arabic_heads = []   # (เลขบท, บรรทัดหัวบทตามที่พิมพ์, ดัชนีหน้า)
    for i, t in enumerate(pages):
        tls = top_lines(t, BODY_RULES['heading_scan_lines'])
        for j, l in enumerate(tls):
            cn = _chapter_match(l)
            if cn is not None and j + 1 < len(tls):
                title = tls[j+1]
                # หัวบทที่เขียน "(ต่อ)" เป็นหน้าถัดไปของบทเดิม ไม่ใช่บทใหม่
                if is_continuation_heading(l) or is_continuation_heading(title):
                    break
                if not re.match(r'\d', title):
                    body_ch.append((cn, title, i, printed.get(i),
                                    tls[j + 2] if j + 2 < len(tls) else ""))
                    if not chapter_number_is_arabic(l):
                        non_arabic_heads.append((cn, soft(l), i))
                break
    # ฟ้อง "รายบท" ตามที่เจ้าหน้าที่สั่ง (ก.ย. 2569) ไม่รวมเป็นข้อเดียว — แต่ละบท
    # ต้องไปแก้คนละหน้า และเล่มจริงปนกันเองได้ (I, II, III, IV, 5, VI)
    for _cn, _head, _idx in non_arabic_heads:
        _want = f"CHAPTER {_cn}" if norm(_head).startswith("CHAPTER") else f"บทที่ {_cn}"
        rep.add("RED", "body", f"บทที่ {_cn} ({page_ref(_idx)})",
                f'หัวบทเขียนว่า "{_head}"',
                f'หัวบทต้องใช้เลขอารบิก คือ "{_want}"',
                "แก้เลขบทให้เป็นเลขอารบิก", "BODY.CHAPTER_NUMBER")
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
        # สารบัญที่พิมพ์บทซ้ำ ต้องบอกว่าซ้ำบทไหนและอยู่หน้าไหน — ของเดิมบอกแค่ว่า
        # "สารบัญมี 8 บท เนื้อหามี 6 บท" เจ้าหน้าที่เปิดเล่มเห็น 6 บทตามที่ควรเป็น
        # เลยนึกว่าระบบนับผิด ทั้งที่สารบัญพิมพ์ CHAPTER 2 กับ CHAPTER 3 ซ้ำจริง
        # (เล่มจริง ก.ย. 2569 — เรนเดอร์หน้า ix ออกมาดูแล้วยืนยัน)
        _toc_numbers = [c[0] for c in toc_ch]
        _dup = [n for n in sorted(set(_toc_numbers)) if _toc_numbers.count(n) > 1]
        if BODY_RULES['check_toc_chapter_presence'] and (
                _dup or len(toc_ch) != len(body_ch)):
            # บอกทั้งจำนวนและชี้บทที่ซ้ำ "ในข้อเดียว" (เจ้าหน้าที่สั่ง ก.ย. 2569)
            # ของเดิมบอกแค่จำนวน เจ้าหน้าที่เปิดเล่มเห็น 6 บทตามที่ควรเป็น เลยนึกว่า
            # ระบบนับผิด ทั้งที่สารบัญพิมพ์ CHAPTER 2 กับ 3 ซ้ำจริง (เรนเดอร์หน้า ix
            # ออกมาดูแล้วยืนยัน) — แยกเป็นสองการ์ดไม่ได้ จะกลายเป็นฟ้องซ้ำเรื่องเดียว
            _counts = f"สารบัญมี {len(toc_ch)} บท เนื้อหามี {len(body_ch)} บท"
            if _dup:
                _at = sorted({c[4] for c in toc_ch if _toc_numbers.count(c[0]) > 1})
                _where = f"สารบัญ ({page_ref(_at[-1])})"
                _found = (f"{_counts} เพราะสารบัญพิมพ์ซ้ำ: "
                          + _join_and([_toc_chapter_label(n, toc_ch)
                                       for n in _dup]))
                _want = "แต่ละบทต้องมีรายการเดียวในสารบัญ"
                _fix = "ลบรายการที่ซ้ำออกจากสารบัญ"
            else:
                _where, _found = "สารบัญ vs เนื้อหา", _counts
                _want, _fix = "จำนวนบทต้องเท่ากัน", "อัปเดตสารบัญหรือเนื้อหา"
            rep.add("RED", "body", _where, _found, _want, _fix, "FRONT.TOC")
        toc_map = {c[0]: (c[1], c[2], c[3], c[4]) for c in toc_ch}
        for cn, title, ppage, pno, _next_line in body_ch:
            if cn in toc_map:
                t_title_n, t_pno, t_raw, toc_page_idx = toc_map[cn]
                nb = norm(title)
                # บทที่ประกาศบังคับชื่อ (โหมด strict) ยึดประกาศเป็นหลัก ไม่เทียบสารบัญ↔
                # เนื้อหา — บทที่ประกาศไม่บังคับ (รูปแบบ 2 บทที่ 3 / โหมดยกเว้นบท) ยังเทียบ
                enforced_title = chapters_mode == "strict" and 1 <= cn <= enforced_chapters
                if BODY_RULES['check_toc_title_against_body'] and t_title_n != nb \
                        and not enforced_title:
                    toc_title = _toc_chapter_title(t_raw)
                    right = _correctly_spelled_side(title, toc_title, cn, option)
                    if right is None:
                        # ไม่รู้ว่าฝั่งไหนถูก จึงห้ามชี้ว่า "ต้องเป็นเหมือนอีกฝั่ง"
                        # เพราะอาจไปสั่งให้แก้ฝั่งที่ถูกอยู่แล้ว
                        rep.add("RED", "body", f"บทที่ {cn} ({page_ref(ppage)})",
                                f'ชื่อบทในสารบัญกับในเนื้อหาไม่ตรงกัน: '
                                f'สารบัญพิมพ์ "{toc_title}" ส่วนเนื้อหาพิมพ์ "{title}"',
                                "ชื่อบทในสารบัญกับในเนื้อหาต้องสะกดตรงกัน",
                                "", "FRONT.TOC")
                    else:
                        # ฝั่งหนึ่งสะกดตรงประกาศ อีกฝั่งจึงเป็นฝั่งที่ต้องแก้ — แม้อยู่ใน
                        # โหมดยกเว้นบท (ไม่บังคับ "ชื่อ" ตามประกาศ) ก็ยังต้องสะกดให้ถูก
                        # ของเดิมยึดสารบัญเป็นหลักเสมอ เล่มที่สารบัญพิมพ์ผิดจึงถูกสั่งให้
                        # แก้เนื้อหาที่ถูกอยู่แล้วให้กลายเป็นคำที่ผิดตาม
                        wrong_side, wrong_text, loc = (
                            ("สารบัญ", toc_title, f"สารบัญ ({page_ref(toc_page_idx)}) บทที่ {cn}")
                            if right == "body" else
                            ("เนื้อหา", title, f"บทที่ {cn} ({page_ref(ppage)})"))
                        correct = title if right == "body" else toc_title
                        diff = describe_diff(wrong_text, correct)
                        found_msg = f'ชื่อบทใน{wrong_side}เขียนว่า "{wrong_text}"'
                        if diff:
                            found_msg += f" {diff}"
                        rep.add("RED", "body", loc, found_msg,
                                f'ต้องแก้เป็น "{correct}"', "", "FRONT.TOC")
                # เลิกตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว (กติกา ส.ค. 2569) — ยังบันทึก
                # ไว้เป็นข้อสังเกตสีเหลืองให้เจ้าหน้าที่เห็น แต่ไม่ทำให้เล่มไม่ผ่าน
                if BODY_RULES['check_toc_page_numbers'] and t_pno is None:
                    rep.add(TOC_PAGE_ZONE, "front_matter",
                            f"สารบัญ ({page_ref(toc_page_idx)}) บทที่ {cn}",
                            f"หัวข้อ \"{t_raw}\" ไม่มีเลขหน้า",
                            "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว",
                            "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้", "FRONT.TOC_PAGE_REF")
                elif BODY_RULES['check_toc_page_numbers'] and pno is not None and t_pno != pno:
                    rep.add(TOC_PAGE_ZONE, "body",
                            f"สารบัญ ({page_ref(toc_page_idx)}) กับบทที่ {cn} ({page_ref(ppage)})",
                            f"สารบัญระบุหน้า {t_pno} แต่บทอยู่จริงหน้า {pno}",
                            "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว",
                            "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้", "FRONT.TOC_PAGE_REF")
            elif BODY_RULES['check_toc_chapter_presence']:
                rep.add("RED", "body", f"บทที่ {cn} ({page_ref(ppage)})", "ไม่อยู่ในสารบัญ",
                        "ทุกบทต้องปรากฏในสารบัญ", "", "FRONT.TOC")
    else:
        toc_problem = "ไม่พบรายการบทในสารบัญ" if toc_pages else "ไม่พบหน้าสารบัญ"
        rep.add("RED", "front_matter",
                f"สารบัญ ({page_ref(toc_pages[0])})" if toc_pages else "ส่วนนำ",
                toc_problem, "ส่วนนำต้องมีสารบัญและระบุบททุกบทพร้อมเลขหน้า",
                "เพิ่มหรืออัปเดตสารบัญให้ครบ", "FRONT.TOC_CONTENT")

    # หัวข้อหน้าสารบัญต้องเป็น "TABLE OF CONTENTS" ตาม template
    # เล่มจริงพิมพ์ "CONTENT" ซึ่งทำให้ระบบหาหน้าสารบัญไม่เจอทั้งชุด (ตอนนี้รู้จักแล้ว)
    for toc_idx, printed_heading in toc_heading_wrong:
        rep.add("RED", "front_matter", f"สารบัญ ({page_ref(toc_idx)})",
                f'หัวข้อหน้าสารบัญเขียนว่า "{printed_heading}"',
                f'ต้องแก้เป็น "{TOC_HEADING_CANONICAL}"', "", "FRONT.TOC")

    # หัวข้อระดับหลักในสารบัญต้องเป็นตัวหนา (ไม่บังคับหัวข้อย่อย 1.1, 1.2, ...)
    toc_scan_pages = toc_page_indices
    if toc_scan_pages:
        try:
            with pdfplumber.open(pdf_path) as _pl:
                # สารบัญยาวหลายหน้า แต่ฟอนต์เป็นของทั้งไฟล์ ถ้าบอกน้ำหนักไม่ได้ก็บอก
                # ไม่ได้ทุกหน้าเหมือนกัน จึงฟ้องข้อเดียว ไม่ใช่ซ้ำทีละหน้า
                if all(bold_is_undetectable(_pl.pages[i]) for i in toc_scan_pages):
                    rep.add(UNCERTAIN_ZONE, "front_matter",
                            f"สารบัญ ({page_ref(toc_scan_pages[0])})",
                            "ไฟล์นี้ไม่ได้เก็บชื่อฟอนต์ไว้ ระบบจึงบอกไม่ได้ว่าหัวข้อเป็นตัวหนาหรือไม่",
                            "หัวข้อหลักในสารบัญต้องเป็นตัวหนา",
                            "ตรวจด้วยตา", "FORMAT.BOLD")
                    toc_scan_pages = []
                for toc_idx in toc_scan_pages:
                    if bold_is_undetectable(_pl.pages[toc_idx]):
                        continue
                    nonbold = []
                    for line in _font_lines(_pl.pages[toc_idx]):
                        if _is_toc_major_heading(line['text']) and line['bold_ratio'] < 0.8:
                            nonbold.append(re.sub(r'\s+(?:\d+|[ivxlcdm]+)\s*$', '', line['text'], flags=re.I))
                    if nonbold:
                        rep.add(
                            BOLD_FAILURE_ZONE, "front_matter", f"สารบัญ ({page_ref(toc_idx)})",
                            _nonbold_heading_text(nonbold),
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
    # เก็บไว้ก่อน ยังไม่ฟ้อง — กฎ "หัวข้อบังคับหายจากสารบัญ" (FRONT.TOC_CONTENT)
    # ข้างล่างจับหัวข้อเดียวกันได้ด้วย และบอกได้ว่าเกินคำไหน ("มี (If any) เกินมา")
    # ถ้าฟ้องทั้งสองกฎ เจ้าหน้าที่เห็นการ์ดสองใบจากความผิดเดียว (เจ้าหน้าที่สั่งยุบ
    # ก.ย. 2569 ให้เหลือของ FRONT.TOC_CONTENT) — แต่กฎนี้ยังต้องอยู่ เพราะครอบคลุม
    # หัวข้อ LIST OF ... ที่ไม่มีส่วนนั้นอยู่ในเล่ม ซึ่งกฎข้างล่างไม่แตะ
    toc_list_typos = []
    for toc_page_idx, raw in toc_lines:
        visible = _strip_toc_page_number(raw)
        if norm(visible).startswith('LISTOF'):
            expected = max(
                TOC_ALLOWED_LIST_HEADINGS,
                key=lambda candidate: difflib.SequenceMatcher(None, norm(candidate), norm(visible)).ratio(),
            )
            compared = compare_values(visible, expected, 'toc_heading')
            if compared['status'] != 'exact':
                toc_list_typos.append((toc_page_idx, visible, expected, compared))

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
        toc_by_ch = {c[0]: (_toc_chapter_title(c[3]), c[4], c[5]) for c in toc_ch}
        body_by_ch = ({c[0]: (c[1], c[2], c[4]) for c in body_ch}
                      if body_ch and BODY_RULES['check_body_title_against_canonical'] else {})

        def _title_status(title, cn, next_line=""):
            """สถานะของชื่อบทหนึ่งฝั่ง — คืน None ถ้าถือว่าใช้ได้

            ชื่อที่เป็นแค่ต้นของชื่อในประกาศ ยอมรับได้ต่อเมื่อบรรทัดถัดไปต่อให้ครบจริง
            (ดู canonical_title_wrapped)
            """
            kind, compared, expected = canonical_title_status(title, cn, option)
            if kind == 'exact':
                return None
            if canonical_title_wrapped(title, next_line, canon[cn - 1]):
                return None
            return kind, compared, expected

        for cn in sorted(set(toc_by_ch) | set(body_by_ch)):
            if not (1 <= cn <= enforced_chapters):
                continue
            toc_title, toc_idx, toc_next = toc_by_ch.get(cn, (None, None, ""))
            body_title, body_idx, body_next = body_by_ch.get(cn, (None, None, ""))
            toc_bad = _title_status(toc_title, cn, toc_next) if toc_title is not None else None
            body_bad = _title_status(body_title, cn, body_next) if body_title is not None else None
            if not toc_bad and not body_bad:
                continue

            expected_title = (toc_bad or body_bad)[2]
            both = bool(toc_bad and body_bad)
            same = both and norm(toc_title) == norm(body_title)
            zone = ("ORANGE" if all(s[0] == 'variant' for s in (toc_bad, body_bad) if s)
                    else "RED")
            if both:
                where = f"บทที่ {cn} ในสารบัญ ({page_ref(toc_idx)}) และในเนื้อหา ({page_ref(body_idx)})"
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
                        f'ประกาศใช้ "{expected_title}" แต่คู่มือแสดงแบบที่พบ เจ้าหน้าที่ยืนยันได้',
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
                    f'หัวข้อในหน้านี้เลือกหลายคำ: "{ref_head[0]}"',
                    "เลือกคำเดียว: REFERENCES หรือ BIBLIOGRAPHY", "ลบคำที่ไม่ใช้")
    else:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบหน้ารายการอ้างอิง",
                "ต้องมี REFERENCES/BIBLIOGRAPHY เสมอ", "")
    if bio_page is None:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบประวัติผู้วิจัย (BIOGRAPHY)",
                "ต้องมีและเป็นหน้าสุดท้ายของเล่ม", "")
    else:
        # ลำดับส่วนท้ายเล่มตามประกาศ: รายการอ้างอิง แล้วภาคผนวก (ถ้ามี) แล้วประวัติผู้วิจัย
        #
        # เดิมตรวจแค่ "ต้องไม่มีอะไรต่อจากประวัติผู้วิจัย" ซึ่งจับได้เฉพาะกรณีที่ประวัติ
        # ไม่ได้อยู่ท้ายสุด เล่มที่วางภาคผนวกไว้ "ก่อน" รายการอ้างอิงจึงหลุดไป
        # ทั้งที่ผิดลำดับเหมือนกัน — ตรวจทั้งชุดทีเดียวแบบเดียวกับลำดับส่วนนำ
        end_sections = []
        if ref_head:
            # ใช้ชื่อรวมสองคำแบบเดียวกับกฎสารบัญ เพราะเล่มไทยใช้ได้ทั้ง "รายการอ้างอิง"
            # และ "บรรณานุกรม" ถ้าเลือกคำเดียวจะไปเรียกชื่อส่วนผิดจากที่พิมพ์ในเล่ม
            end_sections.append(("รายการอ้างอิง/บรรณานุกรม", ref_head[1]))
        if appendix_page is not None:
            end_sections.append(("ภาคผนวก", appendix_page))
        end_sections.append(("ประวัติผู้วิจัย", bio_page))
        actual_end = sorted(end_sections, key=lambda item: item[1])
        if [n for n, _i in actual_end] != [n for n, _i in end_sections]:
            rep.add("RED", "end_matter", "ส่วนท้ายเล่ม",
                    "ลำดับที่พบ: " + _ORDER_JOIN.join(
                        f"{name} ({order_ref(idx)})" for name, idx in actual_end),
                    "ลำดับที่ต้องเป็น: " + _ORDER_JOIN.join(
                        name for name, _i in end_sections),
                    "ย้ายแต่ละส่วนของส่วนท้ายเล่มให้เรียงตามลำดับที่กำหนด",
                    "END.STRUCTURE")

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
            (approved or {}).get("program_language", "") if same_student else ""),
        page_texts=pages)

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
        lbl = f"{abstract_page_label(ai, abs_en_pages, abs_th_pages)} ({page_ref(ai)})"
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
            raws = pages[sp].split('\n')
            for kw_idx, raw in enumerate(raws):
                nl = norm(raw)
                if nl.startswith('KEYWORD') or nl.startswith(norm('คำสำคัญ')):
                    tail = raw.split(':', 1)[1] if ':' in raw else raw
                    # รายการ keyword ยาวเกินบรรทัดเดียวได้ — เล่มจริง (ก.ย. 2569)
                    # ฝั่งอังกฤษมี 6 คำ แต่คำที่ 5-6 ตกไปบรรทัดถัดไป ระบบอ่านแค่
                    # บรรทัดแรกจึงนับได้ 4 แล้วปล่อยผ่าน ทั้งที่ฝั่งไทยของเล่มเดียวกัน
                    # โดนฟ้อง 6 คำ — ต่อบรรทัดถัดไปจนถึงบรรทัด "N pages / N หน้า"
                    # ซึ่งปิดท้ายบทคัดย่อเสมอ (สำรวจ 5 เล่ม ไม่มีเล่มไหนต่างออกไป)
                    for more in raws[kw_idx + 1:kw_idx + 4]:
                        if not soft(more) or is_page_count_line(more):
                            break
                        tail += " " + more
                    kws = [k for k in re.split(r'[,;/]', tail) if k.strip()]
                    if len(kws) > 5:
                        rep.add(KEYWORD_COUNT_ZONE, "front_matter",
                                f"{abstract_page_label(ai, abs_en_pages, abs_th_pages)}"
                                f" ({page_ref(sp)})",
                                f"Keywords {len(kws)} คำ", "ไม่เกิน 5 คำตามประกาศ",
                                "ตัดให้เหลือไม่เกิน 5 คำ", "FRONT.KEYWORD_COUNT")
                    done_kw = True
                    break
            if done_kw:
                break

    if count_missing:
        rep.add(FRONT_FAILURE_ZONE, "front_matter", " และ ".join(count_missing),
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
                    abs_label = abstract_page_label(ai, abs_en_pages, abs_th_pages)
                    for abs_page_idx in range(ai, min(ai + span_of(ai), n)):
                        lines = _font_lines(_pl.pages[abs_page_idx])
                        # ชื่อเรื่องบนหน้านี้ต้องชิดซ้ายและไม่หนา (แยกจากข้อสังเกตตัวหนาทั่วไป
                        # เพราะเป็นกฎเฉพาะของบรรทัดชื่อเรื่อง ไม่ใช่ทั้งหน้า)
                        _report_abstract_title_format(
                            rep, lines, f"{abs_label} ({page_ref(abs_page_idx)})")
                        bold_lines = [
                            line['text'] for line in lines
                            if line['bold_ratio'] > 0 and len(norm(line['text'])) >= 2
                            and not _is_abstract_heading(line['text'])
                        ]
                        if bold_lines:
                            examples = ", ".join(f'"{line}"' for line in bold_lines[:5])
                            more = f" และอีก {len(bold_lines) - 5} บรรทัด" if len(bold_lines) > 5 else ""
                            rep.add(ABSTRACT_BOLD_ZONE, "front_matter",
                                    f"{abs_label} ({page_ref(abs_page_idx)})",
                                    f"มีข้อความตัวหนา: {examples}{more}",
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
                    found_msg += f" {diff}"
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
            # บอกให้ครบว่า "เล่มเป็นแบบไหน / ต้องเป็นแบบไหน / แก้ตรงไหนได้บ้าง"
            # ของเดิมบอกแค่สองค่าเทียบกัน คนอ่านไม่รู้ว่าต้องแก้ฝั่งไหน
            rep.add("RED", "body", "โครงบท", f"เล่มจัดบทตามรูปแบบ {option}",
                    f"ข้อมูลอนุมัติระบุรูปแบบ {A['format']}",
                    f"แก้เล่มให้เป็นรูปแบบ {A['format']} หรือแก้ข้อมูลอนุมัติให้เป็นรูปแบบ {option}",
                    "FORM.APPROVED_MATCH")

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
            actual_order = _ORDER_JOIN.join(
                f"{name} ({order_ref(page_idx)})" for name, page_idx in actual_front_sections
            )
            expected_order = _ORDER_JOIN.join(name for name, _idx in ordered_front_sections)
            rep.add(
                "RED", "front_matter", "ส่วนนำ",
                f"ลำดับที่พบ: {actual_order}",
                f"ลำดับที่ต้องเป็น: {expected_order}",
                "ย้ายแต่ละส่วนของส่วนนำให้เรียงตามลำดับที่กำหนด",
                "FRONT.ORDER",
            )

        # ต้องรู้ชื่อนักศึกษาก่อนตรวจชื่อเรื่อง — printed_title ใช้ชื่อนักศึกษาเป็น
        # ขอบล่างของบล็อกชื่อเรื่องบนหน้าปก (บรรทัดถัดจากชื่อเรื่องคือชื่อผู้เขียน)
        student_name = strip_name_prefix(A.get("student_name", ""))
        student_name_th = strip_name_prefix(A.get("student_name_th", ""))
        primary_student_name = student_name_th if thai_book else student_name

        main_title = (A.get("title_th") if thai_book else A.get("title_en")) or ""
        alt_title = "" if A.get("program_language") == "international" else \
            ((A.get("title_en") if thai_book else A.get("title_th")) or "")

        if main_title:
            spots = [("หน้าปก", cover_text)]
            for k2, i2 in enumerate(sig_pages):
                spots.append((f"หน้าลงนาม {k2+1} ({page_ref(i2)})", pages[i2]))
            main_abs = abs_th_idx if thai_book else abs_en_idx
            if main_abs is not None:
                spots.append((f"{abstract_page_label(main_abs, abs_en_pages, abs_th_pages)}"
                              f" ({page_ref(main_abs)})", pages[main_abs]))
            for spot_name, spot_text in spots:
                compared = compare_reference_text(spot_text, main_title, 'title')
                if compared['status'] != 'exact':
                    compared = _title_as_printed(compared, spot_text,
                                                 main_title, primary_student_name)
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
                if compared['status'] != 'exact':
                    compared = _title_as_printed(compared, pages[alt_abs],
                                                 alt_title, primary_student_name)
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

        # ---------- หน้าปกและหน้าลงนามต้องมีชื่อเรื่องภาษาเดียว ----------
        # เจ้าหน้าที่ระบุ (ก.ย. 2569): เล่มภาษาไทยให้มีเฉพาะชื่อเรื่องภาษาไทย
        # เล่มภาษาอังกฤษให้มีเฉพาะชื่อเรื่องภาษาอังกฤษ ทั้งบนหน้าปกและหน้าลงนาม
        # (หน้าบทคัดย่อไม่เข้ากฎนี้ — เล่มสองภาษาต้องมีบทคัดย่อทั้งสองภาษาอยู่แล้ว)
        other_script = "en" if thai_book else "thai"
        other_lang_title = approved_title(A, other_script)
        if other_lang_title:
            other_word = "ภาษาอังกฤษ" if other_script == "en" else "ภาษาไทย"
            book_word = "ภาษาไทย" if thai_book else "ภาษาอังกฤษ"
            one_language_spots = [("หน้าปก", cover_text)] + [
                (f"หน้าลงนาม {k2 + 1} ({page_ref(i2)})", pages[i2])
                for k2, i2 in enumerate(sig_pages)
            ]
            for spot_name, spot_text in one_language_spots:
                printed_other = title_printed_on_page(spot_text, other_lang_title)
                if not printed_other:
                    continue
                rep.add("RED", "front_matter", spot_name,
                        f'หน้านี้มีชื่อเรื่อง{other_word}อยู่ด้วย: "{printed_other[:160]}"',
                        f"เล่ม{book_word}ต้องมีเฉพาะชื่อเรื่อง{book_word}"
                        " ทั้งบนหน้าปกและหน้าลงนาม",
                        f"ลบชื่อเรื่อง{other_word}ออกจากหน้านี้",
                        "FRONT.TITLE_ONE_LANGUAGE")

        # ---------- หน้าบทคัดย่อต้องมีชื่อเรื่องเฉพาะภาษาของหน้านั้น ----------
        # template วางชื่อเรื่องภาษาเดียวไว้หัวหน้าบทคัดย่อแต่ละภาษา — หน้าอังกฤษขึ้นต้น
        # ด้วย "THESIS TITLE" หน้าไทยขึ้นต้นด้วย "หัวข้อวิทยานิพนธ์ภาษาไทย" เจ้าหน้าที่
        # ยืนยัน (ก.ย. 2569) จากเล่มจริงที่พิมพ์ชื่อเรื่องทั้งสองภาษาซ้อนกันบนหน้าบทคัดย่อไทย
        #
        # กฎข้างบนครอบไม่ถึง เพราะกฎนั้นตัดสินจาก "ภาษาของเล่ม" ส่วนหน้าบทคัดย่อสองหน้า
        # ตั้งใจใช้คนละภาษากันอยู่แล้ว จึงต้องตัดสินจาก "ภาษาของหน้า" ทีละหน้าแทน
        abstract_title_spots = [
            (abs_en_idx, "บทคัดย่อภาษาอังกฤษ", "ภาษาอังกฤษ", "thai", "ภาษาไทย"),
            (abs_th_idx, "บทคัดย่อภาษาไทย", "ภาษาไทย", "en", "ภาษาอังกฤษ"),
        ]
        for spot_idx, spot_label, page_word, wrong_script, wrong_word in abstract_title_spots:
            wrong_title = approved_title(A, wrong_script)
            if spot_idx is None or not wrong_title:
                continue
            printed_wrong = title_printed_on_page(pages[spot_idx], wrong_title)
            if not printed_wrong:
                continue
            rep.add("RED", "front_matter", f"{spot_label} ({page_ref(spot_idx)})",
                    f'หน้านี้มีชื่อเรื่อง{wrong_word}อยู่ด้วย: "{printed_wrong[:160]}"',
                    f"หน้าบทคัดย่อ{page_word}ต้องมีเฉพาะชื่อเรื่อง{page_word}",
                    f"ลบชื่อเรื่อง{wrong_word}ออกจากหน้านี้",
                    "FRONT.TITLE_ONE_LANGUAGE")

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
            name_spots = [("หน้าปก", cover_idx, "cover")] + [
                (f"หน้าลงนาม {k + 1} ({page_ref(idx)})", idx, "signature")
                for k, idx in enumerate(sig_pages)
            ]
            for spot_name, spot_idx, spot_kind in name_spots:
                compared = compare_reference_text(pages[spot_idx], core_name, 'student_name')
                if compared['status'] != 'exact':
                    # ช่องของชื่อชี้ได้จากโครงสร้างของหน้า — ต้องรายงานสิ่งที่พิมพ์อยู่
                    # ตรงนั้นจริง ไม่ใช่บรรทัดไหนก็ได้ทั้งหน้าที่คล้ายชื่อที่สุด
                    printed_name = (
                        cover_printed_name(pages[spot_idx],
                                           approved_title(A, "thai" if thai_book else "en"))
                        if spot_kind == "cover"
                        else signature_printed_name(pages[spot_idx]))
                    if printed_name:
                        compared = compare_values(printed_name, core_name, 'student_name')
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

        # ---------- หน้าที่ระบบอ่านตัวเลขไม่ออก ----------
        # ฟอนต์ย่อยที่ตาราง ToUnicode ผิดทำให้ตัวเลขบนหน้ากลายเป็นตัวอักษรอังกฤษ
        # ถ้าปล่อยไว้ หน้านั้นจะโดนฟ้องแดงสองข้อพร้อมกัน (ชื่อสะกดผิด + ไม่พบรหัส)
        # แล้วสั่งให้นักศึกษาแก้ข้อความที่ถูกต้องอยู่แล้ว
        #
        # แต่ "หน้ากระดาษถูกต้องทุกตัวอักษร" — เรนเดอร์หน้านั้นออกมาดูแล้วเห็นรหัส
        # 6437028 ชัดเจน คนอ่านไม่มีทางเห็นความผิดปกติ เสียแค่การดึงข้อความ
        # (PyMuPDF ซึ่งเป็นคนละเอนจินกับ pdfplumber ก็ได้ JKLMNOP เหมือนกัน
        #  แปลว่าตาราง ToUnicode ในไฟล์ผิดจริง ไม่ใช่ตัวอ่านของเราเพี้ยน)
        # เจ้าหน้าที่จึงสั่ง (ก.ย. 2569) ว่า "ปล่อยผ่าน ทำเพียงแจ้งบอกว่าเกิดปัญหาอะไร
        # ให้เจ้าหน้าที่ทราบและต้องไปดูเอง" — บันทึกเป็นข้อมูลประกอบ ไม่ใช่จุดผิด
        # และต้องมีคำสั่งให้เปิดหน้านั้นดูด้วยตาอยู่ในบรรทัดเดียวกัน ไม่งั้นเจ้าหน้าที่
        # อ่านแล้วไม่รู้ว่าต้องทำอะไรต่อ ดู RULES_AND_SOURCES.md หัวข้อเดียวกัน
        unreadable_digit_pages = {}
        for _aidx in (abs_en_idx, abs_th_idx):
            if _aidx is None:
                continue
            _misread = unreadable_id_digits(pages[_aidx], soft(A.get("student_id", "")),
                                            (student_name, student_name_th))
            if _misread:
                unreadable_digit_pages[_aidx] = _misread
        # เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่า "หน้าไหนเพี้ยนควรแจ้ง" — เดิมบอกเฉพาะหน้า
        # บทคัดย่อที่อ่านรหัสนักศึกษาไม่ออก ส่วนหน้าอื่นเงียบสนิท เล่มจริงเล่มหนึ่ง
        # เพี้ยน 38 หน้า แต่รายงานพูดถึงหน้าเดียว
        if font_damaged:
            _shown = ", ".join(page_ref(i) for i in font_damaged[:12])
            if len(font_damaged) > 12:
                _shown += f" และอีก {len(font_damaged) - 12} หน้า"
            rep.add_info("-", f"ฟอนต์ในไฟล์ทำให้ระบบอ่านข้อความเพี้ยน {len(font_damaged)} หน้า",
                         f"หน้าที่พบคือ {_shown} "
                         "หน้ากระดาษแสดงผลถูกต้องตามปกติ เสียเฉพาะการดึงข้อความออกจากไฟล์ "
                         "กรุณาเปิดหน้าเหล่านี้ดูด้วยตาอีกครั้ง")
        for _aidx, _misread in sorted(unreadable_digit_pages.items()):
            _loc = (f"{abstract_page_label(_aidx, abs_en_pages, abs_th_pages)}"
                    f" ({page_ref(_aidx)})")
            # ตารางผลเทียบข้อมูลอนุมัติบันทึกไว้อยู่แล้วว่าช่องนี้ยังไม่ได้เทียบ
            # ("รหัสนักศึกษา ... ระบบอ่านตัวเลขบนหน้านี้ไม่ออก") ข้อสีส้มอีกใบจึงเป็น
            # การพูดซ้ำ และยังลากผลตรวจของทั้งเล่มไปค้างที่ "รอยืนยัน" ด้วย
            # เว้นวรรคหน้าตำแหน่งไว้ให้คำแปลจับ "คำนำ" กับ "ชื่อตำแหน่ง" แยกกันได้
            # (ชื่อตำแหน่งมีคำแปลของตัวเองอยู่แล้ว ถ้ามัดรวมเป็นประโยคเดียวจะแปลไม่ออก)
            rep.add_info("front_matter", f"ระบบไม่ได้เทียบรหัสนักศึกษาที่ {_loc}",
                         "ฟอนต์ที่ฝังมาในไฟล์ทำให้ตัวเลขถูกดึงออกมาเป็น "
                         f'"{_misread}" ส่วนหน้ากระดาษแสดงผลถูกต้องตามปกติ '
                         "และรหัสนักศึกษาถูกเทียบกับข้อมูลอนุมัติที่หน้าอื่นแล้ว "
                         "กรุณาเปิดหน้านี้ดูรหัสนักศึกษาด้วยตาอีกครั้ง")

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
            if aidx in unreadable_digit_pages and compared['status'] != 'exact':
                # หน้าที่ฟอนต์เสีย ตรวจ "สิ่งที่อ่านได้" ตามปกติ แต่ห้ามฟ้อง — ชื่อที่
                # เทียบไม่ตรงบนหน้าแบบนี้แยกไม่ออกว่าเล่มพิมพ์ผิดหรือระบบอ่านมาไม่ครบ
                # ของเดิมข้ามทั้งหน้า ทั้งที่เล่มจริงเทียบชื่อไทยได้ 1.00 เต็มบนหน้า
                # เดียวกับที่อ่านตัวเลขไม่ออก — ทิ้งผลที่ใช้ได้ไปเปล่า ๆ
                rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})",
                                     "pending", "ระบบอ่านข้อความบนหน้านี้ไม่ครบ")
                continue
            if compared['status'] != 'exact':
                # ช่องของชื่อบนหน้าบทคัดย่อคือข้อความหน้ารหัสนักศึกษา ต้องรายงานสิ่งที่
                # พิมพ์อยู่ตรงนั้นจริง ไม่ใช่บรรทัดที่คล้ายชื่อที่สุดทั้งหน้า
                printed_name = abstract_printed_name(pages[aidx])
                if printed_name:
                    compared = compare_values(printed_name, core3, 'student_name')
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
                # ชื่อสะกดถูก แต่ต้องอยู่ "บรรทัดเดียวกับรหัส" ตาม template ด้วย
                # (template กำหนดบรรทัดเดียวว่า "ชื่อ นามสกุล  รหัส  รหัสหลักสูตร/ระดับ")
                _check_student_line_pairs_name_with_id(
                    rep, pages[aidx], core3, soft(A.get("student_id", "")),
                    f"{albl} ({page_ref(aidx)})", nlbl)

        # รหัสนักศึกษา = เลข 7 หลัก + รหัสหลักสูตร (เช่น "6838141 SHSS/M") ต้องตรวจทั้งชุด
        # และต้องปรากฏในบทคัดย่อ "ทุกภาษาที่เล่มมี" (นานาชาติมีเฉพาะอังกฤษ)
        student_id = soft(A.get("student_id", ""))
        if student_id:
            digits_only = re.sub(r'\D', '', student_id)
            cover_digits = re.sub(r'[^\d]', '', cover_text)
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
                if abs_idx in unreadable_digit_pages:
                    rep.add_verification("รหัสนักศึกษา", loc, "pending",
                                         "ระบบอ่านตัวเลขบนหน้านี้ไม่ออก")
                    continue
                if norm(student_id) in norm(pages[abs_idx]):
                    rep.add_verification("รหัสนักศึกษา", loc, "pass")
                else:
                    # เล่มพิมพ์รหัสมาแต่ผิดตัวเลข กับเล่มไม่มีรหัสเลย เป็นคนละเรื่องกัน
                    # ถ้าเจอรหัสบนหน้า ต้องบอกว่าเล่มพิมพ์ว่าอะไรและต่างตรงไหน ไม่ใช่
                    # บอกลอย ๆ ว่า "ไม่พบรหัสนักศึกษา" ซึ่งอ่านแล้วนึกว่าระบบหาไม่เจอ
                    printed = _closest_student_id(pages[abs_idx], student_id)
                    if printed:
                        # ชี้จุดต่างเฉพาะตอนที่ต่างกันจุดเดียว (พิมพ์ผิดหลักเดียว)
                        # ถ้าเป็นคนละรหัสกันคนละเรื่อง การไล่ทีละตัวอักษรจะได้
                        # "ต่างที่ 1 และ ขาด 35 และ มี 17 เกินมา" ซึ่งอ่านไม่รู้เรื่อง
                        # กว่าการดูรหัสเต็มสองอันเทียบกันเอง
                        diff = describe_diff(printed, student_id)
                        found_msg = f'บรรทัดชื่อนักศึกษาพิมพ์รหัสว่า "{printed}"'
                        if diff and " และ " not in diff:
                            found_msg += f" {diff}"
                        detail = printed
                    else:
                        found_msg = ("ไม่พบรหัสนักศึกษาบนหน้านี้ "
                                     "(ต้องมีทั้งตัวเลขและรหัสหลักสูตร)")
                        detail = f"ไม่พบรหัส {student_id}"
                    rep.add_verification("รหัสนักศึกษา", loc, "fail", detail)
                    rep.add("RED", "front_matter", loc, found_msg,
                            f"บรรทัดชื่อนักศึกษาใน{abs_label}ต้องมีรหัส \"{student_id}\"",
                            "", "FORM.APPROVED_MATCH")

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
                rep.add_verification("ข้อความ template ใต้ชื่อหัวข้อ", spot, "pass")
            else:
                # เล่มพิมพ์ประโยคมาแต่ผิดคำ กับเล่มไม่มีประโยคนี้เลย เป็นคนละเรื่องกัน
                # เล่มจริงพิมพ์ "ได้รับการพิจารณาให้เป็นส่วนหนึ่ง..." ตกคำว่า "นับ"
                # ถ้าบอกลอย ๆ ว่า "ไม่พบข้อความ template" เจ้าหน้าที่จะนึกว่าระบบ
                # อ่านไม่เจอ ทั้งที่ประโยคอยู่บนหน้ากระดาษครบ แค่ผิดคำเดียว
                #
                # ตัด "ชื่อปริญญา" ออกจากหน้าก่อน แล้วที่เหลือจึงเป็นข้อความ template
                # ล้วน (ดู signature_template_zone) — ชื่อปริญญาต่อท้ายประโยคนี้พอดี
                # ถ้าไม่ตัดออก ตัวหาช่วงจะคร่อมชื่อปริญญาเข้ามาแล้วสองประเด็นปนกัน
                #
                # กันอีกชั้นด้วยเกณฑ์ความใกล้เคียงที่สูงกว่าค่าปกติ (0.6) เผื่อกรณี
                # ที่ตัดชื่อปริญญาไม่ได้ (เล่มไม่มีชื่อปริญญา หรือฟอร์มไม่ได้กรอกมา)
                # วัดจากเคสจริง: ประโยคที่มีอยู่แต่ผิด ได้ ratio 0.87-0.99
                # ส่วนช่วงที่คร่อมชื่อปริญญาเมื่อไม่มีประโยคเลย ได้ 0.68 — ตั้งที่ 0.8
                template_zone = signature_template_zone(pages[idx], sig_degree)
                near = _closest_run(template_zone, sig_template,
                                    min_ratio=SIGNATURE_TEMPLATE_MIN_RATIO)
                # ข้อความต้องขึ้นต้นด้วย "ส่วนไหนของหน้าที่ผิด" ไม่ใช่พูดคำว่า
                # "หน้าลงนาม" ซ้ำอีกรอบ — ตำแหน่งข้างบนบอกไปแล้วว่าหน้าไหน
                # (เดิมคำว่า "หน้าลงนาม" โผล่ 4 รอบในข้อเดียว: หัวกลุ่ม ตำแหน่ง
                #  สิ่งที่พบ และบรรทัดที่ควรเป็น)
                if near:
                    diff = describe_diff(near, sig_template)
                    found_msg = f'ข้อความ template ใต้ชื่อหัวข้อพิมพ์ว่า "{near}"'
                    if diff:
                        found_msg += f" {diff}"
                    detail = near
                else:
                    # ไม่ยกประโยคเต็มมาตรงนี้ เพราะบรรทัด "ต้องเป็น" ข้างล่างมีอยู่แล้ว
                    # ยกสองรอบทำให้ข้อเดียวมีประโยคยาว ๆ ซ้ำกันสองครั้ง
                    found_msg = "ไม่พบข้อความ template ใต้ชื่อหัวข้อ"
                    detail = "ไม่พบข้อความ template ใต้ชื่อหัวข้อ"
                rep.add_verification("ข้อความ template ใต้ชื่อหัวข้อ", spot, "fail", detail)
                rep.add("RED", "front_matter", spot, found_msg,
                        f'ต้องเป็น "{sig_template}"',
                        "", "FRONT.APPROVAL")
        if cover_degree or sig_degree:
            degree_spots = []
            if cover_degree:
                # หน้าปกวางชื่อปริญญาไว้บรรทัดของมันเอง จึงตรวจคำเกินได้
                degree_spots.append(("หน้าปก", cover_text, cover_degree, True))
            if sig_degree:
                # หน้าลงนามวางชื่อปริญญาไว้กลางประโยค template ("for the degree of ...")
                # มีคำอื่นล้อมรอบโดยชอบ จึงตรวจคำเกินไม่ได้
                degree_spots.extend((f"หน้าลงนาม {k + 1} ({page_ref(idx)})", pages[idx],
                                     sig_degree, False)
                                    for k, idx in enumerate(sig_pages))
            for spot_name, spot_text, expected_degree, own_line in degree_spots:
                compared = compare_reference_text(spot_text, expected_degree, 'degree', degree_line=True)
                extras = degree_line_extras(spot_text, expected_degree) if own_line else ""
                if extras:
                    # ตรงเป๊ะแต่มีคำเกิน = แดง (เจ้าหน้าที่สั่ง ก.ย. 2569) ต้องมาก่อน
                    # กิ่ง exact เพราะ exact ตอบแค่ว่า "มีข้อความนี้อยู่บนหน้า"
                    rep.add_verification("ชื่อปริญญา", spot_name, "fail",
                                         f"มีข้อความเกิน: {extras}")
                    rep.add("RED", "front_matter", spot_name,
                            f'บรรทัดชื่อปริญญามีข้อความเกิน: "{extras}"',
                            f'บรรทัดนี้ต้องเป็น "{expected_degree}" เท่านั้น '
                            "ไม่มีคำอื่นนำหน้าหรือต่อท้าย",
                            "ลบข้อความเกินออกจากบรรทัดชื่อปริญญา", "FORM.APPROVED_MATCH")
                    continue
                if compared['status'] == 'exact':
                    rep.add_verification("ชื่อปริญญา", spot_name, "pass")
                    continue
                if norm(expected_degree) in norm(spot_text):
                    # ตัวอักษรครบทุกตัว ต่างเฉพาะเครื่องหมายวรรคตอน/การเว้นวรรค
                    # (เช่น "M.Sc. ()" กับ "M.Sc.()") = ข้อสังเกตสีเหลือง ผ่านได้
                    # ตามที่เจ้าหน้าที่กำหนด ส.ค. 2569
                    rep.add_verification("ชื่อปริญญา", spot_name, "pending",
                                         "ต่างเฉพาะวรรคตอน/ช่องว่าง")
                    rep.add(DEGREE_SPACING_ZONE, "front_matter", spot_name,
                            f'พบชื่อปริญญาแต่เครื่องหมายวรรคตอน/ช่องว่างต่างจากข้อมูลอนุมัติ: "{compared["actual"]}"',
                            f"ข้อมูลอนุมัติ: \"{expected_degree}\"",
                            "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้", "FORM.DEGREE_SPACING")
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
            # ต้องตรวจคำเกินก่อนทุกกิ่ง — ของเดิมตรวจเฉพาะตอนเทียบได้ exact และเทียบ
            # ด้วย substring ดิบ ๆ เล่มที่เว้นวรรคต่างด้วย ("Dr.PH (PUBLIC HEALTH)"
            # กับ "Dr. P.H.") จึงหลุดไปกิ่ง "ต่างเฉพาะวรรคตอน" แล้วได้เหลืองผ่าน
            extras = degree_line_extras(abstract_text, abbr)
            if extras:
                rep.add_verification("ชื่อปริญญา", vloc, "fail",
                                     f"มีข้อความเกิน: {extras}")
                rep.add("RED", "front_matter", box,
                        f'บรรทัดชื่อปริญญาแบบย่อมีข้อความเกิน: "{extras}"',
                        f"บรรทัดนี้ต้องเป็น \"{abbr}\" เท่านั้น ไม่มีคำอื่นนำหน้าหรือต่อท้าย",
                        "ลบข้อความเกินออกจากบรรทัดชื่อปริญญา", "FORM.APPROVED_MATCH")
            elif compared['status'] == 'exact':
                rep.add_verification("ชื่อปริญญา", vloc, "pass")
            elif norm(abbr) in norm(abstract_text):
                # ตัวอักษรครบ ต่างเฉพาะวรรคตอน/ช่องว่าง = ข้อสังเกตสีเหลือง ผ่านได้
                rep.add_verification("ชื่อปริญญา", vloc, "pending", "ต่างเฉพาะวรรคตอน/ช่องว่าง")
                rep.add(DEGREE_SPACING_ZONE, "front_matter", box,
                        f'พบชื่อปริญญาแบบย่อแต่เครื่องหมายวรรคตอน/ช่องว่างต่างจากข้อมูลอนุมัติ: "{compared["actual"]}"',
                        f"ข้อมูลอนุมัติ: \"{abbr}\"",
                        "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้", "FORM.DEGREE_SPACING")
            elif not _looks_like_degree_line(compared['actual']):
                # หน้านี้ไม่มีบรรทัดชื่อปริญญาแบบย่อเลย (เจอในเล่มจริง: ข้ามจากบรรทัด
                # ชื่อ-รหัสนักศึกษาไป THESIS ADVISORY COMMITTEE เลย) ห้ามยกบรรทัดอื่น
                # มาอ้างว่า "ในเล่มเขียนว่า ..." เพราะเจ้าหน้าที่อ่านแล้วนึกว่าระบบอ่านเพี้ยน
                # ทั้งที่ของจริงคือบรรทัดนี้หายไป ซึ่งเป็นคนละวิธีแก้กัน (ต้องเพิ่มบรรทัด)
                rep.add_verification("ชื่อปริญญา", vloc, "fail", "ไม่พบบรรทัดชื่อปริญญาแบบย่อ")
                rep.add("RED", "front_matter", box,
                        "ไม่พบบรรทัดชื่อปริญญาแบบย่อในหน้านี้",
                        f'ต้องมีบรรทัด "{abbr}" อยู่ใต้บรรทัดชื่อและรหัสนักศึกษา',
                        "", "FORM.APPROVED_MATCH")
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
            _check_cover_year(rep, str(A["year"]), cover_text)

        # ---------- รายชื่อกรรมการบนหน้าลงนาม ----------
        # ถ้ามีข้อมูลกรรมการจาก eThesis → นับจำนวนเทียบกับ บฑ.1 / บฑ.2 ของหน้านั้น
        # ไม่เทียบชื่อทั้งเล่มไทยและเล่มอังกฤษ (นโยบาย ส.ค. 2569 ดู
        # _report_committee_count) ภาษาของเล่มจึงไม่มีผลกับส่วนนี้
        committees = A.get("committees") or {}
        prog_lang = A.get("program_language", "")
        english_book = prog_lang in ("international", "thai_english")
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
                      "เทียบกับ บฑ.1/บฑ.2 ระบบตรวจว่ามีบรรทัดคุณวุฒิครบทุกคน แต่ไม่ตรวจเนื้อหา")

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

            # หัวข้อที่กฎนี้ฟ้องว่า "สะกดผิด" ไปแล้ว กฎ FRONT.TOC ข้างบนต้องไม่ฟ้องซ้ำ
            toc_typos_reported = set()
            for section_kind, (section_label, actual_page_idx) in actual_toc_sections.items():
                candidates = toc_entries_by_kind.get(section_kind, [])
                if not candidates:
                    # หัวข้อที่ "มีอยู่แต่สะกดผิด" ต้องบอกว่าสะกดผิด ไม่ใช่บอกว่าไม่มี
                    # เพราะวิธีแก้คนละอย่างกัน (แก้ตัวสะกด ไม่ใช่เพิ่มบรรทัดใหม่)
                    typo = _toc_misspelled_heading(toc_lines, section_label)
                    if typo:
                        head, typo_idx = typo
                        found_msg = f'สารบัญสะกดหัวข้อนี้ผิด เขียนว่า "{head}"'
                        toc_typos_reported.add(norm(head))
                        diff = describe_diff(head, section_label)
                        if diff:
                            found_msg += f" {diff}"
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
                    # หน้าอ้างอิงจริงเลือกหลายคำ = กฎส่วนท้ายเล่มฟ้องที่หน้านั้นไปแล้ว
                    # ถ้ายังเทียบต่อจะได้ประโยคที่ขัดกันเอง (เล่มจริง ก.ย. 2569 ได้
                    # 'สารบัญใช้คำ "REFERENCES" แต่หน้าอ้างอิงจริงใช้ "REFERENCES"'
                    # เพราะเทียบกันเป็นเซตสองคำ แต่รายงานออกมาได้คำแรกคำเดียว) แล้วยัง
                    # สั่งให้แก้สารบัญที่ถูกอยู่แล้ว ทั้งที่ต้องไปแก้หัวข้อในหน้าอ้างอิง
                    elif toc_terms and ref_head:
                        page_terms = reference_terms(ref_head[0])
                        page_one_word = len(page_terms) == 1 and '/' not in ref_head[0]
                        if page_one_word and set(toc_terms) != set(page_terms):
                            rep.add(
                                "RED", "front_matter",
                                f"สารบัญ ({page_ref(entry['source_page_idx'])}) กับ"
                                f"{section_label} ({page_ref(actual_page_idx)})",
                                f'สารบัญใช้คำ "{toc_terms[0]}" แต่หน้าอ้างอิงจริงใช้ "{page_terms[0]}"',
                                f'คำในสารบัญต้องตรงกับหัวข้อในหน้าจริง คือ "{page_terms[0]}"',
                                f'แก้คำในสารบัญให้เป็น "{page_terms[0]}"', "FRONT.TOC_CONTENT",
                            )
                # เลิกตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว (กติกา ส.ค. 2569) — บันทึกเป็น
                # ข้อสังเกตสีเหลือง เล่มที่มีแต่ข้อเหล่านี้ผ่านได้
                if not entry["page_label"]:
                    rep.add(
                        TOC_PAGE_ZONE, "front_matter",
                        f"สารบัญ ({page_ref(entry['source_page_idx'])})",
                        f"หัวข้อ {section_label} ไม่มีเลขหน้า",
                        "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว",
                        "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้",
                        "FRONT.TOC_PAGE_REF",
                    )
                    continue
                actual_label = page_labels.get(actual_page_idx, "")
                if actual_label and entry["page_label"] != actual_label:
                    location = (f"สารบัญ ({page_ref(entry['source_page_idx'])}) กับ"
                                f"{section_label} ({page_ref(actual_page_idx)})")
                    # กรณีภาคผนวกหลายชุดใช้ข้อความอธิบายต่างออกไป ว่าเลขที่ระบุเป็น
                    # หน้าเริ่มของภาคผนวกอีกชุด ไม่ใช่เลขมั่ว
                    if toc_page_mismatch_is_appendix_alt(section_kind, entry["page_label"],
                                                         appendix_labels):
                        rep.add(
                            TOC_PAGE_ZONE, "front_matter", location,
                            f"สารบัญระบุหน้า {entry['page_label']} ซึ่งเป็นหน้าเริ่มของภาคผนวกอีกชุดหนึ่ง "
                            f"(ภาคผนวกชุดแรกอยู่หน้า {actual_label})",
                            "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว",
                            "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้",
                            "FRONT.TOC_PAGE_REF",
                        )
                    else:
                        rep.add(
                            TOC_PAGE_ZONE, "front_matter", location,
                            f"สารบัญระบุหน้า {entry['page_label']} แต่หัวข้อเริ่มจริงหน้า {actual_label}",
                            "เป็นข้อสังเกต ไม่ได้ตรวจเลขหน้าที่สารบัญอ้างถึงแล้ว",
                            "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้",
                            "FRONT.TOC_PAGE_REF",
                        )

            # เหลือเฉพาะหัวข้อที่กฎข้างล่างไม่ได้ฟ้อง
            toc_list_typos = [t for t in toc_list_typos
                              if norm(t[1]) not in toc_typos_reported]
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

    # ฟ้องหัวข้อ LIST OF ... ที่สะกดผิด เฉพาะที่กฎ FRONT.TOC_CONTENT ไม่ได้ฟ้องไปแล้ว
    # ต้องอยู่ท้ายสุด เพราะกฎนั้นทำงานหลังบล็อกที่เก็บรายการนี้ไว้
    for _idx, _visible, _expected, _compared in toc_list_typos:
        rep.add("RED", "front_matter", f"สารบัญ ({page_ref(_idx)})",
                mismatch_detail("หัวข้อสารบัญ", _compared, _expected),
                f'ควรเป็น "{_expected}"', "แก้การสะกดหัวข้อสารบัญ", "FRONT.TOC")

    _p("สรุปผล")
    return check_result(
        rep,
        {"document_type": doc_type, "option": option, "chapters_mode": chapters_mode,
         "n_pages": n, "approved_data": bool(approved)})
