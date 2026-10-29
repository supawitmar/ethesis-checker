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
import datetime
import threading
from collections import namedtuple
from pathlib import Path
import pdfplumber

from ethesis_rules import (
    BODY_RULES,
    CANONICAL_ACCEPTED_VARIANTS,
    CANONICAL_ENFORCED_COUNT,
    CANONICAL_OPTION_1,
    CANONICAL_OPTION_2,
    DEFAULT_RULE_BY_PART,
    MATCH_RULES,
    FRONT_MATTER_RULES,
    NOT_CHECKED,
    SIGNATURE_TEMPLATE_EN,
    SIGNATURE_TEMPLATE_TH,
    SIGNATURE_TEMPLATE_TH_TAIL,
    TOC_ALLOWED_LIST_HEADINGS,
    TYPE_MARKERS,
    rule_reference,
    rule_zone,
)
from thai_text import fix_thai_pua, fold_sara_am, fold_sara_am_in, strip_student_title

FRONT_FAILURE_ZONE = FRONT_MATTER_RULES['failure_zone']
BOLD_FAILURE_ZONE = rule_zone("FORMAT.BOLD", "ORANGE")
ABSTRACT_BOLD_ZONE = rule_zone("FORMAT.ABSTRACT_BOLD", "YELLOW")
BLANK_PAGE_ZONE = rule_zone("PAGE.BLANK", "YELLOW")
UNCERTAIN_ZONE = rule_zone("UNCERTAIN.REVIEW", "ORANGE")
TOC_PAGE_ZONE = rule_zone("FRONT.TOC_PAGE_REF", "YELLOW")
# อัปเดต 1.7 (เจ้าของยืนยัน 27 ต.ค. 2569): หน้าว่างจริง · สารบัญระบุเลขหน้าเป็นช่วง — ส้ม กดไม่ผ่านแล้วใช้ fail_text
EMPTY_PAGE_ZONE = rule_zone("PAGE.EMPTY", "ORANGE")
TOC_RANGE_ZONE = rule_zone("FRONT.TOC_PAGE_RANGE", "ORANGE")
ABSTRACT_COMMA_ZONE = rule_zone("FRONT.ABSTRACT_COMMA", "YELLOW")
DEGREE_SPACING_ZONE = rule_zone("FORM.DEGREE_SPACING", "YELLOW")
SIG_LABEL_ZONE = rule_zone("PAGE.SIGNATURE_LABEL", "ORANGE")
# keyword เกิน 5 คำ = ข้อสังเกต ผ่านได้ (เจ้าหน้าที่สั่ง ก.ย. 2569) กฎอื่นของ
# FRONT.ABSTRACT (ภาษาครบ จำนวนหน้า ชื่อเรื่อง รายชื่อกรรมการ) ยังเป็นแดงเหมือนเดิม
KEYWORD_COUNT_ZONE = rule_zone("FRONT.KEYWORD_COUNT", "YELLOW")
# ฟอนต์ทำให้อ่านรหัส/ชื่อนักศึกษาไม่ออก = สีส้ม (เจ้าหน้าที่สั่ง ก.ย. 2569 "ถ้ามันเพี้ยนแบบนี้
# งั้นควรใส่สีส้ม") เดิมปล่อยผ่านเป็นข้อมูลประกอบ ซึ่งไม่มีปุ่มให้กดเมื่อเปิดหน้าจริงแล้วพบว่าผิด
FONT_UNREADABLE_ZONE = rule_zone("FORM.FONT_UNREADABLE", "ORANGE")
# วิธีแก้บนการ์ดสีส้มเมื่อเล่มสะกดต่างเฉพาะวรรณยุกต์/สระ/ำ แต่ระบบยืนยันไม่ได้ว่าอ่านถูก (ดู Report.add)
MARKS_UNRELIABLE_FIX = ("ระบบอ่านวรรณยุกต์และสระจากไฟล์นี้ได้ไม่แน่นอน "
                        "เปิดหน้านี้ดูด้วยตา ถ้าพิมพ์ถูกต้องให้กดผ่าน")
# เลขหน้าที่เรียงไม่ต่อเนื่อง = ส้ม ตามที่เจ้าหน้าที่สั่ง (ก.ย. 2569) ทั้งส่วนนำและเนื้อหา
# กฎอื่นของ PAGE.NUMBERING (ชนิดเลขหน้าผิด / ไม่มีเลขหน้า / เลขหน้าอารบิกไม่เริ่มที่บทที่ 1)
# ยังเป็นแดงเหมือนเดิม จึงต้องแยกรหัสกฎ ไม่ใช่ใส่ failure_zone ให้ PAGE.NUMBERING ทั้งก้อน
PAGE_SEQUENCE_ZONE = rule_zone("PAGE.NUMBERING_SEQUENCE", "ORANGE")
# ส่วนเนื้อหา/ส่วนท้าย = ข้อสังเกต (อัปเดต 1.8 เจ้าของสั่ง 28 ต.ค. 2569) — ส่วนนำยังส้ม
PAGE_SEQUENCE_BODY_ZONE = rule_zone("PAGE.NUMBERING_SEQUENCE_BODY", "YELLOW")
# หน้าลงนามเป็นไฟล์สแกน = ส้ม (อัปเดต 1.8.2 เจ้าของสั่ง 28 ต.ค. 2569 — 1.5.2 เป็นแดง) · ไม่พบหน้าลงนามยังแดง
SCANNED_SIGNATURE_ZONE = rule_zone("FRONT.APPROVAL_SCANNED", "ORANGE")

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


# ขีดทุกแบบนับเป็นขีดเดียวกัน (เจ้าหน้าที่สั่ง 20 ต.ค. 2569): ขีดสั้น "-" ขีดยาว "–" "—" ขีดไม่ตัดบรรทัด "‑"
# เครื่องหมายลบ "−" ฯลฯ ต่างกันแค่ความยาวตามฟอนต์/ภาษา แต่คือขีดเหมือนกัน — เล่มจริงพิมพ์ชื่อเรื่อง "Co–PRODUCTION"
# (ขีดยาว) ส่วนข้อมูลอนุมัติเป็น "Co-PRODUCTION" แล้วถูกฟ้องว่าชื่อเรื่องไม่ตรง
# ใช้ตอน "ตัดสินว่าตรงหรือไม่" เท่านั้น ข้อความที่ยกมาจากเล่มยังแสดงตามที่พิมพ์จริง
_DASH_VARIANTS = re.compile('[\u2010\u2011\u2012\u2013\u2014\u2015\u2212\uFE58\uFE63\uFF0D]')


def fold_dashes(s):
    """แทนขีดทุกแบบด้วยขีดสั้น "-" (U+002D)"""
    return _DASH_VARIANTS.sub('-', s or '')


# ---------- ตัวสะกดไทยครบทุกตัว (ใช้ "ตัดสิน" ว่าเล่มสะกดตรงหรือไม่) ----------
# norm() ข้างบนตัดวรรณยุกต์ สระบน-ล่าง การันต์ทิ้ง และมอง ำ เป็น า เพราะ PDF ทำตัวเล็กพวกนี้หายหรือสลับที่บ่อย
# จึงใช้ "หา" ข้อความได้ดี (หัวข้อ ส่วนของเล่ม บรรทัดที่จะยกมาแสดง) แต่ใช้ตัดสินไม่ได้ — เจ้าหน้าที่สั่ง
# 6 ต.ค. 2569 ว่าเล่มพิมพ์ "นา" ที่ควรเป็น "นำ" (หรือกลับกัน) คือสะกดผิด ต้องเป็นสีแดง รวมถึงวรรณยุกต์
# ตัวเทียบชุดนี้จึงคงทุกตัวไว้ (สระอำทุกแบบนับเป็นสระอำผ่าน fold_sara_am · ตัวเล็กบน/ล่างของตัวอักษรเดียวกัน
# เรียงลำดับมาตรฐานก่อน เพราะไฟล์เก็บลำดับต่างกันได้ทั้งที่ตาเห็นเหมือนกัน เช่น "ท่ี" กับ "ที่")
# ใช้เฉพาะจุดที่ "ตัดสินว่าตรงหรือไม่" ส่วนการหาตำแหน่งยังใช้ norm() — ถ้าหาด้วยตัวเทียบนี้ หัวข้อที่ฟอนต์ทำ
# วรรณยุกต์หายจะหาไม่เจอ แล้วเกิดข้อ "ไม่พบ" ต่อกันเป็นพรวนทั้งเล่ม
_SPELLING_CHAR = re.compile('[A-Z0-9ก-๙]')
_UNIT_SEP = chr(0)


def _mark_rank(mark):
    """ลำดับมาตรฐานของตัวเล็กบน/ล่างบนตัวอักษรเดียวกัน: สระ (รวมไม้ไต่คู้ นิคหิต) → วรรณยุกต์ → การันต์"""
    return (_MARK_ORDER.get(mark, 0), mark)


def _spelling_units(text, keep_case=False):
    """ตัวอักษรทีละตัวพร้อมตัวเล็กบน/ล่างของมัน (เรียงลำดับมาตรฐาน)

    ถอดตัวเล็กบน/ล่างออกแล้วแทน ำ ด้วย า จะได้ norm(text) พอดี — คือ "ตำแหน่ง" เดียวกับที่ norm() หาเจอ
    """
    units = []
    folded = fold_sara_am(text)
    for ch in (folded if keep_case else folded.upper()):
        if _TH_MARKS.match(ch):
            if units:
                units[-1].append(ch)
        elif _SPELLING_CHAR.match(ch.upper()):
            units.append([ch])
    return [unit[0] + ''.join(sorted(unit[1:], key=_mark_rank)) for unit in units]


def spelling_key(text):
    """ตัวเทียบ "สะกดตรงทุกตัวอักษร" — เหมือน norm() แต่คง ำ และตัวเล็กบน/ล่างไว้ (ดูหัวข้อข้างบน)

    คั่นทุกตัวด้วยตัวแบ่งหน่วย การค้นข้อความย่อย (spelled_in) จึงตรงขอบตัวอักษรเสมอ — "ย" ไม่ไปตรงกับ "ย่"
    """
    units = _spelling_units(text)
    return _UNIT_SEP + _UNIT_SEP.join(units) + _UNIT_SEP if units else ''


def same_spelling(a, b):
    """สองข้อความสะกดตรงกันทุกตัวอักษร (ไม่สนช่องว่าง/วรรคตอน/ตัวพิมพ์เล็ก-ใหญ่ เหมือน norm())"""
    return spelling_key(a) == spelling_key(b)


def spelled_in(needle, haystack):
    """needle อยู่ใน haystack โดยสะกดตรงทุกตัวอักษร — ตัวตัดสินแทน norm(needle) in norm(haystack)"""
    return spelling_key(needle) in spelling_key(haystack)


def marks_only_difference(a, b):
    """ตัวอักษรครบตรงกันทุกตัว ต่างเฉพาะวรรณยุกต์ สระบน-ล่าง การันต์ หรือ ำ กับ า"""
    return norm(a) == norm(b) and not same_spelling(a, b)


_MARK_RUN = re.compile(_TH_MARKS.pattern + '{2,}')
# ช่องว่างตามด้วยสระที่ขึ้นต้นคำไม่ได้ (ะ า ำ ๅ) หรือตัวเล็กบน/ล่าง ไม่มีในข้อความไทยที่อ่านถูก — เป็นร่องรอยว่า
# ระบบอ่านบรรทัดนั้นเพี้ยน เช่นฟอนต์ที่เก็บวงกลมของ ำ เป็นช่องว่างจริง ("บทน า") ตัวสะกดระดับวรรณยุกต์/สระ
# ของข้อความนั้นจึงเชื่อไม่ได้ (ดู Report.add) — ไม่รวม ๆ ซึ่งราชบัณฑิตยสถานให้เว้นวรรคหน้า ("ต่าง ๆ")
_READ_DEFECT = re.compile('[ ][ะาำๅ' + _TH_MARKS.pattern[1:-1] + ']')


def _has_read_defect(text):
    return bool(_READ_DEFECT.search(soft(text)))


def _canonical_marks(text):
    """เรียงตัวเล็กบน/ล่างที่ติดกันเป็นลำดับมาตรฐาน โดยไม่แตะอย่างอื่น (ใช้ก่อนชี้จุดต่างให้คนอ่าน)"""
    return _MARK_RUN.sub(lambda m: ''.join(sorted(m.group(0), key=_mark_rank)), text or '')


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
    for row in _group_into_lines(_thai_chars(chars,
                                             _fonts_missing_sara_aa(page))):
        line = _compose_thai_line(row)
        if line:
            out_lines.append(line)
    return '\n'.join(out_lines)


# ความสูงสูงสุดที่ถือว่าอักขระยังอยู่ "บรรทัดเดียวกัน" — ระยะบรรทัดจริงของเล่มห่างกัน
# 13-18 pt ส่วนสองคอลัมน์ในแถวเดียวกันเยื้องกันไม่ถึง 2 pt ค่านี้จึงแยกสองอย่างได้ชัด
_LINE_TOLERANCE = 3.0

# ตัวห้อย/ตัวยก (เช่น เลข 2 ของ CO2) วางต่ำหรือสูงกว่าบรรทัดเกิน 3 pt และ **ฟอนต์เล็กลง**
# ถ้าใช้ค่าเดียวกับบรรทัดปกติ มันจะกลายเป็นคนละบรรทัด แล้วถูกเรียงตามลำดับบนลงล่าง
# ไปต่อท้ายบรรทัดถัดไป — เล่มจริง 6237391 EGCH/M (ก.ย. 2569) หน้าลงนามเป็นแบบนี้
#
#   top=148.8 size=14  "BIOMETHANE FUEL FROM BIOGAS VIA  METHANATION"   <- ช่องว่างที่ CO2 ควรอยู่
#   top=153.1 size=9   "CO2"                                            <- ตัวห้อย ต่ำลง 4.3 pt
#
# ระบบจึงอ่านได้เป็น "...VIA METHANATION" แล้วตามด้วย "CO2" คนละบรรทัด ทำให้ฟ้องว่า
# ขาด "CO2" และมี "CO2" เกินมาพร้อมกัน ทั้งที่เล่มพิมพ์ถูกต้อง (เจ้าหน้าที่แจ้ง
# "มันก็คือ CO2 เหมือนกันนั่นแหละ")
#
# ผ่อนเฉพาะอักขระที่ฟอนต์เล็กกว่าบรรทัดอย่างชัดเจน จึงไม่กระทบการแยกบรรทัดปกติ
# (บรรทัดจริงห่างกัน 13-18 pt ซึ่งเกินค่านี้อยู่มาก)
_SUBSCRIPT_TOLERANCE = 7.0
_SUBSCRIPT_SIZE_RATIO = 0.85


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
    lines, start, start_size = [], None, 0.0
    for c in sorted(chars, key=lambda c: (float(c['top']), float(c['x0']))):
        top = float(c['top'])
        size = float(c.get('size') or 0)
        # ตัวห้อย/ตัวยกของสูตรเคมี: ฟอนต์เล็กกว่าบรรทัดชัดเจน และเยื้องไม่เกินหนึ่งช่วงสั้น ๆ
        subscript = (start is not None and start_size
                     and size and size <= start_size * _SUBSCRIPT_SIZE_RATIO
                     and top - start <= _SUBSCRIPT_TOLERANCE)
        if start is None or (top - start > _LINE_TOLERANCE and not subscript):
            lines.append([])
            start, start_size = top, size
        lines[-1].append(c)
    return lines


_CID_GLYPH = re.compile(r'\(cid:\d+\)')
# ฟอนต์สูตรคณิตศาสตร์ (Cambria Math ฯลฯ) — x̄ ในตารางสถิติวาดด้วย glyph ที่ไม่มีรหัสยูนิโค้ด ("(cid:1876)") กับขีดบน
# ที่กว้างศูนย์ ดูเหมือนฟอนต์เพี้ยนทุกประการ แต่ไม่ใช่ข้อความไทย จึงไม่ทำให้อ่านวรรณยุกต์ผิด
_MATH_FONT = re.compile(r'math', re.I)


def font_damage_score(page, text=None):
    """หน้านี้มีร่องรอยว่า "ฟอนต์ในไฟล์บอกอักขระผิด" กี่ตัว — 0 = ปกติ

    สองสัญญาณที่ฟันธงได้ ไม่ใช่การเดา

    1. อักขระกว้างศูนย์ที่ไม่ใช่สระ/วรรณยุกต์ — ฟอนต์ map วรรณยุกต์ไปเป็นตัวอักษรอื่น
       (ดูเหตุผลเต็มใน _thai_chars ซึ่งทิ้งอักขระพวกนี้ก่อนประกอบข้อความอยู่แล้ว)
    2. (cid:N) — pdfminer หาคำแปลของ glyph นั้นไม่ได้เลย

    วัดกับเล่มจริง 6 เล่ม: เล่มที่ถูกต้อง 4 เล่มได้ 0 ทุกหน้า ไม่มี false positive เลย
    ส่วนเล่มที่ฟอนต์เพี้ยนจับได้ทั้งสองเล่ม (เล่มหนึ่ง 38 หน้า อีกเล่ม 9 หน้า) ตรงกับ
    หน้าที่ตรวจด้วยตาแล้วพบว่าเพี้ยนจริง

    สองอย่างที่ไม่นับ เพราะไม่ทำให้อ่านข้อความไทยผิด (เดิมนับ เล่มจริงสองเล่ม ต.ค. 2569 ขึ้นว่าฟอนต์เพี้ยน
    119 หน้า และ 4 หน้า ทั้งที่อ่านได้ครบ แล้วข้อสะกดผิดระดับวรรณยุกต์ของเล่มนั้นถูกลดเป็นส้มทั้งหมด)
    - รหัส PUA ของฟอนต์ไทย (วรรณยุกต์ตัวต่ำ/เยื้องซ้าย) — แปลงกลับได้ตรงตัว (thai_text.fix_thai_pua)
    - อักขระจากฟอนต์สูตรคณิตศาสตร์ (x̄ ในตารางสถิติ ดู _MATH_FONT)
    """
    bad = formula_cids = 0
    for c in (getattr(page, 'chars', None) or []):
        t = fix_thai_pua(c.get('text'))
        wide = float(c.get('x1', 0)) - float(c.get('x0', 0)) >= 0.5
        if _MATH_FONT.search(c.get('fontname') or ''):
            formula_cids += bool(wide and _CID_GLYPH.fullmatch(t))
            continue
        if not t or t == ' ' or _TH_MARKS.match(t):
            continue
        if not wide:
            bad += 1
    return bad + max(0, len(_CID_GLYPH.findall(text or '')) - formula_cids)


# ฟอนต์ไทยในเล่มที่ตาราง ToUnicode เหลื่อมช่อง ทำให้สระ า กลายเป็น ำ ทั้งเล่ม
# เล่มจริงเล่มหนึ่ง (7 ต.ค. 2569) ฟอนต์ย่อย BrowalliaNew ที่ Word ฝังมาเขียนไว้ว่า
#
#     <00B3> <00B4> <0E30>            ะ ั                 <- ถูก
#     <00B5> <0E33>                   า -> ำ              <- ผิด ต้องเป็น 0E32
#     <00CD> <00CE> [<0E4C> <0020>]   ์ ํ -> ช่องว่าง      <- ผิด ต้องเป็น 0E4D
#
# (ลำดับจริงของฟอนต์คือ ะ ั า ำ ิ ... ตัวเขียนตารางข้ามช่องที่ไม่ได้ใช้แล้วเลื่อนค่าตามมา)
# นิคหิตที่กลายเป็นช่องว่าง _thai_chars ซ่อมอยู่แล้ว ครึ่งบนของ ำ จึงยังมา ส่วนสระ า
# ที่กลายเป็น ำ ยังไม่มีใครซ่อม ระบบจึงอ่าน "ทำนายการ" ได้ "ทำนำยกำร" และตัวตัดสิน
# การสะกด (spelling_key 6 ต.ค. 2569) แยก ำ กับ า ออกจากกัน เล่มที่พิมพ์ถูกจึงถูกฟ้องว่า
# ชื่อเรื่องและชื่อนักศึกษาไม่ตรงข้อมูลอนุมัติ
_SARA_AA, _SARA_AM = 'า', 'ำ'
# จำนวนอักษรไทยในตารางที่ถือว่า "ฟอนต์นี้ใช้พิมพ์ข้อความไทยจริง ไม่ใช่คำสองสามคำ"
# ฟอนต์ย่อยมีแต่ glyph ที่เล่มใช้จริง เอกสารไทยที่ใช้อักษรไทยหลากหลายถึงขนาดนี้
# แล้วไม่ใช้สระ า เลยไม่มี — ตารางที่ "ไม่มี า แต่มี ำ" จึงเป็นหลักฐานว่า map ผิด
# ไม่ใช่การเดาจากสถิติของข้อความในหน้า
_THAI_SUBSET_MIN = 20
# อักษรไทยทุกชนิดรวมสระ วรรณยุกต์ เลขไทย — ใช้นับความกว้างของตารางฟอนต์เท่านั้น
# ตั้งชื่อแยกจาก _THAI_LETTER และ _THAI_CONSONANT ที่ประกาศข้างล่าง ชื่อซ้ำกันตัวหลัง
# จะทับตัวแรกเงียบ ๆ แล้วตัวที่เคยถูกใช้ไปตัดสินอย่างอื่นจะเปลี่ยนความหมายไป
_THAI_IN_FONT_TABLE = re.compile('[ก-๙]')


def _fonts_missing_sara_aa(page):
    """ชื่อฟอนต์บนหน้านี้ที่ตาราง ToUnicode ไม่มีสระ า เลยแต่มี ำ (ดูหัวข้อข้างบน)

    อ่านจากตารางของฟอนต์เอง ไม่ใช่จากข้อความที่อ่านได้ หน้าที่ข้อความสั้นจนไม่มี
    สระ า จึงไม่ถูกตัดสินผิด และฟอนต์อื่นบนหน้าเดียวกันไม่ถูกแตะ

    อ่านตารางไม่ได้ (pdfminer เปลี่ยน API / ฟอนต์ไม่มี ToUnicode) = คืนเซตว่าง
    แล้วอ่านแบบเดิม ดีกว่าซ่อมด้วยข้อมูลที่ไม่ครบ
    """
    try:
        from io import BytesIO
        from pdfminer.cmapdb import CMapParser, FileUnicodeMap
        from pdfminer.pdftypes import resolve1, stream_value
        table = resolve1((getattr(page, 'page_obj', None).resources or {}).get('Font'))
        items = list((table or {}).values())
    except Exception:
        return frozenset()
    missing, fine = set(), set()
    for ref in items:
        try:
            spec = resolve1(ref) or {}
            name = getattr(resolve1(spec.get('BaseFont')), 'name', None)
            if not name or 'ToUnicode' not in spec:
                continue
            table_map = FileUnicodeMap()
            CMapParser(table_map,
                       BytesIO(stream_value(spec['ToUnicode']).get_data())).run()
            letters = {ch for value in table_map.cid2unichr.values() for ch in value
                       if _THAI_IN_FONT_TABLE.match(ch)}
        except Exception:
            continue
        if (len(letters) >= _THAI_SUBSET_MIN
                and _SARA_AM in letters and _SARA_AA not in letters):
            missing.add(name)
        else:
            fine.add(name)
    # ชื่อฟอนต์ซ้ำกันสองตัวบนหน้าเดียว (map ต่างกัน) = พิสูจน์ไม่ได้ว่าตัวไหนเป็นตัวไหน
    # จาก fontname ของ char จึงไม่แตะทั้งคู่
    return frozenset(missing - fine)


def _thai_chars(chars, missing_sara_aa=frozenset()):
    """แปลง "ช่องว่างกว้างศูนย์" ให้เป็นนิคหิต ก่อนประกอบข้อความ

    ฟอนต์ไทยบางตัวใน PDF map นิคหิต (ํ ซึ่งเป็นครึ่งบนของสระ ำ) ไปเป็นอักขระเว้นวรรค
    ทำให้ "จำลองทำนาย" ถูกอ่านออกมาเป็น "จา ลองทา นาย" — เจ้าหน้าที่อ่านข้อความใน
    รายงานไม่รู้เรื่องว่าหมายถึงตรงไหนของเล่ม

    แยกจากช่องว่างจริงได้ชัดเจนด้วยความกว้าง: สำรวจเล่มจริง 9 เล่มพบช่องว่างจริง
    20,303 ตัวกว้าง >= 0.5 pt ทุกตัว ส่วนกว้างศูนย์มี 62 ตัวและตามด้วยสระ า ถึง 55 ตัว
    (จำ สำ คำ ทำ ดำ นำ กำ ซ้ำ) จึงไม่ใช่การเว้นวรรคของจริงแน่นอน

    เมื่อกลายเป็นนิคหิตแล้ว _compose_thai_line จะผูกกลับเข้าพยัญชนะฐานเอง
    ได้ "จํา" แล้วรวมเป็น "จำ" ตามปกติ

    รหัส PUA ของฟอนต์ไทย (วรรณยุกต์ตัวต่ำ/เยื้องซ้าย พยัญชนะตัดเชิง) แปลงกลับเป็นอักษรจริงก่อนอย่างอื่น
    (thai_text.fix_thai_pua) ไม่งั้นวรรณยุกต์พวกนี้เป็น "อักขระกว้างศูนย์ที่ไม่ใช่สระ/วรรณยุกต์" แล้วถูกทิ้ง
    — เล่มจริง ต.ค. 2569 จึงอ่าน "ได้รับการพิจารณา" ได้ "ไดรับการพิจารณา" ทั้งเล่ม

    missing_sara_aa = ชื่อฟอนต์ที่ตาราง ToUnicode ไม่มีสระ า (ดู _fonts_missing_sara_aa)
    ของฟอนต์นั้น ำ ที่อ่านได้คือสระ า ตัวจริง ส่วน ำ ของเล่มมาจากนิคหิต + า อยู่แล้ว
    """
    out = []
    for c in chars:
        text = c.get('text') or ''
        real = fix_thai_pua(text)
        if real == _SARA_AM and c.get('fontname') in missing_sara_aa:
            real = _SARA_AA
        if real != text:
            c, text = {**c, 'text': real}, real
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
    # (ถ้าไม่ยุบจะได้ "คํำสํำคัญ" แทน "คำสำคัญ") และ นิคหิต + วรรณยุกต์ + า คือ "น้ำ" ที่ระบบอ่านจาก
    # PDF ได้ลำดับ "นํ้า" — รวมทุกแบบเป็น ำ (ดู thai_text.fold_sara_am)
    return fold_sara_am(line).strip()


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


def _is_header_page_label(token, front=False, front_style=None):
    """เลขหน้าที่ยอมให้อยู่ในหัวกระดาษได้ ตามส่วนของเล่มและภาษาของเล่ม

    ส่วนนำ: เล่มหลักสูตรไทย ("thai") พยัญชนะไทย (ก ข ค) · เล่มอังกฤษ ("roman") เลขโรมัน
    (i ii iii) · ยังไม่รู้ภาษาเล่ม (None) รับได้ทั้งสองแบบ · ส่วนเนื้อหา/ส่วนท้าย: เลขอารบิกเท่านั้น
    """
    if front:
        roman = bool(re.fullmatch(r'[ivxlcdm]{1,10}', token, re.I))
        thai = bool(re.fullmatch(r'[ก-ฮ]', token))
        if front_style == "thai":
            return thai
        if front_style == "roman":
            return roman
        return roman or thai
    return bool(re.fullmatch(r'\d{1,4}', token))


def header_tokens(pdf_page, words=None):
    """คำทั้งหมดในแถบหัวกระดาษ (บนสุดของหน้า) เรียงตามที่ pdfplumber อ่านได้

    เลขหน้าอยู่ในระยะขอบบน (~5-6% ของความสูง) ส่วนเนื้อความเริ่ม ~9%+ จึงตัดที่ 8%
    เพื่อดูเฉพาะแถบหัวกระดาษ · words = คำของหน้าที่ดึงไว้แล้ว (ไม่ต้องดึงซ้ำ)
    """
    height = float(getattr(pdf_page, 'height', 0) or 0)
    if not height:
        return []
    cutoff = height * 0.08
    tokens = []
    for word in (pdf_page.extract_words() if words is None else words) or []:
        if float(word.get('top', height)) >= cutoff:
            continue
        # รหัส PUA ของฟอนต์ไทยที่ดึงมาเป็นกล่อง แปลงกลับเป็นอักษรจริงก่อน (thai_text.fix_thai_pua)
        token = fix_thai_pua(word.get('text', '') or '').strip()
        if token:
            tokens.append(token)
    return tokens


def edge_page_number_hint(words, width, height):
    """แถบหัว/ท้ายกระดาษมีอะไรที่อาจเป็นเลขหน้าไหม — กันข้อ "ไม่ได้พิมพ์เลขหน้าไว้" ของส่วนเนื้อหา (อัปเดต 1.8)

    _extract_page_label อ่านเลขหน้าเฉพาะบรรทัดแรก/สุดท้ายที่เป็นเลขล้วน เล่มจริง (ต.ค. 2569) พิมพ์เลขหน้าครบทุกหน้า
    แต่อ่านไม่ได้ 63 หน้า: เลขอยู่บรรทัดเดียวกับ running head (52) · หน้าแนวนอนที่เลขเลยแถบ 8% ของความสูงไปนิด (8) ·
    เลขติดกับตัวอักษรข้างหน้า (3) — ข้อนี้เป็นสีแดง จึงนับว่า "มีเลขหน้า" เมื่อแถบขอบบน/ล่างมีตัวเลข หรือมีคำอังกฤษ
    สั้น ๆ (ฟอนต์ที่ map ตัวเลขเป็นตัวอักษร ดู unreadable_id_digits) · วัดแถบจากด้านยาวของหน้า หน้าแนวนอนจึงได้แถบเท่าหน้าตั้ง
    """
    if not height:
        return False
    band = 0.08 * max(width or 0, height)
    for word in words or ():
        if not (float(word.get('top', band)) < band or float(word.get('bottom', 0)) > height - band):
            continue
        token = fix_thai_pua(word.get('text', '') or '').strip()
        if re.search(r'\d', token) or re.fullmatch(r'[A-Za-z]{1,4}', token):
            return True
    return False


def rotated_page_label(words, width, height):
    """เลขหน้าที่พิมพ์เป็นตัวอักษรหมุน (upright=False) ที่ขอบหน้า — '' ถ้าไม่มี (อัปเดต 1.8)

    หน้าตารางแนวนอนของเล่มจริง (ต.ค. 2569) พิมพ์เลขหน้าแนวตั้งที่ขอบขวา _page_text จัดบรรทัดตาม top อักษรหมุน
    แต่ละตัวจึงเป็นบรรทัดของตัวเอง _extract_page_label ได้แค่หลักเดียว ("44" → "4", "103" → "3") แล้วฟ้องเลขหน้า
    ไม่ต่อเนื่องผิด 13 ข้อ — คำของ pdfplumber รวมอักษรหมุนเป็นคำเดียวถูกต้อง จึงอ่านเลขจากคำ (ตัวเลขล้วน ที่ขอบ 12%)
    """
    edge_x, edge_y = 0.12 * (width or 0), 0.12 * (height or 0)
    for word in words or ():
        if word.get('upright', True):
            continue
        token = (word.get('text') or '').strip()
        if not re.fullmatch(r'\d{1,4}', token):
            continue
        if (float(word.get('x0', edge_x)) < edge_x or float(word.get('x1', 0)) > (width or 0) - edge_x
                or float(word.get('top', edge_y)) < edge_y or float(word.get('bottom', 0)) > (height or 0) - edge_y):
            return str(int(token))
    return ""


def merge_rotated_labels(page_labels, rotated_labels, edge_numbered):
    """ใช้เลขหน้าอักษรหมุน (rotated_page_label) แทนเลขที่อ่านจากข้อความ — แก้ page_labels / edge_numbered ในที่

    ข้อความของหน้าเหลือหลักเดียวของเลขนั้นที่บรรทัดแรก/สุดท้าย ("44" อ่านได้ "4") จึงใช้ทั้งคำเมื่อเลขที่อ่านได้เป็นหัว
    หรือท้ายของมัน · อ่านจากข้อความไม่ได้เลย = ไม่เชื่อเป็นเลขหน้า (อาจเป็นตัวเลขในตารางที่หมุน) แต่ก็ไม่นับว่าหน้านั้น
    ไม่ได้พิมพ์เลขหน้า (_report_unnumbered_body_pages) · หน้าที่ไม่มีอักษรหมุนไม่เปลี่ยน
    """
    for i, label in rotated_labels.items():
        read = page_labels.get(i, "")
        if read and (label.startswith(read) or label.endswith(read)):
            page_labels[i] = label
        elif not read:
            edge_numbered.add(i)


def header_extra_text(pdf_page, front=False, tokens=None, front_style=None):
    """ข้อความในแถบหัวกระดาษที่ไม่ใช่เลขหน้าตามกฎของส่วนนั้น คืน '' ถ้ามีแต่เลขหน้า/ว่าง

    หัวกระดาษมีได้เพียงเลขหน้าเท่านั้น (ห้ามมี running head หรือชื่อบท) — ส่วนเนื้อหา/
    ส่วนท้ายเป็นเลขอารบิก ส่วนนำเป็นตามภาษาเล่ม — ก ข ค (หลักสูตรไทย) หรือ i ii iii (อังกฤษ)
    (เจ้าหน้าที่สั่ง 15 ต.ค. 2569 "เลขหน้าในส่วนนำต้องเป็นตามกฎ คือ ก หรือ i แต่ถ้ามีข้อความอื่น
    ปนด้วยก็ให้แจ้ง" และ "หัวกระดาษให้เฉพาะของแต่ละภาษา") เลขโรมัน/พยัญชนะไทยในหัวกระดาษ
    ของบทจึงนับเป็นข้อความอื่น เลขอารบิกในส่วนนำก็เช่นกัน และเลขหน้าของอีกภาษาในส่วนนำด้วย
    front_style = "thai" / "roman" / None (ไม่รู้ภาษาเล่ม รับทั้งสองแบบ)
    """
    if tokens is None:
        tokens = header_tokens(pdf_page)
    return ' '.join(t for t in tokens
                    if not _is_header_page_label(t, front, front_style)).strip()


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


# ป้ายบทบาทใต้คุณวุฒิที่บางเล่มพิมพ์เพิ่ม (Chair / Member / Candidate ...) ไม่ใช่คุณวุฒิ
# เทียบทั้งช่อง (norm) ไม่ใช่ค้นคำย่อย — คุณวุฒิจริงไม่มีทางเป็นคำเหล่านี้ล้วน ๆ
_SIG_ROLE_LABELS = frozenset(norm(label) for label in (
    'Candidate', 'Chair', 'Chairperson', 'Chairman', 'Member', 'Major advisor',
    'Co-advisor', 'Advisor', 'ผู้วิจัย', 'ประธาน', 'ประธานกรรมการ', 'กรรมการ',
    'อาจารย์ที่ปรึกษาหลัก', 'อาจารย์ที่ปรึกษาร่วม'))


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
    chars = _thai_chars(getattr(pdf_page, 'chars', None) or [],
                        _fonts_missing_sara_aa(pdf_page))
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
    return [{**w, 'text': fold_sara_am(w.get('text'))} for w in words]


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
    # แถวชื่อ = บรรทัดถัดจากเส้นประ; แถวใต้ชื่อ = ทุกบรรทัดจนถึงเส้นประถัดไป (ช่องของคนนั้น)
    # คุณวุฒิหาจากข้อความในช่อง ไม่ใช่ "บรรทัดถัดจากชื่อ" เป๊ะ — ดู qual_cell ข้างล่าง
    name_rows, qual_rows = [], []
    for i in range(len(lines) - 1):
        if not line_dotted[i]:
            continue
        name_rows.append(lines[i + 1])
        below = []
        for j in range(i + 2, len(lines)):
            if line_dotted[j]:
                break
            below.append(lines[j])
        qual_rows.append(below)

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

    def qual_cell(rows, left):
        """คุณวุฒิใต้ชื่อ = ข้อความแรกในช่องเดียวกัน ใต้บรรทัดชื่อ ถึงเส้นประถัดไป

        ข้ามบรรทัดที่ช่องนี้ว่าง และป้ายบทบาท (Chair / Member / Candidate ...)
        เดิมดูแค่ "บรรทัดถัดจากชื่อ" เล่มจริง 6736545 PHIE/M ป้าย "Candidate" ของช่องซ้าย
        อยู่สูงกว่าคุณวุฒิของช่องขวา 7 pt จึงแยกเป็นคนละบรรทัด บรรทัดถัดจากชื่อฝั่งขวาว่าง
        ระบบฟ้องแดง "ไม่พบคุณวุฒิ" ทั้งที่คุณวุฒิพิมพ์อยู่ใต้ชื่อ (เจ้าหน้าที่แนะนำให้ดูจาก
        ข้อความในหน้า ไม่ใช่บรรทัดเป๊ะ) — เล่มที่คุณวุฒิอยู่บรรทัดถัดจากชื่อได้ผลเท่าเดิม
        """
        for row in rows or ():
            text = cell(row, left)
            if text and norm(text) not in _SIG_ROLE_LABELS:
                return text
        return ''

    members, member_quals, member_raw = {}, {}, {}
    # แถวเส้นประสุดท้ายคือช่องสถาบัน (คณบดี / ประธานหลักสูตร) ไม่ใช่กรรมการ — ตัดทิ้งเสมอ
    # (เดิมตัดด้วย [:5] ซึ่งพึ่งว่าต้องอ่านเส้นประเจอครบ 6 แถวพอดี ถ้าเจอไม่ครบ
    #  แถวคณบดีจะเลื่อนเข้ามาเป็นกรรมการ แล้วฟ้องว่ามีชื่อนอกรายชื่ออนุมัติ)
    member_rows = list(zip(name_rows, qual_rows))[:-1][:5]
    for idx, (nrow, qrow) in enumerate(member_rows):     # 0..4 = ระดับกรรมการ
        right = cell(nrow, left=False)
        members[idx + 1] = _sig_clean_name(right)                    # ขวา → 1..5
        member_raw[idx + 1] = right
        member_quals[idx + 1] = _sig_qual_text(qual_cell(qrow, left=False))
        if idx >= 1:
            left = cell(nrow, left=True)
            members[10 - idx] = _sig_clean_name(left)                # ซ้าย → 9,8,7,6
            member_raw[10 - idx] = left
            member_quals[10 - idx] = _sig_qual_text(qual_cell(qrow, left=True))
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


# คำนำหน้าชื่อ "นักศึกษา" ตัดออกก่อนเทียบเสมอ (เจ้าหน้าที่ ก.ค. 2569) — ตัวตัดอยู่ที่ thai_text.strip_student_title
# ใช้ตัวเดียวกับตัวนำเข้า eThesis ซึ่งตัดตั้งแต่ตอนอ่านไฟล์เข้าฟอร์ม (เจ้าหน้าที่ 6 ต.ค. 2569)
_strip_student_title = strip_student_title


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
# คำนำหน้านามเปลี่ยนเป็นห้ามมีทั้งสามหน้า (เจ้าหน้าที่ 6 ต.ค. 2569: "ถ้าสมมุติว่า ไปตรวจแล้ว ในเล่มกลับมีคำนำหน้านาม
# ปรากฏไม่ว่าจะหน้าปก หน้าลงนาม หรือหน้าบทคัดย่อ ให้แจ้งสีส้มไว้ ถ้าชื่อที่ตามมาสะกดถูก แต่ให้แดง ถ้าสะกดผิด")
# — สะกดผิด = แดงจากการเทียบชื่อใน run_check ก่อนถึงตัวตรวจนี้ · ตัวตรวจนี้ทำงานเฉพาะเมื่อชื่อสะกดถูกแล้ว
_STUDENT_NAME_STYLE = {
    "cover":     ("upper", False),
    "abstract":  ("upper", False),
    "signature": ("title", False),
}

# หน้าลงนามใช้คำว่า Sentence Case เหมือนข้อชื่อกรรมการ (เจ้าหน้าที่สั่ง ก.ย. 2569
# "จัดไปให้เหมือนกัน") ถ้อยคำเดิมของเจ้าหน้าที่ข้างบนเขียน Capital case ตัวตรวจไม่เปลี่ยน
# เพราะชื่อคนเป็นคำนามเฉพาะ Sentence Case ของชื่อจึงขึ้นต้นตัวใหญ่ทั้งชื่อและนามสกุล
_STYLE_LABEL = {"upper": "ตัวพิมพ์ใหญ่ทั้งหมด (UPPERCASE)",
                "title": "ตัวพิมพ์ใหญ่เฉพาะอักษรแรกของชื่อและนามสกุล (Sentence Case)"}


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
    # ยกเว้นหน้าลงนามที่มีแต่คำนำหน้าเกิน (ตัวพิมพ์ถูก) = เหลือง เล่มผ่านได้ (เจ้าของสั่ง 28 ต.ค. 2569
    # "ชื่อนักศึกษาในหน้าลงนาม มีคำนำหน้านาม เดิมสีส้มปรับเป็นเหลือง") — หน้าปก/บทคัดย่อยังส้ม
    if bad_case and kind != "signature":
        zone = "RED"
    elif kind == "signature" and not bad_case:
        zone = "YELLOW"
    else:
        zone = "ORANGE"
    # ชื่อภาษาไทยไม่มีตัวพิมพ์ใหญ่-เล็ก — บอกเฉพาะเรื่องคำนำหน้า ไม่พูดถึง UPPERCASE/Sentence Case ที่ไม่เกี่ยว
    # (หน้าลงนามเล่มไทยเริ่มถูกตรวจคำนำหน้า ต.ค. 2569 ถ้อยคำเดิมจะสั่งให้ชื่อไทยเป็น "Sentence Case")
    if english:
        expected = (f'{label}ในหน้านี้ต้องเป็น{_STYLE_LABEL[want_case]}'
                    + ("" if allow_title else " และไม่มีคำนำหน้านาม") + f' คือ "{want_text}"')
    else:
        expected = f'{label}ต้องเป็นชื่อ-นามสกุลเท่านั้น ไม่มีคำนำหน้านามหรือยศ คือ "{want_text}"'
    rep.add_verification("ชื่อนักศึกษา", loc,
                         {"RED": "fail", "YELLOW": "notice"}.get(zone, "pending"), "; ".join(reasons))
    rep.add(zone, "front_matter", loc,
            f'{label}บนหน้านี้พิมพ์ว่า "{seen}" ' + " และ ".join(reasons),
            expected, f'แก้{label}บนหน้านี้เป็น "{want_text}"', rule_id)


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
    # นโยบายเจ้าหน้าที่ (ส.ค. 2569): ให้เป็น "สีส้ม" เสมอ ไม่ฟันธงแดง ต่างกันกี่หน้าก็ตาม
    # เพราะ "เลขหน้าสุดท้าย" ที่ระบบอ่านได้ขึ้นกับว่าอ่านเลขหน้าท้ายเล่มออกครบไหม
    # (หน้าภาคผนวกที่เป็นภาพสแกน/หน้าที่พิมพ์เลขหน้าไม่ชัด ทำให้ระบบอ่านได้น้อยกว่าจริง)
    # ระบบยืนยันเองไม่ได้ว่าเป็นความผิดของเล่ม จึงส่งให้เจ้าหน้าที่ตัดสิน
    # อัปเดต 1.5 เคยเปลี่ยนเป็น "ต่างเกิน 2 หน้า = แดง" จากการเข้าใจผิด — เจ้าของยืนยัน 21 ต.ค. 2569 ว่า
    # "ถ้าเลขต่างกัน แจ้งส้ม อันนี้ถูกต้องแล้ว" จึงคืนเป็นส้มเสมอ
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
    """ชื่อกรรมการบนหน้าลงนามต้องเป็น Sentence Case (เจ้าหน้าที่สั่ง ก.ย. 2569)

    ชื่อคนเป็นคำนามเฉพาะ Sentence Case ของชื่อจึงขึ้นต้นตัวใหญ่ทั้งชื่อและนามสกุล
    ("Mathuros Tipayamongkholgul") ตัวตรวจจึงใช้ _is_title_case ได้ตรงตัว ข้อความ
    ต้องอธิบายไว้ด้วยว่าหมายถึงอะไร ไม่งั้นนักศึกษาที่อ่าน Sentence Case ตามตัวอักษร
    จะพิมพ์นามสกุลเป็นตัวเล็ก แล้วโดนฟ้องซ้ำ

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
            f"ชื่อกรรมการบนหน้านี้ไม่ใช่ Sentence Case: {shown}",
            "ชื่อกรรมการบนหน้าลงนามต้องเป็น Sentence Case คือตัวพิมพ์ใหญ่เฉพาะอักษรแรก"
            "ของชื่อและนามสกุล ไม่ใช่ตัวพิมพ์ใหญ่ทั้งหมดแบบหน้าบทคัดย่อ",
            "แก้ชื่อกรรมการบนหน้านี้เป็น Sentence Case แล้วให้เจ้าหน้าที่ยืนยัน",
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

    **จำนวนไม่ตรง = แดงทั้งสองทาง** (เจ้าหน้าที่สั่ง ก.ย. 2569 — เดิมขาดเป็นส้ม)

    ทั้งขาดและเกินต้องแจ้งสีแดง และต้องอ้างถึง บฑ.1 / บฑ.2 ให้ชัดว่าเทียบกับอะไร

    **ข้อควรระวังที่ยอมรับแล้ว:** จำนวนที่นับได้ขึ้นกับว่าระบบอ่านตารางลายเซ็นออกครบไหม
    ถ้าอ่านไม่ครบจะได้ชื่อ *น้อยกว่า* ความจริง แล้วฟ้องแดงทั้งที่เล่มถูก — เจ้าหน้าที่
    รับความเสี่ยงนี้แล้ว จึงต้องเปิดหน้านั้นดูก่อนส่งกลับให้นักศึกษา

    เล่มจริงที่เจอ (ก.ย. 2569) หน้าลงนาม 2 พิมพ์กรรมการซ้ำสองคน คนละสองครั้ง
    (Tran Kiem Hao กับ Karl Peltzer บรรทัดคุณวุฒิเขียน "Ph.D." กับ "PhD" ต่างกัน)
    จึงได้ 7 ชื่อจากที่อนุมัติไว้ 5 — ของเดิมเป็นส้มพร้อมคำแนะนำว่า "ถ้าครบแล้วแปลว่า
    ระบบอ่านบางช่องไม่ออก ให้ผ่านได้" ซึ่งชี้ผิดทางสำหรับกรณีเกิน

    อัปเดต 1.6.1 (เจ้าของยืนยัน 26 ต.ค. 2569): บรรทัดที่ควรเป็น **ไม่ยกรายชื่อ** แล้ว เหลือ "กรุณาตรวจสอบรายชื่อกับ บฑ.x
    อีกครั้ง" (ทั้งหน้าผลตรวจ ข้อความสรุป และชีท) — รายชื่อตามฟอร์มยังอยู่ในรายการสีม่วงให้เจ้าหน้าที่ทาน
    """
    want = _committee_names(expected)
    if not want or len(found_names) == len(want):
        return
    short = len(found_names) < len(want)
    lead = (f"รายชื่อไม่ครบตามที่ได้รับอนุมัติใน {form}" if short else
            f"รายชื่อเกินจากที่ได้รับอนุมัติใน {form}")
    rep.add("RED", "front_matter", loc,
            f"{lead}: หน้านี้มี {len(found_names)} ชื่อ แต่อนุมัติไว้ {len(want)} ชื่อ",
            f"กรุณาตรวจสอบรายชื่อกับ {form} อีกครั้ง",
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


SCAN_IMAGE_SHARE = 0.85      # ภาพเดียวครอบพื้นที่หน้าอย่างน้อยเท่านี้ = แผ่นสแกนเต็มหน้า (อัปเดต 1.8.2)


def full_page_image(pdf_page):
    """แผ่นนี้มีภาพเดียวที่ครอบเกือบทั้งหน้า (SCAN_IMAGE_SHARE) ไหม — แผ่นสแกนที่มีชั้นข้อความ OCR ก็ยังเป็นภาพเต็มหน้า

    สำรวจเล่มจริง 9 เล่ม (28 ต.ค. 2569): แผ่นต้นเล่มของเล่มปกติไม่มีภาพเลย (โลโก้บนหน้าลงนามไม่มี) ภาพใหญ่สุด
    ทั้งเล่มของเล่มอื่นครอบ 72% ของหน้า (ภาคผนวก) · หน้าลงนามสแกนของเล่มที่เจ้าของส่งครอบ 100%
    """
    width, height = float(pdf_page.width or 0), float(pdf_page.height or 0)
    if width <= 0 or height <= 0:
        return False
    for image in pdf_page.images or ():
        w = min(float(image["x1"]), width) - max(float(image["x0"]), 0.0)
        h = min(float(image["bottom"]), height) - max(float(image["top"]), 0.0)
        if w > 0 and h > 0 and w * h >= SCAN_IMAGE_SHARE * width * height:
            return True
    return False


def scanned_signature_pages(page_texts, cover_idx, sig_pages, image_pages, full_image_pages=()):
    """แผ่นภาพสแกนที่อยู่ตรงตำแหน่งหน้าลงนาม (ถัดจากหน้าปก) — ระบบอ่านข้อความบนแผ่นนั้นไม่ได้

    เจ้าของทัก (21 ต.ค. 2569): เล่มที่สแกนหน้าลงนามที่เซ็นแล้วมาใส่เป็นภาพ ได้ "ไม่พบหน้าลงนามในเล่ม"
    ทั้งที่มีหน้าลงนาม แค่อ่านไม่ได้ ต้องแจ้งว่าอ่านไม่ได้และให้แนบหน้าลงนามแบบต้นฉบับที่ยังไม่ลงนาม
    นับแผ่นที่มีภาพและไม่มีข้อความ (หรือมีแค่เลขหน้า) ไล่จากแผ่นถัดจากปกจนเจอแผ่นที่มีข้อความ
    รวมกับหน้าลงนามที่อ่านได้แล้วไม่เกิน 2 แผ่น · หน้าขาวล้วนที่ไม่มีภาพไม่นับ (คงเป็น "ไม่พบหน้าลงนาม")
    อัปเดต 1.8.2 (เจ้าของสั่ง 28 ต.ค. 2569): แผ่นสแกนที่มีชั้นข้อความ OCR เพี้ยนติดมา ("THEE FFECOTFS…") มีข้อความ
    จึงไม่เคยเข้าเงื่อนไข เล่มจริงได้ "ไม่พบหน้าลงนาม" + "ไม่ได้พิมพ์เลขหน้า" + หัวกระดาษ — แผ่นภาพเต็มหน้า
    (full_image_pages) นับเป็นแผ่นสแกนไม่ว่าจะมีข้อความหรือไม่ รวมทั้งแผ่นที่ข้อความ OCR อ่านออกเป็นหน้าลงนามได้
    (ผู้เรียกตัดแผ่นที่คืนไปออกจากหน้าลงนามที่อ่านได้)
    """
    full = set(full_image_pages or ())
    readable = [i for i in sig_pages if i not in full]
    if len(readable) >= 2:
        return []
    found, i = [], cover_idx + 1
    while i < len(page_texts) and len(found) + len(readable) < 2:
        if i not in readable:
            if i not in full and (i not in image_pages or not _is_blank_page_text(page_texts[i])):
                break
            found.append(i)
        i += 1
    return found


def cover_page_label(page_labels, header_words, cover_idx):
    """เลขหน้าที่พิมพ์ไว้ที่หัวกระดาษของหน้าปก ('' ถ้าไม่มี) — หน้าปกต้องไม่มีเลขหน้า

    เล่มจริง (เจ้าของยืนยัน 26 ต.ค. 2569 — อัปเดต 1.6.1) พิมพ์ "1" ที่หัวกระดาษปก แล้วไปขึ้นที่บทที่ 1 ว่า "เลขหน้าไม่ต่อเนื่อง
    หน้าก่อนหน้านี้พิมพ์เลข 1" พร้อมข้อหัวกระดาษส่วนนำ (หน้า 1) อีกข้อ จากต้นเหตุเดียว
    นับเฉพาะเลขที่อยู่ในแถบหัวกระดาษจริง (header_words) — บรรทัดสุดท้ายของปกบางแบบเป็นปี ("2569") ซึ่งไม่ใช่เลขหน้า
    """
    label = page_labels.get(cover_idx, "")
    words = header_words[cover_idx] if 0 <= cover_idx < len(header_words) else ()
    return label if label and label in words else ""


def _report_cover_page_label(rep, label):
    """ข้อเดียวที่หน้าปก ถ้อยคำเจ้าของ "หน้าปกมีเลขหน้า ต้องไม่มี" · ส้มเหมือนสองข้อเดิมที่ข้อนี้มาแทน"""
    if label:
        rep.add("ORANGE", "front_matter", "หน้าปก", "หน้าปกมีเลขหน้า", "ต้องไม่มี",
                "ลบเลขหน้าออกจากหน้าปก", "PAGE.NUMBERING")


def _report_blank_runs(rep, pages, printed, image_pages, sequence_ok, page_ref, skip=(),
                       sequence_reported=True):
    """หน้าที่ดึงข้อความไม่ได้ (หรือมีแค่เลขหน้า) รวมหน้าติดกันเป็นข้อเดียว ไม่ฟ้องแยกทีละหน้า

    ยืนยันเลขหน้าอารบิกและลำดับต่อเนื่องได้ = ข้อสังเกต · อ่านเลขหน้าไม่ได้ = ให้เจ้าหน้าที่ตรวจแทนการฟันธง
    อัปเดต 1.6.1 (เจ้าของยืนยัน 26 ต.ค. 2569):
      - ช่วงที่อ่านเลขหน้าได้ทุกหน้า ไม่บอกว่า "ยืนยันลำดับเลขหน้าไม่ได้" อีก แม้ลำดับทั้งเล่มจะมีจุดกระโดด เพราะจุดกระโดด
        แจ้งแยกเป็น PAGE.NUMBERING_SEQUENCE แล้ว (sequence_reported) — เล่มจริงมีภาคผนวกภาพ 89 หน้าขึ้นส้มทั้งช่วง
      - หน้าที่มีภาพ (image_pages) เรียกว่าหน้ารูปภาพ/สแกน ไม่ใช่ "อาจเป็นหน้าว่าง"
    skip = หน้าที่แจ้งเป็นข้ออื่นไปแล้ว (แผ่นสแกนตรงหน้าลงนาม)
    """
    runs = []
    for idx, page_text in enumerate(pages):
        if not _is_blank_page_text(page_text) or idx in skip:
            continue
        if runs and runs[-1][-1] == idx - 1:
            runs[-1].append(idx)
        else:
            runs.append([idx])
    for run in runs:
        run_ref = page_ref(run[0]) if len(run) == 1 else \
            f"{page_ref(run[0])} ถึง {page_ref(run[-1])} ({len(run)} หน้า)"
        if any(not (pages[i] or '').strip() for i in run):
            kind = "ไม่มีข้อความให้ดึงเลย อาจเป็นหน้ารูปภาพ/สแกน เช่น ภาคผนวก"
        elif any(i in image_pages for i in run):
            kind = "มีเฉพาะเลขหน้ากับรูปภาพ เป็นหน้ารูปภาพ/สแกน"
        else:
            kind = "มีเฉพาะเลขหน้า อาจเป็นหน้าว่างที่ตั้งใจเว้น"
        all_numbered = all(i in printed for i in run)
        if all_numbered and sequence_ok:
            rep.add(BLANK_PAGE_ZONE, "body/end", run_ref,
                    f"ระบบดึงข้อความจากหน้านี้ไม่ได้ ({kind}) แต่เลขหน้าเรียงต่อเนื่องถูกต้อง",
                    "หน้าลักษณะนี้ที่การเรียงเลขหน้ายังคงถูกต้องเป็นข้อสังเกตและผ่านได้",
                    "ตรวจว่าเป็นหน้าภาพหรือหน้าว่างที่ตั้งใจเว้นไว้", "PAGE.BLANK")
        elif all_numbered and sequence_reported:
            rep.add(BLANK_PAGE_ZONE, "body/end", run_ref,
                    f"ระบบดึงข้อความจากหน้านี้ไม่ได้ ({kind}) แต่อ่านเลขหน้าได้ทุกหน้า",
                    "หน้าลักษณะนี้เป็นข้อสังเกตและผ่านได้ จุดที่เลขหน้าไม่ต่อเนื่องแจ้งแยกไว้แล้ว",
                    "ตรวจว่าเป็นหน้าภาพหรือหน้าว่างที่ตั้งใจเว้นไว้", "PAGE.BLANK")
        else:
            rep.add(UNCERTAIN_ZONE, "-", run_ref,
                    f"ระบบดึงข้อความจากหน้านี้ไม่ได้ ({kind}) และยืนยันลำดับเลขหน้าไม่ได้",
                    "เจ้าหน้าที่ตรวจสอบว่าเป็นหน้าภาพ/หน้าว่าง และเลขหน้ายังเรียงถูกต้อง",
                    "ตรวจด้วยตา", "UNCERTAIN.REVIEW")


EMPTY_PAGE_INTRO = "พบหน้าว่าง (ไม่มีข้อความ รูป หรือเลขหน้า)"


def _report_empty_pages(rep, empty_pages, page_labels):
    """หน้าว่างจริงทุกส่วนของเล่ม — ข้อเดียวทั้งเล่ม สีส้ม (อัปเดต 1.7 กฎใหม่ เจ้าของยืนยัน 27 ต.ค. 2569)

    หน้าว่างจริง = ไม่มีข้อความ รูป เส้นหรือกราฟิก และไม่มีเลขหน้า (ต่างจาก PAGE.BLANK ซึ่งเป็นหน้าภาพ/สแกน
    หรือหน้าที่มีแค่เลขหน้า) · "แผ่นที่ N ของไฟล์ PDF" = ลำดับแผ่นใน PDF นับจาก 1 รวมปก ส่วน "หน้า X ของไฟล์รูปเล่ม"
    = เลขหน้าที่พิมพ์ — ห้ามใช้สลับกัน · พบแผ่นเดียวบอกด้วยว่าอยู่ระหว่างหน้าไหน (ก่อนหน้าแรก/หลังหน้าสุดท้ายที่มีเลข
    บอกฝั่งเดียว) · หลายแผ่นไม่ต้องบอก · กดไม่ผ่าน = "ลบหน้าว่างที่แผ่นที่ … ของไฟล์ PDF ออก" (fail_text)
    """
    empty = sorted(empty_pages)
    if not empty:
        return
    sheets = ", ".join(str(i + 1) for i in empty)
    if len(empty) == 1:
        idx = empty[0]
        before = next((page_labels[i] for i in sorted(page_labels, reverse=True) if i < idx), "")
        after = next((page_labels[i] for i in sorted(page_labels) if i > idx), "")
        if before and after:
            where = f" อยู่ระหว่างหน้า {before} และหน้า {after} ของไฟล์รูปเล่ม"
        elif before:
            where = f" อยู่ต่อจากหน้า {before} ของไฟล์รูปเล่ม"
        elif after:
            where = f" อยู่ก่อนหน้า {after} ของไฟล์รูปเล่ม"
        else:
            where = ""
        found = f"{EMPTY_PAGE_INTRO} ที่แผ่นที่ {sheets} ของไฟล์ PDF{where}"
    else:
        found = f"{EMPTY_PAGE_INTRO} {len(empty)} แผ่น ที่แผ่นที่ {sheets} ของไฟล์ PDF"
    remove = f"ลบหน้าว่างที่แผ่นที่ {sheets} ของไฟล์ PDF ออก"
    rep.add(EMPTY_PAGE_ZONE, "-", "หน้าว่าง", found,
            "ไฟล์รูปเล่มต้องไม่มีหน้าว่างที่ไม่มีข้อความ รูป หรือเลขหน้า", remove, "PAGE.EMPTY",
            fail_text=remove)


def _report_signature_page_count(rep, sig_pages, scanned, label_style=None):
    """หน้าลงนามต้องมี 2 หน้าต่อจากหน้าปก — แผ่นสแกนที่อ่านไม่ได้แจ้งเป็นข้อเดียวว่าอ่านไม่ได้"""
    # เจ้าหน้าที่ทัก (19 ต.ค. 2569) เล่มที่ข้ามจากปกไปกิตติกรรมประกาศเลยได้ "พบหน้าลงนาม 0 หน้า /
    # ต้องมี 2 หน้า (Advisory + Examination)" ซึ่งอ่านไม่รู้เรื่อง — บอกตรง ๆ ว่าไม่พบ และต้องมีอะไรตรงไหน
    sig_labels = {"thai": "ก - ข", "roman": "i - ii"}.get(label_style, "i - ii หรือ ก - ข")
    if scanned:
        # ส้ม ไม่ใช่แดง (อัปเดต 1.8.2 เจ้าของสั่ง 28 ต.ค. 2569 "มีหน้าลงนาม แต่เป็นไฟล์สแกน สีส้ม") — กดไม่ผ่าน =
        # จุดที่ต้องแก้ ใช้บรรทัดที่ควรเป็นเดิม (fail_text)
        sheets = " และ ".join(str(i + 1) for i in scanned)
        attach = f"กรุณาแนบหน้าลงนามแบบต้นฉบับที่ยังไม่ลงนาม (หน้า {sig_labels}) แทนหน้าที่สแกน"
        rep.add(SCANNED_SIGNATURE_ZONE, "front_matter", "หน้าลงนาม",
                f"อ่านหน้าลงนามไม่ได้ เนื่องจากเป็นไฟล์สแกน (แผ่นที่ {sheets} ของไฟล์)", attach,
                "แทนหน้าที่สแกนด้วยหน้าลงนามแบบต้นฉบับที่ยังไม่ลงนาม", "FRONT.APPROVAL_SCANNED",
                fail_text=attach)
        return
    if len(sig_pages) != 2:
        rep.add(FRONT_FAILURE_ZONE, "front_matter", "หน้าลงนาม",
                "ไม่พบหน้าลงนามในเล่ม" if not sig_pages else f"พบหน้าลงนาม {len(sig_pages)} หน้า",
                f"ต้องมีหน้าลงนาม 2 หน้า (หน้า {sig_labels}) ต่อจากหน้าปก "
                "คือ หน้าคณะกรรมการที่ปรึกษา และหน้าคณะกรรมการสอบ",
                "เพิ่มหน้าลงนามทั้ง 2 หน้าต่อจากหน้าปกตาม template" if not sig_pages else "ตรวจด้วยตา",
                "FRONT.APPROVAL")


def _report_signature_page_labels(rep, sig_pages, pages, page_ref, zone=None, label_style=None):
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

    คืนดัชนีหน้าที่ฟ้องไป ให้ _check_front_page_numbers ไม่ฟ้องหน้าเดิมซ้ำอีกข้อ

    label_style ("thai" / "roman" จาก _expected_front_label_style) = บอกเลขหน้าเฉพาะภาษาของเล่ม
    เจ้าหน้าที่สั่ง (ก.ย. 2569) "บอกเลขหน้าตามภาษาที่เขียนของแต่ละเล่ม" — เดิมบอกทั้งสองแบบ
    'ต้องเป็นเลขหน้า "ก" (ไทย) หรือ "i" (อังกฤษ)' นักศึกษาเล่มไทยต้องเดาเองว่าใช้ตัวไหน
    ไม่รู้ภาษาเล่ม (ไม่มีข้อมูลอนุมัติ / ไฟล์ eThesis เป็นของคนอื่น) ยังบอกทั้งสองแบบ
    ยังรับเลขหน้าได้ทั้งสองชนิดเหมือนเดิม — เล่มไทยที่พิมพ์ i/ii ถูกกฎชนิดเลขหน้าส่วนนำฟ้องอยู่แล้ว
    หน้าลงนามที่ไม่มีเลขหน้าเป็นสีส้มเหมือนพิมพ์เลขผิด (เจ้าหน้าที่ยืนยัน ก.ย. 2569)
    """
    zone = zone or SIG_LABEL_ZONE
    flagged = []
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
        if label_style == "thai":
            want = f'ต้องเป็นเลขหน้า "{lab_th}"'
        elif label_style == "roman":
            want = f'ต้องเป็นเลขหน้า "{lab_en}"'
        else:
            want = f'ต้องเป็นเลขหน้า "{lab_th}" (ไทย) หรือ "{lab_en}" (อังกฤษ)'
        rep.add(zone, "front_matter",
                f"{signature_page_position(sig_pages, idx)} ({page_ref(idx)})",
                what,
                want,
                "แก้เลขหน้านี้ก่อน แล้วไล่เลขหน้าส่วนนำที่เหลือใหม่ทั้งชุด "
                "เพราะเลขหน้าหน้านี้ผิดทำให้หน้าถัดไปผิดตามไปด้วย",
                "PAGE.SIGNATURE_LABEL")
        flagged.append(idx)
    return flagged


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


# บรรทัดนี้ใช้เฉพาะหน้าปก ("วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร ปริญญา... / บัณฑิตวิทยาลัย
# มหาวิทยาลัยมหิดล / พ.ศ. ...") และช่องล่างซ้ายของตารางลายเซ็น (คณบดีบัณฑิตวิทยาลัย) — หน้าลงนามเล่มไทย
# ใต้ชื่อหัวข้อมีแค่ ประโยค template -> ปริญญา<ชื่อปริญญา> -> วันที่ (ดู signature_template_zone)
_THAI_COVER_ONLY_LINE = "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล"


def signature_page_extra_line(page_text):
    """บรรทัด "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล" ที่โผล่ใต้ชื่อหัวข้อของหน้าลงนามเล่มไทย — คืนบรรทัดตามที่พิมพ์
    หรือ "" ถ้าไม่มี

    เจ้าหน้าที่ (2 ต.ค. 2569) ส่งเล่ม PHIE/M มาว่าทั้งสองหน้าลงนามมีบรรทัดนี้คั่นระหว่างชื่อปริญญากับ
    วันที่ "ซึ่งมันไม่ถูก" template หน้าลงนามเล่มไทยเรียงเป็น ประโยค -> ปริญญา... -> วันที่ เล่มทดสอบไทยจริง
    ก็ไม่มีบรรทัดนี้ ผู้เขียนน่าจะคัดมาจากหน้าปก ซึ่งบรรทัดนี้ถูกต้องตาม template

    ดูเฉพาะส่วนบนของหน้า (ก่อนเส้นประลงนามแรก/หัวข้อคณะกรรมการ ตาม _degree_search_text) เพราะช่องล่างซ้าย
    ของตารางลายเซ็นพิมพ์ "บัณฑิตวิทยาลัย มหาวิทยาลัยมหิดล" ตามกฎอยู่แล้วทุกเล่ม

    ต้องเป็นบรรทัดของตัวมันเอง (หรือแยกสองบรรทัด หรือตามด้วย "วันที่" ในบรรทัดเดียวกัน) ไม่เทียบแค่ว่ามี
    ข้อความนี้อยู่ในบรรทัดไหนก็ได้ เพราะชื่อเรื่องอาจกล่าวถึงบัณฑิตวิทยาลัยกลางประโยค และเทียบด้วย norm
    เพราะข้อความไทยใน PDF เพี้ยนประจำ (หน้าปกของเล่มนี้เองอ่านได้ว่า "บัณฑติ วทิ ยาลัย")
    """
    want = norm(_THAI_COVER_ONLY_LINE)
    date_mark = norm("วันที่")
    lines = [soft(line) for line in _degree_search_text(page_text).splitlines() if soft(line)]
    for i in range(len(lines)):
        for span in (1, 2):
            chunk = soft(" ".join(lines[i:i + span]))
            squashed = norm(chunk)
            if squashed == want or (squashed.startswith(want)
                                    and squashed[len(want):].startswith(date_mark)):
                return chunk
    return ""


def _report_signature_template(rep, spot, page_text, sig_template, sig_degree, thai_book):
    """ข้อความ template ใต้ชื่อหัวข้อของหน้าลงนามหนึ่งหน้า

    ประโยคตายตัวต้องครบ ไม่ใช่แค่ชื่อปริญญาถูก และเล่มไทยต้องไม่มีบรรทัดที่ใช้เฉพาะหน้าปก
    (โค้ดส่วนประโยคย้ายมาจากลูปใน run_check ตรง ๆ ไม่แก้ตรรกะ เพื่อให้เทสต์ได้ทุกกรณี)
    """
    # บรรทัดที่ใช้ได้เฉพาะหน้าปก (ดู signature_page_extra_line) — ตรวจเฉพาะเล่มไทย เพราะ template
    # อังกฤษมี "Faculty of Graduate Studies, Mahidol University" อยู่ในประโยคเองโดยชอบ
    extra_line = signature_page_extra_line(page_text) if thai_book else ""
    # ข้อความที่ถูกต้องมีอันเดียวต่อภาษา (ดู ethesis_rules.py) บอกทั้งเจ้าหน้าที่และนักศึกษาตรง ๆ
    # และลงท้ายด้วยเครื่องหมายคำพูด ข้อความสรุปจึงดึงไปเขียนเป็น "ต้องแก้เป็น ..." ได้
    wanted = f'"{sig_template}"'
    expected_msg = f"ต้องเป็น {wanted}"
    # ประโยคตายตัวต้องสะกดตรงทุกตัวรวมวรรณยุกต์/สระ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569) — ดู spelling_key
    if spelled_in(sig_template, page_text):
        if extra_line:
            rep.add_verification("ข้อความ template ใต้ชื่อหัวข้อ", spot, "fail",
                                 f'มีบรรทัดเกิน: "{extra_line}"')
        else:
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
        template_zone = signature_template_zone(page_text, sig_degree)
        if norm(sig_template) in norm(template_zone):
            # ประโยคครบทุกคำ แต่สะกดวรรณยุกต์/สระ/ำ ไม่ตรง — ยกทั้งประโยคมาชี้จุดต่าง ไม่ใช่ท่อนท้าย
            # (ถ้าเข้ากิ่งท่อนท้ายข้างล่าง จะได้ 'ขาด "ได้รับการพิจารณาให้"' ทั้งที่เล่มพิมพ์ครบ)
            near = _closest_run(template_zone, sig_template,
                                min_ratio=SIGNATURE_TEMPLATE_MIN_RATIO)
        elif thai_book and norm(SIGNATURE_TEMPLATE_TH_TAIL) in norm(template_zone):
            # พิมพ์ประโยคมาแต่ตกคำนำ "ได้รับการพิจารณาให้" (แบบสั้น) — ยกท่อนที่พิมพ์จริงมาบอกว่าขาดอะไร
            # ต้องเทียบด้วยท่อนท้ายก่อนเทียบทั้งประโยค เพราะหน้าต่างยาวเท่าประโยคเต็มจะคร่อมท้ายชื่อเรื่อง
            # เข้ามาด้วย (เล่มทดสอบ 3: "การพัฒนาปิโตรเลียม นับเป็นส่วนหนึ่ง..." ได้ข้อความขาด/เกินปนกัน
            # จนอ่านไม่ออก)
            near = _closest_run(template_zone, SIGNATURE_TEMPLATE_TH_TAIL,
                                min_ratio=SIGNATURE_TEMPLATE_MIN_RATIO)
        else:
            near = _closest_run(template_zone, sig_template,
                                min_ratio=SIGNATURE_TEMPLATE_MIN_RATIO)
            if not near and thai_book:
                # ทั้งคำนำและท่อนท้ายพิมพ์ผิด: ลองหาท่อนที่ใกล้ท่อนท้ายที่สุด ไม่ให้บอกว่า "ไม่พบ" ทั้งที่
                # มีข้อความอยู่บนหน้า
                near = _closest_run(template_zone, SIGNATURE_TEMPLATE_TH_TAIL,
                                    min_ratio=SIGNATURE_TEMPLATE_MIN_RATIO)
        # ข้อความต้องขึ้นต้นด้วย "ส่วนไหนของหน้าที่ผิด" ไม่ใช่พูดคำว่า
        # "หน้าลงนาม" ซ้ำอีกรอบ — ตำแหน่งข้างบนบอกไปแล้วว่าหน้าไหน
        # (เดิมคำว่า "หน้าลงนาม" โผล่ 4 รอบในข้อเดียว: หัวกลุ่ม ตำแหน่ง
        #  สิ่งที่พบ และบรรทัดที่ควรเป็น)
        if near:
            found_msg = found_with_diff(f'ข้อความ template ใต้ชื่อหัวข้อพิมพ์ว่า "{near}"',
                                        analyze_diff(near, sig_template))
            detail = near
        else:
            # ไม่ยกประโยคเต็มมาตรงนี้ เพราะบรรทัด "ต้องเป็น" ข้างล่างมีอยู่แล้ว
            # ยกสองรอบทำให้ข้อเดียวมีประโยคยาว ๆ ซ้ำกันสองครั้ง
            found_msg = "ไม่พบข้อความ template ใต้ชื่อหัวข้อ"
            # แถวในตารางเทียบไม่มีบรรทัด "ควรเป็น" เหมือนการ์ด จึงต้องบอกในแถวเองว่าไม่พบข้อความอะไร
            # (เจ้าหน้าที่ถาม 2 ต.ค. 2569) ส่วนการ์ดไม่ยกซ้ำเพราะบรรทัดข้างล่างมีอยู่แล้ว
            detail = f"ไม่พบข้อความ template ใต้ชื่อหัวข้อ: {wanted}"
        # ตารางเทียบมีแถวเดียวต่อหน้า — ถ้ามีบรรทัดเกินด้วยต้องบอกในแถวนั้นด้วย ไม่ใช่โชว์แค่เรื่อง
        # ประโยค (การ์ดแดงแยกเป็นสองข้ออยู่แล้ว) เจ้าหน้าที่ท้วง 2 ต.ค. 2569
        if extra_line:
            detail += f' และมีบรรทัดเกิน: "{extra_line}"'
        # บรรทัดเกินเป็นการ์ดแดงเสมอ แถวนี้จึง fail ส่วนประโยคที่ต่างเฉพาะวรรณยุกต์/สระบนเล่มฟอนต์เพี้ยนเป็นส้ม (Report.add)
        rep.add_verification("ข้อความ template ใต้ชื่อหัวข้อ", spot,
                             "fail" if extra_line
                             else rep.mismatch_status(getattr(found_msg, "marks_only", False), found_msg),
                             detail)
        rep.add("RED", "front_matter", spot, found_msg,
                expected_msg,
                "", "FRONT.APPROVAL")
    # ประโยคครบแล้วแต่มีบรรทัดเกิน กับประโยคหายแล้วมีบรรทัดเกิน เป็นสองข้อฟ้องแยกกัน (แก้คนละอย่าง)
    if extra_line:
        # บอกแค่ให้ตัดออก (เจ้าหน้าที่ 2 ต.ค. 2569: "ไอ้ส่วนที่เกินมา แค่บอกให้ตัดออกก็พอ") ส่วนเหตุผลว่าทำไม
        # บรรทัดนี้ไม่อยู่ใน template อยู่ใน RULES_AND_SOURCES.md ไม่ต้องไปอยู่ในข้อความที่ส่งนักศึกษา
        # ใส่ช่อง "ควรเป็น" ช่องเดียว: ข้อความสรุปใช้ช่องนี้เป็นบรรทัดที่สาม และไม่ใส่ fix ซ้ำบนการ์ด
        rep.add("RED", "front_matter", spot,
                f'ใต้ชื่อหัวข้อมีบรรทัด "{extra_line}" เกินมา',
                "ตัดบรรทัดนี้ออก", "", "FRONT.APPROVAL")


_SMALL_WORDS = {"a", "an", "and", "at", "by", "for", "in", "of", "on", "or", "the",
                "to", "with"}


def match_printed_case(want, printed):
    """ค่าที่ถูกต้องในตัวพิมพ์แบบเดียวกับที่เล่มพิมพ์ — สำหรับบอกนักศึกษาว่าต้องแก้เป็นอะไร

    ชื่อสาขาจาก eThesis เป็นตัวพิมพ์ใหญ่ทั้งหมด ("PUBLIC HEALTH INFECTIOUS DISEASES ...")
    แต่ช่องประธานหลักสูตรบนหน้าลงนามพิมพ์แบบ Sentence Case ซึ่งถูกต้องแล้ว ของเดิมยกค่า
    จาก eThesis มาตรง ๆ ข้อความจึงเหมือนสั่งให้แก้เป็นตัวใหญ่ทั้งหมด ทั้งที่ผิดแค่สะกด
    (เจ้าหน้าที่แจ้ง ก.ย. 2569: "ชื่อหลักสูตรมันต้องเป็น Sentence Case ซึ่งถูกแล้ว แต่สะกดผิด
    ก็แจ้งผิดไป")

    คำที่เล่มพิมพ์ไว้แล้ว ใช้ตัวพิมพ์ตามเล่ม คำที่ผิดหรือขาด ขึ้นต้นตัวใหญ่ (คำเชื่อมเป็นตัวเล็ก
    ตัวย่อสั้น ๆ อย่าง HIV คงไว้) ถ้าเล่มพิมพ์ตัวใหญ่ทั้งหมด ก็คืนตัวใหญ่ทั้งหมด
    """
    if not want or not printed or not re.search(r'[A-Za-z]', want):
        return want
    if printed.isupper():
        return want.upper()
    seen = {w.lower(): w for w in re.findall(r"[A-Za-z][A-Za-z'-]*", printed)}
    first = [True]

    def recase(match):
        word, low = match.group(0), match.group(0).lower()
        if low in seen:
            out = seen[low]
        elif not first[0] and low in _SMALL_WORDS:
            out = low
        elif len(word) <= 3 and word.isupper():
            out = word
        else:
            out = word[:1].upper() + word[1:].lower()
        first[0] = False
        return out

    return re.sub(r"[A-Za-z][A-Za-z'-]*", recase, want)


def _institution_mismatch(rep, loc, label, want, bottom_text, box, rule_id, box_read=False):
    """ฟ้องช่องสถาบันที่ข้อความไม่ตรง — บอกด้วยว่าเล่มเขียนว่าอะไรและต่างตรงไหน

    เทียบแบบไม่สนตัวพิมพ์เล็ก-ใหญ่ (ตัวพิมพ์ตาม template ถูกแล้ว) ข้อที่ขึ้นจึงเป็นเรื่อง
    สะกดผิด ต้องบอกว่าสะกดผิด และค่าที่ต้องแก้เป็นต้องใช้ตัวพิมพ์ตามที่เล่มพิมพ์
    """
    near = _closest_run(bottom_text, want)
    misspelt = False
    if near:
        want = match_printed_case(want, near)
        analysis = analyze_diff(near, want)
        misspelt = bool(analysis.text)
        found_msg = (found_with_diff(f'{box} สะกด{label}ผิด เขียนว่า "{near}"', analysis)
                     if misspelt else f'{box} เขียนว่า "{near}"')
    else:
        found_msg = f'ไม่พบ{label} "{want}" ใน{box}'
    # สะกดผิด (เจอข้อความแล้ว ชี้จุดต่างได้) = แดง ตามกติกาตัวสะกดไทย "ต่างทุกตัว = สะกดผิด = แดง"
    # (เจ้าหน้าที่ ต.ค. 2569: ช่องประธานหลักสูตรพิมพ์ "สาธารณสุขศาสตร์มหาบัณฑิต" (มีการันต์)
    # "สะกดผิดแบบนี้ควรแดง")
    # หาไม่เจอเลย แยกสองกรณี (เจ้าหน้าที่ ต.ค. 2569: "หาไม่เจอ นี่มันคือระบบไม่เจอ หรือไม่มีในเล่ม
    # ถ้าไม่มีในเล่ม แดง"): ระบบอ่านช่องนี้ได้ (box_read — เจอตำแหน่งของช่องขวาในแถวล่าง) แต่ไม่มี
    # ข้อความนั้น = เล่มไม่มี = แดง · อ่านช่องนี้ไม่ได้เลย = ส้ม เพราะแยกไม่ออกว่าเล่มขาดหรือระบบอ่านไม่ครบ
    zone = "RED" if (misspelt or (not near and box_read)) else "ORANGE"
    rep.add(zone, "front_matter", loc, found_msg,
            f'ข้อความใต้ลายเซ็นต้องมี{label} "{want}"',
            f"โปรดตรวจ{label}มุมล่างขวาให้ถูกต้อง", rule_id)


_HEAD_ROLE_EN = re.compile(r'\b(?:Dean|Director)\b', re.I)


def _right_institution_box_read(kind, bottom_text):
    """ระบบอ่านช่องสถาบัน "มุมล่างขวา" ได้จริงไหม — ใช้แยก "เล่มไม่มีข้อความ" ออกจาก "ระบบอ่านไม่ครบ"

    ดูจากตำแหน่งที่ template พิมพ์ไว้ในช่องขวาเสมอ ไม่ดูจากพิกัด
      หน้าที่ปรึกษา : ตำแหน่งระดับหลักสูตร (ประธานหลักสูตร / Program Director) ซึ่งช่องซ้ายไม่มี
      หน้ากรรมการสอบ: คำตำแหน่งหัวหน้าส่วนงานต้องมี 2 ที่ (ช่องซ้ายคือคณบดีบัณฑิตวิทยาลัย 1 ที่
                      ช่องขวาคือคณบดี/ผู้อำนวยการคณะอีก 1 ที่) — เจอที่เดียวแปลว่าอ่านช่องขวาไม่ได้
    """
    text = bottom_text or ""
    if kind == "advisory":
        return programme_head_title(text) is not None
    squashed = norm(text)
    th = sum(squashed.count(norm(w)) for w in ("คณบดี", "ผู้อำนวยการ"))
    return th >= 2 or len(_HEAD_ROLE_EN.findall(text)) >= 2


def programme_name(degree, english):
    """ชื่อหลักสูตรเต็มที่ช่องประธานหลักสูตรต้องพิมพ์ — จากชื่อปริญญาบนปกตามข้อมูลอนุมัติ "ปริญญา (สาขา)"

    ไทย "<ชื่อปริญญา> สาขาวิชา<ชื่อสาขา>" · อังกฤษ "<Degree> Program in <Subject>" (template อังกฤษ เล่มทดสอบ 1-2)
    ค่าที่บอกให้แก้ใช้ "สาขาวิชา" เสมอ (เจ้าของยืนยัน 27 ต.ค. 2569) · '' ถ้าไม่มีชื่อสาขาในวงเล็บ
    """
    subject = _degree_subject(degree)
    degree_name = re.sub(r"\s*\(.*$", "", soft(degree)).strip()
    if not subject or not degree_name:
        return ""
    if english:
        return f"{degree_name} Program in {subject}"
    return f"{degree_name} สาขาวิชา{subject}"


def _check_programme_name(rep, degree, bottom_text, english, box_read, loc):
    """หน้าลงนาม 1 ช่องประธานหลักสูตร: ชื่อหลักสูตรเต็ม (อัปเดต 1.7 เจ้าของยืนยัน 27 ต.ค. 2569)

    เดิมตรวจแยกสองข้อ (ชื่อสาขา / ชื่อปริญญา) — ตอนนี้ข้อเดียว 'ระบุชื่อหลักสูตรผิด' + 'ต้องแก้เป็น "<ชื่อเต็ม>"'
    โดยไม่มีบรรทัด "เขียนว่า …" หรือ "ต่างที่ …" · รับทั้ง "สาขาวิชา" และ "สาขา" (อังกฤษ Program/Programme in)
    วงเล็บท้ายชื่อ เช่น "(ภาคพิเศษ)" "(International Program)" ไม่ดู (ค้นแบบ substring อยู่แล้ว)

    แถวล่างสุดอ่านได้สองแบบต่อกัน (ลำดับการอ่าน + แยกคอลัมน์ ดู signature_committee_slots) ช่องซ้ายอาจคั่นกลาง
    ระหว่างชื่อปริญญากับ "สาขาวิชา…" ได้ (เล่มจริง: "พยาบาลศาสตรมหาบัณฑิต บัณฑิตวิทยาลัย … สาขาวิชา…") จึงถือว่าถูก
    เมื่อพบชื่อเต็มติดกัน หรือพบชื่อปริญญา และ "สาขาวิชา/สาขา<ชื่อสาขา>" ติดกันสะกดตรงครบทุกตัว — ชื่อสาขาเดี่ยว ๆ ไม่นับ
    เพราะชื่อคณะมักมีชื่อสาขาอยู่ด้วย ("Faculty of Tropical Medicine") การฟ้องเกินทั้งที่เล่มถูกเสียหายกว่า (หลักเดิม)
    สะกดผิด/ไม่มี = แดง · อ่านช่องนี้ไม่ได้ (box_read=False) และหาไม่เจอ = ส้ม (ตรรกะเดิม)
    """
    want = programme_name(degree, english)
    if not want:
        return
    subject = _degree_subject(degree)
    degree_name = re.sub(r"\s*\(.*$", "", soft(degree)).strip()
    joints = ("Program in", "Programme in") if english else ("สาขาวิชา", "สาขา")
    full = [f"{degree_name} {joint} {subject}" if english else f"{degree_name} {joint}{subject}"
            for joint in joints]
    if any(spelled_in(text, bottom_text) for text in full):
        return
    tails = [f"{joint} {subject}" for joint in joints]
    if spelled_in(degree_name, bottom_text) and any(spelled_in(tail, bottom_text) for tail in tails):
        return
    # เจอชื่อหลักสูตรแบบหยาบ (ตัดวรรณยุกต์/สระ) แต่สะกดไม่ตรง = สะกดผิด แดงเสมอ
    rough = any(norm(text) in norm(bottom_text) for text in full) or \
        (norm(degree_name) in norm(bottom_text) and any(norm(tail) in norm(bottom_text) for tail in tails))
    zone = "RED" if (rough or box_read) else "ORANGE"
    shown = match_printed_case(want, bottom_text) if english else want
    rep.add(zone, "front_matter", loc,
            "ช่องประธานหลักสูตร (มุมล่างขวา) ระบุชื่อหลักสูตรผิด",
            f'ต้องแก้เป็น "{shown}"',
            "แก้ชื่อหลักสูตรในช่องประธานหลักสูตรให้ตรงตามที่ได้รับอนุมัติ", "FRONT.COMMITTEE")


def _check_signature_institution(rep, kind, bottom_text, approved, english_book,
                                 loc_prefix="", loc_suffix=""):
    """ช่องสถาบันแถวล่างสุดของหน้าลงนาม — บทบาทต่างกันในสองหน้า (ยืนยันจาก template ทางการ)

    หน้าอาจารย์ที่ปรึกษา: มุมล่างขวา = "ประธานหลักสูตร ... สาขาวิชา ..." → ต้องมีชื่อสาขา
    หน้ากรรมการสอบ     : มุมล่างขวา = "คณบดี/ผู้อำนวยการคณะ/สถาบัน ..." → ต้องมีชื่อคณะ
    มุมล่างซ้ายเป็นคณบดีบัณฑิตวิทยาลัยทั้งสองหน้า จึงไม่ใช้ตรวจคณะของนักศึกษา

    รับ bottom_text ที่เรียงตามลำดับการอ่านมาแล้ว (ดู signature_committee_slots)
    จึงค้นจากข้อความทั้งแถวล่างรวมกัน — ช่องซ้ายเป็นบัณฑิตวิทยาลัยเสมอ จึงไม่ชนกัน
    """
    box_read = _right_institution_box_read(kind, bottom_text)
    if kind == "advisory":
        degree = approved.get("degree_cover_th" if not english_book else "degree_cover_en", "") \
            or approved.get("degree_cover_en", "")
        _check_programme_name(rep, degree, bottom_text, english_book, box_read,
                              f"{loc_prefix}ประธานหลักสูตร{loc_suffix}")
        return
    # เล่มอังกฤษเทียบชื่อคณะไม่ได้ เพราะชื่อคณะจาก eThesis เป็นภาษาไทย
    faculty = approved.get("faculty", "")
    if faculty and not english_book and not spelled_in(faculty, bottom_text):
        _institution_mismatch(
            rep, f"{loc_prefix}คณบดีคณะ{loc_suffix}", "ชื่อคณะ", faculty,
            bottom_text, "ช่องคณบดีคณะ (มุมล่างขวา)", "FRONT.COMMITTEE", box_read)


# ตำแหน่งระดับหลักสูตร — ใช้ได้เฉพาะมุมล่างขวาของหน้าลงนาม 1 (ประธานหลักสูตร)
_PROGRAMME_HEAD_EN = re.compile(r'Program(?:me)?\s*Director', re.I)
_PROGRAMME_HEAD_TH = ("ประธานหลักสูตร", "ผู้อำนวยการหลักสูตร")


def programme_head_title(bottom_text):
    """ตำแหน่งระดับหลักสูตรในแถวล่างสุดของหน้าลงนาม — คืน (คำที่พบ, "en"/"th") หรือ None

    ค้นจากข้อความ ไม่ใช่จากพิกัดของช่อง (เจ้าหน้าที่แนะนำให้ดูจากสิ่งที่ควรอยู่ในหน้า) —
    ช่องล่างซ้ายเป็นคณบดีบัณฑิตวิทยาลัยเสมอ ตำแหน่งระดับหลักสูตรที่โผล่ในแถวนี้จึงเป็นของ
    ช่องล่างขวา

    ฝั่งอังกฤษใช้ regex ที่ยอมแค่ช่องว่างคั่นสองคำ ห้ามใช้ norm — norm ตัดวงเล็บทิ้ง
    คุณวุฒิอย่าง "Ph.D. (Tropical Health Program)" ที่อยู่เหนือบรรทัด "Director" จะถูกอ่าน
    ต่อกันเป็น PROGRAMDIRECTOR ส่วนฝั่งไทยต้องใช้ norm เพราะสระ/วรรณยุกต์ใน PDF เพี้ยนประจำ
    """
    text = bottom_text or ""
    match = _PROGRAMME_HEAD_EN.search(text)
    if match:
        return soft(match.group(0)), "en"
    squashed = norm(text)
    for title in _PROGRAMME_HEAD_TH:
        if norm(title) in squashed:
            return title, "th"
    return None


# ช่องล่างขวาของหน้าลงนาม 2 ใน template พิมพ์ "Dean/Director" ให้นักศึกษาเลือกเอาคำเดียว
# ทั้งสองคำติดกันด้วยเครื่องหมาย "/" = ยังไม่ได้เลือก ต้องมี "/" (หรือ "or") คั่นจริงเท่านั้น —
# ห้ามเทียบแค่ว่าสองคำอยู่ติดกัน เพราะแถวล่างสุดเรียงตามลำดับการอ่านของสองช่อง ช่องซ้าย
# (คณบดีบัณฑิตวิทยาลัย) กับช่องขวาที่เลือกแล้วจึงมาต่อกันเป็น "Dean Director" / "คณบดี ผู้อำนวยการ"
# ได้ในเล่มที่ถูกต้อง (มีเทสต์คุมทั้งสองภาษา)
_FACULTY_HEAD_CHOICE_EN = re.compile(r'\b(?:Dean\s*(?:/|or)\s*Director|Director\s*(?:/|or)\s*Dean)\b', re.I)
# ฝั่งไทยเทียบหลังตัดสระ/วรรณยุกต์ทิ้ง (สระ/วรรณยุกต์ใน PDF เพี้ยนประจำ) แต่ต้องคง "/" ไว้ จึงไม่ใช้ norm
_FACULTY_HEAD_CHOICE_TH = re.compile(r'คณบด\s*/\s*ผอานวยการ|ผอานวยการ\s*/\s*คณบด')


def faculty_head_choice(bottom_text):
    """ช่องล่างขวาของหน้าลงนาม 2 ยังเป็น "Dean/Director" ทั้งสองคำ — คืน (คำที่พบ, "en"/"th") หรือ None

    ตัวอย่างจริง (เล่มนานาชาติ สถาบันโภชนาการ เจ้าหน้าที่ส่งมา 30 ก.ย. 2569): แถวล่างสุดอ่านได้
        "Ph.D. (Medical Technology) Dean/Director Dean Institute of Nutrition ..."
    "Dean" ตัวที่สองคือช่องล่างซ้าย (บัณฑิตวิทยาลัย) ส่วน "Dean/Director" คือช่องล่างขวาที่ยังไม่ได้เลือก
    """
    text = bottom_text or ""
    match = _FACULTY_HEAD_CHOICE_EN.search(text)
    if match:
        return soft(match.group(0)), "en"
    if _FACULTY_HEAD_CHOICE_TH.search(_TH_MARKS.sub("", text.replace("ำ", "า"))):
        return "คณบดี/ผู้อำนวยการ", "th"
    return None


FACULTY_HEAD_CHECK_NOTICE = "กรุณาตรวจสอบชื่อและคุณวุฒิของอาจารย์กับทางหลักสูตรอีกครั้ง"


def _check_faculty_head_title(rep, bottom_text, loc):
    """หน้าลงนาม 2 มุมล่างขวาต้องเป็นตำแหน่ง Dean หรือ Director ไม่ใช่ Program Director

    เจ้าหน้าที่สั่ง (ก.ย. 2569): "หน้า ii หรือ ข ... มุมล่างขวา ต้องเป็น Dean ก็ Director
    ไม่ใช่ Program director" — ช่องนี้เป็นของหัวหน้าส่วนงาน (คณบดีคณะ / ผู้อำนวยการสถาบัน)
    ส่วนประธานหลักสูตรลงนามมุมเดียวกันของหน้าลงนาม 1

    เล่มจริง 6736545 PHIE/M คัดช่องนี้มาจากหน้าลงนาม 1 ทั้งช่อง ("Program Director /
    Master of Science Program in ...") แล้วระบบไม่ฟ้องเลย เพราะเล่มอังกฤษข้ามการเทียบชื่อคณะ
    (_check_signature_institution) กฎนี้ไม่พึ่งข้อมูลอนุมัติ จึงตรวจได้ทั้งเล่มไทยและอังกฤษ

    ค่าที่ถูกมีสองตัวเลือก ประโยค expected จึงห้ามจบด้วยเครื่องหมายคำพูด ไม่งั้น
    _corrected_value ดึงตัวท้ายไปพิมพ์ในข้อความสรุปว่า 'ต้องแก้เป็น "Director"' ตัวเดียว
    **สีแดง** ตามที่เจ้าหน้าที่สั่ง (ก.ย. 2569) "อันนี้ขอสีแดงเลย" — ต่างจากข้ออื่นของช่องเดียวกัน
    (ชื่อสาขา/คณะ = ส้ม) เพราะคำตำแหน่งอ่านได้แน่นอน ไม่ใช่การเทียบข้อความที่อาจอ่านเพี้ยน
    """
    found = programme_head_title(bottom_text)
    if found:
        title, lang = found
        heads = ("Dean", "Director") if lang == "en" else ("คณบดี", "ผู้อำนวยการ")
        # บรรทัดของตัวเองใต้บรรทัด "ตำแหน่งต้องเป็น ..." (อัปเดต 1.6.1 เจ้าของยืนยัน 26 ต.ค. 2569) — ช่องนี้มักคัด
        # ชื่อและคุณวุฒิมาจากหน้าลงนาม 1 ทั้งช่อง นักศึกษาต้องไปถามหลักสูตรว่าหัวหน้าส่วนงานคือใคร
        rep.add("RED", "front_matter", loc,
                f'ช่องคณบดีคณะ (มุมล่างขวา) ใช้ตำแหน่ง "{title}"',
                f'ตำแหน่งต้องเป็น "{heads[0]}" หรือ "{heads[1]}" เท่านั้น',
                "ใช้ตำแหน่งของหัวหน้าส่วนงาน ส่วนประธานหลักสูตรลงนามในหน้าลงนาม 1",
                "FRONT.COMMITTEE", notice=FACULTY_HEAD_CHECK_NOTICE)
    # ช่องเดียวกันนี้ template พิมพ์ "Dean/Director" ไว้ให้เลือกเอาคำเดียว (เจ้าหน้าที่ 30 ก.ย. 2569
    # ส่งเล่มที่ยังเป็น "Dean/Director" ทั้งสองคำมาว่าระบบไม่ฟ้อง) — สีแดงเหมือนข้อบน และเหมือนข้อ
    # "REFERENCES/BIBLIOGRAPHY" ที่ยังไม่ได้เลือกคำเดียว: คำตำแหน่งอ่านได้แน่นอน ไม่ใช่การเทียบข้อความ
    # ประโยค expected ห้ามจบด้วยเครื่องหมายคำพูด (เหตุผลเดียวกับข้างบน)
    choice = faculty_head_choice(bottom_text)
    if choice:
        printed, lang = choice
        heads = ("Dean", "Director") if lang == "en" else ("คณบดี", "ผู้อำนวยการ")
        rep.add("RED", "front_matter", loc,
                f'ช่องคณบดีคณะ (มุมล่างขวา) ใช้ตำแหน่ง "{printed}"',
                f'ต้องเลือกตำแหน่งเดียว: "{heads[0]}" หรือ "{heads[1]}" อย่างใดอย่างหนึ่ง',
                "เลือกเหลือคำเดียวตามหัวหน้าส่วนงานของนักศึกษา แล้วลบอีกคำออก",
                "FRONT.COMMITTEE")


def _check_signature_heads_without_committees(rep, sig_pages, pages, pdf_path, page_ref):
    """ตรวจตำแหน่งมุมล่างขวาของหน้าลงนาม 2 เมื่อไม่มีรายชื่อกรรมการจาก eThesis

    run_check เรียก _check_committees ก็ต่อเมื่อมีรายชื่อกรรมการ ไม่งั้นข้ามทั้งหน้าลงนามเงียบ ๆ รวมข้อ
    ตำแหน่งที่เป็นกฎของ template ล้วน (ดู comment ใน _check_committees: "ไม่ต้องมีรายชื่อจาก eThesis")
    ไฟล์ eThesis ไม่บังคับแนบ และอ่านพลาดได้ (เช่นแนบผิดหน้า) เล่มที่ตำแหน่งผิดจึงหลุดได้ทั้งที่ระบบมีข้อมูลพอ

    ตรวจเฉพาะข้อตำแหน่งข้อเดียว ข้ออื่นของหน้าลงนาม (ตัวพิมพ์ชื่อ คุณวุฒิ ฯลฯ) ยังทำงานเมื่อมีรายชื่อเท่านั้น
    เงื่อนไขเลือกหน้าต้องตรงกับใน _check_committees (หน้าลงนามลำดับที่สอง และหัวข้อบนหน้าไม่ขัด)
    """
    try:
        with pdfplumber.open(pdf_path) as _pl:
            for idx in sig_pages[:2]:
                if not 0 <= idx < min(len(_pl.pages), len(pages)):
                    continue
                if signature_page_committee(sig_pages, idx) != "exam":
                    continue
                if _committee_page_kind(pages[idx]) not in ("", "exam"):
                    continue
                bottom_text = signature_committee_slots(_pl.pages[idx])[2]
                page_label = signature_page_position(sig_pages, idx)
                _check_faculty_head_title(rep, bottom_text,
                                          f"{page_label} คณบดีคณะ ({page_ref(idx)})")
    except Exception:
        return


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
        # ตำแหน่งมุมล่างขวาของหน้าลงนาม 2 เป็นกฎของ template ไม่ต้องมีรายชื่อจาก eThesis
        # จึงตรวจก่อนบรรทัดข้ามข้างล่าง — ถ้าหัวข้อบนหน้าบอกว่าเป็นหน้าที่ปรึกษา (สลับหน้ากัน)
        # ไม่ฟ้อง เพราะหน้าที่ปรึกษาต้องมี Program Director อยู่แล้ว มีรายการสีม่วงเตือนเรื่องสลับหน้าแทน
        if kind == "exam" and heading_kind in ("", "exam"):
            _check_faculty_head_title(rep, bottom_text, f"{page_label} คณบดีคณะ ({page_ref(idx)})")
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
# พยัญชนะไทยเท่านั้น — ใช้แยก "นามสกุลไทยที่ค้างอยู่ตรงช่องรหัส" ออกจาก "ตัวเลขที่ฟอนต์
# ทำเพี้ยน" ห้ามตั้งชื่อ _THAI_LETTER เพราะชื่อนั้นประกาศซ้ำอีกที่ข้างล่าง (สำหรับตัดสิน
# ภาษาชื่อเรื่อง ครอบสระ วรรณยุกต์ และเลขไทยด้วย) ตัวหลังจะทับตัวนี้เงียบ ๆ — เคยพลาดมาแล้ว
# ตัวเลขที่ฟอนต์ทำเพี้ยนเป็นเลขไทยหรือสระ จึงถูกตัดสินผิดว่าเป็นนามสกุล แล้วฟ้องแดง
_THAI_CONSONANT = re.compile('[ก-ฮ]')
# เลขไทยทั้งช่อง = อ่านออกชัดเจน แค่ใช้เลขผิดระบบ ไม่ใช่ฟอนต์ทำเพี้ยน — ต้องไปทางข้อแดง
# "พิมพ์เป็นเลขไทย" (เจ้าหน้าที่ยืนยัน ก.ย. 2569 ว่ารหัสนักศึกษาต้องเป็นเลขอารบิกเท่านั้น)
_THAI_DIGITS_ONLY = re.compile('[๐-๙]+')
_THAI_DIGIT = re.compile('[๐-๙]')


def is_page_count_line(line):
    """บรรทัด "97 pages" / "106 หน้า" ที่ปิดท้ายบทคัดย่อ

    ใช้เป็นจุดหยุดของรายการ keyword — สำรวจเล่มจริง 5 เล่ม (ทั้งไทยและอังกฤษ)
    พบว่ารายการ keyword จบด้วยบรรทัดนี้เสมอ ไม่มีเล่มไหนต่างออกไป
    """
    return bool(re.search(r'(\d{1,4})\s*PAGES?', line or "", re.I)
                or re.search(r'(\d{1,4})(หนา)', norm(line)))


_PAGE_COUNT_WHOLE_LINE = re.compile(r'(\d{1,4})(?:PAGES?|หนา)')


def abstract_page_count(lines):
    """จำนวนหน้ารวมที่ระบุท้ายบทคัดย่อ ("154 หน้า" / "97 pages") — None ถ้าไม่พบ (อัปเดต 1.8)

    ใช้บรรทัดที่เป็นจำนวนหน้าทั้งบรรทัด บรรทัดท้ายสุดก่อน — เดิมเอาบรรทัดแรกที่มี "N หน้า" อยู่ตรงไหนก็ได้
    เล่มจริง (ต.ค. 2569) เนื้อความบทคัดย่อมี "จัดเป็น 3 หน้าที่…" อยู่ก่อนบรรทัด "154 หน้า" จึงได้ "ระบุจำนวนหน้า 3"
    ไม่มีบรรทัดแบบนั้น = ใช้บรรทัดท้ายสุดที่มี "N หน้า/N pages" ปนข้อความอื่น (เล่มที่พิมพ์ไว้ในบรรทัดเดียวกับข้อความอื่น)
    """
    whole = [m for m in (_PAGE_COUNT_WHOLE_LINE.fullmatch(norm(line)) for line in lines) if m]
    if whole:
        return int(whole[-1].group(1))
    loose = [m for line in lines
             if (m := re.search(r'(\d{1,4})\s*PAGES?', line, re.I) or re.search(r'(\d{1,4})(หนา)', norm(line)))]
    return int(loose[-1].group(1)) if loose else None


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
            if len(slot) != len(digits) or _THAI_CONSONANT.search(slot):
                continue
            if _THAI_DIGITS_ONLY.fullmatch(slot):
                continue
            # เลขอารบิกที่อ่านได้ครบแต่คนละตัวกับข้อมูลอนุมัติ ไม่ใช่ฟอนต์เพี้ยน — คือรหัสไม่ตรง
            # (เล่มพิมพ์ผิด หรือแบบฟอร์มกรอกผิด) ต้องฟ้องแดงตามปกติ ตรวจทุกช่องในแบบฟอร์ม
            # (ก.ย. 2569): ใส่รหัสในแบบฟอร์มผิดหลักเดียว ได้การ์ดส้ม "ระบบอ่านรหัสไม่ออก
            # เพราะฟอนต์" แทนที่จะบอกว่ารหัสไม่ตรง
            if slot.isascii() and slot.isdigit():
                continue
            if any(norm(slot) in name for name in known if name):
                continue
            return slot
    return ""


def thai_numeral_student_id(printed, student_id, abs_label):
    """(พบ, ควรเป็น, ต้องแก้) เมื่อรหัสนักศึกษาพิมพ์เป็นเลขไทย — คืน None ถ้าไม่ใช่

    เจ้าหน้าที่ยืนยัน (ก.ย. 2569) ว่ารหัสนักศึกษาต้องเป็นเลขอารบิกเท่านั้น ของเดิมรหัส
    ที่พิมพ์เป็นเลขไทยถูกนับเป็น "ฟอนต์ทำเพี้ยน อ่านไม่ออก" แล้วผ่านไปเป็นแค่ข้อมูลประกอบ
    """
    if not printed or not _THAI_DIGIT.search(printed):
        return None
    return (f'บรรทัดชื่อนักศึกษาพิมพ์รหัสเป็นเลขไทย "{printed}"',
            f'รหัสนักศึกษาใน{abs_label}ต้องเป็นเลขอารบิกเท่านั้น คือ "{student_id}"',
            "แก้รหัสนักศึกษาเป็นเลขอารบิก")


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


def _committee_tokens(block):
    """แยกรายชื่อกรรมการเป็นช่อง ๆ ที่จุลภาค (หรือ ;) ซึ่งไม่ได้อยู่ในวงเล็บ

    สาขาวิชาในวงเล็บมีจุลภาคได้ — เล่มจริงเขียน "Ph.D. (Educational Measurement, Evaluation and Research)"
    แยกที่จุลภาคทุกตัวแล้ว "(Educational Measurement" กลายเป็นชื่อคนที่ 4 ได้สามข้อผิด ๆ (ไม่เป็นตัวพิมพ์ใหญ่ /
    รายชื่อเกิน / ไม่มีจุลภาคหน้าชื่อ) — วงเล็บมีข้อฟ้องของตัวเองอยู่แล้ว (อัปเดต 1.6.1 เจ้าของยืนยัน 26 ต.ค. 2569)
    ; ที่ใช้คั่นชื่อแทนจุลภาคนับเป็นตัวคั่นด้วย (ข้อ "ใช้ ; คั่นชื่อ" ฟ้องแยก ดู committee_semicolons)
    """
    tokens, depth, start = [], 0, 0
    text = block or ""
    for k, ch in enumerate(text):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch in ",;" and not depth:
            tokens.append(text[start:k])
            start = k + 1
    tokens.append(text[start:])
    return [t.strip() for t in tokens]


def committee_semicolons(block):
    """รายชื่อกรรมการใช้ ; คั่นช่องแทนจุลภาคไหม (นับเฉพาะนอกวงเล็บ)"""
    depth = 0
    for ch in block or "":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth = max(0, depth - 1)
        elif ch == ";" and not depth:
            return True
    return False


def _committee_entry_complete(line):
    """บรรทัดนี้จบ "ชื่อ, คุณวุฒิ" ของคนล่าสุดครบแล้วหรือยัง

    ใช้ตัดสินว่าบรรทัดถัดไปเป็น "ชื่อที่ห่อคำมา" หรือเป็นเนื้อความที่ไม่เกี่ยวแล้ว
    โดยดูจากตัวข้อความเอง ไม่ใช่นับบรรทัด — ท่อนสุดท้ายเป็นคุณวุฒิ = ครบ,
    เป็นชื่อคน = ยังค้าง (คุณวุฒิของคนนั้นถูกห่อไปบรรทัดถัดไป)
    ผู้เรียกส่งข้อความที่รวมมาถึงบรรทัดนี้ วงเล็บสาขาที่ห่อข้ามบรรทัดจึงยังเป็นช่องเดียวกัน (_committee_tokens)
    """
    tail = soft(line).rstrip()
    if tail.endswith((",", ";")):
        return False
    return _is_degree_only(_committee_tokens(tail)[-1])


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
                     or not _committee_entry_complete(" ".join(block_lines)))
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
    for tok in [t for t in _committee_tokens(block) if t]:
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


def abstract_committee_heading(doc_type, english):
    """หัวข้อรายชื่อกรรมการที่ปรึกษาบนหน้าบทคัดย่อ ตาม template ('' ถ้าไม่รู้ประเภทเล่ม)

    เล่มอังกฤษ "THESIS ADVISORY COMMITTEE" · เล่มไทย "คณะกรรมการที่ปรึกษาวิทยานิพนธ์"
    (วัดจากเล่มจริง 8 เล่ม ครบสามประเภทและทั้งสองภาษา)
    """
    if not doc_type:
        return ""
    if english:
        return f"{DOC_TYPE_NAME_EN.get(doc_type, doc_type)} ADVISORY COMMITTEE"
    return f"คณะกรรมการที่ปรึกษา{DOC_TYPE_NAME_TH.get(doc_type, doc_type)}"


_ABS_HEADING_AS_PRINTED = (
    re.compile(r"([^\s:]*คณะกรรมการที่ปรึกษา[^\s:]*)"),
    re.compile(r"([A-Za-z][A-Za-z\s]*ADVISORY\s+COMMITTEE)", re.I),
)


def abstract_committee_heading_printed(page_text, english):
    """หัวข้อรายชื่อกรรมการตามที่หน้านี้พิมพ์จริง ('' ถ้าไม่มีอะไรใกล้เคียง)

    ยกมาทั้งท่อนจนจบคำ ไม่ใช่ตัดกรอบเท่าความยาวข้อความที่ถูกต้อง — เล่มที่พิมพ์ซ้ำคำ
    ("คณะกรรมคณะกรรมการที่ปรึกษา") ถ้าตัดกรอบจะได้ "กรรมคณะกรรมการที่ปรึกษา พ"
    ซึ่งขึ้นต้นกลางคำและมีตัวแรกของชื่อคนติดมา เจ้าหน้าที่อ่านแล้วไม่เห็นว่าซ้ำตรงไหน
    """
    pattern = _ABS_HEADING_AS_PRINTED[1 if english else 0]
    for line in (page_text or "").splitlines():
        match = pattern.search(line)
        if match:
            return soft(match.group(1))
    return ""


def _report_abstract_committee_heading(rep, page_text, doc_type, english, loc):
    """หัวข้อรายชื่อกรรมการที่ปรึกษาต้องตรง template ทุกตัวอักษร

    เล่มจริง 6237950 PHPH/M (ก.ย. 2569) พิมพ์ "คณะกรรมคณะกรรมการที่ปรึกษา" — "คณะกรรม"
    ซ้ำ และขาดคำว่า "วิทยานิพนธ์" ท้ายข้อความ ระบบไม่ฟ้องเลยสักข้อ เพราะตัวจับหัวข้อ
    (_ABS_COMMITTEE_HEADING) ยอมรับ "คณะกรรมการที่ปรึกษา" ตามด้วยอะไรก็ได้ และเมื่อหา
    หัวข้อไม่เจอ (เล่มนี้ไม่มี ":" ด้วย) ก็ข้ามการตรวจทั้งก้อนเงียบ ๆ

    หน้าที่เขียนประเภทเล่มอื่น (เช่น "คณะกรรมการที่ปรึกษาสารนิพนธ์" ในเล่มวิทยานิพนธ์)
    มีข้อ FORM.DOC_TYPE ฟ้องอยู่แล้ว จึงไม่ฟ้องซ้ำที่นี่
    """
    want = abstract_committee_heading(doc_type, english)
    if not want or spelled_in(want, page_text):
        return
    # หัวข้อครบทุกคำแต่สะกดวรรณยุกต์/สระ/ำ ไม่ตรง (เจ้าหน้าที่สั่ง 6 ต.ค. 2569 = สะกดผิด) ต้องฟ้องเสมอ —
    # ข้ามได้เฉพาะหน้าที่ไม่มีหัวข้อนี้เลยแต่เขียนประเภทเล่มอื่นไว้ (FORM.DOC_TYPE ฟ้องอยู่แล้ว)
    if norm(want) not in norm(page_text) and doc_types_printed(page_text):
        return
    printed = abstract_committee_heading_printed(page_text, english) or _closest_run(page_text, want)
    if printed:
        # ไล่ความต่างทีละตัวเฉพาะตอนที่ต่างกันจุดเดียว ไม่งั้นได้ประโยคอย่าง
        # 'มี "กรรม" เกินมา และ ต่างที่ " " ต้องเป็น "วิทยานิ" และ ขาด "นธ์"' ซึ่งอ่านไม่รู้เรื่อง
        # กว่าการดูข้อความสองอันเทียบกันเอง (หลักการเดียวกับข้อรหัสนักศึกษา)
        analysis = analyze_diff(printed, want)
        found = f'หัวข้อรายชื่อคณะกรรมการที่ปรึกษาเขียนว่า "{printed}"'
        if analysis.text and " และ " not in analysis.text:
            found = found_with_diff(found, analysis)
        else:
            found = DetailText(found, marks_only=analysis.marks_only)
    else:
        found = "ไม่พบหัวข้อรายชื่อคณะกรรมการที่ปรึกษาบนหน้านี้"
    rep.add("RED", "front_matter", loc, found, f'ต้องเป็น "{want}"',
            "แก้ข้อความหัวข้อให้ตรง template ทุกตัวอักษร", "FRONT.ABSTRACT")


def _check_abstract_committees(rep, committees, abs_en_pages, abs_th_pages, pages,
                               page_ref, doc_type=""):
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
            abs_label = "บทคัดย่ออังกฤษ" if heading_en else "บทคัดย่อไทย"
            if ai == page_list[0]:
                # หน้าแรกของบทคัดย่อภาษานั้นเท่านั้น — หน้าต่อ ("บทคัดย่อ (ต่อ)") ไม่มีหัวข้อ
                _report_abstract_committee_heading(
                    rep, pages[ai], doc_type, heading_en,
                    f"{abs_label} ({page_ref(ai)})")
            parsed = abstract_committee_block(pages[ai])
            if not parsed:
                continue
            _, block = parsed
            names, degrees = split_abstract_committee(block)
            if not names:
                continue
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
            # ใช้ ; คั่นชื่อ = ข้อเดียวต่อหน้า ไม่ยกชื่อมา (อัปเดต 1.6.1 เจ้าของยืนยัน 26 ต.ค. 2569 — เดิมขึ้น
            # 'ไม่มีจุลภาคคั่นหน้าชื่อ "; ชื่อ…"' ทีละคน)
            if committee_semicolons(block):
                rep.add(ABSTRACT_COMMA_ZONE, "front_matter", loc, "ใช้ ; คั่นชื่อ", "ต้องใช้ ,",
                        "แก้เครื่องหมาย ; ที่คั่นรายชื่อเป็นจุลภาค (,)", "FRONT.ABSTRACT_COMMA")
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
    text = re.sub(r'\b0([1-9])', r'\1', _ERA_PREFIX.sub(' ', text or ""))
    # norm() ตัดช่องว่างทิ้งหมด ตัวเลขสองชุดที่คั่นด้วยช่องว่างหรือขึ้นบรรทัดใหม่จึงต่อกัน
    # เป็นเลขเดียว ("หน้า 2" บรรทัดบน + "7 July" บรรทัดล่าง = "27JULY") ต้องคั่นไว้ก่อน
    return " ".join(norm(part) for part in re.split(r'(?<=\d)\s+(?=\d)', text))


def exam_date_on_page(exam_date, page_text):
    """วันที่สอบตามข้อมูลอนุมัติพิมพ์อยู่บนหน้านี้หรือไม่

    ห้ามค้นแบบ "มีข้อความนี้อยู่ข้างใน" เฉย ๆ — วันที่อนุมัติ "7 July 2026" จะไปเจอ
    อยู่ในหน้าที่พิมพ์ "17 July 2026" หรือ "27 July 2026" แล้วผ่านทั้งที่วันผิด
    ไฟล์ eThesis เขียนวันที่ 1-9 เป็นเลขหลักเดียวเสมอ วันต้นเดือนจึงโดนทุกเล่ม
    ต้องไม่มีตัวเลขติดอยู่หน้าหรือหลังวันที่ที่ค้นเจอ
    """
    want = _exam_date_key(exam_date)
    if not want:
        return False
    return bool(re.search(rf'(?<!\d){re.escape(want)}(?!\d)', _exam_date_key(page_text)))


def _check_exam_date(rep, exam_date, sig_pages, pages, page_ref):
    """วันที่สอบต้องตรงข้อมูลอนุมัติ "ทุกหน้าลงนาม"

    เดิมรวมข้อความสองหน้าลงนามแล้วค้นครั้งเดียว หน้าที่วันที่ผิดหรือหายจึงรอดไปได้
    ถ้าอีกหน้าหนึ่งถูก และตารางยืนยันก็ขึ้นเป็นแถวเดียวแทนที่จะแยกรายหน้า
    """
    if not sig_pages:
        rep.add_verification("วันที่สอบผ่าน", "หน้าลงนาม", "skipped",
                             "ระบบหาหน้าลงนามไม่เจอ")
        return
    for k, idx in enumerate(sig_pages):
        loc = f"หน้าลงนาม {k + 1} ({page_ref(idx)})"
        if exam_date_on_page(exam_date, pages[idx]):
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
    # บรรทัดที่เป็นปีล้วน (มี/ไม่มี "พ.ศ.") แต่เป็นปีอื่น — ไว้บอกว่าเล่มพิมพ์ปีอะไร
    other_year_line = next((line for line in lines
                            if re.fullmatch(r'\d{4}', soft(_ERA_PREFIX.sub(' ', line)))), "")
    rep.add_verification("ปีบนหน้าปก", "หน้าปก", "pass" if year_ok else "fail",
                         "" if year_ok else (year_lines[0] if year_lines else other_year_line))
    if year_ok:
        return
    if year_lines:
        rep.add("RED", "front_matter", "หน้าปก",
                f'พบปี {year} บนหน้าปกแต่ไม่ได้อยู่ในบรรทัดปีของตัวเอง: "{year_lines[0]}"',
                f'หน้าปกต้องมีบรรทัดที่เป็นปีเพียงอย่างเดียว เช่น "{year}" หรือ "พ.ศ. {year}"',
                "เพิ่มหรือแก้บรรทัดปีบนหน้าปกให้มีเฉพาะปี", "FRONT.COVER")
    elif other_year_line:
        # มีบรรทัดปีอยู่แล้วแต่เป็นปีอื่น (เล่มจริง ต.ค. 2569 พิมพ์ปีที่ส่งเล่ม ไม่ใช่ปีที่สอบผ่าน)
        # บอกว่าเล่มเขียนปีอะไร และต้องแก้เป็นปีอะไร (เจ้าหน้าที่สั่ง 19 ต.ค. 2569)
        rep.add("RED", "front_matter", "หน้าปก",
                f'ปีบนหน้าปกในเล่มเขียนว่า "{other_year_line}"',
                # ถ้อยคำเจ้าหน้าที่ (19 ต.ค. 2569) — ไม่ปิดท้ายด้วยเครื่องหมายคำพูด ข้อความสรุปจึงพิมพ์
                # ทั้งประโยคนี้ ไม่ย่อเหลือ 'ต้องแก้เป็น "<ปี>"' (ดู _corrected_value)
                f'ต้องแก้เป็น "{year}" โดยอ้างอิงจากปีที่มีผลสอบผ่าน',
                f'แก้ปีบนหน้าปกจาก "{other_year_line}" เป็น "{year}"', "FRONT.COVER")
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
                              expected_style=None, page_texts=None, reported=(), ignore=()):
    """เลขหน้าส่วนนำ: ชนิดต้องตรงภาษาเล่ม และเรียงต่อเนื่อง ไม่ซ้ำ ไม่ข้าม

    เดิมตรวจเฉพาะค่าเลขหน้าของหน้าลงนาม 2 หน้าแรก (i/ii หรือ ก/ข) หน้าอื่นของส่วนนำ
    จึงไม่ถูกตรวจเลย ฟังก์ชันนี้ตรวจทั้งช่วง

    reported = หน้าที่กฎเลขหน้าหน้าลงนาม (_report_signature_page_labels) ฟ้องไปแล้ว —
    ไม่นับซ้ำในข้อ "ไม่ได้พิมพ์เลขหน้าไว้" / "เลขหน้าผิดชนิด" เล่มจริง 6538041 SHPP/D
    หน้าลงนามสองหน้าไม่มีเลขหน้า แล้วได้สามข้อจากความผิดเดียว: หน้าลงนาม 1 และ 2
    (สีส้ม) กับ "2 หน้าไม่ได้พิมพ์เลขหน้าไว้ คือแผ่นที่ 2 และ 3" (สีแดง) เจ้าหน้าที่สั่ง
    (ก.ย. 2569) "ข้อ 8 กับ 2 ตรวจเหมือนกัน เอาตามข้อ 2 ก็พอ" — ตรงกับกติกา ส.ค. 2569
    ที่ให้เลขหน้าหน้าลงนามเป็นสีส้มเพื่อไม่ให้ฟ้องสองข้อจากความผิดเดียว
    หน้าที่ฟ้องไปแล้วยังนับเป็นหน้าคั่นในการตรวจความต่อเนื่องเหมือนเดิม

    ignore = หน้าว่างจริง (อัปเดต 1.7) ไม่มีเลขหน้าแน่นอนและแจ้งเป็นข้อ PAGE.EMPTY แล้ว — ไม่นับทั้งเป็นหน้าที่อ่านเลข
    ไม่ได้และเป็นหน้าคั่นตอนตรวจความต่อเนื่อง
    """
    if stop_idx is None or stop_idx <= start_idx:
        return
    reported = set(reported or ())
    ignore = set(ignore or ())
    entries, unread = [], []
    for i in range(start_idx, stop_idx):
        if i in ignore:
            continue
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

    off_style = [(i, lab, s) for i, lab, s, _v in entries
                 if s != main_style and i not in reported]
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
        problems, _first = _sequence_problems([(i, lab, v) for i, lab, _s, v in seq], ignore, main_style)
        if problems:
            observed = ", ".join(lab for _i, lab, _s, _v in seq)
            rep.add(PAGE_SEQUENCE_ZONE, "front_matter", "ส่วนนำ",
                    sequence_found(problems),
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

        no_number = [i for i in unread if i not in reported and _has_text(i)]
        unreadable = [i for i in unread if i not in reported and not _has_text(i)]
        for group, zone, detail, rule_id, fix in (
            (no_number, "RED", "ไม่ได้พิมพ์เลขหน้าไว้", "PAGE.NUMBERING",
             "เพิ่มเลขหน้าให้ครบทุกหน้า"),
            (unreadable, UNCERTAIN_ZONE, "ระบบอ่านเลขหน้าไม่ได้ (อาจเป็นหน้าภาพ/สแกน)",
             "UNCERTAIN.REVIEW", "ตรวจด้วยตาว่าหน้าเหล่านี้มีเลขหน้าถูกต้องและต่อเนื่อง"),
        ):
            if not group:
                continue
            shown, more = _sheet_list(group)
            rep.add(zone, "front_matter", "ส่วนนำ",
                    f"{len(group)} หน้า{detail} คือแผ่นที่ {shown} ของไฟล์{more}",
                    f"ทุกหน้าต้องมีเลขหน้าเป็น{want}", fix, rule_id)


def _int_to_roman(value):
    """เลขโรมันตัวเล็ก (4 → "iv") — ใช้แสดงช่วงเลขหน้าส่วนนำ"""
    out = ""
    for number, symbol in ((1000, "m"), (900, "cm"), (500, "d"), (400, "cd"), (100, "c"), (90, "xc"),
                           (50, "l"), (40, "xl"), (10, "x"), (9, "ix"), (5, "v"), (4, "iv"), (1, "i")):
        while value >= number:
            out += symbol
            value -= number
    return out


def _page_label_of(style, value):
    """ลำดับเลขหน้า → ป้ายตามชนิดของเล่ม (ย้อนกลับของ _page_label_order) เช่น ("thai", 3) → "ค" """
    if style == "thai" and 1 <= value <= len(_THAI_PAGE_LETTERS):
        return _THAI_PAGE_LETTERS[value - 1]
    if style == "roman" and value >= 1:
        return _int_to_roman(value)
    return str(value)


def _sequence_problems(seq, ignore=(), style="arabic"):
    """จุดที่เลขหน้าเรียงไม่ต่อเนื่อง — seq = [(ดัชนีหน้า, เลขที่พิมพ์, ลำดับ)] เรียงตามหน้า

    คืน (รายการจุด, ดัชนีหน้าแรกที่พบ) ใช้ทั้งส่วนนำและส่วนเนื้อหา/ส่วนท้าย (อัปเดต 1.8)
    หน้าที่คั่นอยู่แต่อ่านเลขไม่ได้/ใช้ชนิดผิด ยังนับเป็นหน้าของเล่ม — เดิมส่วนนำ "ข้ามไปเลย" (continue) การตรวจ
    ความต่อเนื่องจึงเงียบทั้งช่วง ตอนนี้นับจำนวนหน้าที่คั่นแทน จึงยังฟันธงได้ว่าต่อเนื่องหรือไม่โดยไม่ต้องเดาว่า
    หน้าที่อ่านไม่ออกพิมพ์เลขอะไร · ยกเว้นหน้าว่างจริง (ignore อัปเดต 1.7)

    ถ้อยคำแต่ละจุด (อัปเดต 1.8.2 เจ้าของยืนยัน 28 ต.ค. 2569 — เดิม "กระโดดจาก A ไป B" ทุกแบบ ไม่บอกว่าซ้ำหรือขาดเลขอะไร)
      เลขเดียวกันติดกัน    'เลขหน้า "3" ถูกใช้ซ้ำ 2 หน้า' (หลายหน้า = จุดเดียว)
      ย้อนกลับ            'เลขหน้า 226–227 ซ้ำ (หลังหน้า "227" กลับไปเริ่ม "226" ใหม่)' ช่วง = เลขที่พิมพ์ไว้แล้วในส่วนนั้น
                          และพิมพ์อีกครั้งในช่วงที่ย้อนกลับ (ช่วงนั้นเรียงต่อเนื่องจากเลขใหม่) — เพิ่มเข้า 1.8.2 เจ้าของสั่ง
                          28 ต.ค. 2569: เดิมช่วง = เลขใหม่ถึงเลขก่อนหน้า 1, 2, 5, 1, 2 จึงได้ "1–5 ซ้ำ" ทั้งที่ 3–4 ไม่เคยพิมพ์ ·
                          เลขที่ซ้ำไม่ต่อเนื่องหรือไม่มีเลย 'เลขหน้าย้อนกลับ (หลังหน้า "5" กลับไปเริ่ม "1" ใหม่)' ไม่บอกช่วง
      ข้าม                'ข้ามเลขหน้า 153–154 (จาก "152" ไป "155")' เลขที่ขาด = จากเลขที่ควรเป็น (นับหน้าที่คั่นแล้ว)
      น้อยกว่าที่ควร       'เลขหน้า "12" ควรเป็น "13"' (มากกว่าเลขก่อนหน้า แต่มีหน้าที่คั่นอยู่ซึ่งอ่านเลขไม่ได้)
      สลับกันคู่เดียว       'เลขหน้า "12" กับ "13" สลับกัน (พิมพ์ "13" ก่อน "12")' (เพิ่มเข้า 1.8.2 28 ต.ค. 2569) — ควรเป็น X
                          แต่พบ X+1 แล้วหน้าถัดไปติดกันเป็น X และหน้าหลังจากนั้นกลับมาตรง (หรือคู่นี้เป็นท้ายส่วน)
                          เดิมได้ 3 จุด ข้าม/ซ้ำ/ข้าม · สลับเกินคู่เดียวติดกันใช้ถ้อยคำแบบอื่นตามเดิม
    ช่วงเลขแสดงเป็นชนิดเดียวกับเล่ม (style: "arabic" / "thai" ค–ง / "roman" iii–iv) คั่นด้วย "–"
    """
    ignore = set(ignore or ())

    def span(lo, hi):
        return _page_label_of(style, lo) if lo == hi else f"{_page_label_of(style, lo)}–{_page_label_of(style, hi)}"

    def between(a, b):
        """จำนวนหน้าที่คั่นระหว่างดัชนีหน้า a กับ b (ไม่นับหน้าว่างจริง)"""
        return sum(1 for j in range(a + 1, b) if j not in ignore)

    problems, first, dup_run, k = [], None, 1, 1
    while k < len(seq):
        prev_i, prev_lab, prev_v = seq[k - 1]
        cur_i, cur_lab, cur_v = seq[k]
        k += 1
        if cur_v == prev_v:
            dup_run += 1
            if k == len(seq) or seq[k][2] != cur_v:
                problems.append(f'เลขหน้า "{cur_lab}" ถูกใช้ซ้ำ {dup_run} หน้า')
                first = cur_i if first is None else first
                dup_run = 1
            continue
        dup_run = 1
        want = prev_v + between(prev_i, cur_i) + 1
        if cur_v == want:
            continue
        first = cur_i if first is None else first
        nxt = seq[k] if k < len(seq) else None
        if (nxt and cur_v == want + 1 and nxt[2] == want and not between(cur_i, nxt[0])
                and (k + 1 == len(seq) or seq[k + 1][2] == want + 2 + between(nxt[0], seq[k + 1][0]))):
            problems.append(f'เลขหน้า "{nxt[1]}" กับ "{cur_lab}" สลับกัน (พิมพ์ "{cur_lab}" ก่อน "{nxt[1]}")')
            k += 2                      # หน้าที่สลับและหน้าหลังจากนั้นตรวจแล้ว
            continue
        if cur_v < prev_v:
            end = k
            while end < len(seq) and seq[end][2] == seq[end - 1][2] + between(seq[end - 1][0], seq[end][0]) + 1:
                end += 1
            again = sorted({v for _i, _lab, v in seq[k - 1:end]} & {v for _i, _lab, v in seq[:k - 1]})
            restart = f'(หลังหน้า "{prev_lab}" กลับไปเริ่ม "{cur_lab}" ใหม่)'
            if again and again[-1] - again[0] == len(again) - 1:
                problems.append(f'เลขหน้า {span(again[0], again[-1])} ซ้ำ {restart}')
            else:
                problems.append(f'เลขหน้าย้อนกลับ {restart}')
        elif cur_v > want:
            problems.append(f'ข้ามเลขหน้า {span(want, cur_v - 1)} (จาก "{prev_lab}" ไป "{cur_lab}")')
        else:
            problems.append(f'เลขหน้า "{cur_lab}" ควรเป็น "{_page_label_of(style, want)}"')
    return problems, first


def sequence_found(problems):
    """ข้อความ "พบ" ของข้อเลขหน้าไม่ต่อเนื่อง — 5 จุดแรก + "และอีก N จุด\""""
    more = f" และอีก {len(problems) - 5} จุด" if len(problems) > 5 else ""
    return "เลขหน้าไม่ต่อเนื่อง: " + " และ ".join(problems[:5]) + more


def _sheet_list(group, limit=5):
    """แผ่นในไฟล์แบบที่ข้อเลขหน้าใช้ ("2, 3 และ 4") — คืน (รายการ, ท่อน " และอีก N หน้า" ถ้าเกิน limit)"""
    sheets = [str(i + 1) for i in group[:limit]]
    shown = " และ ".join([", ".join(sheets[:-1]), sheets[-1]]) if len(sheets) > 1 else sheets[0]
    more = f" และอีก {len(group) - limit} หน้า" if len(group) > limit else ""
    return shown, more


def _report_unnumbered_body_pages(rep, pages, page_labels, start_idx, edge_numbered, skip=()):
    """ส่วนเนื้อหาและส่วนท้าย: หน้าที่มีข้อความแต่ไม่ได้พิมพ์เลขหน้า — แดงข้อเดียว (อัปเดต 1.8 เจ้าของสั่ง 28 ต.ค. 2569)

    แบบเดียวกับกลุ่ม "ไม่ได้พิมพ์เลขหน้าไว้" ของส่วนนำ (_check_front_page_numbers) เดิมส่วนนี้ไม่มีข้อเทียบเท่า
    หน้าพวกนี้ถูกนับเป็นหน้าคั่นในการตรวจความต่อเนื่องแล้วผ่านไปเงียบ ๆ (ยังนับเป็นหน้าคั่นเหมือนเดิม)
    ไม่นับ: หน้าว่างจริง (PAGE.EMPTY) · หน้าที่ดึงข้อความไม่ได้หรือมีแค่เลขหน้า/ภาพ (PAGE.BLANK / UNCERTAIN ตามเดิม) ·
    หน้าที่อ่านเลขหน้าได้แม้เป็นชนิดอื่น (PAGE.HEADER ฟ้องอยู่แล้ว) · หน้าที่แถบหัว/ท้ายกระดาษมีสิ่งที่อาจเป็นเลขหน้า
    (edge_page_number_hint — เลขปน running head / หน้าแนวนอน / เลขติดตัวอักษร)
    สำรวจเล่มจริง 5 เล่ม (28 ต.ค. 2569): ไม่มีหน้าที่ template ตั้งใจไม่พิมพ์เลข — หน้าแรกของบท หน้าแนวนอน
    หน้าคั่นภาคผนวก และหน้าประวัติมีเลขหน้าทุกหน้า
    """
    skip = set(skip or ())
    missing = [i for i in range(start_idx, len(pages))
               if i not in skip and i not in edge_numbered and not page_labels.get(i)
               and not _is_blank_page_text(pages[i])]
    if not missing:
        return
    shown, more = _sheet_list(missing)
    rep.add("RED", "body/end", "ส่วนเนื้อหา และส่วนท้าย",
            f"{len(missing)} หน้าไม่ได้พิมพ์เลขหน้าไว้ คือแผ่นที่ {shown} ของไฟล์{more}",
            "ทุกหน้าต้องมีเลขหน้าอารบิก", "เพิ่มเลขหน้าให้ครบทุกหน้า", "PAGE.NUMBERING")


def strip_name_prefix(name):
    """Remove honorifics that must not be printed as part of the student name.

    ใช้ตัวตัดชุดเดียวกับตัวนำเข้า eThesis (thai_text.strip_student_title) — เดิมรู้จักแค่ นาย/นาง/นางสาว/ดร./Mr/Mrs/Miss/Ms/Dr
    ชื่อที่เจ้าหน้าที่พิมพ์ในฟอร์มเองพร้อมยศ ("VDC Lt Col ...") จึงยังมียศติดไปเทียบกับเล่มที่พิมพ์ชื่อล้วนตามกติกา
    """
    return strip_student_title(soft(name))


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


# ---------- ประเภทเล่มที่เล่มเขียนไว้ ต้องตรงกับที่ได้รับอนุมัติ ----------
# เจ้าหน้าที่สั่ง (ก.ย. 2569) หลังเล่ม 6838776 TMCT/M: eThesis อนุมัติเป็นสารนิพนธ์ แต่เล่มเขียน
# INDEPENDENT STUDY ทั้งเล่ม "ให้ฟ้องด้วย"
#
# template วางคำระบุประเภทไว้ตายตัวสามที่ (วัดจากเล่มจริงครบ 8 เล่ม ทั้งสามประเภทและทั้งสองภาษา)
#   หน้าปก       "A THESIS SUBMITTED ..." / "วิทยานิพนธ์นี้เป็นส่วนหนึ่ง ..."
#   หน้าลงนาม    บรรทัด "Thesis" เดี่ยว ๆ และ "Thesis Advisory/Examination Committees"
#                / "คณะกรรมการที่ปรึกษาวิทยานิพนธ์" / "คณะกรรมการสอบวิทยานิพนธ์"
#   บทคัดย่อ     "THESIS ADVISORY COMMITTEE:" / "คณะกรรมการที่ปรึกษาวิทยานิพนธ์:"
# อ่านเฉพาะตำแหน่งเหล่านี้ ไม่ค้นคำทั้งหน้า — เนื้อความบทคัดย่อเขียน "this thesis" ได้ตามปกติ
#
# DISSERTATION / ดุษฎีนิพนธ์ ไม่ใช่ประเภทที่อนุมัติได้ (eThesis มีแค่สามประเภท ปริญญาเอกก็เป็น
# THESIS / วิทยานิพนธ์) แต่ถ้าพิมพ์อยู่ตรงตำแหน่งเหล่านี้ = ประเภทเล่มไม่ตรง เล่มที่ 4 (ป.เอก
# หลักสูตรไทย) เขียน "DISSERTATION ADVISORY COMMITTEE" ในบทคัดย่ออังกฤษ เจ้าหน้าที่ยืนยัน
# (ก.ย. 2569) ว่าต้องเป็น THESIS "ต้องแก้ไข"
DOC_TYPE_NAME_EN = {"THESIS": "THESIS", "THEMATIC PAPER": "THEMATIC PAPER",
                    "INDEPENDENT STUDY": "INDEPENDENT STUDY", "DISSERTATION": "DISSERTATION"}
DOC_TYPE_NAME_TH = {"THESIS": "วิทยานิพนธ์", "THEMATIC PAPER": "สารนิพนธ์",
                    "INDEPENDENT STUDY": "การค้นคว้าอิสระ", "DISSERTATION": "ดุษฎีนิพนธ์"}
_DOC_TYPE_EN_WORD = r"(THESIS|THEMATIC\s+PAPER|INDEPENDENT\s+STUDY|DISSERTATION)"
_DOC_TYPE_EN_SPOTS = tuple(re.compile(p, re.I) for p in (
    rf"\bAN?\s+{_DOC_TYPE_EN_WORD}\s+SUBMITTED\b",
    rf"^\s*(?:AN?\s+)?{_DOC_TYPE_EN_WORD}\s*$",
    rf"\b{_DOC_TYPE_EN_WORD}\s+ENTITLED\b",
    rf"\b{_DOC_TYPE_EN_WORD}\s+(?:ADVISORY|EXAMINATION)\s+COMMITTEES?\b",
))
_DOC_TYPE_TH_SPOTS = ("{}นี้เป็นส่วนหนึ่ง", "คณะกรรมการที่ปรึกษา{}", "คณะกรรมการสอบ{}")
_THAI_CHAR = re.compile(r"[\u0E00-\u0E7F]")
_DOC_TYPE_BY_EN = {norm(name): key for key, name in DOC_TYPE_NAME_EN.items()}


def doc_types_printed(page_text):
    """ประเภทเล่มที่หน้านี้เขียนไว้ตรงตำแหน่งของ template — [(ประเภท, คำที่พิมพ์), ...]"""
    found = []
    for line in (page_text or "").splitlines():
        match = next((m for m in (p.search(line) for p in _DOC_TYPE_EN_SPOTS) if m), None)
        if match:
            found.append((_DOC_TYPE_BY_EN[norm(match.group(1))], soft(match.group(1))))
            continue
        nl = norm(line)
        for key, name in DOC_TYPE_NAME_TH.items():
            if nl == norm(name) or any(norm(spot.format(name)) in nl for spot in _DOC_TYPE_TH_SPOTS):
                found.append((key, name))
                break
    return found


def doc_type_check(approved_type, program_language, spots, ethesis_type=""):
    """ประเภทเล่มที่แต่ละจุดเขียนไว้ เทียบกับประเภทที่เจ้าหน้าที่ระบุ

    spots = [(ตำแหน่ง, ข้อความของหน้า), ...] ตามลำดับในเล่ม หน้าปกมาก่อน
    คืน (แถวในตารางผลเทียบ, [(สี, ตำแหน่ง, ที่พบ, ควรเป็น, ต้องแก้), ...],
         ประเภทที่ใช้เทียบข้อความบังคับบนหน้าปก)

    approved_type = ค่าที่เจ้าหน้าที่เลือกในแบบฟอร์ม — เจ้าหน้าที่สั่ง (ก.ย. 2569) "ถ้าข้อมูลที่อ่านมา
                    แล้วเจ้าหน้าที่เปลี่ยนด้วยมือ ให้เชื่อเจ้าหน้าที่ และดำเนินการตรวจ" เล่มไม่ตรงกับ
                    ค่านี้ = แดง
    ethesis_type  = ค่าที่อ่านได้จากไฟล์ eThesis ใช้เป็นข้อมูลประกอบเมื่อต่างจากที่เลือกเท่านั้น
                    ไม่เอามาตัดสินเล่ม (เล่ม 6237950 PHPH/M: ไฟล์อ่านได้ "วิทยานิพนธ์"
                    เจ้าหน้าที่แจ้งว่าเป็นสารนิพนธ์ ต้องตรวจตามแนวสารนิพนธ์)

    จุดผิดหลายหน้ารวมเป็นข้อเดียว เพราะนักศึกษาแก้เรื่องเดียว (เปลี่ยนประเภทเล่ม) ส่วนตาราง
    ผลเทียบลงทุกจุดที่อ่านเจอ ทั้งตอนตรงและไม่ตรง จะได้แยก "ตรวจแล้วผ่าน" กับ "ไม่ได้ตรวจ" ออก

    หน้าปกที่เขียนประเภทอื่น ถูกฟ้องในข้อนี้แล้ว ข้อความบังคับบนปกจึงเทียบกับประเภทที่ปกเขียนเอง
    (ยังจับคำพิมพ์ผิดอื่นได้) — ถ้าเทียบกับประเภทที่อนุมัติ จะได้อีกข้อที่บอกว่าปก "ขาด
    A THEMATIC PAPER" ทั้งที่ปกเขียน AN INDEPENDENT STUDY อยู่ นักศึกษาอ่านแล้วจะเติมซ้อน
    """
    if not approved_type:
        return [], [], approved_type
    ethesis_type = ethesis_type if ethesis_type and ethesis_type != approved_type else ""
    printed = [(spot, doc_types_printed(text)) for spot, text in spots]
    wrong = [spot for spot, found in printed if any(t != approved_type for t, _ in found)]
    rows = []
    if any(found for _, found in printed):
        rows.append(("ข้อมูลอนุมัติ", "fail" if wrong else "pass",
                     f'"{doc_type_name(approved_type, program_language)}"'))
        if ethesis_type:
            # ไฟล์ eThesis เขียนคนละอย่างกับที่เจ้าหน้าที่เลือก — ลงตารางให้เห็น แต่ไม่ตัดสิน
            rows.append(("ไฟล์ eThesis", "skipped",
                         f'"{doc_type_name(ethesis_type, program_language)}" '
                         "— ระบบตรวจตามประเภทที่เจ้าหน้าที่เลือก"))
        for spot, found in printed:
            if found:
                words = dict.fromkeys(word for _, word in found)
                rows.append((spot, "fail" if spot in wrong else "pass",
                             ", ".join(f'"{word}"' for word in words)))

    def item(zone, want_type, spots_off, fix):
        want = doc_type_name(want_type, program_language)
        # ชื่อที่บอกให้แก้ ใช้ภาษาของคำที่หน้านั้นพิมพ์ ไม่ใช่ภาษาของเล่ม — เล่มไทยพิมพ์คำอังกฤษ
        # ในบทคัดย่ออังกฤษ (เล่มที่ 4 "DISSERTATION") ถ้าใช้ภาษาของเล่ม จะได้ "เล่มเขียนว่า
        # ดุษฎีนิพนธ์ ต้องแก้เป็นวิทยานิพนธ์" ทั้งที่หน้านั้นต้องแก้เป็น THESIS
        off = [(t, word) for spot, found in printed if spot in spots_off
               for t, word in found if t != want_type]

        def named(doc_type, word):
            return (DOC_TYPE_NAME_TH if _THAI_CHAR.search(word) else DOC_TYPE_NAME_EN)[doc_type]

        names = dict.fromkeys(named(t, word) for t, word in off)
        fix_to = dict.fromkeys(named(want_type, word) for _, word in off)
        must_be = (f'ประเภทเล่มที่ได้รับอนุมัติคือ "{want}" ต้องแก้ทุกจุดเป็น '
                   + " หรือ ".join(f'"{name}"' for name in fix_to)
                   + (" ตามภาษาของหน้า" if len(fix_to) > 1 else ""))
        if "หน้าปก" in spots_off:
            cover_sentence = dict(cover_required_items(want_type, program_language))["ข้อความประเภทงาน"]
            must_be += f' โดยหน้าปกต้องเป็น "{cover_sentence}"'
        return (zone, _join_and(spots_off),
                "ประเภทเล่มไม่ตรงกับที่ได้รับอนุมัติ เล่มเขียนว่า "
                + " และ ".join(f'"{name}"' for name in names),
                must_be, fix)

    issues = []
    if wrong:
        issues.append(item("RED", approved_type, wrong,
                           "ตรวจว่าประเภทเล่มที่เลือกไว้ถูกต้อง ถ้าถูกต้องแล้วจึงส่งกลับให้นักศึกษาแก้ทุกจุด"))
    cover_wrong = [t for t, _ in (printed[0][1] if printed and printed[0][0] == "หน้าปก" else [])
                   if t != approved_type]
    return rows, issues, (cover_wrong[0] if cover_wrong else approved_type)


def doc_type_name(doc_type, program_language):
    """ชื่อประเภทเล่มตามภาษาของเล่ม — เล่มไทยเขียน "สารนิพนธ์" เล่มอังกฤษเขียน "THEMATIC PAPER" """
    names = DOC_TYPE_NAME_TH if program_language == "thai" else DOC_TYPE_NAME_EN
    return names.get(doc_type, doc_type)


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
    sig_lang = _one_sided(int(norm(SIGNATURE_TEMPLATE_TH_TAIL) in after_cover),
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
        status, in_file = "skipped", "ระบบอ่านภาษาจากไฟล์ไม่ได้"
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
    expected = soft(fold_dashes(expected))
    page_flat = soft(fold_dashes(page_text))
    if not expected:
        return True, ""
    if re.search(r'[ก-๙]', expected):
        # สะกดตรงทุกตัวรวมวรรณยุกต์/สระ/ำ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569) — ดู spelling_key
        return spelled_in(expected, page_text), "text"
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


def _join_wrapped(lines):
    """ต่อบรรทัดที่ถูกตัดขึ้นบรรทัดใหม่ให้เป็นข้อความเดียว โดยไม่ใส่ช่องว่างที่ไม่มีอยู่ในเล่ม

    ภาษาไทยไม่เว้นวรรคระหว่างคำ ผู้เขียนจึงตัดบรรทัดตรงไหนก็ได้ และรอยตัดนั้นไม่มีช่องว่างจริง
    ของเดิมต่อทุกรอยด้วยช่องว่าง ชื่อเรื่อง "...ในระบบ" / "บริหาร..." จึงถูกอ่านเป็น "ในระบบ บริหาร"
    แล้วรายงานว่า 'ต่างที่ "มาใช้ในระบบ บริหาร..."' ทั้งที่เล่มพิมพ์ติดกัน (เล่มจริง 6 ต.ค. 2569) ส่วนอังกฤษที่ตัดบรรทัดหลังขีดกลาง ("MULTI-" / "CRITERIA") ก็ไม่มีช่องว่างเช่นกัน

    รอยตัดที่อักษรไทยชนอักษรไทย กับรอยตัดหลังขีดกลาง จึงต่อกันตรง ๆ รอยตัดอื่นใส่ช่องว่างเหมือนเดิม
    (ภาษาอังกฤษแยกคำด้วยช่องว่างจริง) ผลต่อการตัดสิน = ไม่มี เพราะ norm() ตัดช่องว่างทิ้งก่อนเทียบ
    """
    out = ''
    for line in lines:
        if out and not ((_THAI_LETTER.match(out[-1]) and _THAI_LETTER.match(line[:1]))
                        or (out[-1] == '-' and line[:1].isalnum())):
            out += ' '
        out += line
    return out


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
            window = _join_wrapped(lines[start:start + span])
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
    # ไฟล์ Turnitin ตรวจในขั้นแรกก่อนตัวเล่ม (อัปเดต 1.4) จึงขึ้นก่อนหน้าปก
    ("ไฟล์ Turnitin", "Turnitin"),
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
    # ถ้อยคำปัจจุบันไม่มีขีดคั่นแล้ว ('ประกาศใช้ "X" แต่คู่มือแสดงแบบที่พบ เจ้าหน้าที่ยืนยันได้')
    # ขีดจึงต้องเป็นทางเลือก ไม่งั้นท่อนที่เขียนถึงเจ้าหน้าที่หลุดไปถึงนักศึกษา (ก.ย. 2569)
    r"|\s*แต่คู่มือแสดงแบบที่พบ\s*[—-]?\s*เจ้าหน้าที่ยืนยันได้"
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
# (19 ต.ค. 2569 เจ้าหน้าที่สั่งแก้สามจุดในข้อโครงสร้างหน้าลงนามผิด: ทื่ → ที่,
#  ทำดำเนินจัดทำ → ดำเนินการจัดทำ, ฝั่ง ละ → ฝั่งละ — คำอื่นยังคงต้นฉบับ)
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


# ---------- รายชื่อกรรมการบนหน้าลงนาม: ข้อที่เจ้าหน้าที่ติ๊กว่าผิด (อัปเดต 1.5) ----------
# เจ้าของสั่ง (20 ต.ค. 2569): ระบบนับจำนวนกรรมการให้แต่ไม่เทียบชื่อ เจ้าหน้าที่เทียบรายชื่อกับ บฑ.1 / บฑ.2
# เองในขั้นที่ 1 อยู่แล้ว จึงต้องมีช่องติ๊ก "รายชื่อไม่ถูกต้อง" / "รายชื่อไม่ครบตามที่ได้รับอนุมัติ" ด้วย
# ติ๊กแล้ว = จุดที่ต้องแก้ของหน้าลงนามนั้น (signature_name_issues) — ไม่ยกรายชื่อมาในข้อความ ให้นักศึกษา
# ตรวจกับเอกสาร บฑ. เอง (เจ้าของยืนยัน 20 ต.ค. 2569)
# (เลขข้อ, ชุดรายชื่อ, ปัญหา) — เลขข้อคือสิ่งเดียวที่หน้าเว็บส่งกลับมา
SIGNATURE_NAME_PROBLEMS = (
    (1, "advisory", "wrong"),
    (2, "advisory", "short"),
    (3, "exam", "wrong"),
    (4, "exam", "short"),
)
# ป้ายช่องติ๊กใต้รายชื่อแต่ละชุดในขั้นที่ 1 — ถ้อยคำของเจ้าของ (20 ต.ค. 2569)
SIGNATURE_NAME_PROBLEM_LABEL = {"wrong": "รายชื่อไม่ถูกต้อง",
                                "short": "รายชื่อไม่ครบตามที่ได้รับอนุมัติ"}
# ข้อเช็กลิสต์บนการ์ดหน้าผลตรวจ เลขข้อตรงกับ SIGNATURE_NAME_PROBLEMS
_SIGNATURE_NAMES_TEXT = ("(1) รายชื่อคณะกรรมการที่ปรึกษา (บฑ.1) ไม่ถูกต้อง"
                         "\n"
                         "(2) รายชื่อคณะกรรมการที่ปรึกษา (บฑ.1) ไม่ครบตามที่ได้รับอนุมัติ"
                         "\n"
                         "(3) รายชื่อคณะกรรมการสอบ (บฑ.2) ไม่ถูกต้อง"
                         "\n"
                         "(4) รายชื่อคณะกรรมการสอบ (บฑ.2) ไม่ครบตามที่ได้รับอนุมัติ")
_SIGNATURE_NAMES_TEXT_EN = ("(1) Advisory committee names (GR.1) are incorrect"
                            "\n"
                            "(2) Advisory committee list (GR.1) is incomplete compared with the approval"
                            "\n"
                            "(3) Examination committee names (GR.2) are incorrect"
                            "\n"
                            "(4) Examination committee list (GR.2) is incomplete compared with the approval")


STAFF_CHECKS = [
    {
        # ไม่บังคับตอบก่อนบันทึกลงชีท (must_answer) — ขั้นที่ 1 บังคับติ๊ก "ตรวจแล้ว" ทุกชุดอยู่แล้ว
        # และหน้าผลตรวจตั้งค่าให้ตามที่ติ๊กไว้ในขั้นที่ 1 เสมอ
        "id": "SIGNATURE_NAMES",
        "item": "รายชื่อกรรมการบนหน้าลงนาม",
        "item_en": "Committee names on the signature pages",
        "why": "ระบบนับจำนวนกรรมการให้ แต่ไม่ได้เทียบชื่อ เจ้าหน้าที่เทียบรายชื่อกับ บฑ.1 / บฑ.2 "
               "ในขั้นที่ 1 ข้อที่ติ๊กว่าผิดจะเข้าข้อความสรุปเป็นจุดที่ต้องแก้",
        "why_en": "The system counts the committee members but does not compare their names. "
                  "Staff compare the names with Forms GR.1 / GR.2 in step 1. Each item ticked "
                  "as wrong goes into the summary as a fix.",
        "rule_id": "FRONT.COMMITTEE",
        "placement": "section",
        "section": "หน้าลงนาม",
        "choices": [
            {
                "id": "SIGNATURE_NAMES_OK",
                "label": "ถูกต้อง",
                "label_en": "Correct",
                "tone": "pass",
                "text": "",
                "text_en": "",
            },
            {
                "id": "SIGNATURE_NAMES_WRONG",
                "label": "รายชื่อไม่ถูกต้อง / ไม่ครบ",
                "label_en": "Names wrong / incomplete",
                "tone": "fail",
                # ข้อเช็กลิสต์ ไม่ใช่ถ้อยคำที่ส่งนักศึกษา — ข้อความสรุปสร้างจาก signature_name_issues
                "text": _SIGNATURE_NAMES_TEXT,
                "text_en": _SIGNATURE_NAMES_TEXT_EN,
            },
        ],
    },
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
        # ต้องตอบเสมอก่อนบันทึกลงชีท แม้คำตอบจะไม่เปลี่ยนค่าที่เขียน (เจ้าหน้าที่สั่ง
        # ก.ย. 2569 "ต้องเช็คสีเหลืองสีส้มให้หมด + โครงสร้างหน้าลงนามจึงจะกดได้")
        # เป็นการยืนยันว่าเจ้าหน้าที่ตรวจหน้านี้ด้วยตาแล้ว ไม่ใช่แค่ค่าที่ลงชีท
        "must_answer": True,
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
                # ถ้อยคำชุดใหม่ที่เจ้าหน้าที่เกลาและอนุมัติเอง (19 ต.ค. 2569) แทนชุดเดิม ก.ย. 2569
                # บรรทัดแรกคือ "ตำแหน่ง" แยกออกมาให้เหมือนข้ออื่นในสรุป · ข้อ (1)-(5) คือสิ่งที่ต้องแก้
                # ใช้ "(1)" ไม่ใช่ "1." เพราะเลขข้อหลักของข้อความสรุปเป็น "5." อยู่แล้ว
                # บรรทัดว่างก่อน "ดูตัวอย่าง..." เจ้าหน้าที่ใส่เอง ข้อความสรุปคงไว้ (_summary_sentence)
                #
                # จำนวนบรรทัดที่มีข้อความของสองภาษาต้องเท่ากันเสมอ (เก้าต่อเก้า) เพราะหน้ารายงานแปล
                # ด้วยการเทียบทีละบรรทัด (ไม่นับบรรทัดว่าง) บรรทัดตำแหน่งก็อยู่ในคู่แปลด้วย
                # ลิงก์คู่มืออยู่บรรทัดของตัวเอง อีเมล/แชทจะได้ไม่ตัดกลางลิงก์
                #
                # ข้อ (2): ฝั่งขวาใส่กรรมการได้ 5 ช่อง (ช่องที่ 6 ของฝั่งขวาคือผู้อำนวยการหลักสูตร)
                # รายชื่อที่ 6 เป็นต้นไปจึงไปฝั่งซ้าย เริ่มช่องเหนือคณบดีไล่ขึ้น (ตรงกับ _SIG ข้างบน)
                "text": ("ในหน้าลงนาม (หน้า i - ii หรือ ก - ข)"
                         "\n"
                         "กรุณาปรับโครงสร้างหน้าลงนามให้ตรงตาม template "
                         "ใช้ฟอนต์และกรอบตารางตามที่กำหนด โดยไม่เลื่อนช่องชื่อ "
                         "และไม่ย่อหรือขยายกรอบให้พอดีกับจำนวนชื่อ"
                         "\n"
                         # ข้อ (1) ถ้อยคำเจ้าของ ตรงตัว (20 ต.ค. 2569) — ครอบทั้งรายชื่อและลำดับ
                         "(1) รายชื่อคณะกรรมการแต่ละชุด และการเรียงตามลำดับตามเอกสารที่ได้รับอนุมัติ "
                         "เริ่มจากช่องด้านขวาบนสุดไล่ลงมาด้านล่าง"
                         "\n"
                         "(2) หากมีคณะกรรมการเกิน 5 ท่าน ให้ใส่รายชื่อที่ 6 เป็นต้นไป"
                         "ในช่องด้านซ้ายของรายชื่อที่ 5 (ช่องเหนือรายชื่อคณบดีบัณฑิตวิทยาลัย) "
                         "แล้วไล่ขึ้นไปหาชื่อนักศึกษา"
                         "\n"
                         "(3) ช่องที่ไม่มีรายชื่อ ให้คงข้อความตัวอย่างไว้และเปลี่ยนสีตัวอักษรเป็นสีขาว "
                         "ห้ามลบช่องหรือกรอบตาราง"
                         "\n"
                         "(4) ลบตำแหน่งที่อยู่ใต้คุณวุฒิของกรรมการออกทั้งหมด (เช่น Co-advisor, Member)"
                         "\n"
                         "(5) คุณวุฒิของอาจารย์ทุกท่าน ให้ใส่เพียงคุณวุฒิสูงสุด เพียง 1 รายการเท่านั้น"
                         "\n\n"
                         "ดูตัวอย่างการเรียงรายชื่อได้ในคู่มือการจัดรูปแบบ (เรียงตามลูกศรสีเหลือง)"
                         "\n"
                         "https://bit.ly/4jXLjgw"),
                "text_en": ("Signature pages (Pages i - ii)"
                            "\n"
                            "Please restructure the signature pages to match the template, "
                            "using the designated font and table frames. Do not move the "
                            "name slots or resize the frames to fit the number of names."
                            "\n"
                            "(1) The names in each committee and their order as in the approved "
                            "document, starting from the top-right slot and going down"
                            "\n"
                            "(2) If there are more than 5 committee members, place the 6th "
                            "name onward in the slot to the left of the 5th name (the slot "
                            "above the Dean of the Faculty of Graduate Studies), then continue "
                            "upward toward the student's name."
                            "\n"
                            "(3) For any slots without a name, keep the sample text and change "
                            "its font color to white. Do not delete the slots or table frames."
                            "\n"
                            "(4) Remove all positions listed below the committee members' "
                            "degrees (for example, Co-advisor, Member)."
                            "\n"
                            "(5) For every committee member, list only their highest degree "
                            "(one entry only)."
                            "\n\n"
                            "See the formatting manual for how to order the names "
                            "(follow the yellow arrows)."
                            "\n"
                            "https://bit.ly/4jXLjgw"),
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

# ถ้อยคำไทยของเจ้าหน้าที่บอกเลขหน้าลงนามไว้ทั้งสองแบบ ("i - ii หรือ ก - ข" และ
# "ก - ข หรือ i - ii") เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้บอกเลขหน้าตามภาษาของเล่มแบบเดียวกับ
# กฎเลขหน้าหน้าลงนาม — เล่มหลักสูตรไทยได้ "ก - ข" เล่มอังกฤษได้ "i - ii"
# แก้เฉพาะท่อนเลขหน้า คำอื่นคงต้นฉบับทุกตัวอักษร ไม่รู้ภาษาเล่มคงถ้อยคำเดิมทั้งสองแบบ
# ฝั่งอังกฤษเขียน "i - ii" แบบเดียวอยู่แล้ว และห้ามมีอักษรไทย จึงไม่แตะ
_SIGNATURE_PAGES_BOTH = re.compile(r"i - ii หรือ ก - ข|ก - ข หรือ i - ii")
_SIGNATURE_PAGES_BY_STYLE = {"thai": "ก - ข", "roman": "i - ii"}


def staff_text_for_book(text, label_style):
    """ถ้อยคำไทยของเจ้าหน้าที่ที่บอกเลขหน้าลงนามเฉพาะแบบของภาษาเล่ม"""
    pages = _SIGNATURE_PAGES_BY_STYLE.get(label_style)
    return _SIGNATURE_PAGES_BOTH.sub(pages, text) if pages and text else text


# ---------- โครงสร้างหน้าลงนาม: ข้อความตามข้อที่เจ้าหน้าที่ติ้กไม่ผ่าน (อัปเดต 1.4) ----------
# เจ้าของสั่ง (19 ต.ค. 2569): หน้าต่างตรวจหน้าลงนามก่อนตรวจเล่มมีเช็กลิสต์ (1)-(5) ข้อความที่แจ้ง
# นักศึกษามาจาก "ข้อที่ติ้กไม่ผ่านเท่านั้น" เรียงเลขใหม่ (1), (2), ... ประโยคนำ บรรทัดคู่มือ และลิงก์คงไว้
# ถ้อยคำทั้งหมดยังมาจาก text/text_en ฉบับเต็มของ SIGNATURE_LAYOUT_WRONG ที่เดียว ไม่แยกเก็บซ้ำ
SIGNATURE_LAYOUT_WRONG = "SIGNATURE_LAYOUT_WRONG"
SIGNATURE_LAYOUT_OK = "SIGNATURE_LAYOUT_OK"
SIGNATURE_ITEM_PREFIX = "SIGNATURE_LAYOUT_ITEM_"
_LAYOUT_ITEM_LINE = re.compile(r"^\((\d+)\)\s*")


def signature_layout_items(text):
    """ข้อเช็กลิสต์ (เลข, ข้อความ) จากถ้อยคำฉบับเต็ม — ใช้แสดงในหน้าต่างและหน้ารายงาน"""
    out = []
    for line in (text or "").split("\n"):
        match = _LAYOUT_ITEM_LINE.match(line.strip())
        if match:
            out.append((int(match.group(1)), line.strip()[match.end():]))
    return out


def signature_failed_items(staff, prefix=SIGNATURE_ITEM_PREFIX):
    """เลขข้อเช็กลิสต์ที่ติ้กไม่ผ่าน จาก id "<prefix><n>" ในชุด staff (ค่าจากหน้าเว็บ เชื่อไม่ได้)"""
    found = set()
    for key in staff or ():
        key = str(key)
        if key.startswith(prefix) and key[len(prefix):].isdigit():
            found.add(int(key[len(prefix):]))
    return found


def signature_layout_text(text, failed_items):
    """ถ้อยคำโครงสร้างหน้าลงนามเฉพาะข้อที่ไม่ผ่าน เรียงเลขใหม่ — ไม่ระบุข้อเลย = ทุกข้อ

    "ไม่ระบุข้อ" คือหน้าเก่าหรือคำขอเก่าที่ส่งมาแค่ SIGNATURE_LAYOUT_WRONG ต้องได้ถ้อยคำเต็มแบบเดิม
    """
    numbers = [n for n, _line in signature_layout_items(text)]
    wanted = {n for n in (failed_items or ()) if n in numbers} or set(numbers)
    out, k = [], 0
    for line in (text or "").split("\n"):
        match = _LAYOUT_ITEM_LINE.match(line.strip())
        if not match:
            out.append(line)
        elif int(match.group(1)) in wanted:
            k += 1
            out.append(f"({k}) " + line.strip()[match.end():])
    return "\n".join(out)


SIGNATURE_NAMES_WRONG = "SIGNATURE_NAMES_WRONG"
SIGNATURE_NAMES_OK = "SIGNATURE_NAMES_OK"
SIGNATURE_NAMES_ITEM_PREFIX = "SIGNATURE_NAMES_ITEM_"
# ข้อจำนวนกรรมการที่ระบบนับเองแล้วไม่ตรง (_report_committee_count) ขึ้นต้นด้วยถ้อยคำเหล่านี้
_COMMITTEE_COUNT_FOUND = ("รายชื่อไม่ครบตามที่ได้รับอนุมัติใน", "รายชื่อเกินจากที่ได้รับอนุมัติใน")
_SIGNATURE_NAME_LEAD = {
    frozenset({"wrong"}): "รายชื่อไม่ถูกต้อง",
    frozenset({"short"}): "รายชื่อไม่ครบ",
    frozenset({"wrong", "short"}): "รายชื่อไม่ถูกต้องและไม่ครบ",
}


def signature_name_items(staff):
    """เลขข้อรายชื่อที่ติ๊กว่าผิด จาก id "SIGNATURE_NAMES_ITEM_<n>" — เลขที่ไม่รู้จักทิ้งเงียบ ๆ"""
    known = {n for n, _kind, _problem in SIGNATURE_NAME_PROBLEMS}
    return signature_failed_items(staff, SIGNATURE_NAMES_ITEM_PREFIX) & known


def committee_names_by_kind(approved):
    """รายชื่อกรรมการตามข้อมูลอนุมัติ {"advisory": [...], "exam": [...]} ในรูปที่แสดงในรายงาน

    เก็บไว้ใน context ของผลตรวจ ข้อความสรุปที่สร้างใหม่ทุกครั้งที่เจ้าหน้าที่กด จึงบอกได้ว่า
    ควรมีชื่ออะไรบ้าง โดยไม่ต้องย้อนไปหาข้อมูลอนุมัติของงาน
    """
    committees = (approved or {}).get("committees") or {}
    return {kind: [_display_committee_name(name) for name in _committee_names(committees.get(kind) or [])
                   if name]
            for kind in SIGNATURE_PAGE_COMMITTEE}


def signature_name_issues(report, staff):
    """จุดที่ต้องแก้จากข้อรายชื่อที่เจ้าหน้าที่ติ๊กว่าผิด — หน้าลงนามละไม่เกินหนึ่งข้อ

    ตำแหน่ง / "รายชื่อไม่ถูกต้อง|ไม่ครบตามที่ได้รับอนุมัติใน บฑ.x" / "กรุณาตรวจสอบรายชื่อตามเอกสาร บฑ.x"
    ไม่ยกรายชื่อตาม บฑ. มาในข้อความ (เจ้าของยืนยัน 20 ต.ค. 2569) — ข้อที่ระบบนับจำนวนเองยังยกรายชื่อเหมือนเดิม
    ติ๊กทั้งสองข้อของหน้าเดียวกัน = ข้อเดียวว่า "ไม่ถูกต้องและไม่ครบ" (แก้ที่รายชื่อชุดเดียวกัน)

    ข้อ "ไม่ครบ" ของหน้าที่ระบบฟ้องจำนวนไม่ตรงไว้แล้ว ไม่แจ้งซ้ำ — นักศึกษาจะได้สองข้อที่พูดเรื่อง
    เดียวกัน ข้อของระบบบอกจำนวนที่นับได้ด้วย จึงเก็บข้อนั้นไว้
    """
    ticked = signature_name_items(staff)
    if not ticked:
        return []
    report = report or {}
    context = report.get("context") or {}
    refs = context.get("signature_refs") or []
    reds = (report.get("issues_by_zone") or {}).get("RED") or []
    out = []
    for k, kind in enumerate(SIGNATURE_PAGE_COMMITTEE, start=1):
        page = f"หน้าลงนาม {k}"
        problems = {problem for n, which, problem in SIGNATURE_NAME_PROBLEMS
                    if which == kind and n in ticked}
        counted = any(
            (str(issue.get("location") or "") == page
             or str(issue.get("location") or "").startswith(page + " ("))
            and str(issue.get("found") or "").startswith(_COMMITTEE_COUNT_FOUND)
            for issue in reds)
        if counted:
            problems.discard("short")
        if not problems:
            continue
        form = committee_source_form(kind)
        expected = f"กรุณาตรวจสอบรายชื่อตามเอกสาร {form}"
        ref = refs[k - 1] if len(refs) >= k and refs[k - 1] else ""
        out.append({
            "part": "front_matter",
            "location": f"{page} ({ref})" if ref else page,
            "found": f"{_SIGNATURE_NAME_LEAD[frozenset(problems)]}ตามที่ได้รับอนุมัติใน {form}",
            "expected": expected,
            "fix": "",
            "system_note": False,
            "staff_choice": SIGNATURE_NAMES_WRONG,
            **rule_reference("FRONT.COMMITTEE"),
        })
    return out


# ---------- ผลตรวจ Turnitin (อัปเดต 1.4) ----------
# เจ้าของสั่ง (19 ต.ค. 2569): แนบไฟล์ Turnitin ทุกเล่ม ดูที่แผ่น Overall Similarity ต้องไม่เกิน 25%
# เกิน = สีแดงเข้าข้อความสรุป · อ่านตัวเลขไม่ได้ = สีส้มให้เจ้าหน้าที่เปิดไฟล์ดูเอง
# รายงาน Turnitin รุ่นปัจจุบันพิมพ์ "28% Overall Similarity" ไว้หน้า 2 (ตัวอย่างจริง 2 ไฟล์ ต.ค. 2569)
TURNITIN_LIMIT = 25
_TURNITIN_OVERALL = re.compile(r"(\d{1,3})\s*%\s*Overall\s+Similarity", re.I)


def turnitin_similarity(pdf_path, max_pages=6):
    """ค่า Overall Similarity (เปอร์เซ็นต์ เต็มจำนวน) จากไฟล์รายงาน Turnitin — None ถ้าหาไม่เจอ/เปิดไม่ได้"""
    try:
        with pdfplumber.open(pdf_path) as pdf:
            for pg in pdf.pages[:max_pages]:
                match = _TURNITIN_OVERALL.search(pg.extract_text() or "")
                if match and int(match.group(1)) <= 100:
                    return int(match.group(1))
    except Exception:
        return None
    return None


def _report_turnitin(rep, turnitin):
    """ข้อของไฟล์ Turnitin ในผลตรวจ — turnitin = {"similarity": int|None} หรือ None (ไม่ได้ส่งมา = ไม่ตรวจ)

    อัปเดต 1.7 (เจ้าของยืนยัน 27 ต.ค. 2569): ระบบอ่านค่าไม่ได้ เจ้าหน้าที่ตัดสินที่ขั้นที่ 1 (apply_turnitin_answer)
      by_staff=True    = ค่าที่เจ้าหน้าที่กรอกเอง ตัดสินเหมือนค่าที่ระบบอ่านได้ทุกอย่าง และรายงานบอกว่ากรอกเอง
      not_turnitin=True = ไฟล์ที่แนบไม่ใช่ผลตรวจ Turnitin = แดง
    """
    if not turnitin:
        return
    value = turnitin.get("similarity")
    # ถ้อยคำที่เจ้าหน้าที่รีวิวแล้ว (19 ต.ค. 2569) เกลาจากข้อความที่เคยใช้ "Please re-check the Turnitin report. ..."
    # เนื้อหาหลัก = บทคัดย่อ และบทที่ 1 ถึงบทสุดท้าย · ไม่บอกช่องทางส่ง (ส่งช่องทางเดิม) แต่บอกว่าติดต่อหอสมุดฯ
    # ขั้นตอน (1) (2) และบรรทัดหอสมุดฯ อยู่ใน notice ให้เป็นบรรทัดของตัวเองในข้อความสรุป
    main_content = "เนื้อหาหลัก (บทคัดย่อ และบทที่ 1 ถึงบทสุดท้าย)"
    if turnitin.get("not_turnitin"):
        # ต่อยอดจากถ้อยคำที่เจ้าหน้าที่รีวิวแล้ว (19 ต.ค. 2569) — ถ้อยคำเจ้าของ 27 ต.ค. 2569
        rep.add("RED", "-", "ไฟล์ Turnitin", "ไฟล์ที่แนบมาไม่ใช่ผลตรวจ Turnitin",
                "กรุณาแนบไฟล์ผลตรวจ Turnitin ที่ถูกต้อง ดังนี้",
                "แนบไฟล์ผลตรวจ Turnitin ที่มีทั้งหน้าปกและหน้า Overall Similarity",
                "TURNITIN.SIMILARITY",
                notice=("(1) ไฟล์ผลตรวจ Turnitin ต้องมีทั้งหน้าปก (Cover page) และหน้า Overall Similarity\n"
                        f"(2) ตรวจ{main_content} โดยค่า Overall Similarity ต้องไม่เกิน {TURNITIN_LIMIT}%"))
        return
    if value is not None and turnitin.get("by_staff"):
        rep.add_info("-", "ไฟล์ Turnitin",
                     f"ค่า Overall Similarity {value}% เจ้าหน้าที่กรอกเองที่ขั้นที่ 1 (ระบบอ่านค่าจากไฟล์ไม่ได้)")
    if value is None:
        rep.add("ORANGE", "-", "ไฟล์ Turnitin",
                "ระบบอ่านค่า Overall Similarity จากไฟล์ Turnitin ไม่ได้",
                "ไฟล์ผลตรวจ Turnitin ที่ส่งต้องมีทั้งหน้าปก (Cover page) และหน้า Overall Similarity "
                f"และค่า Overall Similarity ของ{main_content} ต้องไม่เกิน {TURNITIN_LIMIT}%",
                "เปิดไฟล์ Turnitin ดูค่า Overall Similarity ด้วยตา ถ้าไม่เกินเกณฑ์ให้กดผ่าน",
                "TURNITIN.SIMILARITY")
    elif value > TURNITIN_LIMIT:
        rep.add("RED", "-", "ไฟล์ Turnitin",
                f"ผลตรวจ Turnitin ที่แนบมามีค่า Overall Similarity {value}% "
                f"ซึ่งเกินเกณฑ์ที่กำหนด (ต้องไม่เกิน {TURNITIN_LIMIT}%)",
                "กรุณาดำเนินการดังนี้",
                f"ปรับเนื้อหาให้ค่า Overall Similarity ไม่เกิน {TURNITIN_LIMIT}% แล้วแนบผลตรวจ Turnitin ใหม่",
                "TURNITIN.SIMILARITY",
                notice=(f"(1) ปรับแก้เนื้อหาส่วนที่ซ้ำ แล้วตรวจ Turnitin ใหม่ ให้ค่า Overall Similarity "
                        f"ของ{main_content} ไม่เกิน {TURNITIN_LIMIT}%\n"
                        "(2) แนบไฟล์ผลตรวจ Turnitin ฉบับใหม่ โดยไฟล์ต้องมีทั้งหน้าปก (Cover page) "
                        "และหน้า Overall Similarity\n"
                        "กรณีที่ไม่สามารถปรับแก้ไขร้อยละการคัดลอกได้ กรุณาติดต่อหอสมุดฯ "
                        "เพื่อขอยกเว้น (Exclude) คำที่เป็นคำศัพท์เฉพาะได้"))


def turnitin_answer(raw):
    """คำตอบเรื่อง Turnitin จากหน้าขั้นที่ 1 -> {"choice": "value", "similarity": n} / {"choice": "not_turnitin"} / None

    None = ยังไม่เลือก (คงข้อส้มเดิม) · ValueError = ค่าที่ส่งมาใช้ไม่ได้ (ค่า % ต้องเป็นจำนวนเต็ม 0–100)
    """
    if raw in (None, "", {}):
        return None
    if not isinstance(raw, dict):
        raise ValueError("turnitin")
    choice = raw.get("choice")
    if choice == "not_turnitin":
        return {"choice": "not_turnitin"}
    if choice == "value":
        value = raw.get("similarity")
        if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
            raise ValueError("similarity")
        return {"choice": "value", "similarity": value}
    raise ValueError("choice")


def apply_turnitin_answer(report, answer):
    """ผลตรวจหลังเจ้าหน้าที่ตัดสินไฟล์ Turnitin ที่ระบบอ่านค่าไม่ได้ ที่ขั้นที่ 1 (อัปเดต 1.7 เจ้าของยืนยัน 27 ต.ค. 2569)

    ใช้เฉพาะเมื่อผลตรวจมีข้อส้ม "ระบบอ่านค่า Overall Similarity ... ไม่ได้" — ระบบอ่านค่าได้ = คืนผลเดิม
    เอาข้อส้มนั้นออก แล้วใส่ผลตามที่เจ้าหน้าที่เลือกด้วยกฎเดียวกับตอนระบบอ่านได้ (_report_turnitin) จากนั้น
    สร้างผลตรวจใหม่ทั้งชุดผ่าน check_result (สี ตัวเลข ข้อความสรุป และชีทจึงตามไปทางเดียวกัน)
    """
    choice = (answer or {}).get("choice")
    zones = (report or {}).get("issues_by_zone") or {}
    unread = [issue for issue in zones.get("ORANGE") or []
              if issue.get("rule_id") == "TURNITIN.SIMILARITY"]
    if not unread or choice not in ("value", "not_turnitin"):
        return report
    rep = Report()
    rep.zones = {zone: [dict(issue) for issue in zones.get(zone) or []
                        if not (zone == "ORANGE" and issue in unread)]
                 for zone in ("RED", "ORANGE", "YELLOW")}
    rep.info = [dict(item) for item in report.get("info") or []]
    rep.human_checklist = [dict(item) for item in report.get("human_checklist") or []]
    rep.verification = [{**group, "checks": [dict(c) for c in group.get("checks") or []]}
                        for group in report.get("verification") or []]
    if choice == "value":
        _report_turnitin(rep, {"similarity": answer["similarity"], "by_staff": True})
    else:
        _report_turnitin(rep, {"similarity": None, "not_turnitin": True})
    return check_result(rep, report.get("context"), report.get("not_checked") or NOT_CHECKED)


# ---------- ค่าปรับส่งเล่มล่าช้า (อัปเดต 1.4) ----------
# เจ้าของสั่ง (19 ต.ค. 2569): วันครบกำหนด = วันที่สอบผ่าน + 20 วัน (วันปฏิทิน รวมวันหยุด)
# ส่งเข้าระบบหลังวันครบกำหนด = มีค่าปรับ · ส่งตรงวันครบกำหนดพอดี = ไม่มี
LATE_FEE_GRACE_DAYS = 20
_MONTHS_EN = {name.lower(): i for i, name in enumerate(
    ("January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"), start=1)}
_MONTHS_TH = {name: i for i, name in enumerate(
    ("มกราคม", "กุมภาพันธ์", "มีนาคม", "เมษายน", "พฤษภาคม", "มิถุนายน", "กรกฎาคม",
     "สิงหาคม", "กันยายน", "ตุลาคม", "พฤศจิกายน", "ธันวาคม"), start=1)}


_FEE_ERA = re.compile(r"พ\.?\s*ศ\.?|ค\.?\s*ศ\.?|\bB\.\s*E\.|\bA\.\s*D\.")


def _month_number(word):
    word = (word or "").strip().rstrip(".,")
    if word in _MONTHS_TH:
        return _MONTHS_TH[word]
    word = word.lower()
    if word in _MONTHS_EN:
        return _MONTHS_EN[word]
    return next((i for name, i in _MONTHS_EN.items() if len(word) >= 3 and name.startswith(word)), None)


def parse_exam_date(text):
    """วันที่สอบผ่านจากช่องในแบบฟอร์ม → datetime.date หรือ None ถ้าอ่านไม่ได้

    รับ "30 November 2023" · "30 November, 2023" · "November 30, 2023" · "11 พฤษภาคม 2569"
    (พ.ศ. แปลงเป็น ค.ศ.) · มีคำ "พ.ศ." / "วันที่" นำหน้าได้
    """
    # ไม่ใช้ _ERA_PREFIX — ตัวนั้นไม่สนตัวพิมพ์และไม่บังคับจุด "be" ใน "November" จึงถูกตัดทิ้ง
    value = _FEE_ERA.sub(" ", re.sub(r"^\s*(?:วันที่|on)\s*", "", str(text or ""), flags=re.I))
    value = re.sub(r"\s+", " ", value.replace(",", " ")).strip()
    day_first = re.match(r"^(\d{1,2}) (\S+) (\d{4})$", value)
    month_first = re.match(r"^(\S+) (\d{1,2}) (\d{4})$", value)
    if day_first:
        day, month, year = int(day_first.group(1)), _month_number(day_first.group(2)), int(day_first.group(3))
    elif month_first:
        day, month, year = int(month_first.group(2)), _month_number(month_first.group(1)), int(month_first.group(3))
    else:
        return None
    if year > 2400:
        year -= 543
    try:
        return datetime.date(year, month, day) if month else None
    except ValueError:
        return None


def late_fee(exam_date_text, submitted_iso):
    """คิดค่าปรับจากวันที่สอบผ่านกับวันที่ส่งเข้าระบบ (yyyy-mm-dd จากช่องวันที่ในแบบฟอร์ม)

    คืน {"exam", "due", "submitted": date, "late": bool, "days_late": int} หรือ None ถ้าอ่านวันใดวันหนึ่งไม่ได้
    (None = ระบบคิดไม่ได้ ให้เจ้าหน้าที่เลือกค่าปรับเองแบบเดิม)
    """
    # ช่องวันที่สอบอาจกรอกสองภาษาคั่นด้วย "/" ("29 June 2026 / 29 มิถุนายน 2569") ใช้วันแรกที่อ่านได้
    exam = next((d for d in map(parse_exam_date, str(exam_date_text or "").split("/")) if d), None)
    try:
        submitted = datetime.date.fromisoformat(str(submitted_iso or "").strip())
    except ValueError:
        submitted = None
    if not exam or not submitted:
        return None
    due = exam + datetime.timedelta(days=LATE_FEE_GRACE_DAYS)
    days_late = max(0, (submitted - due).days)
    return {"exam": exam, "due": due, "submitted": submitted,
            "late": days_late > 0, "days_late": days_late}


def staff_checks_for_book(label_style):
    """ทะเบียน STAFF_CHECKS ที่ถ้อยคำไทยบอกเลขหน้าตามภาษาเล่ม (สำเนา ไม่แก้ทะเบียนกลาง)

    หน้ารายงานจับคู่คำแปลอังกฤษด้วยตัวข้อความไทยจากทะเบียนที่ได้รับ จึงต้องได้ถ้อยคำ
    ชุดเดียวกับที่ข้อความสรุปพิมพ์ ไม่งั้นบรรทัดที่เปลี่ยนเลขหน้าแล้วจะหาคู่แปลไม่เจอ
    """
    return [dict(check, choices=[dict(choice, text=staff_text_for_book(choice["text"], label_style))
                                 for choice in check["choices"]])
            for check in STAFF_CHECKS]


def report_label_style(report):
    """ชนิดเลขหน้าส่วนนำของเล่มที่ run_check เก็บไว้ ("thai" / "roman" / None)"""
    return ((report or {}).get("context") or {}).get("front_label_style")


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


def staff_issue(check, choice, label_style=None, staff=None):
    """แปลงตัวเลือกที่เจ้าหน้าที่กด ให้อยู่ในรูปเดียวกับข้ออื่นในข้อความสรุป

    summary_text = ถ้อยคำที่เจ้าหน้าที่กำหนดมาทั้งย่อหน้า ให้พิมพ์ตรงตัว ไม่ผ่านการ
    ประกอบประโยคแบบ "ตำแหน่ง / ที่พบ / ต้องแก้เป็น" เพราะถ้อยคำชุดนี้บอกตำแหน่งไว้
    ในประโยคแรกอยู่แล้ว และเป็นคำสั่งที่เจ้าหน้าที่เขียนมาเอง
    ยกเว้นท่อนเลขหน้าลงนามที่บอกตามภาษาเล่ม (ดู staff_text_for_book)
    """
    return {
        "part": "front_matter",
        "location": check["section"],
        "found": "",
        "expected": "",
        "fix": "",
        "system_note": False,
        "summary_text": staff_text_for_book(
            signature_layout_text(choice["text"], signature_failed_items(staff))
            if choice["id"] == SIGNATURE_LAYOUT_WRONG else choice["text"], label_style),
        "staff_choice": choice["id"],
        **rule_reference(check["rule_id"]),
    }


def staff_section_issues(report, staff=None):
    """จุดที่ต้องแก้จากหัวข้อที่เจ้าหน้าที่กดเอง (placement = section)

    หัวข้อส่วนใหญ่ได้หนึ่งข้อต่อหนึ่งคำตอบ ยกเว้นรายชื่อกรรมการ (อัปเดต 1.5) ที่ได้หน้าลงนามละข้อ
    ข้อความสรุปและตัวเลขกล่อง "ต้องแก้" ต้องนับจากรายการชุดนี้ชุดเดียวกัน (ดู zone_counts)
    """
    out = []
    for check, choice in staff_choices(staff, "section"):
        if choice["id"] == SIGNATURE_NAMES_WRONG:
            out += signature_name_issues(report, staff)
        else:
            out.append(staff_issue(check, choice, report_label_style(report), staff))
    return out


def issues_to_fix(report, failed=None, passed=None, staff=None):
    """รายการในกลุ่ม "กรุณาแก้ไข" ของข้อความสรุป — นับเท่ากับกล่อง "ต้องแก้" บนหัวรายงานเสมอ

    - สีแดง: เข้าเสมอ
    - สีส้ม / สีเหลือง: เข้าเมื่อเจ้าหน้าที่กด "ไม่ผ่าน" เท่านั้น (กลายเป็นสีแดงทันที)
    - สีส้มที่ยังไม่กด อยู่กลุ่ม "รอยืนยัน" แยกต่างหาก (ดู issues_pending) — เจ้าหน้าที่สั่ง
      (ก.ย. 2569) ของเดิมสีส้มที่ยังไม่กดปนอยู่กลุ่มนี้ หัวรายงานจึงเขียน "ต้องแก้ 0" คู่กับ
      ข้อความสรุปที่เขียนว่า "กรุณาแก้ไข 2 จุด" ทั้งที่เจ้าหน้าที่ยังไม่ได้ยืนยันสักข้อ

    ข้อที่ตั้ง system_note=True ไม่เข้าสรุป "โดยปริยาย" เพราะเป็นปัญหาฝั่งเจ้าหน้าที่
    (เช่น ยังไม่ได้กรอกข้อมูลอนุมัติ หรือเลือกไฟล์ eThesis คนละคนกับเล่ม) นักศึกษาแก้
    เล่มยังไงข้อนี้ก็ไม่หาย แต่ถ้าเจ้าหน้าที่กด "ไม่ผ่าน" ต้องเข้าสรุปเหมือนข้อสีส้มอื่น
    — เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่า "สีส้มกับสีเหลืองถ้ากด ต้องให้เป็นสีแดง = แก้ไข"
    ของเดิมกันข้อนี้ออกทุกกรณี ปุ่ม ✗ บนการ์ดสองใบนี้จึงกดแล้วไม่มีอะไรเกิดขึ้น

    failed/passed เป็นชุดคีย์รูปแบบ "ZONE:index" เช่น {"ORANGE:0", "YELLOW:2"}
    staff เป็นชุด id ของ "ตัวเลือก" ใน STAFF_CHECKS ที่เจ้าหน้าที่กด (เช่น
    {"SIGNATURE_LAYOUT_WRONG"}) — เรื่องพวกนี้ระบบตรวจเองไม่ได้ จึงไม่มีใน
    issues_by_zone ตัวเลือกที่ placement เป็น closing ไม่ใช่จุดผิด จึงไม่เข้ารายการนี้
    """
    failed = set(failed or ())
    passed = set(passed or ())
    items = [it for it in (report["issues_by_zone"].get("RED") or [])
             if not it.get("system_note")]
    # จุดที่เจ้าหน้าที่กด "ผิด" เองมาก่อนข้ออื่น เพราะเป็นคำตัดสินของคน ไม่ใช่ของระบบ
    # (ลำดับในข้อความสรุปยังจัดตามส่วนของเล่มอยู่ดี ตรงนี้แค่กันไม่ให้ตกท้ายกลุ่ม)
    items += staff_section_issues(report, staff)
    for index, issue in enumerate(report["issues_by_zone"].get("ORANGE") or []):
        if f"ORANGE:{index}" in failed:
            items.append(_as_failed(issue))
    for index, issue in enumerate(report["issues_by_zone"].get("YELLOW") or []):
        if f"YELLOW:{index}" in failed:
            items.append(_as_failed(issue))
    return items


def _as_failed(issue):
    """ข้อส้ม/เหลืองที่เจ้าหน้าที่กด "ไม่ผ่าน" — ข้อที่มีถ้อยคำเมื่อไม่ผ่าน (fail_text) ใช้ถ้อยคำนั้นแทนบรรทัดที่ควรเป็น

    อัปเดต 1.7 (เจ้าของยืนยัน 27 ต.ค. 2569): เดิมกดไม่ผ่านแล้วข้อความสรุปใช้ถ้อยคำตอนรอยืนยันทั้งหมด ข้อหน้าว่าง
    กับข้อสารบัญระบุเลขหน้าเป็นช่วงต้องบอกนักศึกษาว่าต้องทำอะไร ("ลบหน้าว่างที่แผ่นที่ … ออก")
    """
    text = issue.get("fail_text")
    if not text:
        return issue
    return {**issue, "expected": text, "fix": text, "notice": "", "diff_tail": "", "fix_steps": []}


def issues_pending(report, failed=None, passed=None):
    """สีส้มที่เจ้าหน้าที่ยังไม่ได้กด — กลุ่ม "รอยืนยัน" ของข้อความสรุป

    นับเท่ากับกล่อง "รอยืนยัน" บนหัวรายงาน กด ✗ แล้วย้ายไปกลุ่ม "กรุณาแก้ไข" กด ✓ แล้วหายไป
    กดซ้ำปุ่มเดิมเพื่อยกเลิกได้ ข้อจะกลับมาอยู่กลุ่มนี้ (สลับไปมาได้ตามที่เจ้าหน้าที่สั่ง)
    """
    failed, passed = set(failed or ()), set(passed or ())
    return [issue for index, issue in enumerate(report["issues_by_zone"].get("ORANGE") or [])
            if f"ORANGE:{index}" not in failed and f"ORANGE:{index}" not in passed
            and not issue.get("system_note")]


def _corrected_value(issue):
    """ค่าที่ควรเป็น (ข้อความในเครื่องหมายคำพูดท้าย expected/fix) ถ้ามี

    ใช้ทั้งจัดประโยคให้กระชับ และเป็นกุญแจรวมรายการซ้ำ — ตำแหน่งเดียวกันและค่าที่
    ต้องแก้เหมือนกัน ถือเป็น "จุดเดียว" (การครอสเช็ค 3 ทางอาจรายงานชื่อบทเดียวกัน
    ทั้งตอนเทียบสารบัญและเทียบประกาศ ซึ่งสำหรับนักศึกษาคือการแก้จุดเดียว)
    """
    # "เล่มผิดภาษา" ไม่มีค่าเดี่ยวให้พิมพ์แก้ในเล่ม สิ่งที่ต้องทำคือจัดทำเล่มใหม่ทั้งเล่ม
    # ถ้าดึงค่าท้ายประโยคไป สรุปจะเหลือแค่ 'ต้องแก้เป็น "ภาษาอังกฤษ"' ซึ่งกลืนถ้อยคำ
    # ที่เจ้าหน้าที่กำหนดไว้ทั้งประโยคหายไป — ปล่อยให้ตกไปใช้ประโยค expected เต็ม ๆ
    #
    # เลขหน้าหน้าลงนามพิมพ์ประโยคเต็ม 'ต้องเป็นเลขหน้า "ก"' ตามแบบที่เจ้าหน้าที่เลือก (ก.ย. 2569
    # "เอาตามข้อ 2 ก็พอ") — ถ้าดึงค่าไป จะเหลือ 'ต้องแก้เป็น "ก"' ซึ่งไม่บอกว่าเป็นเลขหน้า
    #
    # ประเภทเล่มผิดต้องแก้หลายหน้า ค่าท้ายประโยคคือข้อความบนหน้าปกอย่างเดียว ถ้าดึงไป
    # สรุปจะบอกแค่ให้แก้ปก แล้วหน้าลงนามกับบทคัดย่อที่เขียนผิดอยู่ด้วยหายไปจากข้อความ
    if issue.get("rule_id") in ("FORM.BOOK_LANGUAGE", "PAGE.SIGNATURE_LABEL", "FORM.DOC_TYPE"):
        return ""
    # บรรทัดที่เขียนถึงเจ้าหน้าที่ไม่ใช่ค่าที่ต้องแก้ ข้ามไปใช้ช่องถัดไป (ดู is_staff_only_line)
    raw = next((text for text in (summary_tidy(issue.get("expected")),
                                  summary_tidy(issue.get("fix")))
                if text and not is_staff_only_line(text)), "")
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


def _found_and_steps(issue):
    """ข้อความ "ที่พบ" สำหรับข้อความสรุป + วิธีแก้เป็นข้อ ๆ (ถ้าข้อนั้นเทียบข้อความแล้วชี้จุดต่างได้)

    ของเดิมถอดแค่ 'ต้องเป็น "..."' ออก เหลือ 'ต่างที่ "A"' ซึ่งบอกว่าต่างตรงไหนแต่ไม่บอกว่า
    ต้องแก้ยังไง นักศึกษาต้องเอาข้อความยาวสองก้อนมาเทียบกันเอง (เจ้าหน้าที่สั่ง 6 ต.ค. 2569
    ว่าควรบอกด้วย รวมถึงกรณีเว้นวรรค) จึงถอดท่อนจุดต่างทั้งท่อนออกจากบรรทัดที่พบ แล้วเขียนเป็น
    คำสั่งทีละบรรทัดแทน เช่น 'ให้ลบ "การ" ที่อยู่หลัง "เทคโนโลยี" ออก'

    ข้อที่ไม่ได้มาจากการเทียบข้อความ (ไม่มี diff_tail/fix_steps) ใช้ของเดิมทุกอย่าง
    """
    found = summary_tidy(issue.get("found"))
    tail = summary_tidy(issue.get("diff_tail"))
    steps = [summary_tidy(step) for step in (issue.get("fix_steps") or ())]
    if tail and steps and found.endswith(tail):
        return found[:-len(tail)].rstrip(), [step for step in steps if step]
    return _prose_found(issue.get("found")), []


def _prose_location(location):
    """ทำตำแหน่งให้เป็นสำนวนคน ไม่ให้มีสัญลักษณ์ตกค้าง (↔ → ใช้คำเชื่อมแทน)"""
    loc = summary_tidy(location)
    loc = loc.replace("↔", "เทียบกับ").replace("→", "ถึง")
    return re.sub(r"\s{2,}", " ", loc).strip()


SUMMARY_INDENT = "   "


# บรรทัดที่เขียนถึงเจ้าหน้าที่ ไม่ใช่ถึงนักศึกษา — อยู่บนการ์ดในรายงานได้ แต่ห้ามติดไปกับข้อความ
# สรุปที่ส่งนักศึกษา เจ้าหน้าที่สั่ง (ก.ย. 2569): "ไม่ต้องเอาข้อความที่เขียนถึงเจ้าหน้าที่ไปแจ้ง
# นักศึกษา" รู้จักจากเนื้อความ ไม่ใช่รายการประโยค กฎใหม่ที่เขียนแบบเดียวกันจึงถูกกันด้วย
#   เอ่ยถึงเจ้าหน้าที่          "เจ้าหน้าที่ตรวจสอบว่าเป็นหน้าภาพ/หน้าว่าง ..."
#                             "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้"
#   บอกว่าเป็นข้อสังเกต/ผ่านได้   "หน้าลักษณะนี้ ... เป็นข้อสังเกตและผ่านได้" — ขัดกับการถูกสั่งให้แก้
#   สั่งให้ตรวจเอง              "ตรวจว่าเป็นหน้าภาพหรือหน้าว่าง..." / "ตรวจด้วยตา"
# ต้องกันทั้งช่อง "ควรเป็น" และช่อง "ต้องแก้" ที่เป็นตัวสำรอง ไม่งั้นตัดช่องแรกแล้วช่องหลังหลุดมาแทน
_STAFF_ONLY_WORDS = ("เจ้าหน้าที่", "ข้อสังเกต", "ผ่านได้")
_STAFF_ONLY_LEADS = ("ตรวจว่า", "ตรวจด้วยตา")


def is_staff_only_line(text):
    """บรรทัดนี้เขียนถึงเจ้าหน้าที่ (ไม่ใช่นักศึกษา) ไหม — ดู _STAFF_ONLY_WORDS"""
    text = (text or "").strip()
    return any(word in text for word in _STAFF_ONLY_WORDS) or text.startswith(_STAFF_ONLY_LEADS)


_PLAIN_EDIT = re.compile(r'แก้ "[^"]*" เป็น "[^"]*"')


def _joined_fix(steps):
    """วิธีแก้ทีละคำสั่งรวมเป็นท่อนเดียว: ['ให้แก้ "A" เป็น "B"', 'ให้แก้ "C" เป็น "D"']
    -> 'ต้องแก้ "A" เป็น "B" และ "C" เป็น "D"' (อัปเดต 1.7 เจ้าของยืนยัน 27 ต.ค. 2569)

    คำสั่ง "แก้ ... เป็น ..." ที่ต่อกันไม่พูดคำว่า "แก้" ซ้ำ · คำสั่งชนิดอื่น (เติม ลบ) คงคำกริยาไว้
    """
    clauses = []
    for step in steps:
        clause = step[len("ให้"):] if step.startswith("ให้") else step
        if clauses and _PLAIN_EDIT.fullmatch(clause) and _PLAIN_EDIT.fullmatch(clauses[-1][1]):
            clauses.append((f' และ {clause[len("แก้ "):]}', clause))
        else:
            clauses.append((f' และ{clause}' if clauses else clause, clause))
    return "ต้อง" + "".join(text for text, _clause in clauses)


def _title_summary_lines(issue, steps, value):
    """ข้อชื่อเรื่องในข้อความสรุป = สองบรรทัด (อัปเดต 1.7 เจ้าของยืนยัน 27 ต.ค. 2569)

        ชื่อเรื่องไม่ตรงกับข้อมูลในระบบ ต้องแก้ "สมองด้วยเกมกระดาน" เป็น "สมองโดยใช้เกมกระดาน"
        ชื่อเรื่องที่ถูกต้อง: "ผลของโปรแกรมฝึกสมองโดยใช้เกมกระดานในผู้สูงอายุ"

    ไม่ยกชื่อเรื่องเต็มที่ผิด (นักศึกษามีอยู่ในเล่มแล้ว) · ชี้จุดไม่ได้ (ต่างกันมาก) = บรรทัดแรกเหลือแค่ประโยคนำ
    """
    lead = issue["title_lead"]
    return [f"{lead} {_joined_fix(steps)}" if steps else lead,
            f'ชื่อเรื่องที่ถูกต้อง: "{value}"']


def _summary_sentence(issue, skip_location=False):
    """หนึ่งจุด = สามบรรทัด ตามที่เจ้าหน้าที่สั่ง: อยู่หน้าไหน / อะไรผิด / ต้องแก้เป็นอะไร

        3. สารบัญ (หน้า ฉ) บทที่ 3
           ชื่อบทในเล่มเขียนว่า "ระเบียบวิธีวิจัย"
           ต้องแก้เป็น "วิธีการดำเนินการวิจัย"

    ที่ต้องแยกบรรทัด ไม่ใช่แค่ความสวยงาม — ตัวแปลอังกฤษเทียบ "ทั้งข้อความ" กับกฎ
    ก่อนเสมอ ถ้ายัดสามท่อนไว้ในบรรทัดเดียว จะไม่มีกฎไหนตรงทั้งประโยค แล้วตกไปใช้
    การแทนที่แบบเศษคำซึ่งให้ผลเพี้ยน (เคยได้ "page after page 93 Change to:Page 94")
    พอแยกบรรทัด แต่ละบรรทัดกลายเป็นข้อความเดี่ยวที่มีกฎเต็มประโยครองรับอยู่แล้ว

    อัปเดต 1.7 (เจ้าของยืนยัน 27 ต.ค. 2569): ใต้ตำแหน่งเหลือสองบรรทัด "พบอะไร" + "ต้องแก้เป็นอะไร"
      - ชื่อเรื่อง = จุดที่ต้องแก้ + ชื่อเรื่องที่ถูกต้อง (_title_summary_lines)
      - ข้ออื่นที่มีค่าที่ถูกต้อง ('ต้องแก้เป็น "..."') ไม่มีคำสั่งแก้ทีละจุด ("ให้แก้ ... เป็น ...") แล้ว
        ข้อที่ไม่มีค่าให้ ยังคงคำสั่งแก้ไว้ ให้มีบรรทัดบอกวิธีแก้เสมอ
      - บรรทัดขั้นตอนเพิ่ม (notice) ยังต่อท้ายเหมือนเดิม · การ์ดบนหน้าผลตรวจแสดงครบเหมือนเดิม
    """
    # ถ้อยคำที่เจ้าหน้าที่กำหนดมาทั้งย่อหน้า (จุดที่กดเพิ่มเอง) พิมพ์ตรงตัวทุกคำ
    # ห้ามประกอบใหม่หรือย่อ และย่อหน้าบรรทัดต่อ ๆ ไปให้ตรงกับข้ออื่นในสรุป
    dictated = issue.get("summary_text")
    if dictated:
        # บรรทัดว่างที่เจ้าหน้าที่ใส่ไว้กลางถ้อยคำ (เช่นก่อนลิงก์คู่มือ 19 ต.ค. 2569) คงไว้หนึ่งบรรทัด
        # โดยไม่มีช่องว่างค้าง · บรรทัดว่างหัว/ท้ายและที่ซ้อนกันตัดทิ้ง
        out = []
        for line in dictated.strip().split("\n"):
            line = line.strip()
            if line or (out and out[-1]):
                out.append(line)
        return "\n".join(line and (SUMMARY_INDENT + line if i else line)
                         for i, line in enumerate(out))
    lines = [] if skip_location else [_prose_location(issue.get("location"))]
    found, steps = _found_and_steps(issue)
    value = _corrected_value(issue)
    if issue.get("title_lead") and value:
        lines += _title_summary_lines(issue, steps, value)
    elif value:
        # มีค่าที่ถูกต้องเต็ม ๆ แล้ว คำสั่งแก้ทีละจุดเป็นบรรทัดที่สาม ไม่ต้องมี (อัปเดต 1.7)
        lines += [found, f'ต้องแก้เป็น "{value}"']
    else:
        # วิธีแก้ทีละคำสั่ง (ให้ลบ ... ออก) ต่อจากบรรทัดที่พบ — แต่ละคำสั่งเป็นบรรทัดเดี่ยว ตัวแปลอังกฤษจึงมี
        # กฎเต็มประโยครองรับ (ดูหัวข้อของฟังก์ชันนี้ว่าทำไมต้องแยกบรรทัด)
        lines += [found] + steps
        # ไม่มีค่าเดี่ยวให้ดึง (เช่น มี 2 ตัวเลือก "ก/i") — ใช้ประโยค expected/fix เต็ม ๆ
        # ซึ่งเขียนไว้ในรูป "ต้องเป็น ..." อยู่แล้ว จึงไม่ต้องเติมคำนำอะไรอีก
        directive = next(
            (text for text in (summary_tidy(issue.get("expected")),
                               summary_tidy(issue.get("fix")))
             if text and not is_staff_only_line(text)), "")
        if directive:
            lines.append(directive)
    # ขั้นตอนที่นักศึกษาต้องไปทำต่อนอกเล่ม (เช่นยื่นขออนุมัติโครงบทที่ต่างจากประกาศ)
    # ต้องติดไปกับข้อนั้นเสมอ ไม่ใช่อยู่แต่บนการ์ดที่เจ้าหน้าที่เห็นคนเดียว
    #
    # **ไม่กรองด้วย is_staff_only_line** ถึงจะมีคำว่า "เจ้าหน้าที่" อยู่ในบรรทัดก็ตาม —
    # ตัวกรองนั้นมีไว้คัด "บรรทัดที่สั่งงานเจ้าหน้าที่" (เช่น "ตรวจด้วยตา") ออกจากข้อความ
    # ที่ส่งนักศึกษา ส่วนบรรทัดนี้เขียนถึงนักศึกษาโดยตรงและสั่งให้ไปติดต่อเจ้าหน้าที่
    # หลักสูตร (ถ้อยคำของเจ้าหน้าที่เอง ก.ย. 2569) ถ้าเอาตัวกรองมาครอบ ถ้อยคำทั้งย่อหน้า
    # จะหายไปจากข้อความที่ส่งนักศึกษาโดยไม่มีอะไรฟ้อง
    lines += [line.strip() for line in (issue.get("notice") or "").split("\n")
              if line.strip()]
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

    ข้อ system_note ใช้กติกาเดียวกับข้อสีส้มอื่นทุกประการ — กด "ไม่ผ่าน" แล้วเป็น
    "ต้องแก้" และเข้าข้อความสรุปด้วย (เจ้าหน้าที่สั่ง ก.ย. 2569) ต่างกันแค่ตอนยังไม่กด
    ข้อนี้ไม่อยู่ในข้อความสรุป (ดู issues_to_fix)
    """
    failed, passed = set(failed or ()), set(passed or ())
    zones = report.get("issues_by_zone") or {}
    # จุดที่เจ้าหน้าที่กดเพิ่มเองเป็นคำตัดสินว่าเล่มผิด นับรวมกับสีแดงเหมือนใน
    # issues_to_fix (ตัวเลือก placement=closing เป็นข้อความปิดท้าย ไม่ใช่จุดผิด)
    red = len(zones.get("RED") or []) + len(staff_section_issues(report, staff))
    pending = notice = 0
    for index, issue in enumerate(zones.get("ORANGE") or []):
        key = f"ORANGE:{index}"
        if key in failed:
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


def _summary_block(lines, items, number):
    """เติมรายการหนึ่งกลุ่มลงข้อความสรุป จัดตามส่วนของเล่ม คืนเลขข้อล่าสุด"""
    grouped = {}
    for issue in items:
        grouped.setdefault(summary_section(issue), []).append(issue)
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
            lines.append(f"{number}. {_summary_sentence(issue, skip_location=skip_loc)}")
    return number


# อัปเดต 1.6 (เจ้าของยืนยัน 22 ต.ค. 2569): ปุ่ม "แนบข้อกำหนดรูปเล่มอิเล็กทรอนิกส์" บนหน้าผลตรวจ
# กดแล้วย่อหน้านี้เข้าข้อความสรุปหลังจุดที่ต้องแก้ทั้งหมด เหนือถ้อยคำปิดท้าย (เรื่องค่าปรับ) ลิงก์อยู่บรรทัดของตัวเอง
# ไม่ใช่จุดที่ต้องแก้ จึงไม่นับ ไม่เปลี่ยนผลตรวจ และไม่ลงช่องรายละเอียดในชีท (ไปพร้อมถ้อยคำปิดท้าย)
# หน้าผลตรวจแปลอังกฤษทั้งก้อน (ลิงก์ฉบับอังกฤษคนละลิงก์) จึงต้องมีคู่ภาษาอังกฤษที่นี่ด้วย
REQUIREMENTS_TEXT = ("ศึกษาข้อกำหนดรูปเล่มอิเล็กทรอนิกส์ และดาวน์โหลด template ตัวอย่างเพื่อใช้ประกอบการแก้ไขได้ที่\n"
                     "https://grad-as.my.canva.site/electronic-file-requirements-th")
REQUIREMENTS_TEXT_EN = ("The electronic file requirements and template are available at\n"
                        "https://grad-as.my.canva.site/electronic-file-requirements-en")


def plain_summary(report, failed=None, passed=None, staff=None,
                  with_verdict=True, with_closing=True, requirements=False):
    """สรุปจุดที่ต้องแก้เป็นข้อความล้วน จัดกลุ่มตามส่วนของเล่ม (ไว้คัดลอก/ให้ AI เรียบเรียง)

    เขียนเป็นประโยคภาษาคน ใช้คำเชื่อม ไม่ใช้เครื่องหมาย - หรือ → และไล่เลขทุกจุด
    แยกสองกลุ่มตามหัวรายงาน (เจ้าหน้าที่สั่ง ก.ย. 2569):
      "กรุณาแก้ไขทั้งหมด N จุด"  = ข้อแดง + ข้อที่กดไม่ผ่าน   (เท่ากับกล่อง "ต้องแก้")
      "รอยืนยัน M จุด"           = ข้อส้มที่ยังไม่ได้กด         (เท่ากับกล่อง "รอยืนยัน")
    เลขข้อไล่ต่อกันข้ามสองกลุ่ม นักศึกษาอ้าง "ข้อ 3" ได้โดยไม่ซ้ำกัน

    เล่มที่มีจุดต้องแก้ขึ้นต้นด้วย "กรุณาแก้ไขทั้งหมด N จุด" เลย ไม่มีหัวเรื่อง
    "ผลการตรวจ: ไม่ผ่าน" (เจ้าหน้าที่สั่ง 7 ต.ค. 2569) เล่มที่ผ่านหรือรอยืนยันยังขึ้นต้น
    ด้วยบรรทัดผลตรวจเหมือนเดิม

    with_verdict / with_closing ปิดหัวเรื่อง "ผลการตรวจ: ..." และถ้อยคำปิดท้าย
    (ค่าปรับ วิธีส่งกลับ ช่องทางติดต่อ) ไว้สำหรับช่องรายละเอียดในชีท ซึ่งต้องการ
    เฉพาะ "จุดที่ต้องแก้" — ดู sheet_row()

    requirements = ปุ่มแนบข้อกำหนดรูปเล่มอิเล็กทรอนิกส์ (REQUIREMENTS_TEXT) ไปพร้อมถ้อยคำปิดท้าย
    """
    items = _dedupe_issues(issues_to_fix(report, failed, passed, staff))
    pending = _dedupe_issues(issues_pending(report, failed, passed))
    verdict = summary_verdict(report, failed, passed, staff)
    # ข้อความปิดท้าย (เช่น เรื่องค่าปรับและช่องทางติดต่อ) ไม่ใช่จุดที่ต้องแก้ จึงไม่ถูกนับ
    # และต้องตามไปด้วยเสมอ แม้เล่มจะไม่มีจุดต้องแก้เลย
    # ต้องอ่านค่า verdict ที่ปรับแล้วข้างบน ไม่ใช่ค่าดิบจาก report — เล่มที่ระบบว่าผ่าน
    # แต่เจ้าหน้าที่กดเพิ่มจุด ยังต้องได้ถ้อยคำ "แก้แล้วส่งกลับ" ไม่ใช่ "เสร็จสิ้นแล้ว"
    picked = staff_choices(staff, "closing", verdict)
    closing = [staff_text_for_book(choice["text"], report_label_style(report))
               for _check, choice in picked]
    # ถ้อยคำชุด "ผ่าน" ขึ้นต้นด้วย "การส่ง E-thesis ... เสร็จสิ้นแล้ว" อยู่แล้ว
    finished = any(check.get("applies_to") == "pass" for check, _choice in picked)
    # เจ้าหน้าที่สั่ง (7 ต.ค. 2569) กรณีไม่ผ่าน "ตัดคำว่า ผลการตรวจ ไม่ผ่าน ออก ให้ขึ้นว่า
    # กรุณาแก้ไข.... ไปเลย" — มีจุดต้องแก้เมื่อไหร่ summary_verdict เป็น "ไม่ผ่าน" เสมอ
    # บรรทัด "กรุณาแก้ไขทั้งหมด N จุด" จึงบอกผลตรวจไปในตัว หน้ารายงานก็อ่านผลตรวจจาก
    # บรรทัดนี้แทน (REPORT_VERDICT ใน report.html)
    lines = [f"ผลการตรวจ: {verdict}"] if with_verdict and not items else []
    if not items and not pending:
        # "ผลการตรวจ: ผ่าน" ตามด้วย "ไม่พบจุดที่ต้องแก้ไข" อ่านรวมกันว่า "จบแล้ว
        # ไม่ต้องทำอะไร" นักศึกษาหยุดอ่านตรงนั้น แล้วพลาดกำหนดส่งหน้าลงนามภายใน
        # 30 วันที่อยู่ข้างล่าง (เจ้าหน้าที่รายงานพฤติกรรมนี้ ก.ย. 2569) เล่มที่มี
        # ถ้อยคำชุด "ผ่าน" ต่อท้ายอยู่แล้วจึงไม่ต้องพิมพ์บรรทัดนี้ซ้ำ
        if not finished:
            lines.append("\nไม่พบจุดที่ต้องแก้ไข")
    number = 0
    if items:
        lines.append(f"\nกรุณาแก้ไขทั้งหมด {len(items)} จุด ดังต่อไปนี้")
        number = _summary_block(lines, items, number)
    if pending:
        lines.append(f"\nรอยืนยัน {len(pending)} จุด ดังต่อไปนี้")
        number = _summary_block(lines, pending, number)
    if with_closing:
        if requirements:
            lines.append("\n" + REQUIREMENTS_TEXT)
        for text in closing:
            lines.append("\n" + text.strip())
    return "\n".join(lines).strip()


# ---------- บันทึกผลตรวจลงชีท "บันทึกการตรวจ E-thesis" ----------
# เจ้าหน้าที่สั่ง (ก.ย. 2569) "ตรวจเล่มจากระบบแล้ว ต้องการบันทึก อยากให้ปุ่มนึงกดแล้ว
# ไปเติมในชีทให้เลย ด้วยไม่ต้องทำไรอีก" — ชีทมีช่องติ๊กว่าผิดตรงไหนของเล่มอยู่แล้ว
# ระบบจึงแปลง "ส่วนของเล่ม" ที่ใช้จัดกลุ่มข้อความสรุป ให้เป็นช่องของชีท
SHEET_COLUMNS = ("Cover", "Abstract", "LoC", "Main Content",
                 "Reference", "Appendix", "Biography", "Other")

# เจ้าหน้าที่กำหนดการจับคู่นี้เอง (24 ก.ย. 2569) — ส่วนนำทั้งหมดรวมถึงหน้าลงนาม
# กิตติกรรมประกาศ และเลขหน้าส่วนนำ นับเป็น Cover
SHEET_SECTION_COLUMN = {
    "ภาษาของเล่ม": "Other",
    "ไฟล์ Turnitin": "Other",   # ชีทไม่มีช่อง Turnitin (อัปเดต 1.4)
    "หน้าปก": "Cover",
    "หน้าลงนาม": "Cover",
    "กิตติกรรมประกาศ": "Cover",
    "บทคัดย่อ": "Abstract",
    "สารบัญ": "LoC",
    "ส่วนนำ": "Cover",
    "เนื้อหา (บท)": "Main Content",
    # "ส่วนท้ายเล่ม" แยกเป็นสามช่องด้วย _SHEET_END_COLUMNS ข้างล่าง
    "อื่น ๆ": "Other",
}

# ส่วนท้ายเล่มมีสามช่องในชีท ต้องดูจากถ้อยคำของข้อนั้นว่าพูดถึงส่วนไหน — ดูตำแหน่ง
# อย่างเดียวไม่พอ เพราะบางข้อระบุตำแหน่งเป็นเลขหน้าเฉย ๆ (เล่มจริง 6736605:
# 'หน้า 99 — หัวข้อในหน้านี้เลือกหลายคำ: "รายการอ้างอิง / บรรณานุกรม"')
_SHEET_END_COLUMNS = (
    ("Reference", re.compile(r"อ้างอิง|บรรณานุกรม|REFERENCE|BIBLIOGRAPH", re.I)),
    ("Appendix", re.compile(r"ภาคผนวก|APPENDIX|APPENDICES", re.I)),
    ("Biography", re.compile(r"ประวัติ|BIOGRAPHY", re.I)),
)

# ข้อเดียวที่ผิดหลายหน้าพร้อมกันต้องติ๊กทุกช่องที่เกี่ยวข้อง ไม่ใช่เฉพาะส่วนแรก
# (เล่มจริง 6838776 ข้อประเภทเล่มมีตำแหน่งว่า "หน้าปก, หน้าลงนาม 1, หน้าลงนาม 2
#  และ บทคัดย่ออังกฤษ" ซึ่งต้องติ๊กทั้ง Cover และ Abstract)
_SHEET_LOCATION_COLUMNS = (
    (re.compile(r"หน้าปก"), "Cover"),
    (re.compile(r"หน้าลงนาม|อาจารย์ที่ปรึกษา|กรรมการสอบ|ประธานหลักสูตร|คณบดี"), "Cover"),
    (re.compile(r"บทคัดย่อ"), "Abstract"),
    (re.compile(r"สารบัญ"), "LoC"),
)


# ข้อ "จำนวนบทไม่เป็นไปตามประกาศ" รู้จากถ้อยคำที่พบ ไม่ใช่จากรหัสกฎ — รหัส BODY.OPTION1
# ใช้ร่วมกับข้อชื่อบทรายบทด้วย ถ้าแยกด้วยรหัสกฎ ข้อชื่อบททุกข้อจะติ๊ก Other ตามไปหมด
_CHAPTER_COUNT_FOUND = re.compile(r"^พบ \d+ บท")


def is_chapter_count_issue(issue):
    """ข้อที่ฟ้องว่าจำนวนบททั้งเล่มไม่ตรงประกาศ (ข้อเดียวที่ขึ้นต้นว่า "พบ N บท")"""
    return bool(_CHAPTER_COUNT_FOUND.match((issue.get("found") or "").strip()))


def sheet_columns_of(issue):
    """ช่องในชีทที่จุดผิดข้อนี้ต้องติ๊ก (ข้อเดียวติ๊กได้หลายช่อง)"""
    # จำนวนบทไม่ตรงประกาศ ต้องติ๊กสามช่อง (เจ้าหน้าที่สั่ง ก.ย. 2569 "ต้องติ้กที่ toc
    # และเนื้อหาพร้อมทั้ง other ด้วย") — บทที่เพิ่มหรือตัดกระทบทั้งสารบัญและเนื้อหา
    # ส่วน Other คือช่องที่สำนักงานใช้ทำสถิติเรื่องที่ไม่ใช่ความผิดของส่วนใดส่วนหนึ่ง
    if is_chapter_count_issue(issue):
        return {"LoC", "Main Content", "Other"}
    section = summary_section(issue)
    if section == "ส่วนท้ายเล่ม":
        blob = " ".join(str(issue.get(key) or "")
                        for key in ("location", "found", "expected"))
        found = {column for column, pattern in _SHEET_END_COLUMNS if pattern.search(blob)}
        return found or {"Other"}
    columns = {SHEET_SECTION_COLUMN.get(section, "Other")}
    location = issue.get("location") or ""
    for pattern, column in _SHEET_LOCATION_COLUMNS:
        if pattern.search(location):
            columns.add(column)
    return columns


def sheet_undecided(report, failed=None, passed=None):
    """ข้อสีส้ม/เหลืองที่เจ้าหน้าที่ยังไม่ได้กดผ่านหรือไม่ผ่าน

    เจ้าหน้าที่สั่ง (ก.ย. 2569) "ต้องตรวจสอบส่วนข้อสีส้ม ข้อสีเหลืองก่อน แล้วจึงค่อยกด
    ปุ่มส่งข้อมูลนี้เข้า sheet" — ถ้ายังมีข้อค้าง ชีทจะได้ผลที่ยังไม่ใช่ข้อสรุป
    """
    failed, passed = set(failed or ()), set(passed or ())
    left = []
    for zone in ("ORANGE", "YELLOW"):
        for index, issue in enumerate((report.get("issues_by_zone") or {}).get(zone) or []):
            if issue.get("system_note"):
                continue        # ปัญหาฝั่งเจ้าหน้าที่ ไม่ใช่จุดผิดของเล่ม
            if f"{zone}:{index}" not in failed and f"{zone}:{index}" not in passed:
                left.append(issue)
    return left


# ค่าที่เขียนได้ในช่อง "ผลการพิจารณา" ของชีท (เจ้าหน้าที่กำหนดถ้อยคำเอง ก.ย. 2569)
# เล่มที่ผ่านแยกสองแบบตามปุ่มค่าปรับ — ผ่านเฉย ๆ กับผ่านแล้วต้องแจ้งค่าปรับ
# ข้อความสั้นในช่องถัดจาก Other — บอกว่า "Other" ที่ติ๊กไว้คือเรื่องอะไร
# (เจ้าหน้าที่กำหนดถ้อยคำเอง: เล่มผิดภาษา ก.ย. 2569 · จำนวนบท ก.ย. 2569)
SHEET_NOTE_LANGUAGE = "ภาษาที่เขียนไม่ตรง"
SHEET_NOTE_CHAPTER_COUNT = "จำนวนบทไม่ตรงประกาศ"

SHEET_DECISION_FIX = "ส่งกลับแก้ไข"
SHEET_DECISION_DONE = "เสร็จสิ้น"
SHEET_DECISION_FEE = "แจ้งค่าปรับ"
# ปุ่มที่บอกว่าเล่มที่ผ่านมีค่าปรับ — id เดียวกับที่ใช้ต่อถ้อยคำปิดท้ายให้นักศึกษา
SHEET_FEE_CHOICE = "PASS_FEE_YES"


def _sheet_row_fingerprint(report, failed, passed, staff):
    """ค่าที่ "เขียนลงชีทจริง" ของคำตอบชุดหนึ่ง — ไม่นับข้อความรายละเอียด

    ข้อความรายละเอียดขยับได้จากคำตอบที่เพิ่ม/ตัดจุดต้องแก้ในส่วนที่ติ๊กไว้อยู่แล้ว
    ถ้านับด้วยจะกลายเป็นว่าหัวข้อพวกนั้นบังคับให้กด ซึ่งขัดกับสิ่งที่เจ้าหน้าที่สั่ง
    (ให้บังคับแค่ข้อสีส้ม/เหลือง โครงสร้างหน้าลงนาม และค่าปรับของเล่มที่ผ่าน)
    """
    row = sheet_row(report, failed, passed, staff)
    return (row["decision"], row["pass_or_not"],
            tuple(sorted(row["flags"].items())), row["note"])


def staff_choice_changes_sheet_row(report, check, failed=None, passed=None, staff=None):
    """คำตอบของหัวข้อนี้เปลี่ยนค่าที่เขียนลงชีทไหม

    ใช้แทนการไล่ชื่อหัวข้อในโค้ด — หัวข้อใหม่ที่เพิ่มทีหลังจะได้คำตอบถูกเองโดยไม่ต้อง
    กลับมาแก้ตรงนี้ เช่น ปุ่มค่าปรับของเล่มที่ผ่าน (เปลี่ยนช่องผลการพิจารณาเป็น
    "แจ้งค่าปรับ") บังคับให้กด ส่วนปุ่มค่าปรับของเล่มที่ไม่ผ่าน (ช่องผลยังเป็น
    "ส่งกลับแก้ไข" เหมือนกันทั้งสองคำตอบ) ไม่บังคับ
    """
    base = set(staff or ())
    seen = {_sheet_row_fingerprint(report, failed, passed,
                                   sorted(base | {choice["id"]}))
            for choice in check["choices"]}
    return len(seen) > 1


def staff_check_blocks_save(report, check, failed=None, passed=None, staff=None):
    """หัวข้อนี้ต้องกดก่อนจึงจะบันทึกลงชีทได้ไหม"""
    return bool(check.get("must_answer")) or staff_choice_changes_sheet_row(
        report, check, failed, passed, staff)


def sheet_staff_pending(report, failed=None, passed=None, staff=None):
    """หัวข้อของเจ้าหน้าที่ที่ต้องกดก่อนบันทึกลงชีท แต่ยังไม่ได้กด

    เจ้าหน้าที่สั่ง (ก.ย. 2569): "ต้องเคลียร์ สีส้ม สีเหลือง โครงสร้างหน้าลงนาม
    และกดว่ามีค่าปรับหรือไม่ก่อนเสมอที่จะส่งข้อมูล"

    ไม่กดปุ่มค่าปรับแล้วบันทึกได้ = ชีทได้ "เสร็จสิ้น" ทั้งที่อาจต้องเป็น "แจ้งค่าปรับ"
    ซึ่งเป็นค่าที่ผิดแบบเงียบ ๆ ไม่มีอะไรบอกว่าตกหล่น

    หัวข้อไหนต้องกดขึ้นกับผลตรวจ ณ ตอนนั้น (check_applies) — เล่มผ่านถามชุด PASS_FEE
    เล่มยังไม่ผ่านถามชุด LATE_FEE ถ้าเจ้าหน้าที่กดโครงสร้างหน้าลงนามว่าผิด ผลตรวจ
    เปลี่ยนเป็นไม่ผ่าน คำถามค่าปรับก็เปลี่ยนชุดตามไปเอง
    """
    verdict = summary_verdict(report, failed, passed, staff)
    pressed = set(staff or ())
    left = []
    for check in staff_checks_for_book(report_label_style(report)):
        if not check_applies(check, verdict):
            continue
        if any(choice["id"] in pressed for choice in check["choices"]):
            continue
        # เจ้าหน้าที่สั่ง (ก.ย. 2569) ว่าไม่ต้องกดค่าปรับก็บันทึกได้ ยกเว้นเล่มที่ผ่าน
        # (เล่มผ่านต้องกด เพราะช่องผลการพิจารณาเปลี่ยนระหว่าง "เสร็จสิ้น" กับ "แจ้งค่าปรับ")
        if not staff_check_blocks_save(report, check, failed, passed, staff):
            continue
        left.append(check)
    return left


def sheet_row(report, failed=None, passed=None, staff=None):
    """ค่าที่จะเขียนลงชีท — คิดจากรายการชุดเดียวกับข้อความสรุป จะได้ไม่ขัดกันเอง

    คืน dict: decision (ช่องผลการพิจารณา), pass_or_not (1/0), flags (ช่องติ๊ก),
    note (ข้อความสั้นของช่อง Other), details (รายละเอียดที่ต้องแก้ไข)
    และ verdict ที่ใช้ตัดสิน
    """
    items = _dedupe_issues(issues_to_fix(report, failed, passed, staff))
    flags = {column: False for column in SHEET_COLUMNS}
    # ช่อง Other ของชีทมีข้อความสั้นกำกับ — เล่มเดียวติ๊ก Other ได้จากหลายเรื่อง
    # (ผิดภาษา + จำนวนบท) จึงเก็บเป็นรายการแล้วต่อกัน ไม่ใช่เอาเรื่องแรกแล้วทิ้งที่เหลือ
    notes = []
    for issue in items:
        for column in sheet_columns_of(issue):
            flags[column] = True
        if issue.get("rule_id") == "FORM.BOOK_LANGUAGE":
            notes.append(SHEET_NOTE_LANGUAGE)
        elif is_chapter_count_issue(issue):
            notes.append(SHEET_NOTE_CHAPTER_COUNT)
    note = " / ".join(dict.fromkeys(notes))        # ตัดของซ้ำ เรียงตามที่เจอในเล่ม
    if items:
        decision = SHEET_DECISION_FIX
    elif SHEET_FEE_CHOICE in set(staff or ()):
        # เล่มผ่านแล้วแต่ยังมีค่าปรับค้าง — คนละช่องผลกับเล่มที่จบเรียบร้อย
        # ส่วน Pass or not ยังเป็น 0 เพราะรูปเล่มผ่านแล้ว ค่าปรับเป็นเรื่องการชำระเงิน
        decision = SHEET_DECISION_FEE
    else:
        decision = SHEET_DECISION_DONE
    # รายละเอียดที่ต้องส่งให้นักศึกษาแก้ไข — รายการจุดที่ต้องแก้ชุดเดียวกับปุ่ม
    # "คัดลอก" เป๊ะ ๆ จะได้ไม่มีทางที่ชีทกับข้อความที่ส่งนักศึกษาจะเป็นคนละเรื่องกัน
    # เขียนเฉพาะเล่มที่ "ส่งกลับแก้ไข" (เจ้าหน้าที่สั่ง ก.ย. 2569 "บันทึกแค่เฉพาะเล่ม
    # ที่ส่งกลับแก้ไข") เล่มที่จบแล้วไม่มีอะไรให้แก้ ช่องนี้จึงต้องว่าง ไม่ใช่เอา
    # ถ้อยคำปิดท้ายของเล่มที่ผ่านไปกองไว้ในช่องที่ชื่อว่า "รายละเอียดที่ต้องแก้ไข"
    # ตัดหัวเรื่อง "ผลการตรวจ: ไม่ผ่าน" กับย่อหน้าค่าปรับออก (เจ้าหน้าที่สั่ง ก.ย. 2569
    # "ข้อความที่เอาไปใส่ ในช่อง v ไม่เอาข้อความค่าปรับ กับ ผลการตรวจ: ไม่ผ่าน")
    # ช่องนี้อยู่แถวเดียวกับช่องผลการพิจารณาที่บอกไปแล้วว่าส่งกลับแก้ไข ส่วนค่าปรับ
    # กับช่องทางติดต่อเป็นถ้อยคำของอีเมล ไม่ใช่ "จุดที่ต้องแก้" ของเล่ม
    # ตั้งแต่ 7 ต.ค. 2569 ข้อความที่ส่งนักศึกษาก็ไม่มีหัวเรื่องนี้แล้ว (ดู plain_summary)
    # ช่องนี้จึงเท่ากับข้อความนั้นตัดย่อหน้าปิดท้ายพอดี
    details = (plain_summary(report, failed, passed, staff,
                             with_verdict=False, with_closing=False)
               if decision == SHEET_DECISION_FIX else "")
    return {
        "decision": decision,
        "pass_or_not": 1 if items else 0,
        "flags": flags,
        "note": note,
        "details": details,
        "verdict": summary_verdict(report, failed, passed, staff),
    }


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
# บรรทัดที่เป็นแต่จุดไข่ปลา/เส้นประ (ตรงกับกิ่งเส้นประของ _DEGREE_SEARCH_STOP)
_DOT_LEADER_LINE = re.compile(r'^[ \t]*(?:[…]{3,}|\.{6,})')
_DEGREE_SEARCH_STOP = re.compile(
    r'^[ \t]*(?:'
    r'[…]{3,}|\.{6,}'                                    # เส้นประสำหรับลงนาม
    # INDEPENDENT STUDY ด้วย (เล่มจริง ก.ย. 2569 "INDEPENDENT STUDY ADVISORY COMMITTEE:") — ของเดิม
    # รู้จักแค่ THESIS/THEMATIC PAPER โซนกรรมการของเล่มการค้นคว้าอิสระจึงไม่ถูกตัดทิ้ง
    r'|(?:THESIS\s+|THEMATIC\s+PAPER\s+|INDEPENDENT\s+STUDY\s+)?(?:ADVISORY|EXAMINATION)\s+COMMITTEE'
    r'|คณะกรรมการที่ปรึกษา|คณะกรรมการสอบ'
    r')', re.I | re.M)


# ตัวย่อคุณวุฒิที่มีจุดคั่นตั้งแต่สองท่อน เช่น Ph.D. / M.Sc. / ปร.ด. / วท.ม.
_DEGREE_ABBR_TOKEN = re.compile(r'(?:[A-Za-z]{1,4}\.){2,}|(?:[ก-๙]{1,4}\.){2,}')


# คำนำหน้าที่ template เขียนไว้เอง ไม่ใช่คำที่นักศึกษาเติมเกิน — ข้อมูลอนุมัติเก็บแต่
# ชื่อปริญญา ("ศิลปศาสตรมหาบัณฑิต (สังคมศาสตร์สิ่งแวดล้อม)") ส่วน template หน้าปกเล่มไทย
# เขียนเป็นประโยคว่า "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร ปริญญา<ชื่อปริญญา>"
# ถ้าไม่ตัดคำพวกนี้ก่อน เล่มที่ถูกต้องจะโดนฟ้องว่ามีข้อความเกิน — เล่มที่ประโยคขึ้นบรรทัดใหม่
# พอดีก่อนคำว่า "ปริญญา" เคยรอดมาได้ แต่เล่มที่ไม่ขึ้นบรรทัดใหม่โดนเต็ม ๆ
# (ที่มา: template ทางการ 2569 ส่วนนำ หน้าปก ทั้งเล่มไทยและเล่มอังกฤษ)
# ประโยคอังกฤษของ template ตัดบรรทัดตรงไหนก็ได้ แล้วแต่ความยาวของชื่อปริญญา บรรทัด
# ชื่อปริญญาจึงขึ้นต้นด้วย "หางประโยค" ท่อนใดก็ได้ ไม่ใช่แค่ "FOR THE DEGREE OF" ที่เคยดักไว้
# เล่มจริงของเจ้าหน้าที่ (ก.ย. 2569, 6738473 SISG/M) ตัดบรรทัดเป็น
#     A THESIS SUBMITTED IN PARTIAL FULFILLMENT
#     OF THE REQUIREMENTS FOR
#     THE DEGREE OF MASTER OF SCIENCE
#     (SURGICAL RESEARCH)
# เล่มนี้พิมพ์ถูกตาม template ทุกคำ แต่โดนฟ้องว่าบรรทัดชื่อปริญญามีคำเกิน
# ("THE DEGREE OF") เพราะหางท่อนนี้ไม่มีในรายการ
_DEGREE_SENTENCE_EN = ("SUBMITTED IN PARTIAL FULFILLMENT OF THE REQUIREMENTS "
                       "FOR THE DEGREE OF")
_DEGREE_SENTENCE_OPENERS = ("A THESIS", "A THEMATIC PAPER", "AN INDEPENDENT STUDY")


def _sentence_tails(sentence):
    """ทุกหางของประโยค (ตัดจากหัวทีละคำ) รวมท่อนคำเดียวท้ายสุด

    ท่อนคำเดียวคือ "OF" (เล่มที่ตัดบรรทัดคาระหว่าง "THE DEGREE" กับ "OF") ตัดทิ้งได้
    โดยไม่กลบความผิด เพราะหลังตัดแล้วยังต้องเท่ากับชื่อปริญญาทั้งท่อน คำเกินท้ายบรรทัด
    จึงยังโผล่อยู่ดี
    """
    words = sentence.split()
    return [" ".join(words[start:]) for start in range(len(words))]


_DEGREE_LINE_TEMPLATE_PREFIXES = tuple(norm(text).upper() for text in (
    "วิทยานิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
    "สารนิพนธ์นี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
    "การค้นคว้าอิสระนี้เป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
    "ได้รับการพิจารณาให้นับเป็นส่วนหนึ่งของการศึกษาตามหลักสูตร",
    "ปริญญา",
    *_DEGREE_SENTENCE_OPENERS,
    *_sentence_tails(_DEGREE_SENTENCE_EN),
))


def cover_degree_line_expected(expected, thai_book):
    """ถ้อยคำ "บรรทัดนี้ต้องเป็น" ของชื่อปริญญาบนหน้าปก ตาม template ไม่ใช่ชื่อเปล่า ๆ

    เจ้าหน้าที่สั่ง (ก.ย. 2569): "ต้องเป็น ปริญญาต่อด้วยชื่อปริญญาก็คือ
    ปริญญาพยาบาลศาสตรมหาบัณฑิต (การพยาบาลเวชปฏิบัติชุมชน) ภาษาอังกฤษก็คือ
    the degree of ........."

    ของเดิมบอกว่าบรรทัดนี้ต้องเป็นชื่อปริญญาเปล่า ๆ "ไม่มีคำอื่นนำหน้าหรือต่อท้าย"
    ซึ่งขัดกับ template ที่เขียนคำว่า "ปริญญา" นำหน้าไว้เอง เจ้าหน้าที่ที่ทำตามข้อความนี้
    จะสั่งให้นักศึกษาลบคำที่ template กำหนดไว้ออก
    """
    return f"ปริญญา{expected}" if thai_book else expected


def degree_line_search_text(page_text, abstract=False):
    """ส่วนของหน้าที่บรรทัดชื่อปริญญาอยู่ได้: ก่อนโซนรายชื่อกรรมการ และไม่รวมบรรทัดชื่อ-รหัสนักศึกษา"""
    return "\n".join(line for line in _degree_search_text(page_text, abstract).splitlines()
                     if not _STUDENT_ID_SHAPE.search(line))


def _without_degree_template_words(nl):
    """ตัดถ้อยคำของ template ที่นำหน้าชื่อปริญญาออกให้หมดทุกชั้น (รับค่าที่ norm แล้ว)

    บรรทัดเดียวมีได้สองชั้น ("...ตามหลักสูตร" แล้วต่อด้วย "ปริญญา") ตัดชั้นเดียวแล้วหยุด
    ไม่พอ — เล่มที่ไม่ขึ้นบรรทัดใหม่พอดีก่อนคำว่า "ปริญญา" จะโดนฟ้องว่ามีข้อความเกิน
    ทั้งที่พิมพ์ตาม template
    """
    trimmed = True
    while trimmed:
        trimmed = False
        for prefix in _DEGREE_LINE_TEMPLATE_PREFIXES:
            if prefix and nl.upper().startswith(prefix):
                nl = nl[len(prefix):]
                trimmed = True
                break
    return nl


def degree_line_extras(page_text, expected, abstract=False):
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

    **มีบรรทัดที่ตรงพอดีอยู่แล้ว = ไม่มีคำเกิน** (ก.ย. 2569 ตอนเพิ่ม M.C.T.M.) — ตัวย่อที่ไม่มี
    สาขาในวงเล็บสั้นมากหลัง norm() ("MCTM") จึงไปโผล่ในบรรทัดอื่นได้โดยบังเอิญ เช่นรหัส
    นักศึกษา "6700000 TMCT/M" (norm แล้ว "...TMCTM") หรือคุณวุฒิกรรมการ "M.D., M.C.T.M."
    ของเดิมข้ามบรรทัดที่ตรงพอดีแล้วคืนบรรทัดพวกนั้นแทน เล่มที่ถูกต้องจึงได้แดง "มีข้อความเกิน"
    บรรทัดชื่อ-รหัสนักศึกษาไม่มีวันเป็นบรรทัดชื่อปริญญา จึงข้ามไปเลย และตัดโซนรายชื่อกรรมการ
    ทิ้งก่อน (_degree_search_text ตัวเดียวกับที่ใช้หาบรรทัดปริญญา) ไม่งั้นเล่มที่พิมพ์ชื่อปริญญา
    ผิดจะได้ข้อความสั่งให้ "ลบข้อความเกิน" ที่ยกบรรทัดชื่อตัวเองหรือบรรทัดกรรมการมาอ้าง
    """
    want = norm(expected)
    if not want:
        return ""
    lines = degree_line_search_text(page_text, abstract).splitlines()
    # ดูทีละบรรทัดก่อน ถ้าไม่เจอค่อยต่อบรรทัดที่ติดกันเข้าด้วยกัน — เจ้าหน้าที่ทัก
    # (ก.ย. 2569) ว่า "อาจจะคนละบรรทัดนะ อยู่ที่แล้วแต่เล่ม" ประโยคของ template
    # ตัดบรรทัดไม่เหมือนกันทุกเล่ม บางเล่มตัดกลางชื่อปริญญาหรือก่อนวงเล็บสาขา
    # ถ้าดูทีละบรรทัดอย่างเดียว เล่มพวกนั้นจะไม่มีบรรทัดไหน "มีชื่อปริญญาอยู่" เลย
    # แล้วคำเกิน ("(ภาคพิเศษ)") จะหลุดไปเงียบ ๆ
    hits = []
    for size in (1, 2, 3):
        for start in range(len(lines) - size + 1):
            chunk = " ".join(lines[start:start + size])
            nl = _without_degree_template_words(norm(chunk))
            if nl == want:
                return ""
            if want in nl:
                hits.append(soft(chunk))
        # เจอในหน้าต่างขนาดนี้แล้ว ไม่ต้องขยายต่อ — ขยายไปก็ได้แต่ข้อความยาวขึ้นเปล่า ๆ
        if hits:
            break
    return min(hits, key=len) if hits else ""


def degree_abbr_expected(abbr):
    """บรรทัด "ควรเป็น" ของข้อชื่อปริญญาแบบย่อในบทคัดย่อ

    ถ้อยคำเดียวทุกตัวย่อ ไม่พูดถึง "สาขาในวงเล็บ" — เจ้าหน้าที่ (ก.ย. 2569) สองรอบ
      1. "ตัวย่อปริญญาบางอัน ไม่มีวงเล็บ ถ้าใส่ข้อความวงเล็บจะสับสนได้" (เล่มจริง M.C.T.M.
         ได้ "...ตามรูปแบบชื่อย่อและสาขาในวงเล็บ" นักศึกษาจะไปหาสาขามาใส่เอง)
      2. "ถ้าเป็นชื่อปริญญาอื่นๆ ที่มีวงเล็บละ ก็ให้ใช้รูปแบบนี้" — ค่าในเครื่องหมายคำพูดแสดง
         สาขาในวงเล็บครบอยู่แล้ว และเมื่อเล่มผิดที่ตัวปริญญา (M.Sc. แทน Ph.D., วศ.บ. แทน วศ.ม.)
         คำว่า "และสาขาในวงเล็บ" ชี้ให้ไปดูวงเล็บซึ่งถูกอยู่แล้ว
    """
    return f'ต้องเป็น "{abbr}" ตามรูปแบบชื่อย่อ'


def _looks_like_degree_line(line):
    """บรรทัดนี้หน้าตาเป็น "ชื่อปริญญาแบบย่อ" หรือไม่

    ใช้แยก "เล่มพิมพ์ชื่อปริญญาผิด" ออกจาก "เล่มไม่มีบรรทัดชื่อปริญญาเลย" ซึ่งวิธีแก้
    คนละอย่างกัน — ถ้าไม่แยก ระบบจะยกบรรทัดที่ใกล้เคียงที่สุดบนหน้ามาอ้างว่าเป็น
    ชื่อปริญญา (เคยได้บรรทัดชื่อ-รหัสนักศึกษา) แล้วเจ้าหน้าที่นึกว่าระบบอ่านเพี้ยน

    ชื่อปริญญาแบบเต็มก็นับ (เล่มจริง ก.ย. 2569 พิมพ์ "MASTER OF CLINICAL TROPICAL MEDICINE"
    ตรงบรรทัดที่ต้องเป็น "M.C.T.M.") — มีบรรทัดปริญญาอยู่ แค่เขียนผิดรูป ต้องยกมาบอกว่าเล่มเขียน
    ว่าอะไร ไม่ใช่บอกว่า "ไม่พบบรรทัด" ซึ่งทำให้นักศึกษาไปเพิ่มบรรทัดใหม่ซ้อนอีกบรรทัด
    """
    text = soft(line)
    return bool(text) and bool(_ABS_DEGREE_HEAD.match(text)
                               or _DEGREE_ABBR_TOKEN.search(text)
                               or _FULL_DEGREE_NAME.search(text))


# "DEGREE OF" นำหน้า และระดับที่สะกดผิด ("DOCTORAL OF") ก็ยังเป็นบรรทัดชื่อปริญญา — เล่มจริง (อัปเดต 1.8.2 28 ต.ค. 2569)
# พิมพ์ "DEGREE OF DOCTORAL OF APPLIED THAI TRADITIONAL MEDICINE" ตรงบรรทัดที่ต้องเป็น "D.ATM." แล้วได้ "ไม่พบบรรทัด"
_FULL_DEGREE_NAME = re.compile(
    r'^\s*(?:DEGREE\s+OF\s+)?(?:MASTER|DOCTOR|BACHELOR)[A-Z]*\s+OF\s+\S|มหาบัณฑิต|ดุษฎีบัณฑิต', re.I)


def _degree_search_text(page_text, abstract=False):
    """ตัดโซนรายชื่อกรรมการทิ้งก่อนหาบรรทัดชื่อปริญญา

    คุณวุฒิใต้ชื่อกรรมการเขียนด้วยตัวย่อชุดเดียวกับชื่อปริญญา และมักมีสาขาในวงเล็บครบ
    ตัวกรอง "วงเล็บครบ" ข้างล่างจึงเลือกบรรทัดของกรรมการแทนบรรทัดปริญญาจริง โดยเฉพาะ
    เล่มที่บรรทัดปริญญาลืมปิดวงเล็บ (เจอในเล่มจริง: "for the degree of Doctor of
    Philosophy (Logistics and Engineering Management" ไม่มีวงเล็บปิด) ผลคือรายงาน
    ฟ้องว่าชื่อปริญญาในเล่มเขียนว่า "LIANGROKAPART, Ph.D., THANANYA WASUSRI, Ph.D."
    ซึ่งเป็นชื่ออาจารย์ ไม่ใช่ชื่อปริญญา
    """
    text = page_text or ""
    # กฎแยกตามชนิดหน้า ไม่เดาจากหน้าตาข้อความ: หน้าบทคัดย่อ (abstract=True) ไม่มีเส้นลงนามเลย
    # เส้นประ "………" บนหน้านี้เป็นเศษใต้ชื่อเรื่อง (เล่มจริง ต.ค. 2569 ทำให้หยุดค้นก่อนบรรทัด
    # "ส.ม." แล้วฟ้องผิดว่าไม่พบ) จึงไม่ใช่จุดตัด — โซนกรรมการของหน้านี้ตัดด้วยหัวข้อ
    # "คณะกรรมการที่ปรึกษา/ADVISORY COMMITTEE" เท่านั้น · เส้นประใต้ชื่อเรื่องฟ้องแยกเอง
    # (_report_abstract_title_dots) · หน้าลงนาม/หน้าปกใช้เส้นประเป็นจุดตัดตามเดิม
    stop = None
    for m in _DEGREE_SEARCH_STOP.finditer(text):
        if abstract and _DOT_LEADER_LINE.match(m.group(0)):
            continue
        stop = m
        break
    head = text[:stop.start()] if stop else text
    return head if soft(head) else text


# บรรทัดวันที่สอบบนหน้าลงนามตาม template: ต่อจากบรรทัดชื่อปริญญาเสมอ และขึ้นต้นด้วย "on" (อังกฤษ)
# หรือ "วันที่" (ไทย) — ดูแค่คำขึ้นต้น ไม่ดูรูปแบบวันที่ เล่มเขียนวันที่แบบไหนก็แยกได้
_EXAM_DATE_LINE = re.compile(r'^(?:on\b|(?:เมื่อ|ณ)?\s*วันที่)', re.I)


def closest_degree_line(page_text, expected, abstract=False):
    """หาข้อความชื่อปริญญาบนหน้านั้น รองรับกรณีถูกตัดขึ้นหลายบรรทัด

    ชื่อปริญญาบนหน้าปกมักถูกตัดเป็น 2-3 บรรทัด ได้หลายแบบ เช่น
      "MASTER OF SCIENCE" / "(INFORMATION TECHNOLOGY MANAGEMENT)"   (ขึ้นบรรทัดตรงวงเล็บ)
      "MASTER OF SCIENCE (WELL-BEING AND" / "SUSTAINABILITY)"        (วงเล็บเปิดค้าง)
    จึงสร้างตัวเลือกจาก "หน้าต่างบรรทัดต่อเนื่อง 1-3 บรรทัด" รอบบรรทัดที่มีคำบ่งชี้
    แล้วเลือกอันที่ใกล้เคียงข้อมูลอนุมัติที่สุด (เล่มไทยต้องมีคำบ่งชี้ไทยด้วย)
    """
    page_text = _degree_search_text(page_text, abstract)
    lines = [soft(line) for line in (page_text or '').splitlines() if soft(line)]
    markers = ('DEGREE', 'MASTER', 'DOCTOR', 'BACHELOR', 'MENG', 'MSC', 'PHD',
               norm('ปริญญา'), norm('มหาบัณฑิต'), norm('ดุษฎีบัณฑิต'))
    candidates = []
    for k, line in enumerate(lines):
        if not any(marker in norm(line) for marker in markers):
            continue
        for span in (1, 2, 3):
            if k + span > len(lines):
                break
            # บรรทัดวันที่สอบ ("on ..." / "วันที่ ...") ตามหลังบรรทัดปริญญาบนหน้าลงนามเสมอ (template)
            # ไม่ใช่ส่วนของชื่อปริญญา — เล่มจริง ต.ค. 2569 ได้ชื่อปริญญา "Doctor of ...
            # on 30 <เดือน>, <ปี>" (ต่อบรรทัดวันที่เข้ามา) แล้วเจ้าหน้าที่อ่านไม่ออกว่าผิดเรื่องปริญญา
            # หรือวันที่ (เจ้าหน้าที่ทัก 19 ต.ค. 2569)
            if span > 1 and _EXAM_DATE_LINE.match(lines[k + span - 1]):
                break
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
    # ขีดต่างแบบไม่นับเป็นความต่าง (fold_dashes) แต่ค่าที่ส่งกลับยังเป็นข้อความตามที่เล่มพิมพ์
    if rule['case_sensitive'] and fold_dashes(actual) == fold_dashes(expected):
        return {'status': 'exact', 'actual': actual, 'score': 1.0}
    if rule['case_sensitive']:
        if fold_dashes(actual).casefold() == fold_dashes(expected).casefold():
            return {'status': 'case', 'actual': actual, 'score': 1.0}
        if actual == expected:
            return {'status': 'exact', 'actual': actual, 'score': 1.0}
        if actual.casefold() == expected.casefold():
            return {'status': 'case', 'actual': actual, 'score': 1.0}
        # ภาษาไทยไม่มีตัวพิมพ์เล็ก-ใหญ่ และการดึงข้อความ PDF เรียงสระ/วรรณยุกต์เพี้ยนได้
        # จึงเทียบด้วยตัวเทียบที่เรียงตัวเล็กบน/ล่างให้ก่อน แต่ยังสะกดตรงทุกตัว (spelling_key)
        if re.search(r'[ก-๙]', expected) and same_spelling(actual, expected):
            return {'status': 'exact', 'actual': actual, 'score': 1.0}
    elif same_spelling(actual, expected):
        return {'status': 'exact', 'actual': actual, 'score': 1.0}
    if marks_only_difference(actual, expected):
        # ตัวอักษรครบทุกตัว ต่างเฉพาะวรรณยุกต์/สระบน-ล่าง/การันต์ หรือ ำ กับ า = สะกดผิด เป็นแดงเหมือนสะกดผิดอื่น
        # (เจ้าหน้าที่สั่ง 6 ต.ค. 2569) ให้เป็น typo เสมอแม้คำสั้น ("บทนา") ข้อความจึงชี้จุดต่างให้ด้วย
        # marks_only บอกกฎปลายทางว่าถ้าฟอนต์ของเล่มเพี้ยน ตัวเล็กพวกนี้อาจเป็นระบบอ่านผิด (ดู Report.add)
        score = difflib.SequenceMatcher(None, _spelling_units(expected), _spelling_units(actual)).ratio()
        return {'status': 'typo', 'actual': actual, 'score': score, 'marks_only': True}
    score = difflib.SequenceMatcher(None, norm(expected), norm(actual)).ratio()
    status = 'typo' if score >= rule['typo_threshold'] else 'mismatch'
    return {'status': status, 'actual': actual, 'score': score}


def compare_reference_text(page_text, expected, rule_name, degree_line=False, abstract=False):
    """Find the relevant PDF line, then classify exact/case/typo/mismatch.

    degree_line=True ค้นเฉพาะส่วนที่บรรทัดชื่อปริญญาอยู่ได้ (degree_line_search_text) — การค้น
    ทั้งหน้าเอาทุกบรรทัดมาต่อกัน ตัวย่อสั้นอย่าง "M.C.T.M." (norm แล้ว "MCTM") จึงไปเจอใน
    รหัสนักศึกษา "6700000 TMCT/M" หรือคุณวุฒิกรรมการ แล้วตอบว่า exact ทั้งที่เล่มพิมพ์ผิดเป็น
    "M.Sc." (เจอตอนเพิ่ม M.C.T.M. ก.ย. 2569)
    """
    rule = MATCH_RULES[rule_name]
    if degree_line:
        page_text = degree_line_search_text(page_text, abstract)
    if not rule['case_sensitive'] and spelled_in(expected, page_text):
        return {'status': 'exact', 'actual': soft(expected), 'score': 1.0}
    matched, reason = exact_reference_status(page_text, expected)
    if matched:
        return {'status': 'exact', 'actual': soft(expected), 'score': 1.0}
    actual = closest_degree_line(page_text, expected, abstract) if degree_line else closest_text_line(page_text, expected)
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


# ---------- ชี้จุดต่าง + บอกวิธีแก้ ----------
# สระอำ (ำ) ผู้อ่านเห็นเป็นตัวเดียวกับพยัญชนะที่มันตามหลัง (นำ น้ำ จำ) แต่ _graphemes ข้างบนนับ ำ
# เป็นตัวอักษรอีกตัว จุดต่างที่เกี่ยวกับ ำ จึงอ่านออกมาเป็น 'ขาด "ำ"' ลอย ๆ ที่ไม่รู้ว่าอยู่ตรงไหน
# ชุดนี้ใช้เฉพาะตอนอธิบายจุดต่าง (ไม่แตะ _graphemes ที่ _closest_run ใช้อยู่)
_DISPLAY_GRAPHEME = re.compile(
    rf'[{_TH_LEAD_VOWEL}]?[^{_TH_ABOVE_BELOW}ำ][{_TH_ABOVE_BELOW}]*ำ?[{_TH_ABOVE_BELOW}]*'
    rf'|[{_TH_ABOVE_BELOW}ำ]+')
# ตัวที่ขึ้นต้นคำไม่ได้ — ถ้าจุดอ้างอิงเริ่มที่ตัวพวกนี้ แปลว่าตัดกลางคำ ให้ถอยไปอีกตัว
_NOT_A_WORD_START = re.compile(rf'^[ะาๆฯำ{_TH_ABOVE_BELOW}]')
_LEAD_VOWEL_START = re.compile(rf'^[{_TH_LEAD_VOWEL}]')
# อักษรต่ำเดี่ยวที่ ห นำได้ (หง หญ หน หม หย หร หล หว)
_LED_BY_HO = "งญนมยรลว"

# text = จุดต่างเป็นประโยคบอกเล่า (ต่อท้าย "ที่พบ" บนการ์ด)
# steps = วิธีแก้เป็นคำสั่งทีละบรรทัด ("ให้ลบ ... ออก") ที่ข้อความสรุปส่งนักศึกษาใช้
# marks_only = ตัวอักษรครบทุกตัว ต่างเฉพาะวรรณยุกต์/สระบน-ล่าง/การันต์/ำ (ดู marks_only_difference)
DiffAnalysis = namedtuple('DiffAnalysis', 'text steps marks_only', defaults=(False,))
_NO_DIFF = DiffAnalysis('', ())
# kind = delete / insert / replace / space_extra (ก้อนที่รวมหลายจุด = chunk) · pos, end = ตำแหน่งใน graphemes
# ของข้อความที่พบ · lo, hi = ช่วงที่ข้อความบอกวิธีแก้ยกมาอ้าง (ใช้เฉพาะช่องว่างเกิน ที่ต้องยกคำรอบ ๆ มาชี้)
# small = รวมมาจากจุดต่างเล็ก ๆ (ไม่เกินสองตัว) หลายจุดในคำเดียวกัน — บอกวิธีแก้แบบยกคำรอบข้างมาทั้งก้อน (typo_step)
_Edit = namedtuple('_Edit', 'kind got want pos end order lo hi small', defaults=(None, None, False))
# จุดต่างเกินนี้แปลว่าต่างกันมากจนไล่ทีละจุดไม่ช่วย ให้ดูข้อความที่ถูกต้องแทน
_MAX_STEPS = 6
# ความยาวของ "ตัวอ้างอิงตำแหน่ง" (กี่ตัวอักษรที่คนมองเห็น) — เริ่มสั้นแล้วยืดจนไม่ซ้ำกับที่อื่น
_ANCHOR_MIN, _ANCHOR_MAX = 4, 12


def _display_graphemes(text):
    out = []
    for grapheme in _DISPLAY_GRAPHEME.findall(text or ""):
        # ช่องว่างที่มีวรรณยุกต์/สระลอยตามหลัง (ข้อความที่สระหลุดจากพยัญชนะ) ไม่ใช่ตัวอักษรตัวเดียว
        if len(grapheme) > 1 and grapheme[0].isspace():
            out += [grapheme[0], grapheme[1:]]
        else:
            out.append(grapheme)
    return out


def _is_latin(unit):
    return bool(unit) and unit[0].isascii() and unit[0].isalnum()


def _grow_to_latin_words(fa, ea, fi, ei, lo, hi, lo2, hi2):
    """ขยายจุดต่างให้ครอบคำอังกฤษทั้งคำเมื่อไปแตะกลางคำ (ผู้อ่านเห็นคำ ไม่ใช่ตัวอักษรเดี่ยว)

    ได้ 'ต่างที่ "Recogniton" ต้องเป็น "Recognition"' แทน 'ขาด "i"' คำต้องติดกันจริงในต้นฉบับ
    (ไม่ข้ามช่องว่าง) ไม่งั้นคำถัดไปจะถูกกลืนเข้ามาด้วย
    """
    def starts_latin():
        return (lo < hi and _is_latin(fa[lo])) or (lo2 < hi2 and _is_latin(ea[lo2]))

    def ends_latin():
        return (lo < hi and _is_latin(fa[hi - 1])) or (lo2 < hi2 and _is_latin(ea[hi2 - 1]))

    while (lo > 0 and lo2 > 0 and starts_latin()
           and fi[lo] - fi[lo - 1] == 1 and ei[lo2] - ei[lo2 - 1] == 1
           and _is_latin(fa[lo - 1]) and _is_latin(ea[lo2 - 1])):
        lo, lo2 = lo - 1, lo2 - 1
    while (hi < len(fa) and hi2 < len(ea) and ends_latin()
           and fi[hi] - fi[hi - 1] == 1 and ei[hi2] - ei[hi2 - 1] == 1
           and _is_latin(fa[hi]) and _is_latin(ea[hi2])):
        hi, hi2 = hi + 1, hi2 + 1
    return lo, hi, lo2, hi2


def _grow_to_syllables(fa, ea, lo, hi, lo2, hi2):
    """ขยายจุดต่างไม่ให้เริ่มที่สระลอย ๆ ("าร") หรือจบที่พยางค์ที่ยังขาดตัวสะกด ("บั")

    ตัวที่ขยายไปครอบต้องตรงกันทั้งสองฝั่ง (อยู่ในช่วงที่ข้อความเหมือนกัน) ไม่ใช่การเดาเพิ่ม
    """
    def starts_bad():
        return ((lo < hi and _NOT_A_WORD_START.match(fa[lo]))
                or (lo2 < hi2 and _NOT_A_WORD_START.match(ea[lo2])))

    def ends_bad():
        return ((lo < hi and _needs_next(fa[hi - 1]))
                or (lo2 < hi2 and _needs_next(ea[hi2 - 1])))

    while lo > 0 and lo2 > 0 and starts_bad() and fa[lo - 1] == ea[lo2 - 1]:
        lo, lo2 = lo - 1, lo2 - 1
    while hi < len(fa) and hi2 < len(ea) and ends_bad() and fa[hi] == ea[hi2]:
        hi, hi2 = hi + 1, hi2 + 1
    return lo, hi, lo2, hi2


# ตัวอ้างอิงรอบข้างที่จบที่พยางค์หน้าของคำ (กระ ประ) ยืดไปถึงรอยต่อคำได้ไม่เกินกี่ตัว
_ANCHOR_WORD_REACH = 4


def _at_word_edge(seq, i):
    """ตำแหน่ง i ของลำดับตัวอักษรที่คนมองเห็นเป็นรอยต่อคำที่มองเห็นได้โดยไม่ต้องมีพจนานุกรม

    ต้น/ท้ายข้อความ · ช่องว่าง · ต้นพยางค์ที่ขึ้นด้วยสระหน้า (เ แ โ ใ ไ ซึ่งเริ่มพยางค์ใหม่เสมอ) · รอยต่ออักษรไทยกับอังกฤษ
    """
    if i <= 0 or i >= len(seq) or not seq[i].strip() or not seq[i - 1].strip():
        return True
    return bool(_LEAD_VOWEL_START.match(seq[i])) or _is_latin(seq[i]) != _is_latin(seq[i - 1])


def _thai_island(fa, fi, start, stop):
    """ข้อความเหมือนที่คั่นระหว่างจุดต่างสองจุด [start, stop) เป็นแค่ตัวเดียวกลางคำไทยไหม

    เล่มจริงเขียน "สมองด้วยเกมกระดาน" อนุมัติ "สมองโดยใช้เกมกระดาน" ตัวเทียบจับ "ย" ตัวเดียวที่บังเอิญตรงกัน
    แล้วแตกเป็น 'ต่างที่ "ด้ว" ต้องเป็น "โด" และ ขาด "ใช้"' ซึ่งตัดกลางคำ — ตัวเดียวที่คั่นอยู่แบบนี้ไม่ใช่ข้อความที่
    ตรงกันจริง ให้รวมสองจุดเป็นจุดเดียว 'ต่างที่ "ด้วย" ต้องเป็น "โดยใช้"' (อัปเดต 1.6.1 เจ้าของยืนยัน 26 ต.ค. 2569)
    ไม่รวมข้ามช่องว่าง (เป็นรอยต่อคำจริง) · ตัวที่คั่นและตัวสองข้างต้องเป็นอักษรไทย (อังกฤษมีตัวรวมเป็นคำของตัวเองแล้ว)
    · ตัวที่คั่นขึ้นด้วยสระหน้าไม่รวม เพราะสระหน้าเริ่มพยางค์ใหม่เสมอ ("กัช|แอ|พลิ" เป็นสองจุดจริง)
    """
    if stop - start != 1 or not (0 < start < len(fa)) or stop >= len(fa):
        return False
    if fi[start] - fi[start - 1] > 1 or fi[stop] - fi[stop - 1] > 1:
        return False
    if _LEAD_VOWEL_START.match(fa[start]):
        return False
    return all(_THAI_LETTER.search(fa[k]) for k in (start - 1, start, stop))


def _extra_gaps(fa, ea, fi, ei, i1, i2, j1):
    """ช่องว่างที่เล่มมีแต่ข้อมูลอนุมัติไม่มี ระหว่างอักษรไทยสองตัวในช่วงที่ข้อความตรงกัน

    ภาษาไทยไม่เว้นวรรคระหว่างคำ ช่องว่างกลางข้อความไทยที่ข้อมูลอนุมัติไม่มีจึงเป็นช่องว่างที่
    พิมพ์เกินจริง คืน index (ในลำดับที่ตัดช่องว่างทิ้ง) ของตัวที่อยู่หลังช่องว่าง
    """
    out = []
    for k in range(1, i2 - i1):
        p, q = i1 + k, j1 + k
        if (fi[p] - fi[p - 1] > 1 and ei[q] - ei[q - 1] == 1
                and _THAI_LETTER.search(fa[p - 1]) and _THAI_LETTER.search(fa[p])
                # ช่องว่างกลางพยางค์ ("กีฬ า") ผู้พิมพ์ไม่เคาะแบบนั้น เป็นช่องว่างที่ระบบอ่านเอง จึงไม่บอก
                and not _NOT_A_WORD_START.match(fa[p]) and not _needs_next(fa[p - 1])):
            out.append(p)
    return out


def _thai_edits(found_s, expected_s, spaces):
    """จุดแก้ของข้อความที่มีอักษรไทย: (graphemes ของข้อความที่พบ, รายการจุดแก้) หรือ None

    เทียบ "ตัวอักษรที่คนมองเห็น" โดยตัดช่องว่างทิ้งก่อน เพราะภาษาไทยไม่เว้นวรรคระหว่างคำ
    ช่องว่างที่ติดมาจึงเป็นเรื่องของการขึ้นบรรทัดใหม่ ไม่ใช่ความต่างของข้อความ (เล่มจริง
    ชื่อเรื่องไทยถูกตัดเป็นสองสามบรรทัด จุดตัดทุกจุดเคยกลายเป็นจุดต่างที่มองไม่เห็นว่าคืออะไร)

    spaces=True เปิดให้บอกช่องว่างที่ "เกินจริง" ระหว่างอักษรไทยด้วย (เจ้าหน้าที่สั่ง 6 ต.ค. 2569)
    ผู้เรียกต้องเป็นฝ่ายที่รู้ว่ารอยตัดบรรทัดไม่ได้ถูกต่อด้วยช่องว่าง (ดู _join_wrapped)
    """
    fg, eg = _display_graphemes(found_s), _display_graphemes(expected_s)
    fi = [i for i, g in enumerate(fg) if g.strip()]
    ei = [i for i, g in enumerate(eg) if g.strip()]
    fa, ea = [fg[i].upper() for i in fi], [eg[i].upper() for i in ei]
    matcher = difflib.SequenceMatcher(None, fa, ea, autojunk=False)
    if matcher.ratio() < 0.5:
        return None

    def grow(core):
        return _grow_to_syllables(fa, ea, *_grow_to_latin_words(fa, ea, fi, ei, *core))

    cores, spans, gaps, pieces = [], [], [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            if spaces:
                gaps += _extra_gaps(fa, ea, fi, ei, i1, i2, j1)
            continue
        core = (i1, i2, j1, j2)
        span = grow(core)
        # ขนาดของจุดต่างจริงแต่ละจุดที่รวมอยู่ในก้อนนี้ (ดู small ของ _Edit)
        piece = [max(i2 - i1, j2 - j1)]
        # จุดต่างสองจุดที่ขยายแล้วชนกัน ต้องรวมที่ "แกน" (ส่วนที่ต่างจริง + ข้อความเหมือนที่คั่นอยู่) แล้วขยายใหม่
        # ห้ามรวมปลายของแต่ละฝั่งแยกกัน: ขยายของจุดแรกอาจกลืนแกนของจุดหลังไปครึ่งหนึ่ง ปลายสองฝั่งจึงไม่
        # สอดคล้องกัน (เล่มจริงลักษณะ "vaccine ne booster" เคยทำให้ "ne" ที่เกินมาหายไปจากรายงานเงียบ ๆ)
        while spans and (span[0] <= spans[-1][1] or _thai_island(fa, fi, spans[-1][1], span[0])):
            prev = cores.pop()
            spans.pop()
            piece = pieces.pop() + piece
            core = (prev[0], core[1], prev[2], core[3])
            span = grow(core)
        cores.append(core)
        spans.append(span)
        pieces.append(piece)
    edits = []
    for (lo, hi, lo2, hi2), piece in zip(spans, pieces):
        got = ''.join(fg[fi[lo]:fi[hi - 1] + 1]) if lo < hi else ''
        want = ''.join(eg[ei[lo2]:ei[hi2 - 1] + 1]) if lo2 < hi2 else ''
        if lo < hi:
            pos, end = fi[lo], fi[hi - 1] + 1
        else:
            pos = fi[lo - 1] + 1 if lo > 0 else 0
            end = pos
        kind = 'replace' if got and want else ('delete' if got else 'insert')
        # จุดต่างเล็ก ๆ หลายจุดที่รวมเป็นคำเดียว ("ด้ว"→"โด" + ขาด "ใช้" = "ด้วย"→"โดยใช้") บอกวิธีแก้แบบยกคำรอบข้าง
        # เหมือนตอนที่แต่ละจุดยังแยกกัน (เจ้าของยืนยัน 26 ต.ค. 2569: 'ให้แก้ "สมองด้วยเกมกระดาน" เป็น "สมองโดยใช้เกมกระดาน"')
        small = len(piece) > 1 and max(piece) <= 2
        edits.append(_Edit(kind, got, want, pos, end, lo, small=small))
    for p in gaps:
        # คำรอบช่องว่างที่ยกมาบอก ไม่ให้ขึ้นต้นด้วยสระลอย ๆ หรือจบกลางพยางค์ (เหมือนตัวอ้างอิงตำแหน่ง)
        # และไม่ข้ามช่องว่างอื่น: ลบช่องว่างแรกไปแล้ว ข้อความที่อีกข้อยกมาอ้างซึ่งมีช่องว่างนั้นอยู่ก็หาไม่เจอ
        gap_start, gap_end = fi[p - 1] + 1, fi[p]
        lo = _snap_anchor_start(fg, fi[max(0, p - 5)])
        hi = _snap_anchor_end(fg, fi[min(len(fi) - 1, p + 4)] + 1)
        lo = max([lo] + [k + 1 for k in range(lo, gap_start) if not fg[k].strip()])
        hi = min([hi] + [k for k in range(gap_end, hi) if not fg[k].strip()])
        edits.append(_Edit('space_extra', ''.join(fg[lo:gap_start]), ''.join(fg[gap_end:hi]),
                           gap_start, gap_end, p, lo, hi))
    edits.sort(key=lambda e: (e.order, e.pos))
    return fg, edits


def _edit_phrase(edit):
    """จุดแก้หนึ่งจุดเป็นประโยคบอกเล่า — คำเชื่อมของแต่ละแบบไม่เหมือนกัน ต่อท้ายประโยคได้เลย"""
    if edit.kind == 'insert':
        return f'ขาด "{edit.want}"'
    if edit.kind == 'delete':
        return f'มี "{edit.got}" เกินมา'
    if edit.kind == 'space_extra':
        return f'มีช่องว่างเกินระหว่าง "{edit.got}" กับ "{edit.want}"'
    return f'ต่างที่ "{edit.got}" ต้องเป็น "{edit.want}"'


def _needs_next(grapheme):
    """พยางค์ที่ยังไม่จบ — ต้องมีตัวสะกด/ตัวตามอยู่ถัดไป (ไม้หันอากาศ ัเ หรือ เ-ีย เ-ือ)"""
    return 'ั' in grapheme or (grapheme[:1] == 'เ' and ('ี' in grapheme or 'ื' in grapheme))


def _snap_anchor_start(fg, start):
    """ขยับจุดเริ่มของตัวอ้างอิงตำแหน่งไม่ให้ตัดกลางคำจนอ่านไม่ออก

    ภาษาไทยไม่มีรอยต่อระหว่างคำให้ยึด จึงทำได้เท่าที่เดาจากตัวอักษร: ห้ามขึ้นต้นด้วยตัวที่เริ่มคำไม่ได้
    (ะ า ๆ) หรือตัวสะกดของพยางค์ก่อนหน้า (บั|ณ) และถ้ามีสระหน้า (เ แ โ ใ ไ) อยู่ถัดไปไม่เกินสองตัว
    ให้ครอบถึงพยางค์นั้น และคำอังกฤษไม่ถูกตัดกลางคำ ("ne bงoost" อ่านยากกว่า "vaccine bงooster" มาก)
    """
    while start > 0 and _is_latin(fg[start]) and _is_latin(fg[start - 1]):
        start -= 1
    while start > 0 and _NOT_A_WORD_START.match(fg[start]):
        start -= 1
    if start > 0 and _needs_next(fg[start - 1]):
        start -= 1
    # ห นำอักษรต่ำ (หล หน หม หญ ...) อยู่พยางค์เดียวกับตัวที่ตามมา — "ลักสูตร" ต้องเป็น "หลักสูตร" (อัปเดต 1.6.1)
    if start > 0 and fg[start - 1] == 'ห' and fg[start][:1] in _LED_BY_HO:
        start -= 1
    if not _LEAD_VOWEL_START.match(fg[start]):
        for extra in (1, 2):
            if start - extra >= 0 and _LEAD_VOWEL_START.match(fg[start - extra]):
                return start - extra
    return 0 if start <= 3 else start


def _past_karan(fg, stop):
    """ห้ามจบก่อนการันต์ หรือก่อนตัวที่ตามด้วยการันต์ (อัปเดต 1.7 เจ้าของยืนยัน 27 ต.ค. 2569)

    ตัวการันต์อยู่พยางค์เดียวกับตัวหน้า — "ศาสต|ร์" ต้องเป็น "ศาสตร์" · "จัน|ทร์" ต้องเป็น "จันทร์"
    """
    while stop < len(fg):
        if '์' in fg[stop]:
            stop += 1
        elif (stop + 1 < len(fg) and '์' in fg[stop + 1]
              and re.fullmatch('[ก-ฮ]', fg[stop])):
            stop += 2
        else:
            break
    return min(stop, len(fg))


# คำเชื่อม/บุพบทที่ขึ้นต้นข้อความรอบจุดแก้ได้โดยไม่ช่วยชี้ตำแหน่ง — ตัดทิ้งถ้าที่เหลือยังชี้ตำแหน่งได้ไม่ซ้ำ (อัปเดต 1.7)
_LEADING_FILLERS = ("และ", "หรือ", "ของ", "ใน", "กับ", "ที่", "โดย", "เพื่อ", "ต่อ", "จาก", "แห่ง", "ตาม",
                    "สำหรับ", "ระหว่าง")


def _drop_leading_filler(fg, lo, limit):
    """จุดเริ่มใหม่หลังตัดคำเชื่อมที่ขึ้นต้นข้อความรอบจุดแก้ (คืน lo เดิมถ้าไม่ได้ขึ้นต้นด้วยคำพวกนี้)

    จุดแก้ "ย์" ใน "และมนุษย์ศาสตร์" ถอยไปต้นพยางค์แล้วได้ 'ให้แก้ "และมนุษย์ศาสต" ...' ทั้งที่ "มนุษย์ศาสตร์" ก็ชี้
    ตำแหน่งได้แล้ว (เจ้าของยืนยัน 27 ต.ค. 2569: ไม่ลากคำนำหน้าที่ไม่จำเป็น) — ผู้เรียกตรวจเองว่าที่เหลือยังไม่ซ้ำ
    """
    for word in _LEADING_FILLERS:
        size = len(_display_graphemes(word))
        if lo + size < limit and ''.join(fg[lo:lo + size]) == word:
            return lo + size
    return lo


def _snap_anchor_end(fg, stop):
    """ขยับจุดสิ้นสุดของตัวอ้างอิงไม่ให้ลงท้ายกลางพยางค์ (เช่น "นามั" ที่ขาดตัวสะกด ย) หรือกลางคำอังกฤษ

    อัปเดต 1.6.1 (เจ้าของยืนยัน 26 ต.ค. 2569): ห้ามจบก่อนสระ/เครื่องหมายที่เริ่มคำไม่ได้ ("เกมกร|ะ") และพยางค์ที่ลงท้าย
    ด้วย ะ โดยไม่มีสระหน้า (กระ ประ ตระ) เป็นพยางค์หน้าของคำ ไม่ใช่ท้ายคำ — ยืดไปถึงรอยต่อคำที่มองเห็นได้ (_at_word_edge)
    ถ้าอยู่ไม่เกิน _ANCHOR_WORD_REACH ตัว: "เกมกระดาน" ไม่ใช่ "เกมกร" · พยางค์ที่จบแล้ว ("อนามัย") ยังจบตรงนั้น
    """
    while stop < len(fg) and _is_latin(fg[stop - 1]) and _is_latin(fg[stop]):
        stop += 1
    for _ in range(2):
        if stop >= len(fg) or not _needs_next(fg[stop - 1]):
            break
        stop += 1
    while stop < len(fg) and _NOT_A_WORD_START.match(fg[stop]):
        stop += 1
    stop = _past_karan(fg, stop)
    if (1 < stop < len(fg) and fg[stop - 1] == 'ะ' and not _LEAD_VOWEL_START.match(fg[stop - 2])
            and not _at_word_edge(fg, stop)):
        for step in range(1, _ANCHOR_WORD_REACH + 1):
            if _at_word_edge(fg, stop + step):
                return min(stop + step, len(fg))
    return min(stop, len(fg))


# ก้อนที่รวมจากจุดแก้หลายจุดยาวเกินนี้ (ตัวอักษรที่คนมองเห็น) ไม่ช่วยนักศึกษา ให้ดูข้อความที่ถูกต้องแทน
_MERGED_MAX = 36


def _crosses(lo, hi, other):
    """ข้อความอ้างอิง [lo, hi) คร่อมจุดแก้ other อยู่หรือไม่

    คร่อมแล้วทำตามทีละข้อไม่ได้: ทำข้อของ other ไปแล้ว ข้อความที่อีกข้อยกมาอ้างก็ไม่ตรงกับตัวเล่มอีก
    """
    if other.pos == other.end:
        return lo < other.pos < hi
    return lo < other.end and other.pos < hi


def _replacement(edit):
    """ข้อความที่ต้องมีแทนที่ช่วงของจุดแก้นี้ในเล่ม — ช่องว่างเกินคือไม่มีอะไรเลย (want ของมันเป็นคำรอบข้าง)"""
    return '' if edit.kind == 'space_extra' else edit.want


def _merge_edits(fg, a, b):
    """รวมจุดแก้สองจุดที่อยู่ใกล้กันเป็นก้อนเดียว (ข้อความเหมือนที่คั่นอยู่ ใส่ไว้ทั้งสองฝั่ง)"""
    got = ''.join(fg[a.pos:b.end])
    if len(_display_graphemes(got)) > _MERGED_MAX:
        return None
    want = _replacement(a) + ''.join(fg[a.end:b.pos]) + _replacement(b)
    return _Edit('chunk', got, want, a.pos, b.end, a.order)


def _count_overlapping(text, needle):
    """จำนวนที่ needle ปรากฏใน text โดยนับที่ซ้อนทับกันด้วย ("จัดจ" ใน "จัดจัดจ" มีสองที่ ไม่ใช่ที่เดียว)"""
    count, at = 0, text.find(needle) if needle else -1
    while at != -1:
        count += 1
        at = text.find(needle, at + 1)
    return count


def _steps_once(fg, edits):
    """วิธีแก้ของจุดแก้ชุดนี้ -> (ข้อ ๆ, None) หรือ (None, ลำดับของจุดแก้ที่ต้องรวมกับจุดถัดไป)"""
    plain = ''.join(fg).replace(' ', '')
    # ข้อความต้องไม่ซ้ำทั้งตอนที่ยังไม่ได้แก้อะไรเลย และตอนที่แก้ข้ออื่นครบแล้ว (แก้คำผิดแล้วบังเอิญได้คำเดียวกับ
    # คำที่ต้องลบ "ของ" ก็ซ้ำขึ้นมาทันที) — ตั้งใหม่ทุกจุดแก้ใน states
    states = [plain]

    def others_applied(skip):
        out, at = [], 0
        for k, other in enumerate(edits):
            if k != skip and other.kind != 'space_extra':
                out += [''.join(fg[at:other.pos]), other.want]
                at = other.end
        out.append(''.join(fg[at:]))
        return ''.join(out).replace(' ', '')

    def occurs(text):
        needle = text.replace(' ', '')
        return max(_count_overlapping(state, needle) for state in states)

    def before(pos, need, alone=False):
        """ข้อความหน้าจุดแก้ที่ไม่ซ้ำ -> (ข้อความ, จุดเริ่ม) — alone=True ต้องไม่ซ้ำ "ด้วยตัวมันเอง"
        (ใช้กับการเติมคำ ที่บอกแค่ "หลัง ...")"""
        for size in range(_ANCHOR_MIN, _ANCHOR_MAX + 1):
            start = _snap_anchor_start(fg, max(0, pos - size))
            anchor = ''.join(fg[start:pos]).strip()
            if not anchor:
                return '', pos
            if occurs(anchor if alone else anchor + need) == 1:
                return anchor, start
        return '', pos

    def after(end, need, alone=False):
        """ข้อความหลังจุดแก้ที่ไม่ซ้ำ -> (ข้อความ, จุดสิ้นสุด)"""
        # จุดแก้จบกลางพยางค์ (ตัวถัดไปเป็นสระที่เริ่มคำไม่ได้) ยกข้อความหลังจุดนั้นมาเป็นตัวอ้างอิงไม่ได้
        if end < len(fg) and _NOT_A_WORD_START.match(fg[end]):
            return '', end
        for size in range(_ANCHOR_MIN, _ANCHOR_MAX + 1):
            stop = _snap_anchor_end(fg, min(len(fg), end + size))
            anchor = ''.join(fg[end:stop]).strip()
            if not anchor:
                return '', end
            if occurs(anchor if alone else need + anchor) == 1:
                return anchor, stop
        return '', end

    def typo_step(edit):
        """ผิดทีละตัวอักษร (ตกตัว/เกินตัว/สะกดผิดตัวเดียว) หรือหลายจุดที่อยู่ติดกัน: ยกคำรอบจุดแก้มาทั้งก้อน
        แล้วบอกให้แก้เป็นอะไร

        'ให้ลบ "ฑ" ที่อยู่หลัง "หาบัณ"' ไม่ช่วยนักศึกษา เห็น "บัณทิต" → "บัณฑิต" ทั้งคำเทียบกันชัดกว่ามาก
        """
        for size in range(4, 10):
            lo = _snap_anchor_start(fg, max(0, edit.pos - size)) if edit.pos else 0
            hi = _snap_anchor_end(fg, min(len(fg), edit.end + size))
            left, right = ''.join(fg[lo:edit.pos]), ''.join(fg[edit.end:hi])
            found_part = (left + edit.got + right).strip()
            if found_part and occurs(found_part) == 1:
                trimmed = _drop_leading_filler(fg, lo, edit.pos)
                shorter = (''.join(fg[trimmed:edit.pos]) + edit.got + right).strip()
                if trimmed != lo and occurs(shorter) == 1:
                    lo, left, found_part = trimmed, ''.join(fg[trimmed:edit.pos]), shorter
                break
        fixed_part = (left + edit.want + right).strip()
        return f'ให้แก้ "{found_part}" เป็น "{fixed_part}"', lo, hi

    def letter_step(edit):
        """วิธีแก้ของจุดแก้หนึ่งจุด -> (ข้อความ, จุดเริ่ม, จุดสิ้นสุด) ของข้อความที่ข้อนี้ยกมาอ้างจากเล่ม"""
        if edit.kind == 'chunk' or edit.small or max(len(_display_graphemes(edit.got)),
                                                     len(_display_graphemes(edit.want))) <= 2:
            return typo_step(edit)
        if edit.kind == 'insert':
            # "เติมหลัง L" ต้องชี้ตำแหน่งได้ด้วย L ตัวเดียว (ถ้า L มีหลายที่ นักศึกษาไม่รู้ว่าที่ไหน)
            left, start = before(edit.pos, '', alone=True)
            right, stop = ('', edit.pos) if left else after(edit.pos, '', alone=True)
            if left:
                return f'ให้เติม "{edit.want}" หลัง "{left}"', start, edit.pos
            if right:
                return f'ให้เติม "{edit.want}" หน้า "{right}"', edit.pos, stop
            # หาตัวอ้างอิงที่ไม่ซ้ำไม่ได้ (เช่น ติดสระที่เริ่มคำไม่ได้): ยกคำรอบจุดนั้นมาทั้งก้อนแทนการเติมลอย ๆ
            return typo_step(edit)
        alone = occurs(edit.got) == 1 and len(_display_graphemes(edit.got)) >= 3
        left, start = ('', edit.pos) if alone else before(edit.pos, edit.got)
        right, stop = ('', edit.end) if (alone or left) else after(edit.end, edit.got)
        if not (alone or left or right):
            # ข้อความที่จะลบ/แก้ซ้ำกับที่อื่น และหาตัวอ้างอิงตำแหน่งไม่ได้: ยกคำรอบจุดนั้นมาทั้งก้อน
            return typo_step(edit)
        lo, hi = (start, edit.end) if left else (edit.pos, stop if right else edit.end)
        if edit.kind == 'delete':
            if left:
                return f'ให้ลบ "{edit.got}" ที่อยู่หลัง "{left}" ออก', lo, hi
            if right:
                return f'ให้ลบ "{edit.got}" ที่อยู่หน้า "{right}" ออก', lo, hi
            return f'ให้ลบ "{edit.got}" ออก', lo, hi
        if left:
            return f'ให้แก้ "{edit.got}" ที่อยู่หลัง "{left}" เป็น "{edit.want}"', lo, hi
        if right:
            return f'ให้แก้ "{edit.got}" ที่อยู่หน้า "{right}" เป็น "{edit.want}"', lo, hi
        return f'ให้แก้ "{edit.got}" เป็น "{edit.want}"', lo, hi

    steps = []
    for k, edit in enumerate(edits):
        if edit.kind == 'space_extra':
            text, lo, hi = f'ให้ลบช่องว่างระหว่าง "{edit.got}" กับ "{edit.want}"', edit.lo, edit.hi
        else:
            states[:] = [plain, others_applied(k)]
            text, lo, hi = letter_step(edit)
        for j in (k - 1, k + 1):
            if 0 <= j < len(edits) and _crosses(lo, hi, edits[j]):
                return None, min(j, k)
        steps.append(text)
    return steps, None


def _edit_steps(fg, edits):
    """วิธีแก้เป็นข้อ ๆ — คำสั่งสั้น ๆ ที่นักศึกษาลงมือทำตามได้เลย พร้อมตัวอ้างอิงตำแหน่งเมื่อจำเป็น

    ของเดิมข้อความสรุปบอกแค่ว่า 'ต่างที่ "..."' แล้วให้นักศึกษาไปเทียบข้อความยาวสองก้อนเอง
    (เจ้าหน้าที่สั่ง 6 ต.ค. 2569: ควรบอกด้วยว่าต้องแก้ยังไง) ตัวอ้างอิงตำแหน่งใส่เฉพาะตอนที่ข้อความ
    ที่จะลบ/แก้ ไปซ้ำกับที่อื่นในชื่อเดียวกัน (ลบ "การ" ที่ไหน — ในชื่อเรื่องมี "การ" อยู่หลายที่)

    แต่ละข้อต้องทำตามเรียงลำดับหรือสลับลำดับก็ได้ผลเหมือนกัน: ถ้าข้อความที่ข้อหนึ่งยกมาอ้างไปคร่อมจุดแก้
    ของข้อข้างเคียง (พิมพ์ผิดสองที่ห่างกันไม่กี่ตัวอักษร) จะรวมสองจุดเป็นข้อเดียว ไม่ปล่อยให้ข้อแรกแก้ไป
    แล้วข้อที่สองหาข้อความที่อ้างไว้ไม่เจอ
    """
    if len(edits) > _MAX_STEPS:
        return []
    edits = list(edits)
    # จุดแก้ที่ช่วงซ้อนทับกันเองไม่ควรเกิด (ตัวขยายคำกลืนจุดอื่น) ถ้าเกิดแล้วรวมกันจะได้ข้อความผิด ไม่ตอบดีกว่าตอบผิด
    if any(left.end > right.pos for left, right in zip(edits, edits[1:])):
        return []
    while True:
        steps, clash = _steps_once(fg, edits)
        if clash is None:
            return steps
        merged = _merge_edits(fg, edits[clash], edits[clash + 1])
        if merged is None:
            return []
        edits[clash:clash + 2] = [merged]


def _analyze_thai(found_s, expected_s, spaces):
    # สระอำทุกแบบ (ำ · ํา · นํ้า) ตาเห็นเหมือนกันทุกประการ ถ้าไม่รวมก่อนเทียบ จุดต่างจะอ่านออกมาว่า
    # 'ต่างที่ "นํา" ต้องเป็น "นำ"' ซึ่งเจ้าหน้าที่และนักศึกษามองไม่เห็นว่าต่างตรงไหน (thai_text.fold_sara_am)
    # ตัวเล็กบน/ล่างที่เก็บคนละลำดับ ("ท่ี" กับ "ที่") ก็เช่นกัน — เรียงลำดับมาตรฐานก่อน (_canonical_marks)
    result = _thai_edits(_canonical_marks(fold_sara_am(found_s)),
                         _canonical_marks(fold_sara_am(expected_s)), spaces)
    if result is None or not result[1]:
        return _NO_DIFF
    fg, edits = result
    return DiffAnalysis(' และ '.join(_edit_phrase(e) for e in edits),
                        tuple(_edit_steps(fg, edits)))


def _word_steps(words, tag, i1, i2, got, want):
    """วิธีแก้ของจุดต่างระดับคำ (อังกฤษ) — ตัวอ้างอิงตำแหน่งคือคำที่อยู่ก่อนหน้า (ยืดจนไม่ซ้ำ)

    ใส่ตัวอ้างอิงเฉพาะเมื่อจำเป็น: ขาดคำ = ต้องบอกว่าเติมตรงไหนเสมอ ส่วนลบ/แก้ ใส่ต่อเมื่อคำนั้น
    มีซ้ำอยู่ในชื่อเรื่องเดียวกัน (ลบ "THE" ตัวไหน)
    """
    span = words[i1:i2]

    def count(seq):
        return sum(1 for k in range(len(words) - len(seq) + 1) if words[k:k + len(seq)] == seq)

    anchor = ''
    if i1 > 0 and (tag == 'insert' or (span and count(span) > 1)):
        for size in (1, 2, 3):
            before = words[max(0, i1 - size):i1]
            if count(before + span) == 1:
                break
        anchor = ' '.join(before)
    if tag == 'insert':
        if anchor:
            return f'ให้เติม "{want}" หลัง "{anchor}"'
        return f'ให้เติม "{want}" หน้า "{words[0]}"' if words else f'ให้เติม "{want}"'
    if tag == 'delete':
        return f'ให้ลบ "{got}" ที่อยู่หลัง "{anchor}" ออก' if anchor else f'ให้ลบ "{got}" ออก'
    # ต่างกันแค่ช่องว่าง ("OFTHE" กับ "OF THE") บอกให้ชัดว่าเป็นเรื่องเว้นวรรค
    if got.replace(' ', '') == want.replace(' ', ''):
        return f'ให้แก้การเว้นวรรค "{got}" เป็น "{want}"'
    if anchor:
        return f'ให้แก้ "{got}" ที่อยู่หลัง "{anchor}" เป็น "{want}"'
    return f'ให้แก้ "{got}" เป็น "{want}"'


def _legacy_diff(a, b, keyfn, join, words):
    """จุดต่างของข้อความที่ไม่มีอักษรไทย: ระดับคำ (อังกฤษหลายคำ) หรือระดับตัวอักษร (คำเดียว/รหัส)"""
    matcher = difflib.SequenceMatcher(None, keyfn(a), keyfn(b))
    if matcher.ratio() < 0.5:
        return _NO_DIFF
    parts, steps = [], []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == 'equal':
            continue
        got, want = join(a[i1:i2]), join(b[j1:j2])
        # ช่องว่างล้วนไม่ต้องพูดถึง — ข้อความที่ถูกต้องมีให้อยู่แล้วในบรรทัดถัดมา
        if not got.strip() and not want.strip():
            continue
        if not want:
            parts.append(f'มี "{got}" เกินมา')
            kind = 'delete'
        elif not got:
            parts.append(f'ขาด "{want}"')
            kind = 'insert'
        else:
            parts.append(f'ต่างที่ "{got}" ต้องเป็น "{want}"')
            kind = 'replace'
        if words:
            steps.append(_word_steps(a, kind, i1, i2, got, want))
    if not parts:
        return _NO_DIFF
    if not words:
        # คำเดียวหรือรหัส: ไล่ทีละตัวอักษรอ่านยาก บอกให้แก้ทั้งคำเลย
        whole_a, whole_b = join(a), join(b)
        steps = [f'ให้แก้ "{whole_a}" เป็น "{whole_b}"'] if len(whole_a) <= 80 else []
    elif len(steps) > _MAX_STEPS:
        steps = []
    return DiffAnalysis(' และ '.join(parts), tuple(steps))


def _analyze_latin(found_s, expected_s):
    # อังกฤษหลายคำ: ลองเทียบระดับคำก่อน (อ่านง่าย เห็นเป็นคำ) ถ้าทุกคำต่างกัน
    # จนเทียบไม่ได้ ค่อยตกไปเทียบระดับตัวอักษร (เช่น "LITTERATURE" ต่าง T กับ S)
    if re.search(r'[A-Za-z]', expected_s) and ' ' in expected_s.strip():
        by_word = _legacy_diff(found_s.split(), expected_s.split(),
                               lambda xs: [x.upper() for x in xs], ' '.join, True)
        if by_word.text:
            return by_word
    return _legacy_diff(_graphemes(found_s), _graphemes(expected_s),
                        lambda xs: xs, ''.join, False)


def analyze_diff(found, expected, spaces=False):
    """ชี้ว่า 'ข้อความที่พบ' ต่างจาก 'ข้อความที่ถูกต้อง' ตรงไหน และต้องแก้ยังไง

    คืน DiffAnalysis(text, steps) — text = จุดต่างเป็นประโยคบอกเล่า steps = วิธีแก้ทีละคำสั่ง
    ว่างทั้งคู่เมื่อตรงกันอยู่แล้ว หรือต่างกันมากจนการชี้จุดไม่ช่วย (ให้ดูข้อความที่ถูกต้องแทน)

    - ข้อความที่มีอักษรไทย: เทียบระดับ "ตัวอักษรที่คนมองเห็น" โดยไม่สนช่องว่าง ชี้เป็นคำที่อ่านออก
      (ขาด "กีฬา") และบอกช่องว่างเกินระหว่างอักษรไทยเมื่อ spaces=True
    - อังกฤษที่มีช่องว่าง: เทียบระดับคำ (เช่น ต่างที่ "REQUIREMENT" ต้องเป็น "REQUIREMENTS")
    """
    found_s, expected_s = soft(found), soft(expected)
    # ตรงกันทุกตัวอักษรรวมวรรณยุกต์/สระ/ำ = ไม่มีอะไรต้องชี้ (ต่างแค่ช่องว่าง/วรรคตอนยังไม่นับเหมือนเดิม)
    if not found_s or not expected_s or same_spelling(found_s, expected_s):
        return _NO_DIFF
    if _THAI_LETTER.search(expected_s):
        analysis = _analyze_thai(found_s, expected_s, spaces)
    else:
        analysis = _analyze_latin(found_s, expected_s)
    if analysis.text and marks_only_difference(found_s, expected_s):
        return analysis._replace(marks_only=True)
    return analysis


def describe_diff(found, expected, spaces=False):
    """ชี้ว่า 'ข้อความที่พบ' ต่างจาก 'ข้อความที่ถูกต้อง' ตรงไหน อย่างไร

    คืน '' ถ้าต่างกันมากจนการชี้จุดไม่ช่วย (ให้ผู้ใช้ดูข้อความเต็มที่ให้ไว้แทน)

    เขียนเป็นคำพูด ไม่ใช้ลูกศร — เจ้าหน้าที่สั่งว่าคำอธิบายไม่ต้องใช้สัญลักษณ์เยอะ
    ข้อความที่คืนมา "ต่อท้ายประโยคได้เลย" คือมีคำเชื่อมของตัวเองมาพร้อม เพราะคำเชื่อม
    ที่เหมาะกับแต่ละกรณีไม่เหมือนกัน ("ต่างที่ ก ต้องเป็น ข" แต่ "ขาด ก" / "มี ก เกินมา"
    ซึ่งถ้าเอา "ต่างที่" ไปนำหน้าจะกลายเป็น "ต่างที่ มี S เกินมา" ที่อ่านไม่เป็นภาษาคน)

    เรื่องช่องว่าง (ถ้อยคำของเจ้าหน้าที่สองครั้งที่ดูเหมือนขัดกัน — ใช้ได้ทั้งคู่):
      - ก.ย. 2569 "ต่างกันแค่ช่องว่าง = ไม่ต้องพูดถึง" เพราะชื่อเรื่องไทยถูกตัดขึ้นบรรทัดใหม่
        2-3 บรรทัด การต่อบรรทัดด้วยช่องว่างทำให้จุดตัดทุกจุดกลายเป็น 'มี " " เกินมา' ที่มองไม่เห็น
        ว่าในเครื่องหมายคำพูดคืออะไร (เล่มจริง ก.ย. 2569) เจ้าหน้าที่ลบทิ้งทั้งหมดก่อนส่ง
      - 6 ต.ค. 2569 "กรณีที่เป็นเว้นวรรคก็ควรบอก" (เล่มจริง ต.ค. 2569 ที่ระบบบอก 'ต่างที่'
        ซึ่งส่วนหนึ่งคือรอยตัดบรรทัดล้วน ๆ ไม่มีช่องว่างจริงในเล่ม)
      จึงแก้ที่ต้นเหตุ: รอยตัดบรรทัดไม่ถูกต่อด้วยช่องว่างอีก (_join_wrapped) แล้วบอกช่องว่างเกินระหว่าง
      อักษรไทยด้วยถ้อยคำที่อ่านออก ("มีช่องว่างเกินระหว่าง "ในระบบ" กับ "บริหาร"") เมื่อผู้เรียก
      ส่ง spaces=True — ช่องว่างล้วน (ข้อความอื่นตรงกันทุกตัว) ยังไม่เป็นจุดผิดเหมือนเดิม
      เพราะ norm() ตัดช่องว่างทิ้งก่อนเทียบ
    """
    return analyze_diff(found, expected, spaces).text


def _case_pairs(found, expected):
    got, want = soft(found).split(), soft(expected).split()
    if len(got) != len(want):
        return []
    # คำที่ต่างกันแค่ชนิดขีดไม่นับ (fold_dashes) — ส่วนที่ต่างตัวพิมพ์ด้วยยังชี้ตามเดิม โดยยกคำตามที่เล่มพิมพ์
    pairs = [(a, b) for a, b in zip(got, want)
             if fold_dashes(a) != fold_dashes(b)
             and fold_dashes(a).casefold() == fold_dashes(b).casefold()]
    # เกินสามคำแปลว่าทั้งประโยคเขียนคนละแบบ (เล่มใช้ Sentence case ทั้งชื่อเรื่อง)
    # ไล่ทีละคำจะยาวจนไม่ช่วยอะไร ให้ดูข้อความที่ถูกต้องในบรรทัดถัดไปแทน
    return pairs if len(pairs) <= 3 else []


def analyze_case_diff(found, expected):
    """ชี้คำที่ต่างกัน "แค่ตัวพิมพ์เล็ก-ใหญ่" — analyze_diff มองไม่เห็นเพราะเทียบแบบตัวใหญ่หมด

    เจ้าหน้าที่ยืนยัน (ก.ย. 2569) ว่าตัวพิมพ์เล็ก-ใหญ่ที่ต่างกันถือว่า "ไม่ตรง" ต้องฟ้อง
    แต่ของเดิมฟ้องแล้วไม่บอกว่าต่างตรงไหน รายงานจึงขึ้นสองบรรทัดที่หน้าตาเหมือนกันเป๊ะ
    (เล่มจริง 6237391 EGCH/M: เล่มพิมพ์ "NI-BASED" ระบบมี "Ni-BASED" ซึ่งเป็นสัญลักษณ์
    ธาตุ) เจ้าหน้าที่กับนักศึกษาต้องไล่สายตาทีละตัวอักษรเอง

    ว่างเมื่อจำนวนคำไม่เท่ากัน หรือไม่มีคำไหนต่างแค่ตัวพิมพ์ — ให้ analyze_diff
    ที่เรียกก่อนหน้าเป็นคนอธิบายแทน
    """
    pairs = _case_pairs(found, expected)
    if not pairs:
        return _NO_DIFF
    # คำนำหน้าเขียนครั้งเดียว แล้วไล่คู่ต่อกันด้วย " และ " เหมือน describe_diff
    # (ซ้ำคำนำหน้าทุกคู่จะอ่านยากเมื่อต่างหลายคำ)
    body = " และ ".join(f'"{a}" ต้องเป็น "{b}"' for a, b in pairs)
    return DiffAnalysis(
        f"ต่างที่ตัวพิมพ์เล็ก-ใหญ่ {body}",
        tuple(f'ให้แก้ตัวพิมพ์เล็ก-ใหญ่ "{a}" เป็น "{b}"' for a, b in pairs))


def analyze_word_diff(found, expected):
    """ชี้คำที่พิมพ์ต่างจากข้อมูลอนุมัติ ทั้งตัวพิมพ์เล็ก-ใหญ่และวรรคตอนในคำ (ขีด/ขีดยาว)

    analyze_diff ไม่สนตัวพิมพ์และวรรคตอน ส่วน analyze_case_diff ดูแค่ตัวพิมพ์ คำที่ต่างทั้งสองอย่าง
    จึงไม่มีใครชี้ — เล่มจริง ต.ค. 2569 พิมพ์คำหนึ่งในชื่อเรื่องเป็นตัวใหญ่ทั้งคำ + ขีดยาว (–) แต่ระบบ
    เป็น "Xx-YYY" (ขีดสั้น) รายงานขึ้นชื่อเรื่องยาวสองก้อนที่ดูเหมือนกันโดยไม่บอกว่าต่างตรงไหน
    ใช้กติกาเดียวกับ analyze_case_diff: จำนวนคำต้องเท่ากัน และต่างไม่เกินสามคำ
    """
    got, want = soft(found).split(), soft(expected).split()
    if len(got) != len(want):
        return _NO_DIFF
    pairs = [(a, b) for a, b in zip(got, want) if fold_dashes(a) != fold_dashes(b)]
    if not pairs or len(pairs) > 3:
        return _NO_DIFF
    body = " และ ".join(f'"{a}" ต้องเป็น "{b}"' for a, b in pairs)
    return DiffAnalysis(f"ต่างที่ {body}",
                        tuple(f'ให้แก้ "{a}" เป็น "{b}"' for a, b in pairs))


def describe_case_diff(found, expected):
    return analyze_case_diff(found, expected).text


class DetailText(str):
    """ข้อความ "ที่พบ" ที่พกวิธีแก้เป็นข้อ ๆ มาด้วย — เป็น str ธรรมดาทุกประการ

    Report.add เก็บ diff_tail (ท่อนจุดต่างท้ายข้อความ) กับ fix_steps ไว้กับข้อนั้น ข้อความสรุป
    จึงถอดท่อนจุดต่างออกจากบรรทัดที่พบ แล้วเขียนวิธีแก้เป็นคำสั่งทีละบรรทัดแทน
    """
    diff_tail = ""
    fix_steps = ()
    # ต่างเฉพาะวรรณยุกต์/สระบน-ล่าง/การันต์/ำ — Report.add ใช้ตัดสินสีเมื่อฟอนต์ของเล่มเพี้ยน
    marks_only = False
    # ข้อชื่อเรื่อง: ประโยคนำของข้อความสรุป (title_mismatch_detail) — ข้ออื่นว่าง
    title_lead = ""

    def __new__(cls, text, diff_tail="", fix_steps=(), marks_only=False):
        obj = super().__new__(cls, text)
        obj.diff_tail = diff_tail
        obj.fix_steps = tuple(fix_steps)
        obj.marks_only = bool(marks_only)
        return obj


def found_with_diff(base, analysis):
    """ข้อความ "ที่พบ" ต่อท้ายด้วยจุดต่าง (ถ้ามี) พร้อมวิธีแก้ที่ข้อความสรุปจะใช้"""
    if not analysis.text:
        return DetailText(base, marks_only=analysis.marks_only)
    return DetailText(f"{base} {analysis.text}", analysis.text, analysis.steps,
                      marks_only=analysis.marks_only)


def _letters_keep_case(text):
    """ตัวอักษรและตัวเลขล้วน โดย "คงตัวพิมพ์เล็ก-ใหญ่ไว้" — norm() แปลงเป็นตัวใหญ่หมด

    คงตัวสะกดไทยครบด้วย (วรรณยุกต์ สระบน-ล่าง การันต์ ำ — ดู spelling_key) เพราะผู้เรียกใช้ตัดสินว่า
    "ต่างเฉพาะวรรคตอน/ช่องว่าง" ซึ่งผ่านได้ (สีเหลือง) — ถ้าตัดตัวเล็กทิ้ง ชื่อปริญญาที่สะกดวรรณยุกต์ผิด
    จะหลุดเป็นเหลืองผ่าน ทั้งที่เป็นสะกดผิด (เจ้าหน้าที่สั่ง 6 ต.ค. 2569 ให้เป็นแดง)
    """
    units = _spelling_units(text, keep_case=True)
    return _UNIT_SEP + _UNIT_SEP.join(units) + _UNIT_SEP if units else ''


def degree_differs_only_in_spacing(expected, page_text, abstract=False):
    """ชื่อปริญญาบนหน้านี้ต่างจากข้อมูลอนุมัติ "เฉพาะวรรคตอน/ช่องว่าง" หรือไม่

    เป็นเงื่อนไขของข้อสังเกตสีเหลือง "ต่างเฉพาะวรรคตอน/ช่องว่าง" ซึ่งผ่านได้ ของเดิมเทียบ
    ด้วย norm() ที่แปลงเป็นตัวใหญ่ทั้งหมด ชื่อปริญญาที่ต่างกันแค่ตัวพิมพ์เล็ก-ใหญ่
    ("MASTER OF SCIENCE" กับ "Master of Science") จึงตกกิ่งนี้ ได้สีเหลืองพร้อมคำอธิบาย
    ที่ผิด แล้วเล่มผ่านได้ — ตัวพิมพ์ผิดต้องเป็นแดงเหมือนเดิม (ดู mismatch_detail)

    ค้นเฉพาะส่วนที่บรรทัดชื่อปริญญาอยู่ได้ (degree_line_search_text) — เล่มจริง ก.ย. 2569 พิมพ์
    "MASTER OF CLINICAL TROPICAL MEDICINE" แทนตัวย่อ "M.C.T.M." แต่ค้นทั้งหน้าแล้วไปเจอ "MCTM"
    ในรหัสนักศึกษา "6838776 TMCT/M" จึงตอบว่าต่างแค่วรรคตอน ได้สีเหลืองผ่านทั้งที่ผิดคนละคำ
    """
    want = _letters_keep_case(expected)
    return bool(want) and want in _letters_keep_case(degree_line_search_text(page_text, abstract))


def mismatch_detail(label, compared, expected=''):
    """Make small differences visible instead of silently accepting fuzzy matches.

    ถ้าส่ง expected มาด้วย จะต่อท้ายว่า "ต่างที่ ..." ชี้ตำแหน่ง/วิธีที่ผิด และเก็บวิธีแก้เป็นข้อ ๆ
    ไว้ให้ข้อความสรุป (DetailText)
    """
    # เขียนให้เหมือนคนพูด: บอกว่า "ในเล่มเขียนว่าอะไร" ก่อน แล้วค่อยบอกว่าต่างยังไง
    # (ของเดิมขึ้นต้นด้วยคำตัดสินแบบระบบ เช่น "ชื่อบทข้อความไม่ตรง:" และมีคะแนน
    #  ความใกล้เคียงซึ่งเจ้าหน้าที่เอาไปใช้อะไรไม่ได้)
    detail = f'{label}ในเล่มเขียนว่า "{compared["actual"]}"'
    # ชี้จุดต่างเฉพาะเมื่อใกล้เคียงกัน (typo/ตัวพิมพ์) — ถ้าเป็นคนละข้อความ
    # (mismatch) การไล่ทีละตัวอักษรจะรกและสับสน ให้ดูข้อความที่ถูกต้องแทน
    analysis = analyze_diff(compared['actual'], expected) \
        if expected and compared['status'] in ('typo', 'case') else _NO_DIFF
    # ถ้าชี้จุดต่างได้แล้ว ไม่ต้องบอกซ้ำว่า "พิมพ์ผิดเล็กน้อย" — จุดต่างบอกอยู่ในตัว
    if analysis.text:
        return found_with_diff(detail, analysis)
    if compared['status'] == 'case':
        detail += ' ต่างกันแค่ตัวพิมพ์เล็ก-ใหญ่'
    elif compared['status'] == 'typo':
        detail += ' พิมพ์ผิดเล็กน้อย'
    return DetailText(detail, marks_only=compared.get('marks_only', False))


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
            window = _join_wrapped(lines[start:start + span])
            if title_script(window) != want:
                continue
            ratio = difflib.SequenceMatcher(None, target, norm(window)).ratio()
            if ratio > best_ratio:
                best, best_ratio = window, ratio
    return best if best_ratio >= min_ratio else ""


def _loose_keys(*phrases):
    """ข้อความ template ภาษาไทยในรูปที่ใช้ "หา" — norm() ตัดวรรณยุกต์/สระบน-ล่าง ช่องว่างทิ้ง (ดู _starts_loosely)"""
    return tuple(norm(phrase) for phrase in phrases)


def _starts_loosely(line, keys):
    """บรรทัดขึ้นต้นด้วยข้อความ template ใน keys โดยไม่สนวรรณยุกต์ สระบน-ล่าง และช่องว่าง

    ใช้ "หา" ขอบเขตของบล็อกบนหน้า ไม่ใช่ตัดสินว่าสะกดถูก (ตัดสินด้วย spelled_in ที่อื่น) — ฟอนต์บางตัวทำ
    วรรณยุกต์หายจริง เล่มจริง ต.ค. 2569 อ่าน "ได้รับการพิจารณา" ได้ "ไดรับการพิจารณา" ตัวหาที่เทียบตรงตัวจึงไม่เจอ
    จุดจบของชื่อเรื่องบนหน้าลงนาม แล้วกินประโยค template ชื่อปริญญา วันที่ และหัวข้อกรรมการไปเป็นชื่อเรื่องด้วย
    กลายเป็นข้อแดง "ชื่อเรื่องไม่ตรง" ทั้งที่เล่มพิมพ์ชื่อเรื่องถูก
    """
    head = norm(line)
    return any(head.startswith(key) for key in keys)


# คำนำหน้าบล็อกชื่อเรื่องบนหน้าลงนาม — ชื่อเรื่องเริ่มบรรทัดถัดจากนี้ (ฝั่งไทยหาแบบไม่สนวรรณยุกต์ ดู _starts_loosely)
_TITLE_LEAD_IN = re.compile(r'^entitled$', re.I)
_TITLE_LEAD_IN_TH = _loose_keys('เรื่อง')
# บรรทัดที่บอกว่าบล็อกชื่อเรื่องจบแล้ว (ข้อความ template ที่ตามหลังชื่อเรื่องเสมอ)
_TITLE_STOP = re.compile(
    r'^(?:was\s+submitted\s+to'
    r'|A\s+(?:THESIS|THEMATIC\s+PAPER|DISSERTATION|MASTER|DOCTOR)'
    r'|ABSTRACT|FACULTY\s+OF)', re.I)
_TITLE_STOP_TH = _loose_keys('ได้รับการพิจารณา', 'วิทยานิพนธ์นี้เป็นส่วนหนึ่ง', 'สารนิพนธ์นี้เป็นส่วนหนึ่ง',
                             'บทคัดย่อ', 'บัณฑิตวิทยาลัย')


def printed_title(page_text, student_name="", title=""):
    """ชื่อเรื่อง "ตามที่พิมพ์จริง" บนหน้านั้น รวมบรรทัดที่ห่อคำมาให้ครบ

    ต้องหาจากโครงสร้างของหน้า ไม่ใช่หาช่วงที่ "ใกล้เคียงข้อมูลอนุมัติที่สุด" เพราะถ้า
    ชื่อในเล่มกับในระบบเป็นคนละเรื่องกันจริง ๆ การหาช่วงที่ใกล้เคียงจะได้เศษข้อความมั่ว
    เล่มจริงเคยได้บรรทัดเนื้อความบทคัดย่อ ("suitable for future implementation of
    Robotic Process Automation (RPA), and to establish a") มาอ้างว่าเป็นชื่อเรื่อง

    ขอบเขต: เริ่มหลังคำว่า "entitled"/"เรื่อง" (หน้าลงนาม) หรือบรรทัดแรกที่ยาวพอ
    (หน้าปก/หน้าบทคัดย่อ) จบเมื่อเจอข้อความ template ที่ตามหลังชื่อเรื่อง ชื่อนักศึกษา
    หรือบรรทัดชื่อ-รหัสนักศึกษา

    หน้าปก: template เรียง ชื่อเรื่อง → ชื่อนักศึกษา → "A THESIS SUBMITTED ..." /
    "วิทยานิพนธ์นี้เป็นส่วนหนึ่ง..." ตายตัว บรรทัดก่อนประโยคนั้นจึงเป็น "ช่องชื่อ" เสมอ ไม่ใช่ชื่อเรื่อง
    ไม่ว่าชื่อจะสะกดอย่างไร (เจ้าหน้าที่สั่ง 19 ต.ค. 2569 "ถ้าหัวข้อสะกดยังไงในเล่มไม่ตรงก็แค่แจ้ง
    อย่าไปเอาชื่อมารวม" — เดิมหยุดเฉพาะชื่อที่สะกดตรงข้อมูลอนุมัติ ชื่อที่สะกดผิดจึงถูกรวมเข้ามา)
    ยกเว้นเล่มที่ไม่ได้พิมพ์ชื่อไว้เลย ซึ่งบรรทัดในช่องนั้นเป็นท่อนหนึ่งของชื่อเรื่อง (title = ข้อมูลอนุมัติ)
    """
    lines = [soft(line) for line in (page_text or '').splitlines() if soft(line)]
    start = None
    for i, line in enumerate(lines):
        if _TITLE_LEAD_IN.match(line.strip()) or norm(line) in _TITLE_LEAD_IN_TH:
            start = i + 1
            break
    if start is None:
        start = next((i for i, line in enumerate(lines)
                      if len(norm(line)) >= 8 and not _ABS_RUNNING_HEAD.search(line)), None)
    if start is None:
        return ""
    want_name = norm(_strip_student_title(student_name)) if student_name else ""
    name_slot = _cover_name_slot(lines)
    out = []
    for i in range(start, min(start + 6, len(lines))):
        line = lines[i]
        if (_TITLE_STOP.match(line.strip()) or _starts_loosely(line, _TITLE_STOP_TH)
                or _ABS_STUDENT_LINE.search(line)):
            break
        if want_name and want_name in norm(line):
            break
        if i == name_slot and not (title and norm(line) and norm(line) in norm(title)):
            break
        out.append(line)
    return _join_wrapped(out).strip()


# ข้อความ template ที่พิมพ์ต่อจากชื่อนักศึกษาบนหน้าปกเสมอ ทุกภาษาและทุกประเภทเล่ม
# ใช้ชี้ "ช่องของชื่อ" บนหน้าปก ซึ่งไม่มีรหัสนักศึกษาให้ยึดแบบหน้าบทคัดย่อ
#
# ไล่ดูจาก template ครบทั้ง 18 ใบ (นานาชาติ / เล่มอังกฤษของหลักสูตรไทย / เล่มไทย
# คูณสามประเภทเล่ม คูณสองรูปแบบ) ได้ถ้อยคำ 7 แบบ — เล่มการค้นคว้าอิสระภาษาอังกฤษ
# ใช้ "AN INDEPENDENT STUDY" ไม่ใช่ "A ..." จึงต้องรับ "AN" ด้วย
_COVER_NAME_STOP = re.compile(
    r'^AN?\s+(?:THESIS|THEMATIC\s+PAPER|DISSERTATION|INDEPENDENT\s+STUDY)\s+SUBMITTED', re.I)
_COVER_NAME_STOP_TH = _loose_keys('วิทยานิพนธ์นี้เป็นส่วนหนึ่ง', 'สารนิพนธ์นี้เป็นส่วนหนึ่ง',
                                  'การค้นคว้าอิสระนี้เป็นส่วนหนึ่ง')


def _cover_name_slot(lines):
    """ดัชนีบรรทัด "ช่องชื่อนักศึกษา" บนหน้าปก (บรรทัดก่อน "A THESIS SUBMITTED ...") — None ถ้าไม่ใช่หน้าปก"""
    stop = next((i for i, line in enumerate(lines)
                 if _COVER_NAME_STOP.match(line.strip())
                 or _starts_loosely(line, _COVER_NAME_STOP_TH)), 0)
    return stop - 1 if stop >= 1 else None


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
                 if _COVER_NAME_STOP.match(line.strip())
                 or _starts_loosely(line, _COVER_NAME_STOP_TH)), 0)
    if stop < 1:
        return ""
    candidate = lines[stop - 1]
    # เล่มที่ไม่ได้พิมพ์ชื่อไว้เลย จะเหลือบรรทัดสุดท้ายของชื่อเรื่องยืนอยู่ตรงช่องนั้น
    # อย่ารายงานเศษชื่อเรื่องว่าเป็นชื่อนักศึกษา ให้ถอยไปใช้วิธีเดิมแทน
    if title and norm(candidate) and norm(candidate) in norm(title):
        return ""
    return candidate


# ป้ายบทบาทที่ template พิมพ์ไว้ "ใต้ชื่อนักศึกษา" บนหน้าลงนามเสมอ
_SIGNATURE_CANDIDATE_LABEL = re.compile(r'^Candidate', re.I)
_SIGNATURE_CANDIDATE_LABEL_TH = _loose_keys('ผู้วิจัย', 'ผู้เขียน')


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
        if i and (_SIGNATURE_CANDIDATE_LABEL.match(line.strip())
                  or _starts_loosely(line, _SIGNATURE_CANDIDATE_LABEL_TH)):
            return _strip_student_title(lines[i - 1].split(",")[0].strip())
    return ""


def _title_as_printed(compared, page_text, expected, student_name=""):
    """แทน "ช่วงที่ใกล้เคียงที่สุด" ด้วยชื่อเรื่องตามที่พิมพ์จริง เมื่อหาบล็อกชื่อเรื่องเจอ

    ใช้เฉพาะตอนที่ยังไม่ตรง — ถ้าตรงอยู่แล้วไม่ต้องแตะ
    ถ้าหาบล็อกไม่เจอ คงค่าเดิมไว้ ดีกว่าไม่มีอะไรให้เจ้าหน้าที่ดูเลย
    """
    printed = printed_title(page_text, student_name, expected)
    if not printed:
        return compared
    return compare_values(printed, expected, 'title')


def title_mismatch_detail(label, compared, expected=''):
    """ข้อความชื่อเรื่องที่ไม่ตรงข้อมูลในระบบ — บอกกลาง ๆ ว่า "ไม่ตรงกับข้อมูลในระบบ"

    ชื่อเรื่องที่เก็บใน eThesis เป็นตัวพิมพ์ใหญ่ทั้งหมด แต่ในเล่มอาจใช้ Sentence case
    ซึ่งไม่ควรตีความว่าเป็น "ตัวพิมพ์เล็ก-ใหญ่ผิด" — ประเด็นคือข้อความไม่ตรงกับที่
    อนุมัติในระบบเฉย ๆ จึงบอกกลาง ๆ แล้วชี้จุดต่างเฉพาะเมื่อใกล้เคียงกันพอ (typo)

    ชื่อเรื่องคือข้อความเดียวที่ถูกตัดขึ้นบรรทัดใหม่หลายบรรทัดและผู้ใช้เห็นว่าช่องว่างต่างกันตรงไหน
    จึงเปิด spaces=True ให้บอกช่องว่างเกินด้วย (รอยตัดบรรทัดไม่ถูกต่อด้วยช่องว่าง ดู _join_wrapped)
    """
    detail = f'{label}ไม่ตรงกับข้อมูลในระบบ: "{compared["actual"]}"'
    if expected and compared['status'] in ('typo', 'case'):
        analysis = analyze_diff(compared['actual'], expected, spaces=True)
        # ต่างกันแค่ตัวพิมพ์เล็ก-ใหญ่ analyze_diff คืนว่าง (มันเทียบแบบตัวใหญ่หมด)
        # ถ้าปล่อยไว้ รายงานจะขึ้นสองบรรทัดที่หน้าตาเหมือนกันเป๊ะโดยไม่บอกว่าต่างตรงไหน
        if not analysis.text and compared['status'] == 'case':
            analysis = analyze_case_diff(compared['actual'], expected)
        if not analysis.text:
            analysis = analyze_word_diff(compared['actual'], expected)
        found = found_with_diff(detail, analysis)
    else:
        found = DetailText(detail, marks_only=compared.get('marks_only', False))
    # ข้อความสรุปไม่ยกชื่อเรื่องเต็มที่ผิด (นักศึกษามีอยู่ในเล่มแล้ว) — ขึ้นด้วยประโยคนี้ต่อด้วยจุดที่ต้องแก้
    # แล้วตามด้วยชื่อเรื่องที่ถูกต้อง (อัปเดต 1.7 เจ้าของยืนยัน 27 ต.ค. 2569 ดู _title_summary_lines)
    found.title_lead = f'{label}ไม่ตรงกับข้อมูลในระบบ'
    return found


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


def bold_is_undetectable(pdf_page):
    """หน้านี้บอก "ตัวหนา" จากชื่อฟอนต์ไม่ได้เลยหรือไม่

    ถ้าบอกไม่ได้ ห้ามสรุปว่า "ไม่เป็นตัวหนา" — เล่มจริงที่หัวข้อสารบัญหนาครบทุกหัวข้อ
    เคยถูกฟ้องว่า "หัวข้อหลักในสารบัญไม่เป็นตัวหนา 13 หัวข้อ" ด้วยเหตุนี้

    **ไม่มีฟอนต์ไหนในหน้าที่ชื่อบอกว่าหนาเลย = บอกไม่ได้** เพราะยังมีอีกสองทางที่ทำให้
    ข้อความหนาโดยไม่มีฟอนต์ตัวหนาในไฟล์
      - ชื่อฟอนต์ย่อยแบบไม่มีความหมาย ("CIDFont+F1", "F2") ที่โปรแกรมแปลง PDF ตั้งไล่
        ตามลำดับที่พบในหน้านั้น ๆ เล่มจริงเจอ CIDFont+F1 บนหน้าสารบัญเป็นตัวหนา แต่
        CIDFont+F1 บนหน้าเนื้อหาเป็นตัวธรรมดา คือชื่อเดียวกันคนละฟอนต์
      - **ตัวหนาเทียม (faux bold)** — โปรแกรมทำเล่มวาดตัวอักษรเดิมซ้ำอีกรอบเป็นเส้นขอบ
        (text render mode 2) แทนการใช้ฟอนต์ตัวหนา ในไฟล์จึงมีแต่ฟอนต์ธรรมดาชื่อเดียว
        เล่มจริง 6736605 NSCN/M ทำแบบนี้ทั้งเล่ม (THSarabunNew ตัวเดียว หน้าสารบัญมีการ
        วาดเส้นทับ 104 จุด) หัวข้อในเล่มหนาครบ แต่ระบบฟ้องว่า "ไม่เป็นตัวหนา 11 หัวข้อ"
        เจ้าหน้าที่ต้องกดผ่านเอง — pdfplumber ไม่ได้ส่ง text render mode ออกมา จึงดูไม่ได้
        ว่าจุดไหนวาดเส้นทับ เหลือทางเดียวคือไม่ฟันธง แล้วให้เจ้าหน้าที่ดูด้วยตา

    วัดกับเล่มจริง 9 เล่ม: 8 เล่มมีฟอนต์ที่ชื่อบอกว่าหนาบนหน้าสารบัญ (ตรวจต่อตามปกติ)
    มีเล่ม 6736605 เล่มเดียวที่ไม่มี ซึ่งเป็นเล่มที่ทำตัวหนาเทียม
    """
    names = {(c.get('fontname') or '') for c in (pdf_page.chars or [])
             if (c.get('text') or '').strip()}
    if not names:
        return False
    return not any(_is_bold_font(name) for name in names)


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
    page_chars = _thai_chars(getattr(pdf_page, 'chars', None) or [],
                             _fonts_missing_sara_aa(pdf_page))

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
    # บอกแค่ว่าไม่ชิดซ้าย ไม่ยกบรรทัดชื่อเรื่องมาทั้งหมด (เจ้าหน้าที่สั่ง ก.ย. 2569 "แจ้งแค่ว่า
    # หัวข้อไม่ได้ชิดซ้ายก็พอ ไม่ต้องเอาชื่อหัวข้อมาใส่") — ชื่อเรื่องยาวสามบรรทัดทำให้ข้อความ
    # สรุปยาวโดยไม่ช่วยอะไร ตำแหน่ง (หน้าบทคัดย่อ) บอกอยู่แล้วว่าต้องไปแก้ที่ไหน
    rep.add("ORANGE", "front_matter", loc,
            "ชื่อเรื่องบนหน้าบทคัดย่อไม่ได้จัดชิดซ้าย",
            "ชื่อเรื่องบนหน้าบทคัดย่อต้องจัดชิดซ้าย ไม่ใช่กึ่งกลางหรือชิดขวา",
            "แก้การจัดวางชื่อเรื่องบนหน้าบทคัดย่อให้ชิดซ้าย",
            "FORMAT.ABSTRACT_LAYOUT")


def _report_abstract_title_dots(rep, lines, loc):
    """ชื่อเรื่องบนหน้าบทคัดย่อต้องไม่มีเส้นประ/จุดไข่ปลาต่อท้าย

    เจ้าหน้าที่ (ต.ค. 2569): เล่มที่มีเส้นประ "………" ใต้ชื่อเรื่องบนบทคัดย่อ "ต้องผิดที่
    ชื่อหัวข้อมี ....." — เดิมเศษนี้ทำให้ระบบหยุดค้นก่อนถึงบรรทัด "ส.ม." แล้วฟ้องว่าไม่พบ
    ชื่อปริญญาแบบย่อทั้งที่มีอยู่ ดูเฉพาะส่วนก่อนบรรทัดชื่อ-รหัสนักศึกษา (บล็อกชื่อเรื่อง)
    """
    stop = next((i for i, l in enumerate(lines)
                 if _ABS_STUDENT_LINE.search(l['text'])), None)
    if stop is None:
        return
    if not any(_DOT_LEADER_LINE.match(soft(l['text']))
               or re.search(r'[…]{3,}|\.{6,}\s*$', l['text'])
               for l in lines[:stop]):
        return
    rep.add("RED", "front_matter", loc,
            "ชื่อเรื่องบนหน้าบทคัดย่อมีเส้นประหรือจุดไข่ปลา (.....) ต่อท้าย",
            "ชื่อเรื่องบนหน้าบทคัดย่อต้องมีเฉพาะชื่อเรื่อง ไม่มีเส้นประหรือจุดไข่ปลา",
            "ลบเส้นประหรือจุดไข่ปลาที่อยู่ใต้ชื่อเรื่องออก",
            "FRONT.ABSTRACT")


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


_TOC_RANGE_TAIL = re.compile(rf'\s({_TOC_PAGE_TOKEN})\s*[-–—]\s*({_TOC_PAGE_TOKEN})\s*$', re.I)


def toc_entry_page_range(line, n_pages):
    """หัวข้อในสารบัญที่ระบุเลขหน้าเป็นช่วง ("12-15" "12–15" "ก-ข" "xi-xii") → (หน้าแรก, หน้าสุดท้าย) หรือ None

    อัปเดต 1.7 (เจ้าของยืนยัน 27 ต.ค. 2569): เลขหน้าของหัวข้อต้องเป็นหน้าแรกของหัวข้อนั้นเท่านั้น
    นับเป็นช่วงเลขหน้าเฉพาะเมื่อสองข้างเป็นเลขหน้าชนิดเดียวกันและเรียงจากน้อยไปมาก และเลขอารบิกต้องไม่เกินจำนวน
    แผ่นของไฟล์ — ชื่อหัวข้อที่ห่อบรรทัดแล้วจบด้วยช่วงปี ("ปี พ.ศ. 2560-2565") จึงไม่ถูกนับ
    (_toc_page_label ยังอ่านช่วงเลขหน้าแบบเดิม คือยึดหน้าแรก)
    """
    match = _TOC_RANGE_TAIL.search(space_dot_leader(line))
    if not match:
        return None
    first, last = match.group(1), match.group(2)
    (style, low), (style2, high) = [_page_label_order(str(int(t)) if t.isdigit() else t.lower())
                                    for t in (first, last)]
    if style is None or style != style2 or low >= high:
        return None
    if style == "arabic" and high > n_pages:
        return None
    return first, last


def _report_toc_page_ranges(rep, sources, pages, page_ref, n_pages):
    """สารบัญ สารบัญตาราง สารบัญรูปภาพ ที่ระบุเลขหน้าของหัวข้อเป็นช่วง — ส้มข้อเดียว ระบุหัวข้อที่พบ (อัปเดต 1.7)

    sources = [(ชื่อส่วน, ดัชนีหน้า)] · กดไม่ผ่าน = "ให้ระบุเฉพาะเลขหน้าแรกของหัวข้อนั้น ๆ" (fail_text)
    """
    found, where = [], {}
    for name, idx in sources:
        for line in (pages[idx] if idx < len(pages) else "").split("\n"):
            if toc_entry_page_range(line, n_pages):
                found.append(space_dot_leader(line).strip())
                where.setdefault(name, idx)
    if not found:
        return
    shown = ", ".join(f'"{entry}"' for entry in found[:5])
    more = f" และอีก {len(found) - 5} หัวข้อ" if len(found) > 5 else ""
    location = " และ".join(f"{name} ({page_ref(idx)})" for name, idx in where.items())
    first_only = "ให้ระบุเฉพาะเลขหน้าแรกของหัวข้อนั้น ๆ"
    rep.add(TOC_RANGE_ZONE, "front_matter", location,
            f"หัวข้อในสารบัญระบุเลขหน้าเป็นช่วง: {shown}{more}",
            "เลขหน้าของหัวข้อในสารบัญต้องเป็นหน้าแรกของหัวข้อนั้นเท่านั้น",
            first_only, "FRONT.TOC_PAGE_RANGE", fail_text=first_only)


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


# ชื่ออังกฤษต่อท้ายชื่อส่วนในข้อ "เล่มมี... แต่ไม่ปรากฏในสารบัญ" — ถ้อยคำเดิมของข้อภาคผนวก
# มี "(APPENDIX)" อยู่แล้ว นักศึกษาเล่มอังกฤษจะรู้ว่าต้องเพิ่มหัวข้อคำไหน
_TOC_SECTION_GLOSS = {"appendix": " (APPENDIX)", "biography": " (BIOGRAPHY)"}


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
    if normalized in N_BIO or normalized == N_BIO_SHORT:
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
# หัวข้อ "ประวัติ" เฉย ๆ — เล่มจริง 6538041 SHPP/D ตั้งหัวข้อหน้าสุดท้ายแบบนี้ ระบบเลยฟ้องผิดว่า
# "ไม่พบประวัติผู้วิจัย" (เจ้าหน้าที่: "ประวัติผู้วิจัย ... ในส่วนท้ายมีนะ") คำเดียวนี้อาจเป็นหัวข้อ
# ในเนื้อหาได้ หน้าเล่มจึงต้องมีช่องข้อมูลของประวัติด้วย (looks_like_biography) ส่วนในสารบัญ
# บรรทัด "ประวัติ <เลขหน้า>" คือรายการของหน้านี้แน่นอน
N_BIO_SHORT = norm('ประวัติ')
# ช่องข้อมูลที่หน้าประวัติผู้วิจัยของ template มีทุกเล่ม (วัดจากเล่มจริงไทย 2 เล่ม อังกฤษ 4 เล่ม)
_BIO_FIELDS = (norm('ชื่อ-สกุล'), norm('วัน เดือน ปีเกิด'), norm('สถานที่เกิด'),
               norm('ประวัติการศึกษา'), norm('วุฒิการศึกษา'),
               'DATEOFBIRTH', 'PLACEOFBIRTH', 'INSTITUTIONSATTENDED')


def looks_like_biography(page_text):
    """หน้านี้มีช่องข้อมูลของประวัติผู้วิจัยอย่างน้อยสองช่องไหม — ดูจากสิ่งที่ควรอยู่ในหน้า

    ชื่อ-สกุล / วัน เดือน ปีเกิด / สถานที่เกิด / ประวัติการศึกษา (อังกฤษ: DATE OF BIRTH /
    PLACE OF BIRTH / INSTITUTIONS ATTENDED) เทียบด้วย norm จึงรับ "ชื่อ – สกุล" และ
    "ประวิติการศึกษา" (สะกดผิดในเล่มจริง) ได้
    """
    text = norm(page_text)
    return sum(1 for field in _BIO_FIELDS if field in text) >= 2


def expected_biography_heading(program_language, printed=""):
    """หัวข้อหน้าประวัติผู้วิจัยที่ถูกต้อง: เล่มไทย "ประวัติผู้วิจัย" เล่มอังกฤษ "BIOGRAPHY"

    ประกาศฯ ข้อ 4.1.3.3 หน้า 6 และ 4.2.3.3 หน้า 7 เรียกส่วนนี้ว่า "ประวัติผู้วิจัย (Biography)"
    คู่มือใช้ "ประวัติผู้วิจัย" ในตัวอย่างเล่มไทยทุกหน้า และ "BIOGRAPHY" ในเล่มอังกฤษทุกหน้า
    ไทย-อังกฤษทำเล่มเป็นภาษาอังกฤษ จึงใช้ BIOGRAPHY เหมือนนานาชาติ
    ไม่รู้ภาษาเล่ม (ไม่มีข้อมูลอนุมัติ หรือไฟล์ eThesis เป็นของคนอื่น) ยึดอักษรของหัวข้อที่พิมพ์
    """
    if program_language == "thai":
        return "ประวัติผู้วิจัย"
    if program_language in ("international", "thai_english"):
        return "BIOGRAPHY"
    return "ประวัติผู้วิจัย" if re.search(r"[ก-๙]", printed or "") else "BIOGRAPHY"


def _report_biography_heading(rep, printed, want, loc):
    """หัวข้อหน้าประวัติผู้วิจัยต้องตรงตามประกาศฯ — เจ้าหน้าที่สั่ง (ก.ย. 2569)

    "หน้าประวัติเล่มนี้เขียนหัวข้อแค่ "ประวัติ" ไม่ใช่ "ประวัติผู้วิจัย" หัวข้อประวัติผู้วิจัย
    ก็ควรต้องถูก" (เล่มจริง 6538041 SHPP/D) — ระบบหาหน้านี้เจอได้จากหัวข้อ "ประวัติ" /
    "ประวัติผู้เขียน" ด้วย (is_biography_heading) แต่หาเจอไม่ได้แปลว่าหัวข้อถูก
    ไม่ฟ้องเรื่องตัวพิมพ์ ("Biography" กับ "BIOGRAPHY") หรือช่องว่าง แต่ตัวสะกดไทยต้องครบทุกตัวรวมวรรณยุกต์/สระ
    (same_spelling — เจ้าหน้าที่สั่ง 6 ต.ค. 2569) สีแดงแบบเดียวกับหัวข้อหน้าสารบัญที่ไม่ใช่ TABLE OF CONTENTS
    """
    if not printed or same_spelling(printed, want):
        return
    rep.add("RED", "end_matter", loc,
            DetailText(f'หัวข้อหน้าประวัติผู้วิจัยเขียนว่า "{soft(printed)}"',
                       marks_only=marks_only_difference(printed, want)),
            f'ต้องแก้เป็น "{want}"', "", "END.STRUCTURE")


def _report_toc_biography_heading(rep, raw, want, loc):
    """บรรทัดประวัติผู้วิจัยในสารบัญต้องใช้หัวข้อเดียวกับที่ประกาศฯ กำหนด

    สารบัญนับ "ประวัติ <เลขหน้า>" เป็นรายการประวัติผู้วิจัยได้ (ไม่ฟ้องว่าไม่มี) แต่หัวข้อยังผิด
    ถ้าไม่ฟ้องตรงนี้ นักศึกษาแก้หัวข้อในหน้าจริงแล้วสารบัญยังเขียนว่า "ประวัติ" อยู่
    """
    head = soft(_strip_toc_page_number(raw or ""))
    if not head or same_spelling(head, want):
        return
    rep.add("RED", "front_matter", loc,
            DetailText(f'สารบัญเขียนหัวข้อนี้ว่า "{head}"', marks_only=marks_only_difference(head, want)),
            f'หัวข้อในสารบัญต้องเป็น "{want}"',
            f'แก้หัวข้อในสารบัญเป็น "{want}"', "FRONT.TOC_CONTENT")


def is_biography_heading(line, page_text):
    """บรรทัดหัวหน้านี้คือหัวข้อประวัติผู้วิจัยไหม — "ประวัติ" เฉย ๆ ต้องมีช่องข้อมูลประวัติในหน้าด้วย"""
    nl = norm(line)
    return nl in N_BIO or (nl == N_BIO_SHORT and looks_like_biography(page_text))

# คำเรียกส่วนอ้างอิง — ต้องเลือกใช้ "คำเดียว" และสารบัญต้องใช้คำเดียวกับหน้าจริง
_REF_TERM_GROUPS = (
    ("REFERENCES", ("REFERENCES", "REFERENCE")),
    ("BIBLIOGRAPHY", ("BIBLIOGRAPHY",)),
    ("รายการอ้างอิง", (norm("รายการอ้างอิง"),)),
    ("บรรณานุกรม", (norm("บรรณานุกรม"),)),
)


def toc_major_headings_sentence(program_language):
    """ชื่อหัวข้อระดับหลักในสารบัญที่ต้องเป็นตัวหนา เขียนด้วยภาษาของเล่ม

    เจ้าหน้าที่สั่ง (ก.ย. 2569) "เล่มไทยควรใช้คำไทย" — เล่มไทยไม่มีหัวข้อชื่อ ACKNOWLEDGEMENTS
    หรือ BIOGRAPHY อยู่จริง บรรทัด "ควรเป็น" ที่ยกชื่ออังกฤษมาจึงบอกไม่ได้ว่าต้องทำอะไร
    """
    if program_language == "thai":
        return ("กิตติกรรมประกาศ, บทคัดย่อ, สารบัญตาราง/สารบัญรูปภาพ, ชื่อบท, "
                "รายการอ้างอิง และ ประวัติผู้วิจัย ต้องเป็นตัวหนา")
    return ("ACKNOWLEDGEMENTS, ABSTRACT, LIST OF ..., ชื่อบท, REFERENCE(S) และ BIOGRAPHY "
            "ต้องเป็นตัวหนา")


def reference_term_choice(program_language, printed=""):
    """สองคำที่เล่มนี้ต้องเลือกใช้คำใดคำหนึ่ง — เล่มไทย "รายการอ้างอิง"/"บรรณานุกรม"
    เล่มอังกฤษ REFERENCES/BIBLIOGRAPHY (เกณฑ์เดียวกับ expected_biography_heading)

    เจ้าหน้าที่สั่ง (ก.ย. 2569) — เล่มจริง 6736605 NSCN/M เป็นเล่มไทย พิมพ์หัวข้อว่า
    "รายการอ้างอิง / บรรณานุกรม" ระบบบอกให้ "เลือกคำเดียว: REFERENCES หรือ BIBLIOGRAPHY"
    เจ้าหน้าที่แก้เป็น "รายการอ้างอิง หรือ บรรณานุกรม" ก่อนส่งให้นักศึกษา เพราะเล่มไทย
    ใช้หัวข้อภาษาอังกฤษไม่ได้ คำที่สั่งให้เลือกต้องเป็นคำที่เล่มนั้นใช้ได้จริง
    ไม่รู้ภาษาเล่ม (ไม่มีข้อมูลอนุมัติ หรือไฟล์ eThesis เป็นของคนอื่น) ยึดอักษรของหัวข้อที่พิมพ์
    """
    if program_language == "thai":
        return "รายการอ้างอิง", "บรรณานุกรม"
    if program_language in ("international", "thai_english"):
        return "REFERENCES", "BIBLIOGRAPHY"
    if re.search(r"[ก-๙]", printed or ""):
        return "รายการอ้างอิง", "บรรณานุกรม"
    return "REFERENCES", "BIBLIOGRAPHY"


def reference_terms(heading):
    """คืนรายชื่อคำเรียกส่วนอ้างอิงที่ปรากฏในหัวข้อ (ตัดเลขหน้า/จุดไข่ปลาออกก่อน)

    ถ้าคืนมากกว่า 1 คำ แปลว่าเลือกหลายคำ (เช่น "REFERENCES/BIBLIOGRAPHY") ซึ่งผิด
    ใช้เทียบว่าคำในสารบัญตรงกับหัวข้อในหน้าจริงหรือไม่ด้วย
    """
    nl = norm(_strip_toc_page_number(heading))
    return [label for label, keys in _REF_TERM_GROUPS if any(k in nl for k in keys)]
N_APPENDIX = ['APPENDIX', 'APPENDICES', norm('ภาคผนวก')]

# ขั้นตอนที่นักศึกษาต้องทำ ถ้าอยากคงโครงบทที่ไม่เป็นไปตามประกาศไว้ (เจ้าหน้าที่กำหนด
# ถ้อยคำเอง ก.ย. 2569) — ไม่ใช่ "วิธีแก้เล่ม" แต่เป็นเรื่องที่ต้องไปยื่นขออนุมัติต่างหาก
# จึงเขียนเป็น notice ต่อท้ายข้อ ไม่ใช่ fix (ซึ่งเป็นคำแนะนำสั้น ๆ บนการ์ด)
CHAPTER_COUNT_NOTICE = "\n".join([
    "หากประสงค์จะคงเนื้อหาที่ไม่เป็นไปตามประกาศ กรุณาติดต่อเจ้าหน้าที่หลักสูตร "
    "และกรอกแบบฟอร์มตามลิงก์ด้านล่าง แล้วส่งผ่านระบบ e-document "
    "มาเพื่อประกอบการพิจารณาโดยด่วน",
    "https://drive.google.com/drive/folders/1oa3oB6WfJlElB8s5QyEWsp9d2H_m7jqy",
    "ทั้งนี้ให้ชี้แจงเหตุผลและความจำเป็น พร้อมระบุชื่อบทและจำนวนบทที่ประสงค์จะคงไว้ในเอกสารด้วย",
])


def chapter_count_found(found, low, high):
    """บรรทัด "พบกี่บท" ที่บอกด้วยว่าต่างจากประกาศกี่บท

    ของเดิมเขียนแค่ "พบ 5 บท" แล้วให้ไปอ่านบรรทัดถัดไปเอง เจ้าหน้าที่สั่ง (ก.ย. 2569)
    ให้บอกให้ชัดว่า "มันผิดยังไง" — ขาดไปกี่บท หรือเกินมากี่บท
    """
    if found < low:
        return f"พบ {found} บท น้อยกว่าที่ประกาศกำหนด {low - found} บท"
    if found > high:
        return f"พบ {found} บท มากกว่าที่ประกาศกำหนด {found - high} บท"
    return f"พบ {found} บท"


def chapter_count_expected(found, canon, thai_book):
    """บอกว่าบทที่ยังไม่มีคือบทไหน (ชื่อตามประกาศ) ไม่ใช่บอกแค่จำนวนที่ต้องมี

    บทที่ขาดคือบทท้าย ๆ เสมอ เพราะการเทียบชื่อบทยึด "ลำดับที่" เป็นหลัก (บทที่ 1 ของ
    เล่มเทียบกับบทที่ 1 ของประกาศ) เล่มที่มี 5 บทจึงขาดบทที่ 6 ไม่ใช่ขาดบทกลาง ๆ
    ชื่อบทใส่ในเครื่องหมายคำพูด เพราะเป็นถ้อยคำตามประกาศ ฉบับอังกฤษต้องคงไว้ทั้งก้อน
    และต้อง **ไม่ลงท้ายประโยคด้วยชื่อในเครื่องหมายคำพูด** — ข้อความสรุปดึงค่าท้ายประโยค
    ไปเขียนว่า 'ต้องแก้เป็น "..."' ซึ่งจะกลายเป็นสั่งให้เปลี่ยนชื่อบท ทั้งที่ปัญหาคือ
    "บทนั้นยังไม่มีในเล่ม" คนละเรื่องกัน จึงเอาประโยคจำนวนบทไว้ท้ายเสมอ
    """
    # ไม่ใส่ปีของประกาศในประโยคนี้ (เจ้าหน้าที่แก้ถ้อยคำมาเอง ก.ย. 2569) — ข้อความ
    # ที่ส่งนักศึกษาอ้าง "ประกาศ" ลอย ๆ พอ เลขปีเป็นเรื่องของเจ้าหน้าที่กับแหล่งอ้างอิง
    tail = f"ตามประกาศ รูปแบบดั้งเดิมกำหนดให้มีครบ {len(canon)} บท"
    missing = list(range(found + 1, len(canon) + 1))
    if not missing:
        return tail
    if len(missing) == 1:
        title = canon[missing[0] - 1][0 if thai_book else 1]
        return f'เล่มนี้ยังไม่มีบทที่ {missing[0]} คือ "{title}" {tail}'
    return f"เล่มนี้ยังไม่มีบทที่ {missing[0]}-{missing[-1]} {tail}"


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
        if same_spelling(actual_title, variant):
            return 'variant', compared, expected
    return 'wrong', compared, expected


def chapter_title_verdict(title, chapter_no, option, next_line=""):
    """สถานะของชื่อบทหนึ่งฝั่ง (สารบัญหรือเนื้อหา) เทียบประกาศ — None ถ้าใช้ได้ ไม่งั้น (kind, compared, expected)

    ชื่อที่เป็นแค่ต้นของชื่อในประกาศ ยอมรับได้ต่อเมื่อบรรทัดถัดไปต่อให้ครบจริง (ดู canonical_title_wrapped)
    และชื่อที่ต่อครบแล้วยังต้องสะกดตรงทุกตัวรวมวรรณยุกต์/สระ/ำ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569) — ถ้าไม่ตรง
    ตัดสินจากชื่อที่ต่อครบ ข้อความจึงชี้จุดที่สะกดผิดได้ถูกที่ ไม่ใช่ยกมาแค่บรรทัดแรก
    """
    canon = CANONICAL_OPT1 if option == 1 else CANONICAL_OPT2
    kind, compared, expected = canonical_title_status(title, chapter_no, option)
    if kind == 'exact':
        return None
    if canonical_title_wrapped(title, next_line, canon[chapter_no - 1]):
        whole = f"{title} {_strip_toc_page_number(next_line)}"
        if any(same_spelling(whole, candidate) for candidate in canon[chapter_no - 1]):
            return None
        return canonical_title_status(whole, chapter_no, option)
    return kind, compared, expected


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
    # ตำแหน่งของข้อประเภทเล่มเป็นรายชื่อหน้า (หน้าปก หน้าลงนาม บทคัดย่อ) ถ้าปล่อยให้เดาจากคำ
    # จะตกหมวด "โครงสร้างเล่ม" ทั้งที่เป็นเรื่องเล่มไม่ตรงกับข้อมูลอนุมัติ
    if issue.get("rule_id") == "FORM.DOC_TYPE":
        return "ไม่ตรงข้อมูลอนุมัติ"
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
        # เล่มที่ฟอนต์ในไฟล์ทำให้อ่านตัวอักษรเพี้ยน (font_damage_score) — ฟอนต์แบบนี้ทำวรรณยุกต์/สระหายหรือกลายเป็น
        # ตัวอื่นได้ ข้อที่ต่างกันแค่ตัวเล็กบน/ล่างหรือ ำ กับ า จึงแยกไม่ออกว่าเล่มพิมพ์ผิดหรือระบบอ่านผิด (ดู add)
        self.marks_unreliable = False

    def marks_doubtful(self, marks_only, read_text=""):
        """จุดที่ต่างเฉพาะวรรณยุกต์/สระ/ำ แต่ระบบยืนยันไม่ได้ว่าอ่านถูก — ฟอนต์ของเล่มเพี้ยน หรือข้อความที่อ่านมามีร่องรอยเพี้ยน"""
        return bool(marks_only) and (self.marks_unreliable or _has_read_defect(read_text))

    def mismatch_status(self, marks_only, read_text=""):
        """สถานะในตารางเทียบของจุดที่ไม่ตรง — ต้องตรงกับสีของการ์ดคู่กัน (ดู add)"""
        return "pending" if self.marks_doubtful(marks_only, read_text) else "fail"

    def add_verification(self, topic, location, status, detail=""):
        """บันทึกผลเทียบข้อมูลอนุมัติรายตำแหน่ง

        สถานะต้องตรงกับการ์ดที่คู่กับตำแหน่งนั้น (เจ้าหน้าที่สั่ง ก.ย. 2569 ให้กฎสัมพันธ์กัน)
          pass     ตรง
          fail     ไม่ตรง — มีการ์ดแดงคู่กัน
          pending  มีการ์ดส้มคู่กัน (รอเจ้าหน้าที่กด ✓/✗)
          notice   มีการ์ดเหลืองคู่กัน (ข้อสังเกต)
          skipped  ไม่มีการ์ดที่ตำแหน่งนี้ — ไม่มีหน้านั้นในเล่ม หรืออ่านไม่ได้ทั้งไฟล์
                   (หน้าที่ขาดถูกฟ้องแดงในกฎของมันเองอยู่แล้ว)
        """
        group = next((g for g in self.verification if g["topic"] == topic), None)
        if group is None:
            group = {"topic": topic, "checks": []}
            self.verification.append(group)
        group["checks"].append({"location": location, "status": status,
                                "detail": soft(detail)})

    def add(self, zone, part, loc, found, expected, fix="", rule_id=None,
            system_note=False, notice="", fail_text=""):
        """system_note=True = ข้อจำกัดของระบบ ไม่ใช่จุดที่นักศึกษาแก้ได้

        ยังแสดงในรายงานฝั่งเจ้าหน้าที่ตามปกติ แต่ไม่นับเป็น "จุดที่ต้องแก้" ในข้อความ
        สรุปที่ส่งให้นักศึกษา (เช่น ช่องข้อมูลอนุมัติที่เจ้าหน้าที่ยังไม่ได้กรอก ระบบจึง
        ข้ามการเทียบ — เป็นเรื่องของการกรอกฟอร์ม นักศึกษาทำอะไรกับเล่มก็ไม่หาย)

        fail_text = ถ้อยคำที่ใช้แทนบรรทัด "ควรเป็น" เมื่อเจ้าหน้าที่กด "ไม่ผ่าน" ข้อส้ม/เหลืองข้อนี้ (อัปเดต 1.7)
        ตอนรอยืนยันข้อความสรุปยังใช้บรรทัด "ควรเป็น" เดิม ดู issues_to_fix
        """
        rule_id = rule_id or DEFAULT_RULE_BY_PART.get(part, "FORM.REQUIRED")
        if zone == "RED" and self.marks_doubtful(getattr(found, "marks_only", False), found):
            # สะกดต่างเฉพาะวรรณยุกต์/สระบน-ล่าง/การันต์/ำ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569 ให้เป็นแดง) แต่ฟอนต์ของเล่มเพี้ยน
            # หรือข้อความที่อ่านมามีร่องรอยเพี้ยน — "การอ่านที่เพี้ยนต้องไม่กลายเป็นคำตัดสินว่าเล่มไม่ผ่าน" จึงเป็นสีส้ม
            # ให้เจ้าหน้าที่เปิดหน้าดู (แนวเดียวกับข้อชื่อนักศึกษาบนหน้าที่อ่านตัวเลขไม่ออก ซึ่งเจ้าหน้าที่สั่งให้ฟอนต์เพี้ยน
            # เป็นสีส้ม ก.ย. 2569)
            zone, fix, rule_id = FONT_UNREADABLE_ZONE, MARKS_UNRELIABLE_FIX, "FORM.FONT_UNREADABLE"
        # ไม่เติม "แก้ไขให้เป็นไปตามข้อกำหนด: <expected>" อัตโนมัติอีกแล้ว — มันคือการ
        # พูดซ้ำบรรทัด "ควรเป็น" ที่อยู่เหนือมันคำต่อคำ ทำให้การ์ดยาวขึ้นโดยไม่ได้ข้อมูล
        # เพิ่ม ข้อที่มีวิธีแก้จริงจะส่ง fix มาเอง ข้อที่ไม่ส่งก็ไม่ต้องมีบรรทัดนี้
        self.zones[zone].append({
            "part": part,
            "location": loc,
            "found": str(found) if isinstance(found, DetailText) else found,
            "expected": expected,
            # ท่อนจุดต่างท้าย "ที่พบ" กับวิธีแก้เป็นข้อ ๆ (DetailText) ข้อความสรุปถอดท่อนจุดต่างออก
            # แล้วเขียนวิธีแก้เป็นคำสั่งทีละบรรทัดแทน — ข้อที่ไม่ได้มาจากการเทียบข้อความไม่มีสองช่องนี้
            "diff_tail": getattr(found, "diff_tail", ""),
            "fix_steps": list(getattr(found, "fix_steps", ())),
            # ข้อชื่อเรื่อง: ข้อความสรุปใช้ประโยคนี้ + จุดที่ต้องแก้ แทนบรรทัดที่พบ (อัปเดต 1.7)
            "title_lead": getattr(found, "title_lead", ""),
            "fix": fix,
            # ข้อความที่ต้องส่งถึง "นักศึกษา" ต่อท้ายข้อนั้น (คนละอย่างกับ fix ซึ่งเป็น
            # คำแนะนำสั้น ๆ บนการ์ด) — เช่นขั้นตอนขออนุมัติที่ต้องทำนอกเล่ม
            # ใส่ได้หลายบรรทัด แต่ละบรรทัดถูกแปลเป็นอังกฤษแยกกัน
            "notice": notice,
            "system_note": system_note,
            "fail_text": fail_text,
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


def signature_preview(pdf_path, limit=20):
    """หน้าลงนามของเล่มแบบเร็ว สำหรับหน้าต่างตรวจหน้าลงนามก่อนตรวจเล่ม (อัปเดต 1.4)

    อ่านเฉพาะหน้าต้นเล่ม แล้วชี้หน้าลงนามด้วยตัวเดียวกับ run_check (front_section_kind)
    หน้าต่างจึงเห็นหน้าเดียวกับที่ผลตรวจอ้าง ไม่ต้องรอตรวจทั้งเล่มเสร็จ
    คืน [{"index": ดัชนีหน้าในไฟล์, "label": เลขหน้าที่พิมพ์}]
    ช่องประธานหลักสูตร/คณบดี-ผู้อำนวยการ ให้เจ้าหน้าที่ดูจากภาพ (ข้อความที่อ่านได้มีเส้นประลายเซ็นปนจนอ่านไม่รู้เรื่อง)
    แผ่นสแกนตรงตำแหน่งหน้าลงนาม (scanned_signature_pages) ได้ "scanned": True ด้วย — ระบบอ่านไม่ได้
    แต่เจ้าหน้าที่ยังดูรายชื่อจากภาพได้ ผลตรวจแจ้งให้แนบหน้าลงนามแบบต้นฉบับที่ยังไม่ลงนาม
    """
    texts, sig, images, full = [], [], set(), set()
    with pdfplumber.open(pdf_path) as pdf:
        for i, pg in enumerate(pdf.pages[:limit]):
            texts.append(_page_text(pg))
            kind, _heading = front_section_kind(texts[-1])
            if kind == "signature":
                sig.append(i)
            try:
                if pg.images:
                    images.add(i)
                    if full_page_image(pg):
                        full.add(i)
            except Exception:
                pass
            try:
                pg.flush_cache()
            except Exception:
                pass
    scanned = scanned_signature_pages(texts, find_cover_page(texts), sig, images, full)
    # แผ่นสแกนที่ข้อความ OCR อ่านออกเป็นหน้าลงนาม ได้ "scanned" เหมือนแผ่นสแกนอื่น (อัปเดต 1.8.2) — ไม่นับซ้ำ
    # เลขหน้าจากชั้น OCR ของแผ่นภาพเต็มหน้าเชื่อไม่ได้ ("1i") จึงไม่แสดง
    return [{"index": i, "label": "" if i in scanned and i in full else _extract_page_label(texts[i]),
             **({"scanned": True} if i in scanned else {})}
            for i in sorted(set(sig) | set(scanned))]


_ROMAN_FRONT_LABELS = ("i", "ii", "iii", "iv", "v", "vi", "vii", "viii", "ix", "x")


def signature_tab_labels(pages, program_language):
    """ป้ายปุ่มสลับหน้าลงนามในขั้นที่ 1 — "หน้าลงนาม 1 (ก)" / "หน้าลงนาม 1 (i)" ตามภาษาเล่ม

    เจ้าของสั่ง (20 ต.ค. 2569): "ปรับเป็นช่องเดียว แต่กดเปลี่ยนหน้า หน้าลงนาม 1 (ก) หน้าลงนาม 2 (ข)
    เปลี่ยนไปตามภาษาของเล่ม" · หน้าลงนามคือหน้าแรก ๆ ของส่วนนำ (ปกไม่มีเลขหน้า) หน้าที่ k จึงได้เลขที่ k
    ของชนิดเลขหน้าตามภาษาเล่ม (_expected_front_label_style) · ไม่รู้ภาษาเล่ม = ใช้เลขหน้าที่พิมพ์ในเล่ม
    ไม่มีทั้งสองอย่าง = ไม่มีวงเล็บ
    """
    style = _expected_front_label_style(program_language)
    letters = (_THAI_PAGE_LETTERS if style == "thai" else
               _ROMAN_FRONT_LABELS if style == "roman" else ())
    out = []
    for k, page in enumerate(pages or [], start=1):
        label = letters[k - 1] if k <= len(letters) else (page.get("label") or "")
        out.append(f"หน้าลงนาม {k} ({label})" if label else f"หน้าลงนาม {k}")
    return out


def render_page_png(pdf_path, index, resolution=220):
    """ภาพหน้าเดียวของเล่มเป็น PNG (bytes) — ใช้แสดงหน้าลงนามในหน้าต่างตรวจก่อนตรวจเล่ม

    วาดทีละภาพเท่านั้น (_RENDER_LOCK) — ตัววาด (pdfium ใต้ pdfplumber.to_image) ใช้พร้อมกันหลายเธรดไม่ได้
    หน้าต่างขอภาพหน้าลงนามสองภาพพร้อมกัน ทดสอบจริงแล้วระบบดับทั้งระบบ (exit 0x80000003)
    """
    import io
    with _RENDER_LOCK, pdfplumber.open(pdf_path) as pdf:
        buf = io.BytesIO()
        pdf.pages[index].to_image(resolution=resolution).save(buf, format="PNG")
        return buf.getvalue()


_RENDER_LOCK = threading.Lock()


def check_result(rep, context=None, not_checked=NOT_CHECKED):
    """ผลตรวจหนึ่งชุด — **ทุกทางออกของ run_check ต้องผ่านฟังก์ชันนี้**

    หน้ารายงานอ่านคีย์ชุดนี้ครบทุกตัวเสมอ ทางออกไหนตกคีย์ไปหน้ารายงานพัง 500
    โดยตัวตรวจเองไม่มีอะไรฟ้องเลย เจอจริงตอนใช้งาน: ด่าน "ภาษาของเล่มไม่ตรงกับที่
    อนุมัติ" คืน dict ที่คัดลอกคีย์มาจาก result ตัวหลัก แต่ result ตัวหลักเติม
    plain_summary ทีหลัง (นอก dict literal) คีย์นั้นจึงหายไป แล้ว report.html พังที่
    {{ report.plain_summary | tojson }} — เล่มที่ภาษาไม่ตรงเปิดรายงานไม่ได้เลย

    ต้องเรียงข้อและจัดหมวดที่นี่ด้วย ไม่ใช่ให้ผู้เรียกทำเอง ด้วยเหตุผลเดียวกัน

    context["front_label_style"] = ชนิดเลขหน้าส่วนนำตามภาษาเล่ม ใช้เลือกเลขหน้าลงนาม
    ในถ้อยคำปุ่มเจ้าหน้าที่ (ดู staff_text_for_book) ทั้งทะเบียนที่ส่งให้หน้ารายงาน
    และข้อความสรุปที่สร้างใหม่ทุกครั้งที่กด
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
        "staff_findings": staff_checks_for_book((context or {}).get("front_label_style")),
    }
    result["plain_summary"] = plain_summary(result)
    # หน้าเว็บกันไว้ก่อนยิงด้วยค่านี้ ต้องมาจากฝั่งเซิร์ฟเวอร์ชุดเดียวกับที่ตรวจซ้ำตอนบันทึก
    # ไม่ใช่ให้หน้าเว็บไล่ชื่อหัวข้อเอง (สองฝั่งจะหลุดจากกันเมื่อกฎเปลี่ยน)
    for check in result["staff_findings"]:
        check["blocks_save"] = staff_check_blocks_save(result, check)
    return result


def run_check(pdf_path, approved, chapters_mode="strict", progress=None,
              skip_identity_check=False, turnitin=None):
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

    # ข้อมูลอนุมัติเข้าระบบทางนี้ทางเดียว (ไฟล์ eThesis หรือที่เจ้าหน้าที่พิมพ์/วางในฟอร์ม) คนเขียนสระอำได้
    # หลายแบบ (ำ · ํา · นํ้า) จึงรวมเป็น ำ ที่นี่ ให้ตรงกับข้อความที่อ่านจากเล่มเสมอ
    approved = fold_sara_am_in(approved)
    rep = Report()
    if not str(pdf_path).lower().endswith(".pdf"):
        rep.add("ORANGE", "-", Path(pdf_path).name, "ไม่ใช่ไฟล์ PDF", "ระบบตรวจ PDF เท่านั้น", "ส่งไฟล์ PDF")
        return check_result(rep)

    # ไฟล์ Turnitin อ่านไว้ตั้งแต่ตอนอัปโหลด (main /check) ส่งมาแค่ตัวเลข ไม่เกี่ยวกับตัวเล่ม
    _report_turnitin(rep, turnitin)

    _p("เปิดไฟล์ PDF")
    pages = []
    header_words = []    # คำในแถบหัวกระดาษต่อหน้า — ตัดสินทีหลังเมื่อรู้ว่าหน้าไหนเป็นส่วนนำ
    font_damaged = []          # ดัชนีหน้าที่ฟอนต์ในไฟล์ทำให้อ่านข้อความเพี้ยน
    # หน้าที่มีภาพ — แยกหน้าลงนามที่สแกนมาออกจาก "ไม่พบหน้าลงนาม" และเรียกหน้าที่มีแค่เลขหน้ากับภาพว่า
    # หน้ารูปภาพ/สแกน ไม่ใช่หน้าว่าง (อัปเดต 1.6.1 — เล่มจริงมีภาคผนวกเป็นภาพ 89 หน้า)
    image_pages = set()
    full_image_pages = set()   # แผ่นภาพเต็มหน้า (full_page_image) — หน้าลงนามสแกนที่มีชั้นข้อความ OCR (อัปเดต 1.8.2)
    empty_pages = set()        # หน้าว่างจริง — ข้อ PAGE.EMPTY (อัปเดต 1.7)
    edge_numbered = set()      # หน้าที่แถบหัว/ท้ายกระดาษมีสิ่งที่อาจเป็นเลขหน้า (edge_page_number_hint อัปเดต 1.8)
    rotated_labels = {}        # เลขหน้าที่พิมพ์เป็นอักษรหมุน (rotated_page_label อัปเดต 1.8)
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
                if _pg.images:
                    image_pages.add(_i)
                    if _i < 20 and full_page_image(_pg):      # ใช้เฉพาะตำแหน่งหน้าลงนามต้นเล่ม
                        full_image_pages.add(_i)
            except Exception:
                pass
            try:
                # หน้าว่างจริง (อัปเดต 1.7): ไม่มีข้อความ รูป เส้นหรือกราฟิก — ไม่มีเลขหน้าด้วยเพราะเลขหน้าก็เป็นข้อความ
                if not (pages[-1] or "").strip() and not (
                        _pg.chars or _pg.images or _pg.lines or _pg.rects or _pg.curves):
                    empty_pages.add(_i)
            except Exception:
                pass
            try:
                _words = _pg.extract_words()
                header_words.append(header_tokens(_pg, words=_words))
                if edge_page_number_hint(_words, float(_pg.width or 0), float(_pg.height or 0)):
                    edge_numbered.add(_i)
                _rotated = rotated_page_label(_words, float(_pg.width or 0), float(_pg.height or 0))
                if _rotated:
                    rotated_labels[_i] = _rotated
            except Exception:
                header_words.append([])
                edge_numbered.add(_i)          # อ่านแถบขอบไม่ได้ = ไม่ฟันธงว่าไม่มีเลขหน้า
            try:
                _pg.flush_cache()
            except Exception:
                pass
    # ฟอนต์ที่ทำตัวอักษรเพี้ยนหน้าหนึ่งก็ใช้ทั้งเล่ม — ตัวสะกดระดับวรรณยุกต์/สระของเล่มนี้เชื่อไม่ได้ (ดู Report.add)
    rep.marks_unreliable = bool(font_damaged)

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

    # ไฟล์ eThesis เป็นของนักศึกษาคนเดียวกับเล่มไหม — ใช้ภายในเท่านั้น ไม่มีการ์ดแล้ว
    # (เจ้าหน้าที่สั่ง ก.ย. 2569: เลือกไฟล์ผิดคนก็ตรวจผิด ให้เจ้าหน้าที่ตรวจใหม่เอง)
    # ยังต้องรู้ไว้กันข้อสรุปที่ "หยุดตรวจทั้งเล่ม" หรือ "เดาโครงเล่ม" จากข้อมูลของคนอื่น
    # คือด่านภาษาของเล่ม ผังบท และชนิดเลขหน้าส่วนนำ ส่วนการเทียบข้อมูลอนุมัติทำตามปกติ
    same_student, _checked, _found = (
        ethesis_matches_book(approved, pages)
        if approved and not skip_identity_check else (True, [], []))
    # ภาษาของเล่มตามข้อมูลอนุมัติ ใช้ได้เฉพาะเมื่อไฟล์ eThesis เป็นของคนเดียวกับเล่ม
    # ถ้อยคำในรายงานต้องเป็นภาษาของเล่ม — เจ้าหน้าที่สั่ง (ก.ย. 2569) "เล่มไทยควรใช้คำไทย"
    book_wording_language = (approved or {}).get("program_language", "") if same_student else ""
    # ชนิดเลขหน้าส่วนนำตามภาษาเล่ม (ก ข ค / i ii iii) — ใช้ทั้งกฎเลขหน้าและถ้อยคำปุ่มเจ้าหน้าที่
    front_label_style = _expected_front_label_style(book_wording_language)
    # ---------- ภาษาของเล่มต้องตรงกับที่ได้รับอนุมัติ ----------
    # ต้องมาก่อน rep.add ตัวแรก เพราะเมื่อเล่มทำผิดภาษา กฎอื่นแทบทุกข้อจะฟ้องพร้อมกัน
    # หมด (ข้อความบังคับบนปก ประโยค template หน้าลงนาม ชนิดเลขหน้า ชื่อบททุกบท)
    # วัดกับเล่มจริง: เล่มไทยที่จับคู่กับข้อมูล "เล่มอังกฤษ" ได้แดง 24 ข้อ โดยไม่มีข้อไหน
    # บอกสาเหตุจริงเลย และบางข้ออ่านแล้วสับสน เช่นยกชื่อเรื่องมาอ้างว่าเป็นชื่อนักศึกษา
    cover_idx = find_cover_page(pages)
    cover_text = pages[cover_idx] if pages else ""
    scanned_sig = scanned_signature_pages(pages, cover_idx, sig_pages, image_pages, full_image_pages)
    # แผ่นสแกนที่ชั้นข้อความ OCR อ่านออกเป็นหน้าลงนาม ไม่ตรวจเป็นหน้าลงนามที่อ่านได้ — แจ้งข้อเดียวว่าเป็นไฟล์สแกน
    # เหมือนแผ่นสแกนอื่น (อัปเดต 1.8.2) ข้อความ OCR เพี้ยนจะฟ้องชื่อ/กรรมการผิดทั้งหน้า
    sig_pages[:] = [i for i in sig_pages if i not in scanned_sig]
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
                 "approved_data": bool(approved), "front_label_style": front_label_style,
                 "committees": committee_names_by_kind(approved)},
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
    merge_rotated_labels(page_labels, rotated_labels, edge_numbered)
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

    # หน้าปกต้องไม่มีเลขหน้า — ข้อเดียวที่หน้าปก ไม่นับเลขบนปกในลำดับเลขหน้าอารบิก/หัวกระดาษส่วนนำ (อัปเดต 1.6.1)
    cover_label = cover_page_label(page_labels, header_words, cover_idx)
    _report_cover_page_label(rep, cover_label)
    seq = sorted((i, v) for i, v in printed.items() if not (cover_label and i == cover_idx))
    arabic_sequence_ok = bool(seq) and seq[0][1] == 1 and all(
        seq[k][1] == seq[k - 1][1] + 1 for k in range(1, len(seq))
    )
    if BODY_RULES['check_page_sequence']:
        if seq and seq[0][1] != 1:
            rep.add("RED", "body", page_ref(seq[0][0]), f"เลขหน้าอารบิกแรกที่พบคือ {seq[0][1]}",
                    "เลขหน้าอารบิกต้องเริ่มที่ 1 ณ บทที่ 1", "แก้การตั้งเลขหน้า", "PAGE.NUMBERING")
        # หน้าที่คั่นอยู่แต่อ่านเลขไม่ได้ ยังนับเป็นหน้าของเล่ม — 71, (อ่านไม่ออก), 73 ต่อเนื่องแล้ว ·
        # หน้าว่างจริงไม่นับ (อัปเดต 1.7) · อัปเดต 1.8 (เจ้าของสั่ง 28 ต.ค. 2569): สีเหลือง ข้อเดียวของส่วนนี้แบบส่วนนำ
        # ตำแหน่งคือหน้าแรกที่พบ — เดิมส้มข้อละจุด เล่มจริงที่อ่านเลขหน้าแนวนอนผิดได้ 13 ข้อ ("หน้า 0" ซ้ำ 7 ข้อ)
        problems, first_bad = _sequence_problems([(i, str(v), v) for i, v in seq], empty_pages)
        if problems:
            rep.add(PAGE_SEQUENCE_BODY_ZONE, "body/end", page_ref(first_bad),
                    sequence_found(problems),
                    "เลขหน้าต้องเรียงต่อเนื่องทีละหน้า ไม่ซ้ำ ไม่ข้าม", "",
                    "PAGE.NUMBERING_SEQUENCE_BODY")
    last_arabic = max(printed.values()) if printed else None

    # แผ่นสแกนตรงหน้าลงนามแจ้งไว้แล้วในข้อ "อ่านหน้าลงนามไม่ได้" — ไม่แจ้งซ้ำว่าดึงข้อความไม่ได้
    # หน้าว่างจริงแจ้งเป็นข้อ PAGE.EMPTY ข้อเดียวทั้งเล่ม ไม่ซ้อนกับ PAGE.BLANK บนแผ่นเดียวกัน (อัปเดต 1.7)
    _report_blank_runs(rep, pages, printed, image_pages, arabic_sequence_ok, page_ref,
                       skip=list(scanned_sig) + sorted(empty_pages),
                       sequence_reported=BODY_RULES['check_page_sequence'])
    _report_empty_pages(rep, empty_pages, page_labels)

    # เลขหน้าลงนาม i/ii หรือ ก/ข
    _report_signature_page_count(rep, sig_pages, scanned_sig, front_label_style)
    sig_label_reported = _report_signature_page_labels(
        rep, sig_pages, pages, page_ref, label_style=front_label_style)

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
    # สารบัญ สารบัญตาราง สารบัญรูปภาพ ระบุเลขหน้าเป็นช่วง (อัปเดต 1.7) — หน้าสารบัญตาราง/รูปภาพนับหน้าต่อด้วย
    range_sources = [("สารบัญ", idx) for idx in toc_page_indices]
    front_marks = sorted(set(sig_pages + abs_th_pages + abs_en_pages + ack_pages + toc_pages + list_pages))
    for list_start in list_pages:
        if any(idx == list_start for _name, idx in range_sources):
            continue
        name = soft(front_section_kind(pages[list_start])[1]) or "สารบัญตาราง/รูปภาพ"
        later = [b for b in front_marks if b > list_start]
        for idx in _toc_continuation_pages(pages, list_start, min(later) if later else n):
            range_sources.append((name, idx))
    _report_toc_page_ranges(rep, range_sources, pages, page_ref, n)
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
                        found_msg = found_with_diff(
                            f'ชื่อบทใน{wrong_side}เขียนว่า "{wrong_text}"',
                            analyze_diff(wrong_text, correct))
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
                            "ไฟล์นี้ไม่มีฟอนต์ที่บอกว่าเป็นตัวหนา ระบบจึงบอกไม่ได้ว่าหัวข้อเป็นตัวหนาหรือไม่",
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
                            toc_major_headings_sentence(book_wording_language),
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
        thai_book = book_wording_language == "thai"
        # ตำแหน่งต้องบอกว่าไปแก้ที่ไหน (เจ้าหน้าที่ทัก ก.ย. 2569) — บทที่เพิ่มหรือตัด
        # ต้องตรงกันทั้งในสารบัญและในเนื้อหา แก้ที่เดียวแล้วเล่มจะขัดกันเองทันที
        # ของเดิมเขียนแค่ "ทั้งเล่ม" ซึ่งไม่ได้บอกอะไรเลยว่าต้องไปทำอะไรตรงไหน
        where = "ทั้งเล่ม (สารบัญและเนื้อหา)"
        if option == 1 and len(body_ch) != 6:
            rep.add("RED", "body", where,
                    chapter_count_found(len(body_ch), 6, 6),
                    chapter_count_expected(len(body_ch), CANONICAL_OPT1, thai_book),
                    "ปรับโครงบทตามประกาศ ทั้งในสารบัญและในเนื้อหา", "BODY.OPTION1",
                    notice=CHAPTER_COUNT_NOTICE)
        if option == 2 and len(body_ch) not in (2, 3):
            rep.add("RED", "body", where,
                    chapter_count_found(len(body_ch), 2, 3),
                    "รูปแบบตีพิมพ์ต้องมี 2-3 บท",
                    "ปรับโครงบทตามประกาศ ทั้งในสารบัญและในเนื้อหา", "BODY.OPTION2",
                    notice=CHAPTER_COUNT_NOTICE)

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
        rule_id = "BODY.OPTION1" if option == 1 else "BODY.OPTION2"
        toc_by_ch = {c[0]: (_toc_chapter_title(c[3]), c[4], c[5]) for c in toc_ch}
        body_by_ch = ({c[0]: (c[1], c[2], c[4]) for c in body_ch}
                      if body_ch and BODY_RULES['check_body_title_against_canonical'] else {})

        def _title_status(title, cn, next_line=""):
            return chapter_title_verdict(title, cn, option, next_line)

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
            same = both and same_spelling(toc_title, body_title)
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
    bio_heading = ""
    last_major = None
    has_appendix_body = False
    appendix_page = None
    appendix_pages = []
    # ส่วนท้ายเล่มอยู่ "หลังเนื้อหา" เสมอ จึงต้องไม่สแกนส่วนนำ/สารบัญ มิฉะนั้นบรรทัด
    # ในสารบัญ เช่น "APPENDIX D 90" จะถูกนับเป็นหัวบทภาคผนวกจริง ทำให้หน้าเริ่มของ
    # ภาคผนวกกลายเป็นหน้าส่วนนำ (เช่น "x") แล้วฟ้องเลขหน้าผิดทั้งที่เล่มถูก
    end_scan_start = min((c[2] for c in body_ch), default=0)

    # หัวกระดาษต้องมีเพียงเลขหน้าตามกฎของส่วนนั้นเท่านั้น (ไม่มี running head/ชื่อบท/ข้อความอื่น)
    #   ส่วนนำ (ก่อนบทแรก)          ก ข ค หรือ i ii iii
    #   ส่วนเนื้อหา/ส่วนท้าย         เลขอารบิกเท่านั้น (iv หรือ ก ในหัวกระดาษของบทก็นับเป็นข้อความอื่น)
    # รวมทุกหน้าที่พบข้อความอื่นของแต่ละช่วงเป็นรายการเดียว ให้เจ้าหน้าที่ยืนยัน (ส้ม)
    if body_ch:
        def header_extra_at(i, front):
            words = header_words[i] if i < len(header_words) else []
            return header_extra_text(None, front=front, tokens=words,
                                     front_style=front_label_style)

        # เลขหน้าส่วนนำที่บอกในข้อความ ให้ตรงภาษาของเล่ม (ไม่รู้ภาษาเล่ม = บอกทั้งสองแบบ)
        front_labels = {"thai": "ก ข ค", "roman": "i ii iii"}.get(
            front_label_style, "ก ข ค หรือ i ii iii")

        header_spans = (
            ("front_matter", "หัวกระดาษส่วนนำ", range(0, end_scan_start), True,
             "พบข้อความอื่นนอกจากเลขหน้าในหัวกระดาษส่วนนำ",
             f"หัวกระดาษส่วนนำต้องมีเพียงเลขหน้า ({front_labels}) เท่านั้น",
             f"ลบข้อความอื่นออกจากหัวกระดาษ ให้เหลือเฉพาะเลขหน้า ({front_labels})"),
            # ถ้อยคำของเจ้าหน้าที่ (15 ต.ค. 2569) พิมพ์ตรงตัว — สะกด "อารบิก" ตามราชบัณฑิตฯ (เจ้าหน้าที่ยืนยัน)
            ("body/end", "หัวกระดาษส่วนเนื้อหา และส่วนท้าย",
             range(end_scan_start, len(pages)), False,
             "พบข้อความอื่นนอกจากเลขหน้าในหัวกระดาษส่วนเนื้อหา และส่วนท้าย",
             "หัวกระดาษส่วนเนื้อหา และส่วนท้าย ต้องมีเพียงเลขหน้าอารบิก เท่านั้น",
             "ลบข้อความอื่น (เช่น ชื่อบท/running head) ออกจากหัวกระดาษ ให้เหลือเฉพาะเลขหน้าอารบิก"),
        )
        def header_reported_elsewhere(i, front):
            # หัวกระดาษปกที่มีแค่เลขหน้า แจ้งไว้แล้วในข้อ "หน้าปกมีเลขหน้า" (อัปเดต 1.6.1) — ไม่แจ้งซ้ำที่นี่
            # แผ่นสแกนตรงหน้าลงนามแจ้งเป็นข้อเดียวแล้ว — ข้อความ OCR บนหัวกระดาษไม่ใช่สิ่งที่เล่มพิมพ์ (อัปเดต 1.8.2)
            if i in scanned_sig:
                return True
            return front and cover_label and i == cover_idx and header_extra_at(i, front) == cover_label

        for part, label, span, front, found, expected, fix in header_spans:
            header_bad = [i for i in span if header_extra_at(i, front)
                          and not header_reported_elsewhere(i, front)]
            if not header_bad:
                continue
            # ส่วนนำมีไม่กี่หน้า บอกเลขหน้าในวงเล็บได้ ส่วนเนื้อหา/ส่วนท้ายบางเล่มเป็นร้อยหน้า
            # บอกแค่จำนวนหน้าที่ระบบนับได้ในบรรทัดถัดไปพอ (เจ้าหน้าที่สั่ง 15 ต.ค. 2569)
            if front:
                shown = ", ".join(page_ref(i) for i in header_bad[:5])
                more = f" และอีก {len(header_bad) - 5} หน้า" if len(header_bad) > 5 else ""
                location = f"{label} ({shown}{more})"
            else:
                location = label
            # ไม่ยกตัวอย่างข้อความที่พบ (เจ้าหน้าที่สั่ง 15 ต.ค. 2569) — ระบบอาจอ่านหัวกระดาษ
            # ได้ไม่ครบ/เพี้ยน ข้อความที่ยกมาจะอ่านไม่รู้เรื่อง บอกแค่หน้าและจำนวนหน้าพอ
            rep.add("ORANGE", part, location,
                    f'{found} {len(header_bad)} หน้า',
                    expected, fix, "PAGE.HEADER")

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
            if is_biography_heading(l, t):
                bio_page = i
                bio_heading = l
                last_major = ("BIO", i)
            if any(nl.startswith(w) for w in N_APPENDIX):
                has_appendix_body = True
                appendix_pages.append(i)
                appendix_page = i if appendix_page is None else appendix_page
                last_major = ("APP", i)
    if ref_head:
        if ref_head[2] > 1 or '/' in ref_head[0]:
            one, other = reference_term_choice(book_wording_language, ref_head[0])
            rep.add("RED", "end_matter", page_ref(ref_head[1]),
                    f'หัวข้อในหน้านี้เลือกหลายคำ: "{ref_head[0]}"',
                    f"เลือกคำเดียว: {one} หรือ {other}", "ลบคำที่ไม่ใช้")
    else:
        one, other = reference_term_choice(book_wording_language)
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบหน้ารายการอ้างอิง",
                f"ต้องมีหัวข้อ {one} หรือ {other} เสมอ", "")
    bio_want = expected_biography_heading(book_wording_language, bio_heading)
    if bio_page is None:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบประวัติผู้วิจัย (BIOGRAPHY)",
                "ต้องมีและเป็นหน้าสุดท้ายของเล่ม", "")
    else:
        _report_biography_heading(rep, bio_heading, bio_want,
                                  f"ประวัติผู้วิจัย ({page_ref(bio_page)})")
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
    # มีข้อมูลอนุมัติและมีหน้าสารบัญ = กฎหัวข้อบังคับในสารบัญ (FRONT.TOC_CONTENT) ฟ้องข้อนี้เอง
    # พร้อมแยกกรณี "สะกดผิด" ออกได้ — เดิมฟ้องทั้งสองกฎ เล่มจริง 6538041 SHPP/D จึงได้สองข้อ
    # เรื่องเดียวกัน ("เล่มมีภาคผนวก ... แต่ไม่ปรากฏในสารบัญ" กับ "ไม่พบหัวข้อ ภาคผนวก ในสารบัญ")
    if has_appendix_body and not toc_has_appendix and not (approved and toc_pages):
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
        front_label_style,
        page_texts=pages, reported=list(sig_label_reported) + scanned_sig, ignore=empty_pages)
    # ส่วนเนื้อหาและส่วนท้าย: หน้าที่มีข้อความแต่ไม่ได้พิมพ์เลขหน้า (อัปเดต 1.8)
    if body_ch:
        _report_unnumbered_body_pages(rep, pages, page_labels, body_ch[0][2], edge_numbered,
                                      skip=empty_pages)

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
        stated = abstract_page_count([raw for sp in span_pgs for raw in pages[sp].split('\n')])
        if stated is None:
            count_missing.append(lbl)
        elif last_arabic is not None and stated != last_arabic:
            count_wrong.append((lbl, stated))
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
                        _report_abstract_title_dots(
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
                                    # บรรทัด "ควรเป็น" ต้องเป็นข้อกำหนดที่นักศึกษาอ่านแล้วรู้ว่า
                                    # ต้องทำอะไร (เจ้าหน้าที่สั่ง ก.ย. 2569 "เพิ่มว่า ในหน้าบทคัดย่อ
                                    # ต้องไม่มีตัวหนา") — ของเดิมเขียนว่า "แจ้งเป็นข้อสังเกต...
                                    # แต่เล่มยังผ่านได้" ซึ่งเป็นบรรทัดของเจ้าหน้าที่ จึงถูกตัดออก
                                    # จากข้อความสรุป นักศึกษาเห็นแค่ว่า "มีข้อความตัวหนา" ลอย ๆ
                                    # โดยไม่มีบรรทัดบอกว่าต้องแก้เป็นอะไร
                                    # ถ้อยคำสั้น ๆ ตามที่เจ้าหน้าที่เขียนมาเอง (ก.ย. 2569)
                                    # "หน้าบทคัดย่อต้องไม่มีข้อความตัวหนา แต่ให้เป็นข้อสังเกต"
                                    # ข้อยกเว้นเรื่องหัวข้อบทคัดย่อ (ซึ่ง template ตั้งหนาไว้เอง
                                    # และ _is_abstract_heading ไม่นับอยู่แล้ว) ย้ายไปอยู่บรรทัด
                                    # ของเจ้าหน้าที่แทน — นักศึกษาไม่ต้องตัดสินใจเรื่องนี้ เพราะ
                                    # หัวข้อไม่เคยถูกฟ้องอยู่แล้ว ถ้าเขียนปนไว้ในบรรทัดที่ส่งไป
                                    # นักศึกษาอาจไปแก้หัวข้อที่ถูกต้องอยู่แล้วให้ผิด
                                    "หน้าบทคัดย่อต้องไม่มีข้อความตัวหนา",
                                    "เป็นข้อสังเกต เล่มยังผ่านได้ หัวข้อบทคัดย่อเป็นตัวหนา"
                                    "ตาม template ได้ เจ้าหน้าที่พิจารณาว่าต้องแก้หรือไม่",
                                    "FORMAT.ABSTRACT_BOLD")
        except Exception:
            rep.add(UNCERTAIN_ZONE, "front_matter", "บทคัดย่อ",
                    "ระบบอ่านรูปแบบตัวหนาในบทคัดย่อไม่ได้",
                    "เจ้าหน้าที่ตรวจสอบรูปแบบตัวหนาในบทคัดย่อ",
                    "ตรวจด้วยตา", "UNCERTAIN.REVIEW")

    # ---------- เทียบข้อมูลอนุมัติ ----------
    _p("เทียบข้อมูลอนุมัติ (ชื่อเรื่อง/ชื่อนักศึกษา)")
    # ไม่มีการ์ด "ไฟล์ eThesis คนละคนกับเล่ม" และ "ไม่ได้กรอกข้อมูลอนุมัติ" แล้ว
    # (เจ้าหน้าที่สั่ง ก.ย. 2569) สองเรื่องนี้เป็นงานของเจ้าหน้าที่ ไม่ใช่ความผิดของเล่ม
    #   - ช่องข้อมูลอนุมัติว่าง: หน้าเว็บบังคับกรอก และ /check ปฏิเสธก่อนสร้างงาน
    #     (ใช้รายการเดียวกัน FRONT_MATTER_RULES["required_form_fields"]) จึงมาไม่ถึงตรงนี้
    #   - ไฟล์ eThesis ผิดคน: เทียบตามข้อมูลที่ให้มาตามปกติ ชื่อ/รหัส/ชื่อเรื่องที่ไม่ตรง
    #     จะขึ้นแดงให้เห็นเองว่าเลือกไฟล์ผิด **ห้ามข้ามการเทียบเงียบ ๆ** — วัดกับเล่มจริง
    #     เล่ม 1 คู่กับไฟล์ของเล่ม 3 ถ้าข้ามโดยไม่มีการ์ด จะได้ "ผ่าน" ทั้งที่ไม่ได้เทียบอะไรเลย
    if approved:
        A = approved
        program_language = A.get("program_language", "")

        # ---- ประเภทเล่ม: ทุกจุดที่เล่มเขียนประเภทไว้ ต้องตรงกับที่ได้รับอนุมัติ ----
        # ประเภทที่ใช้ตรวจคือค่าที่เจ้าหน้าที่เลือกในแบบฟอร์ม (main.py) ค่าที่อ่านได้จากไฟล์ eThesis
        # เป็นข้อมูลประกอบเมื่อต่างกัน — "เจ้าหน้าที่เปลี่ยนด้วยมือ ให้เชื่อเจ้าหน้าที่" (ก.ย. 2569)
        approved_type = A.get("doc_type", "")
        type_spots = [("หน้าปก", cover_text)] + [
            (f"หน้าลงนาม {k + 1} ({page_ref(idx)})", pages[idx]) for k, idx in enumerate(sig_pages)
        ] + [
            (f"{abstract_page_label(idx, abs_en_pages, abs_th_pages)} ({page_ref(idx)})", pages[idx])
            for idx in sorted(i for i in (abs_th_idx, abs_en_idx) if i is not None)
        ]
        type_rows, type_issues, cover_type = doc_type_check(
            approved_type, program_language, type_spots, A.get("doc_type_ethesis", ""))
        for where, status, detail in type_rows:
            rep.add_verification("ประเภทเล่ม", where, status, detail)
        for zone, *type_item in type_issues:
            rep.add(zone, "front_matter", *type_item, "FORM.DOC_TYPE")
        missing_cover_items = [
            (label, expected_text)
            for label, expected_text in cover_required_items(cover_type, program_language)
            # ต้องสะกดตรงทุกตัวรวมวรรณยุกต์/สระ/ำ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569) — ดู spelling_key
            if expected_text and not spelled_in(expected_text, cover_text)
        ]
        for label, expected_text in missing_cover_items:
            snippet, ratio = _best_cover_match(expected_text, cover_text)
            # เกณฑ์ 0.8: ข้อความพิมพ์ผิดเล็กน้อย (ตก S/สลับคำ) จะได้คะแนนสูงกว่านี้
            # ส่วนการบังเอิญไปตรง substring คนละบรรทัด (โดยเฉพาะไทย) จะต่ำกว่า
            if ratio >= 0.8 and snippet:
                found_msg = found_with_diff(
                    f"หน้าปกพิมพ์ \"{snippet}\" ไม่ตรงข้อความบังคับ ({label})",
                    analyze_diff(snippet, expected_text))
            else:
                found_msg = f"ไม่พบข้อความบังคับ ({label}) บนหน้าปก"
            rep.add(
                "RED", "front_matter", "หน้าปก",
                found_msg,
                f"ข้อความที่ถูกต้อง: \"{expected_text}\"",
                "แก้ข้อความบนหน้าปกให้ตรง template ทางการทุกตัวอักษร",
                "FRONT.COVER_REQUIRED",
            )
        if chapters_mode == "strict" and A.get("format") and str(option) != str(A["format"]):
            # บอกให้ครบว่า "เล่มเป็นแบบไหน / ต้องเป็นแบบไหน / แก้ตรงไหนได้บ้าง"
            # ของเดิมบอกแค่สองค่าเทียบกัน คนอ่านไม่รู้ว่าต้องแก้ฝั่งไหน
            rep.add("RED", "body", "โครงบท", f"เล่มจัดบทตามรูปแบบ {option}",
                    f"ข้อมูลอนุมัติระบุรูปแบบ {A['format']}",
                    f"แก้เล่มให้เป็นรูปแบบ {A['format']} หรือแก้ข้อมูลอนุมัติให้เป็นรูปแบบ {option}",
                    "FORM.APPROVED_MATCH")
        # โหมด free ใช้รูปแบบตามที่เลือกตรวจต่อ (resolve_option) จึงไม่เคยเทียบกับเล่มเลย
        # ตรวจทุกช่องในแบบฟอร์ม (ก.ย. 2569): เลือกรูปแบบผิดในโหมด free แล้วไม่มีอะไรฟ้อง
        # ยังต้องแจ้ง แต่เป็นสีส้ม — โหมดนี้ไม่บังคับชื่อบท การดูรูปแบบจากชื่อบทที่ 1
        # ("บทสรุป/SUMMARY" = รูปแบบ 2) จึงเชื่อได้น้อยกว่าโหมด strict
        if chapters_mode == "free" and A.get("format") and any(c[0] == 1 for c in body_ch):
            book_option = resolve_option(body_ch, None, "strict")
            if str(book_option) != str(A["format"]):
                rep.add("ORANGE", "body", "โครงบท", f"เล่มจัดบทตามรูปแบบ {book_option}",
                        f"ข้อมูลอนุมัติระบุรูปแบบ {A['format']}",
                        "โหมด free ระบบดูรูปแบบจากชื่อบทที่ 1 เท่านั้น เปิดเล่มดูว่าจัดบทตามรูปแบบใด "
                        "ถ้าไม่ตรงกับข้อมูลอนุมัติให้กดไม่ผ่าน",
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
                                     "pass" if compared['status'] == 'exact'
                                     else rep.mismatch_status(compared.get('marks_only'), compared['actual']),
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
                                     "pass" if compared['status'] == 'exact'
                                     else rep.mismatch_status(compared.get('marks_only'), compared['actual']),
                                     "" if compared['status'] == 'exact' else compared['actual'])
                if compared['status'] != 'exact':
                    # บอกภาษาตรง ๆ และบอกว่าเป็นค่าในเล่ม — เจ้าหน้าที่ (ต.ค. 2569): ให้เขียนว่า
                    # "ชื่อเรื่องภาษาไทยในรูปเล่ม ไม่ตรงกับข้อมูลในระบบ" (เดิม "ชื่อเรื่องอีกภาษา...")
                    # ช่องว่างท้ายป้ายตามถ้อยคำของเจ้าหน้าที่ title_mismatch_detail ต่อ "ไม่ตรงกับ..." ทันที
                    alt_word = ("ชื่อเรื่องภาษาอังกฤษในรูปเล่ม " if thai_book
                                else "ชื่อเรื่องภาษาไทยในรูปเล่ม ")
                    rep.add("RED", "front_matter", f"{alt_lbl} ({page_ref(alt_abs)})",
                            title_mismatch_detail(alt_word, compared, alt_title),
                            f"ต้องตรงข้อมูลอนุมัติทุกตัวอักษร: \"{alt_title}\"",
                            "แก้ชื่อเรื่องให้ตรงข้อมูลในระบบ", "FORM.APPROVED_MATCH")
            else:
                # ไม่มีหน้าบทคัดย่อภาษานั้นในเล่ม = ถูกฟ้องเป็นสีแดงในกฎ "ภาษาครบตามหลักสูตร"
                # อยู่แล้ว จึงไม่ฟ้องซ้ำด้วยข้อความที่ฟังเหมือนระบบอ่านไม่ได้
                rep.add_verification("ชื่อเรื่อง (ตาม บฑ.1)", alt_lbl, "skipped",
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
            misspelled_at_end = ""
            if thai_book:
                # ชื่อต้องสะกดตรงทุกตัวรวมวรรณยุกต์/สระ/ำ (เจ้าหน้าที่สั่ง 6 ต.ค. 2569) — ดู spelling_key
                exact_at_end = spelled_in(expected_ack_name, ack_tail_text)
                name_elsewhere = spelled_in(expected_ack_name, ack_full_text)
                wrong_case = False
                if not exact_at_end and norm(expected_ack_name) in norm(ack_tail_text):
                    # ชื่อครบทุกตัวอักษรแต่วรรณยุกต์/สระ/ำ ไม่ตรง — ยกที่พิมพ์จริงมาชี้จุดต่าง ไม่ใช่บอกว่า "ไม่พบชื่อ"
                    misspelled_at_end = _closest_run(ack_tail_text, expected_ack_name)
            else:
                exact_at_end = expected_ack_name in ack_tail_text
                name_elsewhere = expected_ack_name in ack_full_text
                wrong_case = expected_ack_name.casefold() in ack_tail_text.casefold()
            if not exact_at_end:
                if wrong_case:
                    found_ack = f"พบชื่อผู้เขียนท้ายกิตติกรรมประกาศ แต่ตัวพิมพ์ไม่ตรงรูปแบบ: {ack_tail_text[-120:]}"
                elif misspelled_at_end:
                    found_ack = found_with_diff(
                        f'ชื่อผู้เขียนท้ายกิตติกรรมประกาศเขียนว่า "{misspelled_at_end}"',
                        analyze_diff(misspelled_at_end, expected_ack_name))
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
                    # ชื่อที่บอกให้แก้ต้องอยู่ในรูปแบบตัวพิมพ์ของหน้านั้น — หน้าลงนามเป็น
                    # Sentence Case ไม่ใช่ UPPERCASE ตามข้อมูลอนุมัติ (เจ้าหน้าที่สั่ง 19 ต.ค. 2569)
                    # ไม่งั้นนักศึกษาแก้ตัวสะกดแล้วกลายเป็นตัวพิมพ์ผิดแทน
                    shown_name = core_name
                    if re.search(r'[A-Za-z]', core_name):
                        shown_name = (core_name.upper()
                                      if _STUDENT_NAME_STYLE[spot_kind][0] == "upper"
                                      else person_name_sentence_case(core_name))
                    rep.add_verification("ชื่อนักศึกษา", spot_name,
                                         rep.mismatch_status(compared.get('marks_only'), compared['actual']),
                                         compared['actual'])
                    rep.add("RED", "front_matter", spot_name,
                            mismatch_detail("ชื่อนักศึกษา", compared, shown_name),
                            f"ต้องสะกดตรงข้อมูลอนุมัติทุกหน้า: \"{shown_name}\"",
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
            # การ์ดสีส้ม มีปุ่ม ✓/✗ (เจ้าหน้าที่สั่ง ก.ย. 2569) — เดิมเป็นข้อมูลประกอบตามกติกา
            # "ฟอนต์เพี้ยนปล่อยผ่าน" แต่เจ้าหน้าที่ที่เปิดหน้าจริงแล้วพบว่ารหัสพิมพ์ผิดจริง
            # ไม่มีปุ่มส่งเข้าข้อความสรุป และตารางผลเทียบขึ้นสีส้มโดยไม่มีการ์ดคู่กัน
            rep.add(FONT_UNREADABLE_ZONE, "front_matter", _loc,
                    "ระบบอ่านรหัสนักศึกษาบนหน้านี้ไม่ออก เพราะฟอนต์ในไฟล์ทำให้ตัวเลข"
                    f'ถูกดึงออกมาเป็น "{_misread}"',
                    f'รหัสนักศึกษาต้องเป็น "{soft(A.get("student_id", ""))}"',
                    "เปิดหน้านี้ดูรหัสนักศึกษาด้วยตา ถ้าพิมพ์ถูกต้องให้กดผ่าน",
                    "FORM.FONT_UNREADABLE")

        # ชื่อนักศึกษาในบทคัดย่อ: ไม่พบ = 🔴, มีคำนำหน้า = 🟠
        if A.get("program_language") in ("thai", "thai_english"):
            name_checks = [
                (student_name_th, abs_th_idx, "บทคัดย่อภาษาไทย", "ชื่อภาษาไทย"),
                (student_name, abs_en_idx, "บทคัดย่อภาษาอังกฤษ", "ชื่อภาษาอังกฤษ"),
            ]
        else:
            name_checks = [(student_name, abs_en_idx, "บทคัดย่อ", "ชื่อนักศึกษา")]
        for nm3, aidx, albl, nlbl in name_checks:
            # ไม่มีชื่อให้เทียบ = ข้าม ไม่ขึ้นการ์ด — ช่องชื่อบังคับกรอกทั้งหน้าเว็บและ /check
            # (FRONT_MATTER_RULES["required_form_fields"]) เล่มจึงถูกปฏิเสธก่อนถึงตรงนี้
            # เดิมมีการ์ดแดง "ไม่ได้กรอก...ของนักศึกษาในฟอร์ม" ซึ่งขึ้นผ่านหน้าเว็บไม่ได้
            # เจ้าหน้าที่ยืนยันให้เอาออก (ก.ย. 2569) เหมือนการ์ด "ไม่ได้กรอกข้อมูลอนุมัติ"
            if not nm3:
                continue
            if aidx is None:
                # เล่มไม่มีหน้าบทคัดย่อภาษานี้ — กฎ "ภาษาครบตามหลักสูตร" ฟ้องแดงไปแล้ว
                rep.add_verification("ชื่อนักศึกษา", albl, "skipped",
                                     f"เล่มไม่มีหน้า{albl}")
                continue
            core3 = _strip_student_title(nm3)
            compared = compare_reference_text(pages[aidx], core3, 'student_name')
            if aidx in unreadable_digit_pages and compared['status'] != 'exact':
                # หน้าที่ฟอนต์เสีย ตรวจ "สิ่งที่อ่านได้" ตามปกติ ชื่อที่ตรงก็ผ่าน (เล่มจริงเทียบ
                # ชื่อไทยได้ 1.00 เต็มบนหน้าเดียวกับที่อ่านตัวเลขไม่ออก) ส่วนชื่อที่เทียบไม่ตรง
                # แยกไม่ออกว่าเล่มพิมพ์ผิดหรือระบบอ่านมาไม่ครบ จึงเป็นการ์ดส้มให้เจ้าหน้าที่ดู
                # หน้าจริง ไม่ใช่แดง — เดิมข้ามเงียบ ๆ ไม่มีการ์ด (เจ้าหน้าที่สั่ง ก.ย. 2569
                # ให้ฟอนต์เพี้ยนเป็นสีส้ม และกฎทั้งหน้าต้องไปทางเดียวกัน)
                rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})",
                                     "pending", "ระบบอ่านข้อความบนหน้านี้ไม่ครบ")
                rep.add(FONT_UNREADABLE_ZONE, "front_matter", f"{albl} ({page_ref(aidx)})",
                        f'{nlbl}ที่ระบบอ่านได้คือ "{compared["actual"]}" '
                        "ซึ่งอาจเพี้ยนเพราะฟอนต์ในไฟล์",
                        f'{nlbl}ต้องเป็น "{core3}"',
                        "เปิดหน้านี้ดูชื่อด้วยตา ถ้าพิมพ์ถูกต้องให้กดผ่าน",
                        "FORM.FONT_UNREADABLE")
                continue
            if compared['status'] != 'exact':
                # ช่องของชื่อบนหน้าบทคัดย่อคือข้อความหน้ารหัสนักศึกษา ต้องรายงานสิ่งที่
                # พิมพ์อยู่ตรงนั้นจริง ไม่ใช่บรรทัดที่คล้ายชื่อที่สุดทั้งหน้า
                printed_name = abstract_printed_name(pages[aidx])
                if printed_name:
                    compared = compare_values(printed_name, core3, 'student_name')
            if compared['status'] != 'exact':
                rep.add_verification("ชื่อนักศึกษา", f"{albl} ({page_ref(aidx)})",
                                     rep.mismatch_status(compared.get('marks_only'), compared['actual']),
                                     compared['actual'])
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
                rep.add_verification("รหัสนักศึกษา", "บทคัดย่อ", "skipped",
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
                    thai_digits = thai_numeral_student_id(printed, student_id, abs_label)
                    if thai_digits:
                        rep.add_verification("รหัสนักศึกษา", loc, "fail", printed)
                        rep.add("RED", "front_matter", loc, *thai_digits,
                                "FORM.APPROVED_MATCH")
                        continue
                    if printed:
                        # ชี้จุดต่างเฉพาะตอนที่ต่างกันจุดเดียว (พิมพ์ผิดหลักเดียว)
                        # ถ้าเป็นคนละรหัสกันคนละเรื่อง การไล่ทีละตัวอักษรจะได้
                        # "ต่างที่ 1 และ ขาด 35 และ มี 17 เกินมา" ซึ่งอ่านไม่รู้เรื่อง
                        # กว่าการดูรหัสเต็มสองอันเทียบกันเอง
                        analysis = analyze_diff(printed, student_id)
                        found_msg = f'บรรทัดชื่อนักศึกษาพิมพ์รหัสว่า "{printed}"'
                        if analysis.text and " และ " not in analysis.text:
                            found_msg = found_with_diff(found_msg, analysis)
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
        # template ทางการเขียนทั้งสองหน้าเป็น "ได้รับการพิจารณาให้นับเป็นส่วนหนึ่ง..." ถูกต้องอันเดียว
        # (ดู ethesis_rules.py) เล่มที่พิมพ์แบบสั้นจึงไม่ตรง template
        sig_template = (SIGNATURE_TEMPLATE_TH if thai_book else SIGNATURE_TEMPLATE_EN)
        for k, idx in enumerate(sig_pages):
            _report_signature_template(
                rep, f"หน้าลงนาม {k + 1} ({page_ref(idx)})", pages[idx],
                sig_template, sig_degree, thai_book)
        if cover_degree or sig_degree:
            degree_spots = []
            if cover_degree:
                # หน้าปกวางชื่อปริญญาไว้บรรทัดของมันเอง จึงตรวจคำเกินได้
                degree_spots.append(("หน้าปก", cover_text, cover_degree, True,
                                     cover_degree_line_expected(cover_degree, thai_book)))
            if sig_degree:
                # เล่มอังกฤษวางชื่อปริญญาไว้กลางประโยค template ("for the degree of ...")
                # มีคำอื่นล้อมรอบโดยชอบ จึงตรวจคำเกินไม่ได้ · เล่มไทยวาง "ปริญญา..." ไว้
                # บรรทัดของมันเองเหมือนหน้าปก จึงตรวจคำเกินด้วยกฎเดียวกัน (เจ้าหน้าที่ ต.ค. 2569:
                # หน้าลงนามพิมพ์ "ปริญญาสาธารณสุขศาสตร์มหาบัณฑิต (ภาคพิเศษ)" "ต้องผิดกฎเดียวกับ"
                # หน้าปก — เดิมผ่านเพราะถามแค่ว่ามีชื่อปริญญาอยู่บนหน้าไหม)
                degree_spots.extend((f"หน้าลงนาม {k + 1} ({page_ref(idx)})", pages[idx],
                                     sig_degree, thai_book,
                                     cover_degree_line_expected(sig_degree, True)
                                     if thai_book else sig_degree)
                                    for k, idx in enumerate(sig_pages))
            for spot_name, spot_text, expected_degree, own_line, expected_line in degree_spots:
                compared = compare_reference_text(spot_text, expected_degree, 'degree', degree_line=True)
                extras = degree_line_extras(spot_text, expected_degree) if own_line else ""
                if extras:
                    # ตรงเป๊ะแต่มีคำเกิน = แดง (เจ้าหน้าที่สั่ง ก.ย. 2569) ต้องมาก่อน
                    # กิ่ง exact เพราะ exact ตอบแค่ว่า "มีข้อความนี้อยู่บนหน้า"
                    rep.add_verification("ชื่อปริญญา", spot_name, "fail",
                                         f"มีข้อความเกิน: {extras}")
                    # ยกทั้งบรรทัดมาให้ดู แล้วบอกว่าบรรทัดนี้ต้องเป็นอะไรตาม template
                    # (ของเดิมเรียกทั้งบรรทัดว่า "ข้อความเกิน" ทั้งที่ส่วนใหญ่ของบรรทัดถูกต้อง)
                    rep.add("RED", "front_matter", spot_name,
                            f'บรรทัดชื่อปริญญาในเล่มเขียนว่า "{extras}"',
                            f'บรรทัดนี้ต้องเป็น "{expected_line}" เท่านั้น '
                            "ไม่มีคำอื่นต่อท้าย",
                            "ลบข้อความเกินออกจากบรรทัดชื่อปริญญา", "FORM.APPROVED_MATCH")
                    continue
                if compared['status'] == 'exact':
                    rep.add_verification("ชื่อปริญญา", spot_name, "pass")
                    continue
                if degree_differs_only_in_spacing(expected_degree, spot_text):
                    # ตัวอักษรครบทุกตัว ต่างเฉพาะเครื่องหมายวรรคตอน/การเว้นวรรค
                    # (เช่น "M.Sc. ()" กับ "M.Sc.()") = ข้อสังเกตสีเหลือง ผ่านได้
                    # ตามที่เจ้าหน้าที่กำหนด ส.ค. 2569
                    rep.add_verification("ชื่อปริญญา", spot_name, "notice",
                                         "ต่างเฉพาะวรรคตอน/ช่องว่าง")
                    rep.add(DEGREE_SPACING_ZONE, "front_matter", spot_name,
                            f'พบชื่อปริญญาแต่เครื่องหมายวรรคตอน/ช่องว่างต่างจากข้อมูลอนุมัติ: "{compared["actual"]}"',
                            f"ข้อมูลอนุมัติ: \"{expected_degree}\"",
                            "ไม่ต้องแก้ เว้นแต่เจ้าหน้าที่เห็นว่าควรแก้", "FORM.DEGREE_SPACING")
                else:
                    rep.add_verification("ชื่อปริญญา", spot_name,
                                         rep.mismatch_status(compared.get('marks_only'), compared['actual']),
                                         compared['actual'])
                    rep.add("RED", "front_matter", spot_name,
                            mismatch_detail("ชื่อปริญญา", compared, expected_degree),
                            f"ต้องเป็น \"{expected_degree}\"",
                            "แก้ชื่อปริญญาให้ตรงข้อมูลอนุมัติ", "FORM.APPROVED_MATCH")

        # ตรวจชื่อปริญญาแบบย่อในบทคัดย่อ — เล่มหลักสูตรไทย/ไทย-อังกฤษ ต้องตรวจทั้ง
        # บทคัดย่ออังกฤษ (M.Sc./Ph.D.) และบทคัดย่อไทย (วท.ม./ปร.ด.) จึงทำเป็น helper
        # ตัวย่อ = ค่าที่เจ้าหน้าที่กรอกในฟอร์มเสมอ — ช่องว่างไม่ถึงตรงนี้ เพราะฟอร์มและ /check บังคับกรอกช่องตัวย่อของ
        # บทคัดย่อทุกภาษาที่หลักสูตรมี และทุกภาษาที่มีชื่อปริญญา (อัปเดต 1.8.2 เจ้าของสั่ง 28 ต.ค. 2569)
        def _check_degree_abbr(abbr, abstract_idx, lang):
            if not abbr or abstract_idx is None:
                return
            abstract_text = pages[abstract_idx]
            compared = compare_reference_text(abstract_text, abbr, 'degree', degree_line=True, abstract=True)
            vloc = f"ชื่อย่อใน{lang} ({page_ref(abstract_idx)})"
            box = f"{lang} ({page_ref(abstract_idx)})"
            # ต้องตรวจคำเกินก่อนทุกกิ่ง — ของเดิมตรวจเฉพาะตอนเทียบได้ exact และเทียบ
            # ด้วย substring ดิบ ๆ เล่มที่เว้นวรรคต่างด้วย ("Dr.PH (PUBLIC HEALTH)"
            # กับ "Dr. P.H.") จึงหลุดไปกิ่ง "ต่างเฉพาะวรรคตอน" แล้วได้เหลืองผ่าน
            extras = degree_line_extras(abstract_text, abbr, abstract=True)
            if extras:
                rep.add_verification("ชื่อปริญญา", vloc, "fail",
                                     f"มีข้อความเกิน: {extras}")
                rep.add("RED", "front_matter", box,
                        f'บรรทัดชื่อปริญญาแบบย่อมีข้อความเกิน: "{extras}"',
                        f"บรรทัดนี้ต้องเป็น \"{abbr}\" เท่านั้น ไม่มีคำอื่นนำหน้าหรือต่อท้าย",
                        "ลบข้อความเกินออกจากบรรทัดชื่อปริญญา", "FORM.APPROVED_MATCH")
            elif compared['status'] == 'exact':
                rep.add_verification("ชื่อปริญญา", vloc, "pass")
            elif degree_differs_only_in_spacing(abbr, abstract_text, abstract=True):
                # ตัวอักษรครบ ต่างเฉพาะวรรคตอน/ช่องว่าง = ข้อสังเกตสีเหลือง ผ่านได้
                rep.add_verification("ชื่อปริญญา", vloc, "notice", "ต่างเฉพาะวรรคตอน/ช่องว่าง")
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
                rep.add_verification("ชื่อปริญญา", vloc,
                                     rep.mismatch_status(compared.get('marks_only'), compared['actual']),
                                     compared['actual'])
                rep.add("RED", "front_matter", box,
                        mismatch_detail("ชื่อปริญญาแบบย่อ", compared, abbr),
                        degree_abbr_expected(abbr),
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
        else:
            # ไม่มีรายชื่อกรรมการ (ไม่ได้แนบ eThesis หรืออ่านไม่ได้) — ข้อตำแหน่ง Dean/Director
            # ของหน้าลงนาม 2 ไม่ต้องใช้รายชื่อ ต้องตรวจต่อ ไม่ใช่ข้ามไปพร้อมทั้งหน้า
            _check_signature_heads_without_committees(rep, sig_pages, pages, pdf_path, page_ref)
        # หน้าบทคัดย่อ: รูปแบบรายชื่อกรรมการ (ตัวพิมพ์ใหญ่/วงเล็บ/ตำแหน่งวิชาการ) เป็นกฎ
        # ของ template ล้วน จึงตรวจเสมอ ส่วนการนับจำนวนทำเมื่อมีข้อมูล eThesis
        _check_abstract_committees(rep, committees, abs_en_pages, abs_th_pages,
                                   pages, page_ref, A.get("doc_type", ""))

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
                # ถ้อยคำเจ้าหน้าที่ (19 ต.ค. 2569) แทน "หลักสูตรนานาชาติใช้บทคัดย่ออังกฤษเท่านั้น"
                rep.add("RED", "front_matter", "บทคัดย่อ", "มีบทคัดย่อภาษาไทย",
                        "ต้องมีเพียงบทคัดย่อภาษาอังกฤษเท่านั้น ให้ลบบทคัดย่อไทยออก",
                        "ลบบทคัดย่อไทยออก", "FRONT.ABSTRACT")
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
                        found_msg = found_with_diff(found_msg, analyze_diff(head, section_label))
                        rep.add(
                            "RED", "front_matter", f"สารบัญ ({page_ref(typo_idx)})",
                            found_msg,
                            f'หัวข้อในสารบัญต้องสะกดว่า "{section_label}"',
                            f'แก้ตัวสะกดในสารบัญเป็น "{section_label}"',
                            "FRONT.TOC_CONTENT",
                        )
                        continue
                    # หัวข้อทุกตัวในชุดนี้ "มีอยู่ในเล่ม" แล้ว (หาเจอจากหน้าจริง) จึงบอกแบบนั้น
                    # เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้แจ้งแบบข้อภาคผนวก "เล่มมี... แต่ไม่ปรากฏ
                    # ในสารบัญ" — คำว่า "ไม่พบหัวข้อ" อ่านเหมือนเล่มไม่มีส่วนนั้น
                    rep.add(
                        "RED", "front_matter", f"สารบัญ ({page_ref(toc_pages[0])})",
                        f"เล่มมี{section_label}{_TOC_SECTION_GLOSS.get(section_kind, '')} "
                        "แต่ไม่ปรากฏในสารบัญ",
                        f"สารบัญต้องมีหัวข้อ {section_label} พร้อมเลขหน้า",
                        f"เพิ่มหัวข้อ {section_label} และเลขหน้าจริงลงในสารบัญ",
                        "FRONT.TOC_CONTENT",
                    )
                    continue
                entry = candidates[0]
                if section_kind == "biography":
                    _report_toc_biography_heading(
                        rep, entry["raw"], bio_want,
                        f"สารบัญ ({page_ref(entry['source_page_idx'])})")
                if section_kind == "references":
                    # (1) สารบัญต้องเลือกคำเดียว: REFERENCES หรือ BIBLIOGRAPHY (ไม่ใช่ทั้งคู่)
                    toc_terms = reference_terms(entry["raw"])
                    if len(toc_terms) > 1 or '/' in entry["raw"]:
                        one, other = reference_term_choice(book_wording_language, entry["raw"])
                        rep.add(
                            "RED", "front_matter", f"สารบัญ ({page_ref(entry['source_page_idx'])})",
                            f'หัวข้ออ้างอิงในสารบัญเลือกหลายคำ: "{_strip_toc_page_number(entry["raw"])}"',
                            f"ต้องเลือกใช้คำเดียว: {one} หรือ {other} อย่างใดอย่างหนึ่ง",
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
         "n_pages": n, "approved_data": bool(approved), "front_label_style": front_label_style,
         # ข้อรายชื่อกรรมการที่เจ้าหน้าที่ติ๊ก (อัปเดต 1.5) ต้องบอกหน้าและรายชื่อที่ได้รับอนุมัติ
         # ลำดับเดียวกับภาพในขั้นที่ 1 (signature_preview) — แผ่นสแกนก็เป็นภาพหน้าลงนามในขั้นนั้นด้วย
         "signature_refs": [page_ref(idx) for idx in sorted(sig_pages + scanned_sig)[:2]],
         "committees": committee_names_by_kind(approved)})
