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
import unicodedata

import pdfplumber

from thai_text import fix_thai_pua, fold_sara_am, strip_student_title

THAI_MONTHS = {
    'มกราคม': 'January', 'กุมภาพันธ์': 'February', 'มีนาคม': 'March',
    'เมษายน': 'April', 'พฤษภาคม': 'May', 'มิถุนายน': 'June',
    'กรกฎาคม': 'July', 'สิงหาคม': 'August', 'กันยายน': 'September',
    'ตุลาคม': 'October', 'พฤศจิกายน': 'November', 'ธันวาคม': 'December',
}

# ตัวย่อปริญญาอังกฤษ "เดาจากตัวอักษรแรกไม่ได้" — DOCTOR OF PHILOSOPHY ไม่ใช่ D.O.P.
# และ MASTER OF SCIENCE ไม่ใช่ M.S. จึงต้องเปิดตารางเท่านั้น ไม่เดาเอง
# ปริญญาที่ยังไม่มีในตาราง ระบบจะไม่เดาและไม่ฟ้องแดง (ดู FORM.REQUIRED ใน checker)
# ตัวย่อชื่อปริญญาอังกฤษ — ต้องเป็น "ตารางตายตัว" เดาจากอักษรตัวแรกของแต่ละคำไม่ได้
# เพราะแบบแผนไม่สม่ำเสมอ: DOCTOR OF PUBLIC HEALTH ย่อว่า "Dr. P.H." ไม่ใช่ "D.P.H."
# ขณะที่ DOCTOR OF PUBLIC ADMINISTRATION ย่อว่า "D.P.A." ตามแบบปกติ
# ชื่อที่ไม่อยู่ในตารางคืน '' เพื่อให้เจ้าหน้าที่กรอกเอง ดีกว่าเดาผิดแล้วฟ้องเล่มที่ถูก
# ตารางนี้เป็นชุดเดียวของระบบ — หน้าเว็บเคยมีตารางซ้ำสำหรับทางวางข้อความ (หลุดกันจริง ขาด
# D.N.S. / D.P.A. / Dr. P.H. / M.P.A.) ทางนั้นถูกถอดออกแล้ว (ก.ย. 2569) อย่าเพิ่มตารางซ้ำกลับไป
# ทางวางข้อความหน้าประวัตินักศึกษา MIS (อัปเดต 1.16) อ่านฝั่งเซิร์ฟเวอร์ด้วย parse_mis_profile ซึ่งใช้ตารางนี้ ไม่มีตารางในหน้าเว็บ
DEGREE_ABBR = {
    'DOCTOR OF PHILOSOPHY': 'Ph.D.',
    'DOCTOR OF NURSING SCIENCE': 'D.N.S.',      # ยืนยันจากเล่มจริง (เล่มที่ 6)
    'DOCTOR OF PUBLIC ADMINISTRATION': 'D.P.A.',
    'DOCTOR OF PUBLIC HEALTH': 'Dr. P.H.',      # ไม่ใช่ D.P.H. — เจ้าหน้าที่ยืนยัน ส.ค. 2569
    'DOCTOR OF APPLIED THAI TRADITIONAL MEDICINE': 'D.ATM.',    # เจ้าของยืนยัน 28 ต.ค. 2569 (อัปเดต 1.8.2)
    'MASTER OF APPLIED THAI TRADITIONAL MEDICINE': 'M.ATM.',    # เจ้าของยืนยัน 29 ต.ค. 2569 (เพิ่มหลังออก 1.8.2)
    'MASTER OF CLINICAL TROPICAL MEDICINE': 'M.C.T.M.',   # เจ้าหน้าที่พบในแบบฟอร์ม ก.ย. 2569
    'MASTER OF ENGINEERING': 'M.Eng.',
    'MASTER OF SCIENCE': 'M.Sc.',
    'MASTER OF ARTS': 'M.A.',
    'MASTER OF BUSINESS ADMINISTRATION': 'M.B.A.',
    'MASTER OF PUBLIC ADMINISTRATION': 'M.P.A.',
    'MASTER OF PUBLIC HEALTH': 'M.P.H.',
    'MASTER OF NURSING SCIENCE': 'M.N.S.',
    'MASTER OF EDUCATION': 'M.Ed.',
    # หลักสูตรศิลปศาสตรและวิทยาศาสตรมหาบัณฑิต สาขาวิชากลยุทธ์ทางธุรกิจ (ภาคพิเศษ) — เจ้าของยืนยัน 18 พ.ย. 2569 "M.A.S. (Business Strategy)"
    # ชื่อเต็มในระบบตามเจ้าของ "MASTER OF ARTS AND SCIENCE(BUSINESS STRATEGY)" · เทียบทั้งชื่อ ไม่ชน 'MASTER OF ARTS'
    'MASTER OF ARTS AND SCIENCE': 'M.A.S.',
}

MINOR_WORDS = {'a', 'an', 'and', 'as', 'at', 'by', 'for', 'from',
               'in', 'of', 'on', 'or', 'the', 'to', 'with'}

# ตัวย่อชื่อปริญญาไทย = อักษรย่อของสาขา + ระดับ (.ม. = มหาบัณฑิต, .ด. = ดุษฎีบัณฑิต)
# เช่น วิทยาศาสตรมหาบัณฑิต → วท.ม. / ปรัชญาดุษฎีบัณฑิต → ปร.ด.
# eThesis ไม่มีตัวย่อไทยให้ดึง จึงเดาจากชื่อปริญญาไทย (เจ้าหน้าที่แก้ได้)
DEGREE_ABBR_TH_STEM = {
    'ปรัชญา': 'ปร',
    'วิทยาศาสตร': 'วท',
    'ศิลปศาสตร': 'ศศ',
    'วิศวกรรมศาสตร': 'วศ',
    'พยาบาลศาสตร': 'พย',
    'สาธารณสุขศาสตร': 'ส',
    'บริหารธุรกิจ': 'บธ',
    'ศึกษาศาสตร': 'ศษ',
    'รัฐประศาสนศาสตร': 'รป',
    'เภสัชศาสตร': 'ภ',
    'สังคมศาสตร': 'สค',
    'อักษรศาสตร': 'อ',
    'นิเทศศาสตร': 'นศ',
    'เทคโนโลยีสารสนเทศ': 'ทส',
}

# ตัวย่อไทยที่ยืนยันเป็นรายชื่อเต็ม (สาขา + ระดับ) — ไม่ใส่ใน DEGREE_ABBR_TH_STEM เพราะ stem ใช้กับทั้งสองระดับ
# แล้วจะได้ระดับที่ไม่มีใครยืนยัน · เพิ่มทีละระดับเมื่อเจ้าของยืนยัน
DEGREE_ABBR_TH_FULL = {
    'การแพทย์แผนไทยประยุกต์ดุษฎีบัณฑิต': 'พทป.ด.',     # เจ้าของยืนยัน 28 ต.ค. 2569 (อัปเดต 1.8.2)
    'การแพทย์แผนไทยประยุกต์มหาบัณฑิต': 'พทป.ม.',      # เจ้าของยืนยัน 29 ต.ค. 2569 (เพิ่มหลังออก 1.8.2)
    'ศิลปศาสตรและวิทยาศาสตรมหาบัณฑิต': 'ศศ.วท.ม.',    # เจ้าของยืนยัน 18 พ.ย. 2569 (สาขาวิชากลยุทธ์ทางธุรกิจ)
}


# ไฟล์ eThesis (ฟอนต์ DTAC2013) เก็บสระ/วรรณยุกต์ที่ต้องขยับตำแหน่ง และ ญ ฐ ที่ตัดเชิง เป็นรหัส Private Use Area
# ชุดเดียวกับฟอนต์ TH Sarabun ในเล่ม — แปลงกลับด้วยตารางเดียวกัน (thai_text.fix_thai_pua ซึ่งอ่านมาจากตัวฟอนต์)
# ไม่งั้นชื่อ/หัวข้อภาษาไทยจะแสดงเป็นกล่องว่างในฟอร์ม หรือถูก _PUA_LEFTOVER ลบทิ้งเงียบ ๆ จนชื่อกรรมการขาดสระ
# ตารางเดิมของไฟล์นี้เดา U+F70F เป็นนิคหิต แต่ในฟอนต์คือ ญ ตัดเชิง — ไฟล์ eThesis ทดสอบมีชื่อกรรมการที่สะกด "ญญู"
# ญ ตัวที่สองจึงกลายเป็นนิคหิตในรายชื่อตาม บฑ. ที่รายงานพิมพ์ให้เจ้าหน้าที่ทาน (แก้ ต.ค. 2569)
_PUA_LEFTOVER = re.compile('[\uf700-\uf71f]')


def _fix_thai_pua(text):
    text = fix_thai_pua(text)
    # สระอำมักถูกแตกเป็น นิคหิต+สระอา (ํ + า) และเมื่อมีวรรณยุกต์เป็น นิคหิต+วรรณยุกต์+สระอา (นํ้า)
    # — รวมกลับเป็น ำ ทุกแบบ
    text = fold_sara_am(text)
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


def _spaced_degree(value):
    """จัดชื่อปริญญาให้เว้นวรรค 1 เคาะก่อนวงเล็บสาขาเสมอ

    eThesis พิมพ์ติดกัน เช่น "MASTER OF SCIENCE(INFORMATION TECHNOLOGY MANAGEMENT)"
    ทำให้ค่าที่เติมในฟอร์มอ่านยาก จึงเว้นวรรคให้เป็นมาตรฐานเดียวกันทุกช่อง
    (การเทียบกับเล่มไม่สนใจช่องว่างอยู่แล้ว จึงไม่กระทบผลตรวจ)
    """
    v = re.sub(r'\s*\(\s*', ' (', value or '')
    v = re.sub(r'\s*\)\s*', ')', v)
    return re.sub(r'\s+', ' ', v).strip()


def _degree_abbr_th(value):
    """เดาตัวย่อชื่อปริญญาไทย เช่น "ปรัชญาดุษฎีบัณฑิต(อายุรศาสตร์เขตร้อน)" → "ปร.ด. (อายุรศาสตร์เขตร้อน)"

    คืน '' ถ้าเดาไม่ได้ (สาขาไม่อยู่ในตาราง) ให้เจ้าหน้าที่กรอกเอง
    """
    v = re.sub(r'\s+', '', value or '')
    m = re.match(r'^(.+?)(ดุษฎีบัณฑิต|มหาบัณฑิต)\(?(.*?)\)?$', v)
    if not m:
        return ''
    stem, level, field = m.group(1), m.group(2), m.group(3).strip()
    # ชื่อเต็มเทียบแบบไม่สนการันต์ — eThesis/เล่มบางฉบับพิมพ์ "ศิลปศาสตร์และวิทยาศาสตร์..." (ชื่อปริญญาที่ถูกไม่มีการันต์)
    base = DEGREE_ABBR_TH_FULL.get(stem + level) or DEGREE_ABBR_TH_FULL.get(stem.replace('์', '') + level)
    if not base:
        abbr = DEGREE_ABBR_TH_STEM.get(stem)
        if not abbr:
            return ''
        base = f"{abbr}.{'ด' if level == 'ดุษฎีบัณฑิต' else 'ม'}."
    return f'{base} ({field})' if field else base


# ค่าที่จะรับเป็น "วันที่สอบผ่าน" ต้องมีเลขปี (พ.ศ./ค.ศ.) หรือเขียนเป็นวันที่มีขีดคั่น
# หน้า eThesis บางใบเว้นช่องนี้ว่างไว้ บรรทัดถัดจากหัวข้อจึงเป็น "หัวข้อถัดไป" ไม่ใช่ค่า
# เจอกับเล่มจริง (ก.ย. 2569): ได้ "การกำหนดรูปแบบรูปเล่ม" มาเป็นวันที่สอบผ่าน แล้วฟอร์ม
# เติมค่านั้นให้อัตโนมัติ ถ้าเจ้าหน้าที่ไม่ทันแก้ รายงานจะฟ้องนักศึกษาว่า
#     พบวันที่สอบผ่านไม่ตรงกันกับในระบบ: "07 July 2026"
#     ต้องแก้เป็น "การกำหนดรูปแบบรูปเล่ม"
# ปล่อยว่างไว้ให้เจ้าหน้าที่กรอกเอง ดีกว่าเติมค่าที่ไม่ใช่วันที่
_DATE_LIKE = re.compile(r'\d{1,2}\s*[/\-.]\s*\d{1,2}\s*[/\-.]\s*\d{2,4}'
                        r'|(?<!\d)(?:25\d{2}|20\d{2})(?!\d)')


def _looks_like_a_date(value):
    return bool(_DATE_LIKE.search(value or ''))


# ช่อง "วันที่สอบ" ในหน้า eThesis เขียนแบบอเมริกัน ("July 7, 2026") ส่วนเล่มพิมพ์
# แบบวันขึ้นก่อน ("07 July 2026") ถ้าไม่เรียงใหม่ ระบบจะฟ้องว่าวันที่ไม่ตรงทั้งที่เป็น
# วันเดียวกัน (เลขวันมีศูนย์นำหน้าหรือไม่ ตัวเทียบยอมอยู่แล้ว)
_MONTH_FIRST_DATE = re.compile(r'^([A-Za-z]+)\s+(\d{1,2})\s*,?\s*(25\d{2}|20\d{2})$')


def _exam_date(value, use_english):
    v = re.sub(r'\s+', ' ', value).strip()
    month_first = _MONTH_FIRST_DATE.match(v)
    if month_first:
        v = f'{int(month_first.group(2))} {month_first.group(1)} {month_first.group(3)}'
    m = re.match(r'^(\d{1,2})\s+(\S+)\s+(25\d{2}|20\d{2})$', v)
    if not use_english or not m or m.group(2) not in THAI_MONTHS:
        return v
    year = int(m.group(3))
    if year > 2400:
        year -= 543
    return f'{int(m.group(1))} {THAI_MONTHS[m.group(2)]} {year}'


# บทบาทกรรมการ (อยู่ท้ายบรรทัด) และคำนำหน้าวิชาการ (อยู่ต้นบรรทัด) ในหน้า eThesis
# บรรทัดกรรมการรูปแบบ: "<เลข> <คำนำหน้า+ชื่อ-สกุล> <บทบาท> [(ผู้ทรงคุณวุฒิภายนอก)]"
_COMMITTEE_ROLE_RE = re.compile(
    r'\s*((?:อาจารย์ที่ปรึกษา|ประธาน|กรรมการ|ผู้ทรงคุณวุฒิ)\S*'
    r'(?:\s*\([^)]*\))?)\s*$')
# ตัวย่อไทย (ผศ. รศ. ศ. ดร.) บังคับต้องมีจุด ไม่งั้นจะกินอักษรตัวแรกของชื่อจริง
# ที่ขึ้นต้นด้วย ศ เช่น "ศศิธร" -> "ศิธร" หรือ "ศิริพร" -> "ิริพร" (ข้อมูลอนุมัติเพี้ยน
# ตั้งแต่ต้นทาง แล้วทำให้เล่มที่ถูกต้องถูกฟ้องว่าชื่อกรรมการไม่ตรง)
_ACADEMIC_TITLE_RE = re.compile(
    r'^\s*(?:ศาสตราจารย์|รองศาสตราจารย์|ผู้ช่วยศาสตราจารย์|อาจารย์|ผศ\.|รศ\.|ศ\.)?'
    r'\s*(?:ดร\.)?\s*')


def _committee_member(line):
    """แยกบรรทัด "<เลข> <คำนำหน้า+ชื่อ> <บทบาท>" → {'name','role'} หรือ None

    ตัดเลขลำดับหน้า, บทบาทท้ายบรรทัด, และคำนำหน้าวิชาการต้นบรรทัด เหลือเฉพาะชื่อ-สกุล
    (eThesis เลขลำดับมีช่องว่างได้ เช่น 1,2,4,5 — จึงยึด "ลำดับที่ปรากฏ" ไม่ยึดเลข)
    """
    m = re.match(r'^\s*\d+\s+(.*\S)\s*$', line)
    if not m:
        return None
    rest = m.group(1)
    role = ''
    role_m = _COMMITTEE_ROLE_RE.search(rest)
    if role_m:
        role = re.sub(r'\s+', ' ', role_m.group(1)).strip()
        rest = rest[:role_m.start()].strip()
    name = _ACADEMIC_TITLE_RE.sub('', rest).strip()
    # ชื่อคนไม่มีตัวเลข — กันบรรทัดที่ขึ้นต้นด้วยเลขแต่ไม่ใช่รายชื่อ (เช่น "1 มกราคม 2569")
    # ถูกนับเป็นกรรมการ แล้วทำให้ระบบฟ้องว่ามีกรรมการเกินจากข้อมูลอนุมัติ
    if not name or re.search(r'\d', name):
        return None
    return {'name': name, 'role': role}


# หัวข้อถัดไปในหน้า eThesis ที่บอกว่าจบรายชื่อกรรมการแล้ว — ใช้กันไม่ให้หัวข้อพวกนี้
# ถูกเข้าใจผิดว่าเป็นบรรทัดที่ห่อมาจากกรรมการคนก่อนหน้า
_COMMITTEE_STOP_RE = re.compile(
    r'^(?:กำหนดสอบ|การเปลี่ยนแปลง|คณะกรรมการ|ผลการสอบ|วันที่|หัวข้อ)')


def _is_committee_continuation(prev_line, line):
    """บรรทัดนี้ห่อมาจากบรรทัดกรรมการก่อนหน้าหรือไม่

    บรรทัดกรรมการรูปแบบ "<เลข> <ชื่อ> <บทบาท> [(ผู้ทรงคุณวุฒิภายนอก)]" ถ้า PDF
    ตัดบรรทัดกลางวงเล็บหรือกลางชื่อ บรรทัดต่อมาจะไม่ขึ้นต้นด้วยเลขลำดับ
    ถ้าไม่ต่อกลับ รายชื่อจะขาดคน แล้วระบบจะฟ้องผิดว่าเล่มมีกรรมการไม่ครบ
    """
    if not line or re.match(r'^\s*\d', line) or _COMMITTEE_STOP_RE.match(line):
        return False
    if prev_line.count('(') > prev_line.count(')'):
        return True                                  # วงเล็บยังไม่ปิด
    # ชื่อห่อบรรทัด: บรรทัดก่อนยังไม่มีบทบาท แต่บรรทัดนี้ลงท้ายด้วยบทบาท
    return bool(_COMMITTEE_ROLE_RE.search(line)) and not _COMMITTEE_ROLE_RE.search(prev_line)


def parse_committees(lines):
    """ดึงคณะกรรมการที่ปรึกษาและกรรมการสอบจากหน้า eThesis เรียงตามที่ปรากฏ

    คืน {'advisory': [{'name','role'}...], 'exam': [...]}
    """
    result = {'advisory': [], 'exam': []}
    target, raw = None, []      # raw = บรรทัดดิบของกรรมการคนล่าสุด (ไว้ต่อบรรทัดที่ห่อ)
    for line in lines:
        if line.startswith('คณะกรรมการที่ปรึกษา'):
            target, raw = 'advisory', []
            continue
        if line.startswith('คณะกรรมการสอบ'):
            target, raw = 'exam', []
            continue
        if target is None:
            continue
        member = _committee_member(line)
        if member:
            result[target].append(member)
            raw = [line]
            continue
        if raw and _is_committee_continuation(raw[-1], line):
            merged = _committee_member(' '.join(raw + [line]))
            if merged:
                result[target][-1] = merged
                raw.append(line)
                continue
        if result[target]:
            target, raw = None, []   # จบลิสต์เมื่อพ้นบรรทัดของกรรมการ
    return result


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


def _student_id(text):
    """ดึงรหัสนักศึกษาเต็มจากข้อความ

    รหัสเต็ม = เลข 7 หลัก + รหัสหลักสูตร เช่น "6838141 SHSS/M" ต้องใช้ทั้งชุด
    เพราะบทคัดย่อพิมพ์ครบทั้งสองส่วน  ถ้าหารหัสหลักสูตรไม่เจอจึงใช้เลข 7 หลักอย่างเดียว
    (lookaround กันไปตรงกับเลขชุดอื่น แต่ยอมให้ตัวอักษรติดท้ายได้ เช่น "6838141SHSS/M")
    """
    full = re.search(r'(?<!\d)(\d{7})\s*([A-Z]{2,8}/[A-Z])', text or '')
    if full:
        return f'{full.group(1)} {full.group(2)}'
    digits = re.search(r'(?<!\d)\d{7}(?!\d)', text or '')
    return digits.group(0) if digits else ''


def student_names(lines):
    """(ชื่อไทย, ชื่ออังกฤษ) จากช่อง "ชื่อ-สกุล" ของหน้า eThesis — ค่าว่างถ้าไม่พบ

    eThesis พิมพ์ชื่อไทยต่อป้าย "ชื่อ-สกุล" แล้วชื่ออังกฤษบรรทัดถัดไป นักศึกษาต่างชาติไม่มีชื่อไทย
    ช่องนั้นจึงเป็นชื่ออังกฤษ — เจ้าหน้าที่สั่ง (ก.ย. 2569) ให้ดึงค่านั้นมาเป็นชื่อไทยด้วย ไม่ปล่อยว่าง
    เพราะหลักสูตรไทย/ไทย-อังกฤษบังคับกรอกช่องชื่อไทย ถ้าว่างระบบจะไม่ให้ตรวจ ถ้าไม่มีค่าในช่อง
    ชื่อไทยเลยแต่มีชื่ออังกฤษ ใช้ชื่ออังกฤษแทนด้วยเหตุผลเดียวกัน

    คำนำหน้าชื่อตัดออกตั้งแต่ตรงนี้ (เจ้าหน้าที่ 6 ต.ค. 2569: "หลักการเขียนในเล่ม มันต้องเขียนแบบไม่มี คำนำหน้า ถ้างั้นแก้ไข
    ให้การอ่านฟอร์มจากไฟล์ ที่แนบเข้าไปให้ตัดคำนำหน้านามทิ้งไปก่อนตรวจเลย") ด้วยตัวตัดเดียวกับตัวตรวจ
    (thai_text.strip_student_title) — เดิมรู้จักแค่ นาย/นาง/นางสาว/น.ส./ดร. และ Mr/Mrs/Miss/Ms/Dr ฟอร์มจึงได้
    "VDC Lt Col ..." "Dental Surgeon ..." "ทพญ. ..." ไปทั้งก้อน แล้วฟ้องแดงเล่มที่พิมพ์ชื่อล้วนถูกตามกติกา
    · ฝั่งอังกฤษดึงเฉพาะชื่อ-สกุล ตัดคำนำหน้าแบบไหนก็ได้ทิ้ง ไม่ใช่เฉพาะที่อยู่ในรายการ (english_name_only)
    """
    name_th = name_en = ''
    name_value, name_index = _find(lines, 'ชื่อ-สกุล')
    if name_value:
        if re.search(r'[ก-๙]', name_value):
            name_th = strip_student_title(name_value)
        else:
            name_th = name_en = english_name_only(name_value)
        offset = 2 if name_value == _next(lines, name_index) else 1
        following = _next(lines, name_index, offset)
        if following and re.search(r'[A-Za-z]', following) and not re.search(r'[ก-๙]', following):
            name_en = english_name_only(following)
    if not name_th and name_en:
        name_th = name_en
    return name_th, name_en


# คำหนึ่งคำของชื่อ-สกุลภาษาอังกฤษในไฟล์ eThesis — ตัวพิมพ์ใหญ่ล้วน (มีขีดกลาง/อัญประกาศเดี่ยวได้)
_EN_NAME_WORD = re.compile(r"[A-Z][A-Z'\-]*")


def english_name_only(value):
    """ชื่อนักศึกษาภาษาอังกฤษ "เฉพาะชื่อ-สกุล" — คำนำหน้าแบบไหนก็ตัดทิ้ง (เจ้าหน้าที่สั่ง 6 ต.ค. 2569)

    เจ้าหน้าที่: "ให้ตอนที่อัปโหลดไฟล์ e-thesis เข้าไป ส่วนชื่อนักศึกษาให้ดึงเฉพาะ ชื่อไว้ ตัดคำนำหน้าอื่นทิ้ง"
    เช่น "VDC Lt Col <ชื่อ> <สกุล>" ต้องได้ "<ชื่อ> <สกุล>" — ไม่ใช่ตัดเฉพาะคำที่อยู่ในรายการ

    eThesis เก็บเป็น "<คำนำหน้า> <ชื่อ> <สกุล>" ชื่อ-สกุลอังกฤษเป็นตัวพิมพ์ใหญ่ล้วนเสมอ ส่วนคำนำหน้าพิมพ์ตามที่กรอก
    (ไฟล์จริง 11 ไฟล์: Miss · Mr. · DR. · Dental Surgeon · VDC Lt Col) ชื่อจึงคือ "คำตัวพิมพ์ใหญ่ล้วนที่ต่อกันอยู่ท้ายสุด"
    1. ตัดคำนำหน้าที่รู้จักก่อน (thai_text.strip_student_title — จำเป็นกับคำนำหน้าตัวใหญ่ล้วนไม่มีจุดอย่าง MR ที่ข้อ 2
       แยกจากชื่อไม่ได้)
    2. ถ้ายังมีคำนำหน้าคำตัวใหญ่ชุดท้าย และในนั้นมีคำที่มีตัวพิมพ์เล็กหรือจุด (หน้าตาคำนำหน้าที่กรอกเอง) ตัดทิ้งทั้งหมด
       — ชุดท้ายต้องมีอย่างน้อย 2 คำ (ชื่อ + สกุล) ชื่อที่พิมพ์ตัวเล็กปน ("Somchai Jaidee") หรือมีคำเชื่อมตัวเล็กกลางชื่อ
       ("... van ...") จึงไม่ถูกตัดคำในชื่อทิ้ง
    """
    words = strip_student_title(value).split()
    start = len(words)
    while start and _EN_NAME_WORD.fullmatch(words[start - 1]):
        start -= 1
    head, name = words[:start], words[start:]
    if len(name) >= 2 and any(re.search(r'[a-z.]', word) for word in head):
        return ' '.join(name)
    return ' '.join(words)


def _degree_fields(english, thai):
    """ช่องชื่อปริญญาทั้งหมดจากชื่อปริญญาอังกฤษ/ไทย — ชุดเดียวของไฟล์ eThesis และหน้า MIS (อัปเดต 1.16 แยกออกมาจาก parse_ethesis_pdf)

    แยกตามตำแหน่งที่ใช้ตรวจ: ปก = ต้นฉบับ eThesis เป็นตัวพิมพ์ใหญ่เสมอ, หน้าลงนาม = Sentence
    case สำหรับอังกฤษ (ไทยคงเดิม), บทคัดย่อ = ตัวย่อ
    ปกอังกฤษ .upper() เสมอ (อัปเดต 1.8.2 เจ้าของสั่ง 28 ต.ค. 2569) — ไฟล์ eThesis บางคนพิมพ์
    "Doctor of Applied Thai Traditional Medicine" ตัวเล็กปนใหญ่ ข้อหน้าปกจึงสั่งให้แก้เป็น "Doctor of…"
    ชื่อปริญญาที่ไม่มีในตารางตัวย่อ = ไม่มีช่องตัวย่อ (หน้าฟอร์มบอกให้กรอกเอง)
    """
    data = {}
    if english:
        data['degree_cover_en'] = _spaced_degree(english).upper()
        data['degree_sig_en'] = _degree_name(english)
        abbr = _degree_abbr(english)
        if abbr:
            data['degree_abbr_en'] = abbr
    if thai:
        data['degree_cover_th'] = _spaced_degree(thai)
        data['degree_sig_th'] = _spaced_degree(thai)
        abbr_th = _degree_abbr_th(thai)
        if abbr_th:
            data['degree_abbr_th'] = abbr_th
    return data


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
    # รหัส นศ. = เลข 7 หลัก ตามด้วยรหัสหลักสูตร เช่น "6838141 SHSS/M" หรือติดกัน
    # "6838141SHSS/M" — ใช้ lookaround กันเลขอื่น แต่ยอมให้ตัวอักษรติดท้ายได้
    # และค้นครอบคลุมบรรทัดถัดไป 2 บรรทัด เผื่อค่าตกบรรทัด
    # ค้นครอบคลุมบรรทัดถัดไป 2 บรรทัด เผื่อค่าตกบรรทัด
    id_scope = ' '.join([id_value, _next(lines, id_index), _next(lines, id_index, 2)])
    student_id = _student_id(id_scope)
    if student_id:
        data['student_id'] = student_id

    name_th, name_en = student_names(lines)
    if name_th:
        data['student_name_th'] = name_th
    if name_en:
        data['student_name'] = name_en

    data['title_th'] = title_th or _find(lines, 'ชื่อหัวข้อภาษาไทย')[0]
    data['title_en'] = title_en or _find(lines, 'ชื่อหัวข้อภาษาอังกฤษ')[0]

    course = _find(lines, 'หลักสูตร')[0]
    writing = _find(lines, 'ภาษาที่เขียน')[0]
    if course:
        # ตัดรหัสหลักสูตรท้าย เช่น " [4801D02G]" ออก เหลือชื่อหลักสูตร
        data['program'] = re.sub(r'\s*\[[^\]]*\]\s*$', '', course).strip()
    faculty = _find(lines, 'คณะ')[0]   # _find กันชนกับ "คณะกรรมการ..." ด้วย guard อยู่แล้ว
    if faculty and not faculty.startswith('กรรมการ'):
        data['faculty'] = faculty.strip()
    committees = parse_committees(lines)
    if committees['advisory'] or committees['exam']:
        data['committees'] = committees
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
        data.update(_degree_fields(english, thai))

    exam_value = _find(lines, 'วันที่สอบผ่าน')[0]
    use_english = data.get('program_language') != 'thai'
    if not _looks_like_a_date(exam_value):
        # ช่อง "วันที่สอบผ่าน" ว่างในหน้า eThesis บางใบ ที่หยิบมาได้จึงเป็นหัวข้อถัดไป
        # เจ้าหน้าที่กำหนด (ก.ย. 2569) ให้ถอยไปใช้ช่อง "วันที่สอบ" แทน ซึ่งอยู่ตารางเดียวกัน
        # (_find เจอบรรทัด "วันที่สอบ" ก่อน "วันที่สอบผ่าน" อยู่แล้วเพราะอยู่เหนือกว่า
        #  และกันการชนกันของสองป้ายนี้ไว้แล้วในตัว _find เอง)
        exam_value = _find(lines, 'วันที่สอบ')[0]
    if not _looks_like_a_date(exam_value):
        # อ่านไม่ได้ทั้งสองช่อง ปล่อยว่างให้เจ้าหน้าที่กรอกเอง ดีกว่าเติมค่าที่ไม่ใช่วันที่
        exam_value = ''
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


# ---------- หน้า "ประวัตินักศึกษา" (Student Profile) ของระบบ MIS ที่คัดลอกมาวาง (อัปเดต 1.16) ----------
# เจ้าของ 26 พ.ย. 2569: "ข้อมูลนักศึกษาจาก eThesis ... ใช้ได้กับกรณีที่นักศึกษาส่งไฟล์เข้าระบบแล้ว แต่กรณีที่นักศึกษายังไม่ได้ส่ง
# ทำยังไงดี" แล้วเลือก "วิธีเดิมไป คือก็อปมาใส่" — ยกเว้นคำสั่ง ก.ย. 2569 ที่ถอดทางวางข้อความออก เฉพาะกรณีนักศึกษายังไม่ส่ง eThesis
# ตัวอ่านอยู่ฝั่งเซิร์ฟเวอร์ตัวเดียวนี้ และใช้ตาราง/ตัวจัดรูปชุดเดียวกับไฟล์ eThesis (ต้นเหตุที่ทางเดิมถูกถอดคือตารางซ้ำในหน้าเว็บ)
# ข้อความที่วางใช้เติมฟอร์มอย่างเดียว ไม่เก็บลงไฟล์หรือชีท
#
# หน้าเว็บเป็นแถว "ป้าย : ค่า" และเมนูซ้ายปนอยู่ในบรรทัดเดียวกับป้ายได้ ("Menu ประเภท :" "Soft Skills ปริญญาที่ได้รับ(TH) :")
# ป้ายจึงหาได้ทุกตำแหน่งในบรรทัด เทียบแบบตัดช่องว่างทิ้ง ("หลักสูตร(TH)" กับ "หลักสูตร (EN)" วรรคไม่เหมือนกัน) และเลือกป้ายที่ยาวที่สุด
# ("วันที่สอบวิทยานิพนธ์ผ่าน" ไม่ใช่ "วันที่สอบวิทยานิพนธ์") · ค่าอยู่ท้ายป้ายหรือบรรทัดถัดไป คั่นด้วย ":" แท็บ หรือช่องว่าง
_MIS_FIELDS = (
    ('student_id', 'รหัสนักศึกษา'),
    ('name_th', 'ชื่อ-นามสกุล'),
    ('name_en', 'ชื่อภาษาอังกฤษ'),
    ('program_th', 'หลักสูตร(TH)'),
    ('program_en', 'หลักสูตร(EN)'),
    ('degree_th', 'ปริญญาที่ได้รับ(TH)'),
    ('degree_en', 'ปริญญาที่ได้รับ(EN)'),
    ('exam_passed', 'วันที่สอบวิทยานิพนธ์ผ่าน'),
    ('exam', 'วันที่สอบวิทยานิพนธ์'),
    ('title_en', 'ชื่อวิทยานิพนธ์(อังกฤษ)'),
    ('title_th', 'ชื่อวิทยานิพนธ์(ไทย)'),
    ('advisor', 'อาจารย์ที่ปรึกษาหลัก'),
    ('co_advisor', 'อาจารย์ที่ปรึกษาร่วม'),
)
# ป้ายอื่นในหน้าเดียวกัน — ไม่ใช้ค่า แต่เป็นจุดจบของค่าที่ตัดบรรทัด (ไม่เติมประเภทเล่มจาก "แผนการศึกษา" — ห้ามเดา)
_MIS_OTHER_LABELS = (
    'ระดับปริญญา', 'สถานภาพปัจจุบัน', 'ประเภทการศึกษาต่อ', 'แผนการศึกษา', 'รูปแบบการศึกษา', 'แขนง/สาขาวิชาเอก(TH)',
    'แขนง/สาขาวิชาเอก(EN)', 'รหัสหลักสูตร', 'ประธานหลักสูตรปัจจุบัน', 'สอบวัดคุณสมบัติ', 'สอบประมวลความรู้', 'สอบปากเปล่า',
    'อาจารย์ที่ปรึกษาโครงร่าง', 'วันที่สอบโครงร่าง', 'วันที่สอบผ่านโครงร่าง', 'วันที่อนุมัติหัวข้อ', 'วันที่สำเร็จการศึกษา',
    'สภาอนุมัติปริญญาครั้งที่', 'ปีการศึกษาที่สภาอนุมัติปริญญา', 'ส่งรูปเล่มออนไลน์', 'การเผยแพร่ผลงานวิจัย', 'วันที่ส่งรูปเล่ม',
    'ผลการสอบภาษาอังกฤษ',
)
_MIS_BLANK = re.compile(r'[\s​﻿]+')
# ค่าที่ตัดบรรทัดต่อได้ (ชื่อเรื่อง) — บรรทัดต่อต้องไม่ใช่แถว "ป้ายสั้น ๆ :" ของช่องอื่น
_MIS_LABEL_ROW = re.compile(r'^[^:：]{1,40}[:：]')
# บรรทัดที่ปรึกษาร่วมคนถัดไป (ขึ้นบรรทัดใหม่) ต้องขึ้นต้นด้วยตำแหน่งวิชาการหรือเลขลำดับ — กันเมนู/ข้อความอื่นถูกนับเป็นอาจารย์
_MIS_PERSON_START = re.compile(
    r'^\s*(?:\d+\s*[.)]\s*)?(?:ศาสตราจารย์|รองศาสตราจารย์|ผู้ช่วยศาสตราจารย์|อาจารย์|ผศ\.|รศ\.|ศ\.|ดร\.|'
    r'(?:Assoc(?:iate)?|Asst|Assistant)\.?\s*Prof|Prof(?:essor)?\b|Dr\.)', re.I)
_EN_ACADEMIC_TITLE_RE = re.compile(
    r'^\s*(?:(?:Assoc(?:iate)?|Asst|Assistant)\.?\s+)?(?:Prof(?:essor)?\.?\s+)?(?:Dr\.?\s+)?', re.I)
_MIS_MAX_CHARS = 200_000


_EN_TO_TH_MONTH = {english: thai for thai, english in THAI_MONTHS.items()}


def _thai_exam_date(value):
    """วันที่สอบรูปไทย พ.ศ. (คู่กลับของ _exam_date) — วันที่ที่เป็นไทยอยู่แล้วคงตามที่พิมพ์ · อ่านไม่ออก = คืนค่าเดิม"""
    v = re.sub(r'\s+', ' ', value or '').strip()
    month_first = _MONTH_FIRST_DATE.match(v)
    if month_first:
        v = f'{int(month_first.group(2))} {month_first.group(1)} {month_first.group(3)}'
    m = re.match(r'^(\d{1,2})\s+(\S+)\s+(25\d{2}|20\d{2})$', v)
    if not m or m.group(2) in THAI_MONTHS:
        return v
    month = _EN_TO_TH_MONTH.get(m.group(2).capitalize())
    if not month:
        return v
    year = int(m.group(3))
    return f'{int(m.group(1))} {month} {year + 543 if year < 2400 else year}'


def _exam_date_variant(date, english):
    """{exam_date, year} ของวันที่รูปหนึ่ง — ปีตามรูปนั้น (อังกฤษ ค.ศ. / ไทย พ.ศ.)"""
    match = re.search(r'\b(25\d{2}|20\d{2})\b', date)
    year = ''
    if match:
        year_value = int(match.group(1))
        if english and year_value > 2400:
            year_value -= 543
        elif not english and year_value < 2400:
            year_value += 543
        year = str(year_value)
    return {'exam_date': date, 'year': year}


def _mis_compact(line):
    """บรรทัดที่ตัดช่องว่างทิ้ง + ตำแหน่งของแต่ละตัวในบรรทัดเดิม (ไว้ตัดค่าจากบรรทัดเดิม)"""
    chars, where = [], []
    for k, ch in enumerate(line):
        if not _MIS_BLANK.fullmatch(ch):
            chars.append(ch)
            where.append(k)
    return ''.join(chars), where


_MIS_KEYS = sorted([(re.sub(r'\s+', '', label), key) for key, label in _MIS_FIELDS]
                   + [(re.sub(r'\s+', '', label), None) for label in _MIS_OTHER_LABELS],
                   key=lambda item: -len(item[0]))


def _mis_labels_in(line):
    """ป้ายในบรรทัดนี้ เรียงซ้ายไปขวา: [(key หรือ None, ตำแหน่งเริ่ม, ตำแหน่งหลังป้าย)] ในบรรทัดเดิม"""
    compact, where = _mis_compact(line)
    found, pos = [], 0
    while pos < len(compact):
        for label, key in _MIS_KEYS:
            if compact.startswith(label, pos):
                end = pos + len(label)
                found.append((key, where[pos], where[end - 1] + 1))
                pos = end
                break
        else:
            pos += 1
    return found


def _mis_value(text):
    """ค่าหลังป้าย — ตัด ":" แท็บ และช่องว่างหน้า-หลัง · "-" = ไม่มีค่า"""
    value = re.sub(r'^[\s:：]+', '', text or '')
    value = re.sub(r'\s+', ' ', value).strip()
    return '' if value in ('-', '–', '—') else value


def _mis_rows(text):
    """[(key, ค่า, ดัชนีบรรทัด)] ตามลำดับในข้อความ — ค่าว่างท้ายป้ายใช้บรรทัดถัดไปที่ไม่ใช่ป้าย"""
    lines = [fold_sara_am(unicodedata.normalize('NFC', line)) for line in (text or '').splitlines()]
    labelled = [_mis_labels_in(line) for line in lines]
    rows = []
    for index, labels in enumerate(labelled):
        for k, (key, _start, end) in enumerate(labels):
            stop = labels[k + 1][1] if k + 1 < len(labels) else len(lines[index])
            value = _mis_value(lines[index][end:stop])
            if not value and k + 1 == len(labels):
                following = next((j for j in range(index + 1, min(index + 3, len(lines))) if lines[j].strip()), None)
                if following is not None and not labelled[following] and not _MIS_LABEL_ROW.match(lines[following]):
                    value = _mis_value(lines[following])
                    index_used = following
                else:
                    index_used = index
            else:
                index_used = index
            if key:
                rows.append((key, value, index_used))
    return rows, lines, labelled


def _mis_continuation(lines, labelled, index, accept, limit=3):
    """บรรทัดต่อของค่าที่ตัดบรรทัด (หลังบรรทัด index) จนกว่าจะเจอป้าย/แถวป้าย หรือบรรทัดที่ accept ไม่รับ"""
    out = []
    for j in range(index + 1, min(index + 1 + limit, len(lines))):
        line = lines[j].strip()
        if not line:
            if out:
                break
            continue
        if labelled[j] or _MIS_LABEL_ROW.match(line) or not accept(line):
            break
        out.append(re.sub(r'\s+', ' ', line))
    return out


def _mis_join(parts):
    """ต่อค่าที่ตัดบรรทัด — ไทยต่อชิด อังกฤษ/ตัวเลข/วงเล็บเว้นวรรค (กติกาเดียวกับ _cell_value ของไฟล์ eThesis)"""
    out = ''
    for part in parts:
        if out and (re.match(r'[A-Za-z0-9(]', part) or re.search(r'[A-Za-z0-9)]$', out)):
            out += ' '
        out += part
    return out


def _mis_person(text):
    """ชื่ออาจารย์ — ตัดเลขลำดับและตำแหน่งวิชาการ (ตัวตัดเดียวกับรายชื่อกรรมการจาก eThesis + คำนำหน้าภาษาอังกฤษ)"""
    name = re.sub(r'^\s*\d+\s*[.)]\s*', '', text or '')
    name = _ACADEMIC_TITLE_RE.sub('', name).strip()
    name = _EN_ACADEMIC_TITLE_RE.sub('', name).strip()
    name = re.sub(r'\s+', ' ', name)
    return '' if not re.search(r'[A-Za-zก-๙]', name) or re.search(r'\d', name) else name


def _mis_people(values):
    """ค่าช่องอาจารย์ (หลายบรรทัด/หลายป้ายได้) → รายชื่อ · คั่นด้วยจุลภาค อัฒภาค หรือเลขลำดับได้"""
    people = []
    for value in values:
        for part in re.split(r'\s*[,;、]\s*|\s+(?=\d+\s*[.)]\s)', value or ''):
            name = _mis_person(part)
            if name and name not in people:
                people.append(name)
    return people


def parse_mis_profile(text):
    """ข้อความที่คัดลอกจากหน้าประวัตินักศึกษา (MIS) → dict ของค่าที่เติมฟอร์มได้ (รูปเดียวกับ parse_ethesis_pdf)

    คืน {} ถ้าไม่ใช่หน้านี้ (ต้องพบรหัสนักศึกษา และป้ายอื่นที่มีค่าอย่างน้อย 2 ป้าย) · ป้ายที่หาไม่เจอข้ามไป ไม่ล้ม
    ไม่เติม: ประเภทเล่ม รูปแบบเล่ม กรรมการสอบ (บฑ.2) คณะ · ภาษาหลักสูตรเติมเฉพาะหลักสูตรนานาชาติ
    (หน้านี้ไม่มีช่อง "ภาษาที่เขียน" ที่ eThesis ใช้แยกหลักสูตรไทยกับไทย-อังกฤษ — ให้เจ้าหน้าที่เลือกเอง ห้ามเดา)
    """
    text = (text or '')[:_MIS_MAX_CHARS]
    rows, lines, labelled = _mis_rows(text)
    first = {}
    for key, value, index in rows:
        if value and key not in first:
            first[key] = (value, index)
    student_id = _student_id(first.get('student_id', ('', 0))[0])
    if not student_id or len([key for key in first if key != 'student_id']) < 2:
        return {}

    def value(key):
        return first.get(key, ('', 0))[0]

    data = {'student_id': student_id}
    name_th, name_en = value('name_th'), value('name_en')
    if name_en:
        data['student_name'] = english_name_only(name_en)
    if name_th and re.search(r'[ก-๙]', name_th):
        data['student_name_th'] = strip_student_title(name_th)
    elif data.get('student_name'):
        data['student_name_th'] = data['student_name']     # นักศึกษาต่างชาติไม่มีชื่อไทย — กติกาเดียวกับ eThesis

    for key in ('title_en', 'title_th'):
        if key not in first:
            continue
        title, index = first[key]
        if key == 'title_en':
            accept = lambda line: bool(re.search(r'[A-Za-z]', line)) and not re.search(r'[ก-๙]', line)
        else:
            accept = lambda line: bool(re.search(r'[ก-๙]', line))
        data[key] = _mis_join([title] + _mis_continuation(lines, labelled, index, accept))

    program_th, program_en = value('program_th'), value('program_en')
    if program_th or program_en:
        data['program'] = re.sub(r'\s*\[[^\]]*\]\s*$', '', program_th or program_en).strip()
    if re.search(r'นานาชาติ', program_th) or re.search(r'international', program_en, re.I):
        data['program_language'] = 'international'

    data.update(_degree_fields(value('degree_en'), value('degree_th')))

    exam_value = value('exam_passed')
    if not _looks_like_a_date(exam_value):
        exam_value = value('exam')                # ถอยไปใช้วันที่สอบ — กติกาเดียวกับ eThesis (เจ้าหน้าที่ ก.ย. 2569)
    if _looks_like_a_date(exam_value):
        # ส่งทั้งสองรูปมาพร้อมกัน (หัวหน้าทีม QA 1.16) — หน้าแบบฟอร์มเลือกรูปตามภาษาหลักสูตรที่เจ้าหน้าที่เลือก
        # (thai = ไทย พ.ศ. · thai_english/international = อังกฤษ ค.ศ. กติกาเดียวกับ eThesis) ไม่มีตารางเดือนในหน้าเว็บ
        # ช่องที่เติมตอนนี้: นานาชาติ = อังกฤษ · ยังไม่รู้ภาษา = รูปไทยตามที่หน้าเว็บพิมพ์ จนกว่าจะเลือกภาษา
        variants = {'thai': _exam_date_variant(_thai_exam_date(exam_value), False),
                    'english': _exam_date_variant(_exam_date(exam_value, True), True)}
        chosen = variants['english' if data.get('program_language') == 'international' else 'thai']
        data['exam_date'], data['year'] = chosen['exam_date'], chosen['year']
        data['exam_date_variants'] = variants

    advisors = []
    for key, role in (('advisor', 'อาจารย์ที่ปรึกษาหลัก'), ('co_advisor', 'อาจารย์ที่ปรึกษาร่วม')):
        values = []
        for row_key, row_value, index in rows:
            if row_key == key and row_value:
                values.append(row_value)
                values += _mis_continuation(lines, labelled, index, lambda line: bool(_MIS_PERSON_START.match(line)))
        for name in _mis_people(values):
            if all(name != person['name'] for person in advisors):
                advisors.append({'name': name, 'role': role})
    if advisors:
        data['committees'] = {'advisory': advisors, 'exam': []}
    return {key: item for key, item in data.items() if item}

