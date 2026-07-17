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

FUZZY_NAME_THRESHOLD = 0.82

NOT_CHECKED = [
    "ระยะขอบ ระยะบรรทัด ชนิดและขนาดฟอนต์",
    "Plagiarism หรือเปอร์เซ็นต์ความซ้ำซ้อน",
    "มาตรฐาน PDF/A, embedded fonts และความคมชัดของภาพ",
    "รายชื่อ/บทบาทกรรมการ ตำแหน่งลายเซ็น และข้อความมุมล่างขวาของหน้าลงนาม",
]

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


def top_lines(page_text, k=10):
    return [l.strip() for l in page_text.split('\n') if l.strip()][:k]


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


# ---------- normalized heading keys ----------
N_ABSTRACT_TH = norm('บทคัดย่อ')
N_ACK = [norm('กิตติกรรมประกาศ'), 'ACKNOWLEDGEMENT', 'ACKNOWLEDGEMENTS']
N_TOC = [norm('สารบัญ'), 'TABLEOFCONTENTS', 'CONTENTS']
N_LISTS = [norm('สารบัญตาราง'), norm('สารบัญรูป'), norm('สารบัญภาพ'), norm('คำย่อ'),
           'LISTOFTABLES', 'LISTOFFIGURES', 'LISTOFABBREVIATIONS', 'LISTOFILLUSTRATIONS']
N_ENTITLED = ['ENTITLED', norm('เรื่อง')]
N_REF = ['REFERENCES', 'BIBLIOGRAPHY', norm('รายการอ้างอิง'), norm('บรรณานุกรม')]
N_BIO = ['BIOGRAPHY', norm('ประวัติผู้วิจัย'), norm('ประวัติผู้เขียน')]
N_APPENDIX = ['APPENDIX', 'APPENDICES', norm('ภาคผนวก')]

CANONICAL_OPT1 = [("บทนำ", "INTRODUCTION"), ("วรรณกรรมและงานวิจัยที่เกี่ยวข้อง", "LITERATURE REVIEW"),
                  ("วิธีการดำเนินการวิจัย", "RESEARCH METHODOLOGY"), ("ผลการวิจัย", "RESULTS"),
                  ("การอภิปรายผล", "DISCUSSION"), ("บทสรุปและข้อเสนอแนะ", "CONCLUSION AND RECOMMENDATIONS")]
CANONICAL_OPT2 = [("บทสรุป", "SUMMARY"), ("ผลงานตีพิมพ์", "PUBLICATION"), ("เนื้อหาเพิ่มเติม", "ADDITIONAL CONTEXT")]

TYPE_MARKERS = {
    "THESIS": ["A THESIS SUBMITTED", "วิทยานิพนธ์นี้เป็นส่วนหนึ่ง"],
    "THEMATIC PAPER": ["A THEMATIC PAPER SUBMITTED", "สารนิพนธ์นี้เป็นส่วนหนึ่ง"],
    "INDEPENDENT STUDY": ["AN INDEPENDENT STUDY SUBMITTED", "การค้นคว้าอิสระนี้เป็นส่วนหนึ่ง"],
}


def _chapter_match(line):
    """Return chapter number if the (normalized) line is 'CHAPTER n' / 'บทที่ n'."""
    nl = norm(line)
    m = re.fullmatch(r'(CHAPTER|บทท)(\d{1,2})', nl)
    return int(m.group(2)) if m else None


def resolve_option(body_ch, approved, chapters_mode):
    """Resolve document option without forcing canonical titles in free mode."""
    inferred = 2 if any(
        norm(chapter[1]).startswith(norm(CANONICAL_OPT2[0][0]))
        or norm(chapter[1]).startswith(norm(CANONICAL_OPT2[0][1]))
        for chapter in body_ch
    ) else 1
    selected = str((approved or {}).get("format", ""))
    if chapters_mode == "free" and selected in {"1", "2"}:
        return int(selected)
    return inferred




def classify(issue):
    f, e, loc = issue.get("found", ""), issue.get("expected", ""), issue.get("location", "")
    text = f + " " + e + " " + loc
    if "ตัวอักษรหนา" in text:
        return "รูปแบบตัวอักษร"
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

    def add(self, zone, part, loc, found, expected, fix=""):
        self.zones[zone].append({"part": part, "location": loc, "found": found,
                                  "expected": expected, "fix": fix})

    def add_info(self, part, topic, detail):
        self.info.append({"part": part, "topic": topic, "detail": detail})

    def add_human(self, item, why):
        self.human_checklist.append({"item": item, "why": why})

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
                    "summary": {z.lower(): len(v) for z, v in rep.zones.items()}, "context": {"n_pages": 0}}
        _w, _h = _pdf.pages[0].width, _pdf.pages[0].height
        for _i, _pg in enumerate(_pdf.pages):
            if _i % 5 == 0 or _i == n - 1:
                _p(f"อ่านข้อความแบบละเอียด (หน้า {_i+1}/{n})")
            pages.append(_pg.extract_text() or "")
            try:
                _pg.flush_cache()
            except Exception:
                pass

    W_, H_ = _w / 72 * 2.54, _h / 72 * 2.54
    if not (20.5 <= W_ <= 21.5 and 29.2 <= H_ <= 30.2):
        rep.add("YELLOW", "-", "ทั้งเล่ม", f"ขนาดกระดาษ {W_:.1f}x{H_:.1f} ซม.", "A4 (21.0x29.7)", "")

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
        for j, nl in enumerate(nls[:8]):
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

    # ---------- เลขหน้า ----------
    _p("ตรวจเลขหน้าและความต่อเนื่อง")
    printed = {}
    for i, t in enumerate(pages):
        lines = [l.strip() for l in t.split('\n') if l.strip()]
        for cand in (lines[:1] + lines[-1:] if lines else []):
            if re.fullmatch(r'\d{1,3}', cand):
                printed[i] = int(cand)
                break
    seq = sorted(printed.items())
    if seq and seq[0][1] != 1:
        rep.add("RED", "body", f"หน้า PDF {seq[0][0]+1}", f"เลขหน้าอารบิกแรกที่พบคือ {seq[0][1]}",
                "เลขหน้าอารบิกต้องเริ่มที่ 1 ณ บทที่ 1", "แก้การตั้งเลขหน้า")
    for k in range(1, len(seq)):
        a, b = seq[k-1][1], seq[k][1]
        if b != a + 1:
            rep.add("RED", "body/end", f"ช่วงเลขหน้า {a}→{b}", f"เลขหน้ากระโดดจาก {a} ไป {b}",
                    "เลขหน้าต้องต่อเนื่อง ไม่ซ้ำ ไม่ข้าม", "")
    last_arabic = max(printed.values()) if printed else None

    # เลขหน้าลงนาม i/ii หรือ ก/ข
    if len(sig_pages) != 2:
        rep.add("RED" if not sig_pages else "ORANGE", "front_matter", "หน้าลงนาม",
                f"พบหน้าลงนาม {len(sig_pages)} หน้า", "ต้องมี 2 หน้า (Advisory + Examination)", "ตรวจด้วยตา")
    expected_labels = [("i", "ก"), ("ii", "ข")]
    for k, i2 in enumerate(sig_pages[:2]):
        lines2 = [l.strip() for l in pages[i2].split('\n') if l.strip()]
        tokens = (lines2[:1] + lines2[-1:]) if lines2 else []
        lab_en, lab_th = expected_labels[k]
        if not any(t.lower() == lab_en or norm(t) == norm(lab_th) for t in tokens):
            rep.add("ORANGE", "front_matter", f"หน้าลงนามหน้า {k+1} (หน้า PDF {i2+1})",
                    f"ระบบไม่พบเลขหน้า \"{lab_en}\" หรือ \"{lab_th}\" บนหัว/ท้ายหน้า",
                    f"หน้าลงนามหน้า {k+1} ต้องมีเลขหน้า {lab_en} (อังกฤษ) หรือ {lab_th} (ไทย)",
                    "ตรวจด้วยตา — PDF บางไฟล์ดึงเลขหน้าไม่ได้")

    # ---------- สารบัญ ↔ บท ----------
    _p("ตรวจสารบัญและชื่อบท")
    toc_text = "\n".join(pages[i] for i in (toc_pages + [p+1 for p in toc_pages]) if i < n)
    toc_ch = []   # (chap_no, title_norm, page_no, raw_line)
    for line in toc_text.split('\n'):
        raw = line.strip()
        if not raw:
            continue
        m_pg = re.search(r'(\d{1,3})\s*$', raw)
        nl = norm(raw)
        m_ch = re.match(r'^(CHAPTER|บทท)(\d{1,2})', nl)
        if m_ch and m_pg:
            title_n = re.sub(r'\d+$', '', nl[m_ch.end():])
            if not title_n:
                continue
            toc_ch.append((int(m_ch.group(2)), title_n, int(m_pg.group(1)), raw))

    body_ch = []  # (chap_no, title_raw, pdf_idx, printed_no)
    for i, t in enumerate(pages):
        tls = top_lines(t, 4)
        for j, l in enumerate(tls):
            cn = _chapter_match(l)
            if cn is not None and j + 1 < len(tls):
                title = tls[j+1]
                if not re.match(r'\d', title):
                    body_ch.append((cn, title, i, printed.get(i)))
                break
    rep.add_info("body", "บทที่พบในเนื้อหา",
                 [f"บทที่ {c[0]}: {c[1]} (หน้า {c[3]})" for c in body_ch])

    if toc_ch:
        if len(toc_ch) != len(body_ch):
            rep.add("RED", "body", "สารบัญ vs เนื้อหา",
                    f"สารบัญมี {len(toc_ch)} บท เนื้อหามี {len(body_ch)} บท",
                    "จำนวนบทต้องเท่ากัน", "อัปเดตสารบัญหรือเนื้อหา")
        toc_map = {c[0]: (c[1], c[2]) for c in toc_ch}
        for cn, title, ppage, pno in body_ch:
            if cn in toc_map:
                t_title_n, t_pno = toc_map[cn]
                nb = norm(title)
                if t_title_n != nb and not (t_title_n.startswith(nb[:20]) or nb.startswith(t_title_n[:20])):
                    rep.add("RED", "body", f"บทที่ {cn}",
                            f"ชื่อบทในเนื้อหา: \"{title}\" ไม่ตรงกับสารบัญ",
                            "ชื่อบทต้องสะกดตรงกันทั้งสองที่", "แก้ให้ตรงกัน")
                if pno is not None and t_pno != pno:
                    rep.add("RED", "body", f"บทที่ {cn}",
                            f"สารบัญระบุหน้า {t_pno} แต่บทอยู่จริงหน้า {pno}",
                            "เลขหน้าในสารบัญต้องตรงตำแหน่งจริง", "อัปเดตสารบัญ")
            else:
                rep.add("RED", "body", f"บทที่ {cn}", "ไม่อยู่ในสารบัญ",
                        "ทุกบทต้องปรากฏในสารบัญ", "")
    else:
        rep.add("ORANGE", "front_matter", "สารบัญ", "ระบบหาหน้าสารบัญไม่เจอ",
                "ต้องมีสารบัญเพื่อเทียบชื่อบท/เลขหน้า", "ตรวจด้วยตา")

    # ชื่อบทตามประกาศ
    option = resolve_option(body_ch, approved, chapters_mode)
    if chapters_mode == "strict" and body_ch:
        canon = CANONICAL_OPT1 if option == 1 else CANONICAL_OPT2
        if option == 1 and len(body_ch) != 6:
            rep.add("RED", "body", "ทั้งเล่ม", f"พบ {len(body_ch)} บท",
                    "ประกาศ 2569: รูปแบบดั้งเดิมต้องมี 6 บท", "ปรับโครงบทตามประกาศ")
        if option == 2 and len(body_ch) not in (2, 3):
            rep.add("RED", "body", "ทั้งเล่ม", f"พบ {len(body_ch)} บท", "รูปแบบตีพิมพ์ต้องมี 2-3 บท", "")
        for idx0, (cn, title, _, _) in enumerate(body_ch):
            if idx0 < len(canon):
                th, en = canon[idx0]
                nb = norm(title)
                if nb not in (norm(th), norm(en)) and not norm(en).startswith(nb[:20]):
                    rep.add("RED", "body", f"บทที่ {cn}", f"ชื่อบท \"{title}\"",
                            f"ตามประกาศ 2569 บทที่ {idx0+1} = \"{th}\" / \"{en}\"", "แก้ตามประกาศ")

    # ---------- ส่วนท้ายเล่ม ----------
    _p("ตรวจส่วนท้ายเล่ม (อ้างอิง/ภาคผนวก/ประวัติ)")
    ref_head = None
    bio_page = None
    last_major = None
    has_appendix_body = False
    for i, t in enumerate(pages):
        for l in top_lines(t, 3):
            nl = norm(l)
            n_ref_terms = sum(1 for w in N_REF if w in nl)
            if n_ref_terms and any(nl.startswith(w) or nl == w or n_ref_terms > 1 for w in N_REF):
                ref_head = (l, i, n_ref_terms)
                last_major = ("REF", i)
            if nl in N_BIO:
                bio_page = i
                last_major = ("BIO", i)
            if any(nl.startswith(w) for w in N_APPENDIX):
                has_appendix_body = True
                last_major = ("APP", i)
    if ref_head:
        if ref_head[2] > 1 or '/' in ref_head[0]:
            rep.add("RED", "end_matter", f"หน้า {printed.get(ref_head[1], '?')}",
                    f"หัวข้อ \"{ref_head[0]}\"", "เลือกคำเดียว: REFERENCES หรือ BIBLIOGRAPHY", "ลบคำที่ไม่ใช้")
    else:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบหน้ารายการอ้างอิง",
                "ต้องมี REFERENCES/BIBLIOGRAPHY เสมอ", "")
    if bio_page is None:
        rep.add("RED", "end_matter", "ทั้งเล่ม", "ไม่พบประวัติผู้วิจัย (BIOGRAPHY)",
                "ต้องมีและเป็นหน้าสุดท้ายของเล่ม", "")
    elif last_major and last_major[0] != "BIO":
        rep.add("RED", "end_matter", f"หน้า {printed.get(last_major[1], '?')}",
                "หลัง BIOGRAPHY ยังมีส่วนอื่น", "ประวัติผู้วิจัยต้องเป็นหน้าสุดท้าย", "ย้ายไปท้ายสุด")

    toc_has_appendix = any(w in norm(toc_text) for w in N_APPENDIX)
    if has_appendix_body and not toc_has_appendix:
        rep.add("RED", "front_matter", "สารบัญ", "เล่มมีภาคผนวก (APPENDIX) แต่ไม่ปรากฏในสารบัญ",
                "หัวข้อภาคผนวกต้องอยู่ในสารบัญ", "เพิ่ม APPENDIX/ภาคผนวก ในสารบัญ")
    if toc_has_appendix and not has_appendix_body:
        rep.add("RED", "front_matter", "สารบัญ", "สารบัญระบุภาคผนวก (APPENDIX) แต่ไม่พบในเนื้อหาเล่ม",
                "สารบัญต้องตรงกับเนื้อหาจริง", "ลบออกจากสารบัญ หรือเพิ่มภาคผนวกในเล่ม")

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
                rep.add("RED", "front_matter", f"{gname} (เริ่มหน้า PDF {grp_pages[0]+1})",
                        f"กินพื้นที่ {sp} หน้า", f"{gname}ต้องไม่เกิน {gmax} หน้า", "ตัดเนื้อหาให้สั้นลง")

    # ---------- กฎหน้าบทคัดย่อ (ตรวจทั้งช่วงของบทคัดย่อ ไม่ใช่แค่หน้าแรก) ----------
    abstract_idxs = sorted(set(abs_en_pages + abs_th_pages))
    for ai in abstract_idxs:
        span_pgs = list(range(ai, min(ai + span_of(ai), n)))
        lbl = f"บทคัดย่อ (หน้า PDF {ai+1})"
        # ห้ามตัวหนา — สแกนทุกหน้าในช่วง
        try:
            with pdfplumber.open(pdf_path) as _pl:
                for sp in span_pgs:
                    bold_chars = [ch for ch in _pl.pages[sp].chars
                                  if "BOLD" in (ch.get("fontname") or "").upper()]
                    if bold_chars:
                        sample = "".join(ch["text"] for ch in bold_chars[:60]).strip()
                        rep.add("RED", "front_matter", f"บทคัดย่อ (หน้า PDF {sp+1})",
                                f"พบตัวอักษรหนา {len(bold_chars)} ตัวอักษร เช่น \"{sample[:50]}\"",
                                "หน้าบทคัดย่อต้องไม่ใช้ตัวอักษรหนา", "เอา bold ออกจากข้อความในหน้าบทคัดย่อ")
        except Exception:
            pass
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
            rep.add("ORANGE", "front_matter", lbl,
                    "ระบบไม่พบการระบุจำนวนหน้า (เช่น 123 pages / 123 หน้า)",
                    "ท้ายบทคัดย่อต้องระบุจำนวนหน้ารวมของเล่ม", "ตรวจด้วยตา")
        elif last_arabic is not None and int(m2.group(1)) != last_arabic:
            rep.add("RED", "front_matter", lbl,
                    f"ระบุจำนวนหน้า {m2.group(1)} แต่เลขหน้าสุดท้ายของเล่มคือ {last_arabic}",
                    f"จำนวนหน้าที่ระบุต้องเท่ากับเลขหน้าสุดท้าย ({last_arabic})",
                    "แก้ตัวเลขให้ตรงเลขหน้าสุดท้าย")
        # keywords ≤5 — ค้นทุกหน้าในช่วง
        for sp in span_pgs:
            done_kw = False
            for raw in pages[sp].split('\n'):
                nl = norm(raw)
                if nl.startswith('KEYWORD') or nl.startswith(norm('คำสำคัญ')):
                    tail = raw.split(':', 1)[1] if ':' in raw else raw
                    kws = [k for k in re.split(r'[,;/]', tail) if k.strip()]
                    if len(kws) > 5:
                        rep.add("RED", "front_matter", f"บทคัดย่อ (หน้า PDF {sp+1})",
                                f"Keywords {len(kws)} คำ", "ไม่เกิน 5 คำตามประกาศ", "ตัดให้เหลือ ≤5")
                    done_kw = True
                    break
            if done_kw:
                break

    # ---------- เทียบข้อมูลอนุมัติ ----------
    _p("เทียบข้อมูลอนุมัติ (ชื่อเรื่อง/ชื่อนักศึกษา)")
    sig_text = "\n".join(pages[i] for i in sig_pages) if sig_pages else ""
    if approved:
        A = approved
        if A.get("doc_type") and doc_type and A["doc_type"] != doc_type:
            rep.add("RED", "front_matter", "หน้าปก", f"เล่มเป็น {doc_type}",
                    f"ข้อมูลอนุมัติ: {A['doc_type']}", "ตรวจว่าใช้ template ประเภทถูก")
        if chapters_mode == "strict" and A.get("format") and str(option) != str(A["format"]):
            rep.add("RED", "body", "โครงบท", f"เล่มเป็นรูปแบบ {option}",
                    f"ข้อมูลอนุมัติ: รูปแบบ {A['format']}", "")

        thai_book = A.get("program_language") == "thai"
        main_title = (A.get("title_th") if thai_book else A.get("title_en")) or ""
        alt_title = (A.get("title_en") if thai_book else A.get("title_th")) or ""

        if main_title:
            spots = [("หน้าปก", pages[0] if pages else "")]
            for k2, i2 in enumerate(sig_pages):
                spots.append((f"หน้าลงนาม {k2+1}", pages[i2]))
            main_abs = abs_th_idx if thai_book else abs_en_idx
            if main_abs is not None:
                spots.append(("บทคัดย่อ", pages[main_abs]))
            for spot_name, spot_text in spots:
                found, score = fuzzy_contains(norm(spot_text), main_title, 0.6)
                if not found:
                    rep.add("RED", "front_matter", spot_name,
                            f"ชื่อเรื่องไม่ตรงกับข้อมูลอนุมัติ (คะแนนใกล้เคียง {score:.2f})",
                            f"ต้องตรง บฑ.1: \"{main_title[:60]}...\"", "เทียบทีละคำกับเอกสารอนุมัติ")
        if alt_title:
            alt_abs = abs_en_idx if thai_book else abs_th_idx
            alt_lbl = "บทคัดย่อภาษาอังกฤษ" if thai_book else "บทคัดย่อภาษาไทย"
            if alt_abs is not None:
                found, score = fuzzy_contains(norm(pages[alt_abs]), alt_title, 0.6)
                if not found:
                    rep.add("RED", "front_matter", alt_lbl,
                            f"ชื่อเรื่องอีกภาษาไม่ตรงกับข้อมูลอนุมัติ (คะแนน {score:.2f})",
                            f"ต้องตรง บฑ.1: \"{alt_title[:60]}...\"", "เทียบทีละคำกับเอกสารอนุมัติ")
            else:
                rep.add("ORANGE", "front_matter", alt_lbl, "ระบบหาหน้าบทคัดย่อภาษานี้ไม่เจอ",
                        f"ชื่อเรื่อง \"{alt_title[:40]}...\" ต้องปรากฏในบทคัดย่อภาษานั้น", "ตรวจด้วยตา")

        if A.get("student_name"):
            found, score = fuzzy_contains(all_norm, A["student_name"], 0.85)
            if not found:
                rep.add("RED", "front_matter", "ทั้งเล่ม",
                        f"ไม่พบชื่อ \"{A['student_name']}\" ในเล่ม (คะแนน {score:.2f})",
                        "ชื่อ-นามสกุลต้องตรงข้อมูลอนุมัติทุกจุด", "ตรวจการสะกด")

        # ชื่อนักศึกษาในบทคัดย่อ: ไม่พบ = 🔴, มีคำนำหน้า = 🟠
        if A.get("program_language") in ("thai", "thai_english"):
            name_checks = [
                (A.get("student_name_th"), abs_th_idx, "บทคัดย่อภาษาไทย", "ชื่อภาษาไทย", True),
                (A.get("student_name"), abs_en_idx, "บทคัดย่อภาษาอังกฤษ", "ชื่อภาษาอังกฤษ", True),
            ]
        else:
            name_checks = [(A.get("student_name"), abs_en_idx, "บทคัดย่อ", "ชื่อนักศึกษา", False)]
        PREFIX_RE = r"(นางสาว|นาง|นาย|MRS\.?|MISS|MS\.?|MR\.?|ดร\.?|DR\.?)"
        for nm3, aidx, albl, nlbl, required in name_checks:
            if not nm3:
                if required:
                    rep.add("ORANGE", "front_matter", albl, f"ไม่ได้กรอก{nlbl}ของนักศึกษาในฟอร์ม",
                            f"หลักสูตรไทยต้องตรวจ{nlbl}ในหน้า{albl}", "กรอกฟอร์มให้ครบแล้วตรวจใหม่")
                continue
            if aidx is None:
                rep.add("ORANGE", "front_matter", albl, f"ระบบหาหน้า{albl}ไม่เจอ จึงเทียบ{nlbl}ไม่ได้",
                        f"{nlbl} \"{nm3}\" ต้องปรากฏในหน้า{albl}", "ตรวจด้วยตา")
                continue
            found, score = fuzzy_contains(norm(pages[aidx]), nm3, 0.85)
            if not found:
                rep.add("RED", "front_matter", f"{albl} (หน้า PDF {aidx+1})",
                        f"ไม่พบ{nlbl} \"{nm3}\" หรือสะกดไม่ตรง (คะแนน {score:.2f})",
                        f"{nlbl}ของนักศึกษาต้องปรากฏในหน้า{albl} สะกดตรงข้อมูลอนุมัติ", "ตรวจการสะกด")
            else:
                first_tok = nm3.split()[0]
                if re.search(PREFIX_RE + r"\s*" + re.escape(norm(first_tok))[:12], norm(pages[aidx]), re.I) and \
                   re.search(PREFIX_RE, pages[aidx], re.I):
                    rep.add("ORANGE", "front_matter", f"{albl} (หน้า PDF {aidx+1})",
                            f"พบคำนำหน้านามหน้า{nlbl} (เช่น นาย/นางสาว/Mr./Miss)",
                            "ชื่อนักศึกษาต้องไม่มีคำนำหน้านาม", "ลบคำนำหน้านามออก แล้วให้เจ้าหน้าที่ยืนยัน")

        if A.get("student_id") and re.sub(r'\D', '', A["student_id"]) not in re.sub(r'[^\d]', '', "\n".join(pages[:12])):
            rep.add("RED", "front_matter", "บทคัดย่อ", f"ไม่พบรหัสนักศึกษา {A['student_id']}",
                    "รหัสต้องปรากฏในบทคัดย่อ", "")
        if A.get("degree"):
            found, score = fuzzy_contains(norm("\n".join(pages[:12])), A["degree"], 0.7)
            if not found:
                rep.add("RED", "front_matter", "หน้าปก/ลงนาม", f"ไม่พบชื่อปริญญา \"{A['degree']}\" (คะแนน {score:.2f})",
                        "ต้องตรงข้อมูลอนุมัติ", "")
        if A.get("exam_date") and norm(A["exam_date"]) not in norm(sig_text):
            rep.add("RED", "front_matter", "หน้าลงนาม", f"ไม่พบวันที่สอบ \"{A['exam_date']}\"",
                    "วันที่บนหน้าลงนาม = วันที่มีผลสอบผ่าน", "")
        if A.get("year") and str(A["year"]) not in (pages[0] if pages else ""):
            rep.add("RED", "front_matter", "หน้าปก", f"ไม่พบปี {A['year']} บนหน้าปก", "ปี = ปีที่มีผลสอบผ่าน", "")

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
        if prog == "international" and has_th_abs:
            rep.add("RED", "front_matter", "บทคัดย่อ", "มีบทคัดย่อภาษาไทย",
                    "หลักสูตรนานาชาติใช้บทคัดย่ออังกฤษเท่านั้น", "ลบบทคัดย่อไทย")
        if prog in ("thai", "thai_english") and not (has_en_abs and has_th_abs):
            rep.add("RED", "front_matter", "บทคัดย่อ",
                    f"พบบทคัดย่อ: EN={has_en_abs}, TH={has_th_abs}",
                    "หลักสูตรไทยต้องมีทั้ง 2 ภาษา", "")

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
    }
