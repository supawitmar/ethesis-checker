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


# ฟอนต์ไทย Angsana/Cordia ใน eThesis PDF เก็บวรรณยุกต์/การันต์ไว้ใน
# Private Use Area (U+F700-F70F): F700-F704 สระบน, F705-F709 วรรณยุกต์ตำแหน่งปกติ,
# F70A-F70E วรรณยุกต์ยกเหนือสระบน, F70F นิคหิต
# แมปกลับเป็นยูนิโค้ดปกติ ไม่งั้นชื่อ/หัวข้อภาษาไทยจะแสดงเป็นกล่องว่างในฟอร์ม
_PUA_TONE = {
    '\uf700': '\u0e31', '\uf701': '\u0e34', '\uf702': '\u0e35',
    '\uf703': '\u0e36', '\uf704': '\u0e37',
    '\uf705': '\u0e48', '\uf706': '\u0e49', '\uf707': '\u0e4a',
    '\uf708': '\u0e4b', '\uf709': '\u0e4c',
    '\uf70a': '\u0e48', '\uf70b': '\u0e49', '\uf70c': '\u0e4a',
    '\uf70d': '\u0e4b', '\uf70e': '\u0e4c',
    '\uf70f': '\u0e4d',
}
_PUA_LEFTOVER = re.compile('[\uf700-\uf71f]')


def _fix_thai_pua(text):
    for pua, real in _PUA_TONE.items():
        text = text.replace(pua, real)
    # สระอำมักถูกแตกเป็น นิคหิต+สระอา (ํ + า) — รวมกลับเป็น ำ
    text = text.replace('ํา', 'ำ')
    return _PUA_LEFTOVER.sub('', text)


def _lines_from_pages(pages):
    parts = [page.extract_text() or '' for page in pages]
    text = _fix_thai_pua('\n'.join(parts)).replace('\r', '').replace('**', '').replace(' ', ' ')
    out = []
    for raw in text.split('\n'):
        line = re.sub(r'[\t ]+', ' ', raw).strip()
        if line:
            out.append(line)
    return out


def _lines(pdf_path):
    with pdfplumber.open(pdf_path) as pdf:
        return _lines_from_pages(pdf.pages)


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


def _cell_value(pages, label):
    """ดึงค่าของช่องที่ค่าอาจห่อหลายบรรทัด (เช่น ชื่อหัวข้อ) ด้วยพิกัด (geometry)

    ในหน้า eThesis ป้ายอยู่คอลัมน์ซ้าย ค่าอยู่คอลัมน์ขวา เมื่อค่ายาวจะห่อเป็น
    หลายแถว และป้ายถูกจัดกึ่งกลางแนวตั้งจึงไปแทรกอยู่ "ระหว่าง" แถวของค่า
    (เช่น หัวข้อบรรทัดบน / ป้าย / หัวข้อบรรทัดล่าง) ทำให้อ่านแบบบรรทัดต่อบรรทัด
    ได้ค่าไม่ครบ จึงจับแต่ละแถวของค่าเข้ากับป้ายซ้ายที่ใกล้สุดตามแนวตั้ง แล้ว
    ต่อเฉพาะแถวที่เป็นของป้ายนี้เป็นข้อความเดียว (ไทยต่อชิด อังกฤษเว้นวรรค)
    """
    for page in pages:
        words = [dict(w, text=_fix_thai_pua(w['text'])) for w in page.extract_words()]
        target = next((w for w in words if w['text'].strip() == label), None)
        if not target:
            continue
        target_top = round(target['top'], 1)
        value_x = target['x1'] + 15
        # ป้ายทั้งหมดในคอลัมน์ซ้าย (x0 ใกล้ป้ายนี้) ใช้เป็นจุดอ้างอิงแนวตั้ง
        label_tops = sorted({round(w['top'], 1) for w in words
                             if abs(w['x0'] - target['x0']) <= 30 and w['text'].strip()})
        rows = {}
        for w in words:
            if w['x0'] < value_x:
                continue
            nearest = min(label_tops, key=lambda t: abs(t - w['top']))
            if nearest == target_top:
                rows.setdefault(round(w['top'], 1), []).append(w)
        if not rows:
            continue
        out = ''
        for top in sorted(rows):
            for w in sorted(rows[top], key=lambda w: w['x0']):
                token = w['text']
                if out and (re.match(r'[A-Za-z0-9(]', token)
                            or re.search(r'[A-Za-z0-9)]$', out)):
                    out += ' '
                out += token
        return out.strip()
    return ''


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


def _detect_format(pdf):
    """หารูปแบบที่ถูกเลือกในแถว 'ล่าสุด'

    ในหน้า eThesis ตัวเลือกรูปแบบเป็น radio button วาดด้วยเส้นโค้ง (วงกลม)
    ตัวที่ถูกเลือกจะมี "จุดทึบเล็ก" (curve เติมสีขนาด ~4-8px) อยู่กลางวง
    ส่วนวงนอกมีทั้งสองตัว จึงใช้จุดทึบเล็กเป็นตัวชี้ว่าเลือกอันไหน
    คืน '' ถ้าอ่านไม่ได้ (ให้เจ้าหน้าที่เลือกเอง)
    """
    for page in pdf.pages:
        words = [dict(w, text=_fix_thai_pua(w['text'])) for w in page.extract_words()]
        latest = next((w for w in words if w['text'].strip() == 'ล่าสุด'), None)
        if not latest:
            continue
        row_top = latest['top']
        options = []
        for word in words:
            if abs(word['top'] - row_top) <= 10:
                m = re.search(r'รูปแบบที่\s*([12])', word['text'])
                if m:
                    options.append((int(m.group(1)), word['x0']))
        if len(options) < 2:
            continue
        dots = [c for c in page.curves
                if abs(c['top'] - row_top) <= 10 and c.get('fill') and 3 <= c['width'] <= 8]
        counts = {}
        for dot in dots:
            option = min(options, key=lambda o: abs(o[1] - dot['x0']))
            counts[option[0]] = counts.get(option[0], 0) + 1
        if counts:
            return str(max(counts, key=counts.get))
    return ''


def parse_ethesis_pdf(pdf_path):
    """คืน dict ของค่าที่ดึงได้ (เฉพาะช่องที่พบ) สำหรับเติมแบบฟอร์ม"""
    with pdfplumber.open(pdf_path) as pdf:
        lines = _lines_from_pages(pdf.pages)
        fmt = _detect_format(pdf)
        # ชื่อหัวข้อมักห่อหลายบรรทัด ต้องอ่านด้วยพิกัด (geometry) ไม่งั้นได้ไม่ครบ
        title_th = _cell_value(pdf.pages, 'ชื่อหัวข้อภาษาไทย')
        title_en = _cell_value(pdf.pages, 'ชื่อหัวข้อภาษาอังกฤษ')
    data = {}
    if fmt:
        data['format'] = fmt

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

    data['title_th'] = title_th or _find(lines, 'ชื่อหัวข้อภาษาไทย')[0]
    data['title_en'] = title_en or _find(lines, 'ชื่อหัวข้อภาษาอังกฤษ')[0]

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

    # "แผนการศึกษา" ในหน้า eThesis มักอยู่คนละบรรทัดกับค่า จึงเก็บค่าจากทุก
    # ตำแหน่ง (ท้ายบรรทัดเดียวกันหรือบรรทัดถัดไป) — ค่าที่ต้องการคือ "วิทยานิพนธ์"
    plans = []
    for i, line in enumerate(lines):
        if line.startswith('แผนการศึกษา'):
            value = re.sub(r'^\s*[:：]?\s*', '', line[len('แผนการศึกษา'):]).strip()
            plans.append(value or _next(lines, i))
    if any(plan == 'วิทยานิพนธ์' for plan in plans):
        data['doc_type'] = 'THESIS'
    elif any(plan == 'สารนิพนธ์' for plan in plans):
        data['doc_type'] = 'THEMATIC PAPER'
    elif any(plan == 'การค้นคว้าอิสระ' for plan in plans):
        data['doc_type'] = 'INDEPENDENT STUDY'

    return {key: value for key, value in data.items() if value}
