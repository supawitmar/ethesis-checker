# -*- coding: utf-8 -*-
"""อ่านข้อมูลอนุมัติจากไฟล์ PDF ที่พิมพ์จากระบบ eThesis เพื่อ "เติมแบบฟอร์ม" อัตโนมัติ

ค่าที่ได้เป็นเพียงตัวช่วยกรอก เจ้าหน้าที่ต้องตรวจทานทุกช่องก่อนตรวจเล่ม
โครงสร้างข้อความอิงจากหน้าข้อมูลนักศึกษาใน eThesis ที่จัดเป็นตารางป้าย-ค่า
(เช่น "รหัสนักศึกษา   6537730 EGIT/D")

ตัวเลือก "รูปแบบ" มาจากช่องติ๊กในแถว "ล่าสุด" ของหัวข้อ
"การกำหนดรูปแบบรูปเล่มอิเล็กทรอนิกส์" ซึ่งบางไฟล์ติ๊กเป็นภาพ อ่านอัตโนมัติไม่ได้
กรณีนั้นจะไม่ส่งค่า format กลับ เพื่อให้เจ้าหน้าที่เลือกเอง
"""
import re
import pdfplumber

THAI_PREFIX = re.compile(
    r'^(?:นางสาว|นาย|นาง|น\.ส\.|ด\.ญ\.|ด\.ช\.|'
    r'ว่าที่\s*(?:ร|พ)\.?[ตทอ]\.?|ดร\.?|ผศ\.?|รศ\.?|ศ\.?)\s*'
)
EN_PREFIX = re.compile(r'^(?:MR|MRS|MISS|MS|DR)\.?\s+', re.I)

THAI_MONTHS = {
    'มกราคม': 'January', 'กุมภาพันธ์': 'February', 'มีนาคม': 'March',
    'เมษายน': 'April', 'พฤษภาคม': 'May', 'มิถุนายน': 'June',
    'กรกฎาคม': 'July', 'สิงหาคม': 'August', 'กันยายน': 'September',
    'ตุลาคม': 'October', 'พฤศจิกายน': 'November', 'ธันวาคม': 'December',
}

DEGREE_ABBR = {
    'DOCTOR OF PHILOSOPHY': 'Ph.D.',
    'MASTER OF ENGINEERING': 'M.Eng.',
    'MASTER OF SCIENCE': 'M.Sc.',
    'MASTER OF ARTS': 'M.A.',
    'MASTER OF BUSINESS ADMINISTRATION': 'M.B.A.',
    'MASTER OF PUBLIC HEALTH': 'M.P.H.',
    'MASTER OF NURSING SCIENCE': 'M.N.S.',
    'MASTER OF EDUCATION': 'M.Ed.',
}

MINOR_WORDS = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from',
               'in', 'of', 'on', 'or', 'the', 'to', 'with'}

# สัญลักษณ์ติ๊กที่พบได้ในข้อความ PDF (ถ้าติ๊กเป็นภาพจะไม่มีตัวใดเลย)
TICK = r'(?:☑|☒|✓|✔|●|◉|◾|■|\[x\]|\(x\))'


def _lines(pdf_path):
    parts = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or '')
    text = '\n'.join(parts).replace('\r', '').replace('**', '').replace(' ', ' ')
    out = []
    for raw in text.split('\n'):
        line = re.sub(r'[\t ]+', ' ', raw).strip()
        if line:
            out.append(line)
    return out


def _find(lines, label):
    """คืน (ค่า, index ของบรรทัดป้าย) — ค่ามาจากท้ายบรรทัดเดียวกันหรือบรรทัดถัดไป

    รองรับทั้งกรณีมีช่องว่าง/เครื่องหมายคั่นระหว่างป้ายกับค่า และกรณีที่ PDF
    บางไฟล์ดึงป้ายติดกับค่าโดยไม่มีตัวคั่น (เช่น "รหัสนักศึกษา6537730")
    """
    for i, line in enumerate(lines):
        if line == label:
            return (lines[i + 1] if i + 1 < len(lines) else ''), i
        if line.startswith(label):
            remainder = line[len(label):]
            # ต้องขึ้นต้นด้วยตัวคั่น หรือตัวเลข/วงเล็บ (ค่า) — กันชนกับป้ายอื่นที่
            # ขึ้นต้นเหมือนกัน เช่น "วันที่สอบ" vs "วันที่สอบผ่าน"
            if remainder[:1] in (' ', '\t', ':', '：') or remainder[:1].isdigit() \
                    or remainder[:1] in '([（':
                value = re.sub(r'^\s*[:：]?\s*', '', remainder).strip()
                return (value or (lines[i + 1] if i + 1 < len(lines) else '')), i
    return '', -1


def _next(lines, index, offset=1):
    return lines[index + offset] if index >= 0 and 0 <= index + offset < len(lines) else ''


def _degree_name(value):
    v = re.sub(r'\s*\(\s*', ' (', value)
    v = re.sub(r'\s*\)\s*', ')', v)
    v = re.sub(r'\s+', ' ', v).strip().lower()

    def cap(m):
        word, off = m.group(0), m.start()
        starts = off == 0 or v[off - 1] == '('
        if not starts and word in MINOR_WORDS:
            return word
        return word[:1].upper() + word[1:]

    return re.sub(r'[a-z]+', cap, v)


def _degree_abbr(value):
    v = re.sub(r'\s*\(\s*', ' (', value)
    v = re.sub(r'\s*\)\s*', ')', v)
    v = re.sub(r'\s+', ' ', v).strip()
    m = re.match(r'^(.+?)(?:\s*\((.+)\))?$', v)
    if not m:
        return ''
    name = m.group(1).strip().upper()
    field = (m.group(2) or '').strip().upper()
    abbr = DEGREE_ABBR.get(name)
    if not abbr:
        return ''
    return f'{abbr} ({field})' if field else abbr


def _exam_date(value, use_english):
    v = re.sub(r'\s+', ' ', value).strip()
    m = re.match(r'^(\d{1,2})\s+(\S+)\s+(25\d{2}|20\d{2})$', v)
    if not use_english or not m or m.group(2) not in THAI_MONTHS:
        return v
    year = int(m.group(3))
    if year > 2400:
        year -= 543
    return f'{int(m.group(1))} {THAI_MONTHS[m.group(2)]} {year}'


def _detect_format(lines):
    """หาเลขรูปแบบที่ถูกติ๊กในแถว 'ล่าสุด' — คืน '' ถ้าติ๊กเป็นภาพ (อ่านไม่ได้)"""
    for line in lines:
        if 'ล่าสุด' not in line:
            continue
        marked = re.search(TICK + r'\s*รูปแบบที่\s*([12])', line, re.I)
        if marked:
            return marked.group(1)
        # ถ้าไม่มีสัญลักษณ์ติ๊กเลย และเห็นทั้ง 1 และ 2 = อ่านไม่ได้
    return ''


def parse_ethesis_pdf(pdf_path):
    """คืน dict ของค่าที่ดึงได้ (เฉพาะช่องที่พบ) สำหรับเติมแบบฟอร์ม"""
    lines = _lines(pdf_path)
    data = {}

    id_value, id_index = _find(lines, 'รหัสนักศึกษา')
    id_match = re.search(r'\b\d{7}\b', id_value + ' ' + _next(lines, id_index))
    if id_match:
        data['student_id'] = id_match.group(0)

    name_value, name_index = _find(lines, 'ชื่อ-สกุล')
    if name_value:
        if re.search(r'[ก-๙]', name_value):
            data['student_name_th'] = THAI_PREFIX.sub('', name_value)
        else:
            data['student_name'] = EN_PREFIX.sub('', name_value)
        offset = 2 if name_value == _next(lines, name_index) else 1
        following = _next(lines, name_index, offset)
        if following and re.search(r'[A-Za-z]', following) and not re.search(r'[ก-๙]', following):
            data['student_name'] = EN_PREFIX.sub('', following)

    data['title_th'] = _find(lines, 'ชื่อหัวข้อภาษาไทย')[0]
    data['title_en'] = _find(lines, 'ชื่อหัวข้อภาษาอังกฤษ')[0]

    course = _find(lines, 'หลักสูตร')[0]
    writing = _find(lines, 'ภาษาที่เขียน')[0]
    if re.search(r'นานาชาติ', course):
        data['program_language'] = 'international'
    elif re.search(r'อังกฤษ|english', writing, re.I):
        data['program_language'] = 'thai_english'
    elif re.search(r'ไทย|thai', writing, re.I):
        data['program_language'] = 'thai'

    degree_value, degree_index = _find(lines, 'ชื่อปริญญา')
    if degree_index >= 0:
        candidates = [degree_value] if degree_value else []
        for offset in (1, 2):
            candidate = _next(lines, degree_index, offset)
            if candidate and candidate != degree_value:
                candidates.append(candidate)
        english = next((c for c in candidates
                        if re.search(r'[A-Za-z]', c) and not re.search(r'[ก-๙]', c)), '')
        thai = next((c for c in candidates if re.search(r'[ก-๙]', c)), '')
        if english:
            data['degree_source'] = english
            data['degree'] = _degree_name(english)
            abbr = _degree_abbr(english)
            if abbr:
                data['degree_abbr'] = abbr
        if thai:
            data['degree_th'] = thai

    exam_value = _find(lines, 'วันที่สอบผ่าน')[0]
    use_english = data.get('program_language') != 'thai'
    if exam_value:
        data['exam_date'] = _exam_date(exam_value, use_english)
        year_match = re.search(r'\b(25\d{2}|20\d{2})\b', data['exam_date'])
        if year_match:
            year = int(year_match.group(1))
            data['year'] = str(year - 543) if (use_english and year > 2400) else str(year)

    plans = [line[len('แผนการศึกษา'):].strip()
             for line in lines if line.startswith('แผนการศึกษา ')]
    if any(plan == 'วิทยานิพนธ์' for plan in plans):
        data['doc_type'] = 'THESIS'
    elif any(plan == 'สารนิพนธ์' for plan in plans):
        data['doc_type'] = 'THEMATIC PAPER'
    elif any(plan == 'การค้นคว้าอิสระ' for plan in plans):
        data['doc_type'] = 'INDEPENDENT STUDY'

    fmt = _detect_format(lines)
    if fmt:
        data['format'] = fmt

    return {key: value for key, value in data.items() if value}
