#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ตรวจว่าข้อความไทยที่เพิ่มใหม่มีคำแปลอังกฤษครบใน TR ของ report.html

จำลอง trEN() ของหน้ารายงาน (แทนที่ตามลำดับใน TR) แล้วรายงานว่าเหลืออักษรไทยไหม
ข้อความในเครื่องหมายคำพูดถือเป็น "ค่าจากเล่มจริง" ไม่นับว่าต้องแปล

    python tools/check_i18n.py "ข้อความที่หนึ่ง" "ข้อความที่สอง"
    python tools/check_i18n.py --file messages.txt   # บรรทัดละ 1 ข้อความ
    python tools/check_i18n.py --lint                # ตรวจโครงสร้าง TR อย่างเดียว (ไม่ต้องมีเล่มทดสอบ)
    python tools/check_i18n.py --corpus              # ใช้เล่มทดสอบมาตรฐาน
    python tools/check_i18n.py --corpus DIR          # ระบุโฟลเดอร์ PDF เอง

--corpus คือด่านสำคัญ: --lint ตรวจแค่ว่า regex ใน TR ใช้ได้ ไม่ได้ตรวจว่าข้อความจริง
แปลครบ เคยหลุดจนรายงานอังกฤษออกมาเป็นไทยปนอังกฤษ เช่น "Degree nameพิมพ์ผิดเล็กน้อย"
(เพราะ TR แทนที่แบบเศษคำ คำอังกฤษเลยไปโผล่กลางคำไทย)

หมายเหตุ: ตัวแทนที่จริงคือ JavaScript สคริปต์นี้ใช้ re ของ python จำลอง
regex ที่ python ไม่รองรับจะถูกข้าม (แจ้งเตือนด้วย --lint)
"""
import os
import re
import sys
from pathlib import Path

CODE = Path(__file__).resolve().parents[1]              # ...\code (git root)
PROJECT = CODE.parent                                   # ...\e-thesis checker
REPORT_HTML = CODE / "templates" / "report.html"
# เล่มทดสอบเป็นวิทยานิพนธ์จริงของนักศึกษา จึงไม่อยู่ใน repo — ตั้ง ETHESIS_TEST_DIR
# ชี้ไปที่อื่นได้ถ้าไม่ได้เก็บไว้ข้าง ๆ repo
DEFAULT_CORPUS = Path(os.environ.get("ETHESIS_TEST_DIR")
                      or (PROJECT / "test")) / "testing"

THAI_PAGE_LETTERS = set("กขคงจฉชซฌญฎฏฐฑฒณดตถทธนบปผฝพฟภมยรลวศษสหฬอฮ")

# "บฑ." คือชื่อแบบฟอร์มของบัณฑิตวิทยาลัย คงไว้เป็นภาษาไทยถูกแล้ว ไม่ใช่คำที่ต้องแปล
KEEP_THAI = {"บฑ"}


# คำแปลเขียนได้ทั้ง '...' และ "..." (ใช้ "..." เมื่อในข้อความมี apostrophe)
_TR_ENTRY = re.compile(
    r"""\[/(.+?)/([gimsuy]*),\s*\n?\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*\]""",
    re.S)


def load_tr():
    src = REPORT_HTML.read_text(encoding="utf-8")
    block = src[src.index("var TR = ["):]
    block = block[:block.index("\n];") + 3]
    pairs = [(m.group(1), m.group(2), m.group(3) if m.group(3) is not None else m.group(4))
             for m in _TR_ENTRY.finditer(block)]
    return block, pairs


def load_catmap():
    """CATMAP ของ report.html — ชื่อหมวด (.tr-cat) แปลด้วยการเทียบทั้งสตริง ไม่ใช่ TR"""
    src = REPORT_HTML.read_text(encoding="utf-8")
    block = src[src.index("var CATMAP = {"):]
    block = block[:block.index("};") + 2]
    return dict(re.findall(r"'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'", block))


def unparsed_entries(block):
    """entry ที่ขึ้นต้นด้วย '[/' แต่ _TR_ENTRY แยกไม่ออก — คืน [(บรรทัด, ข้อความ, สาเหตุ)]

    สาเหตุที่เจอจริง: คำแปลที่คร่อมด้วย '...' แล้วมี apostrophe อยู่ข้างใน
    (เช่น "the student's English name") JS จะปิดสตริงตรง apostrophe แล้ว
    <script> พังทั้งบล็อกแบบเงียบ ๆ — ไม่มี error ในหน้าเว็บให้เห็น รู้อีกทีคือ
    ปุ่มสลับภาษาไม่ทำงานและกล่องสรุปว่างเปล่า
    """
    # ต้องตัดเป็นก้อนทีละ entry ก่อนแล้วค่อย match — ถ้าปล่อยให้ finditer วิ่งทั้งบล็อก
    # entry ที่สตริงขาดจะ "กลืน" entry ถัดไปเข้าไปเป็นก้อนเดียวจนแมตช์ผ่าน แล้วด่าน
    # จะไปชี้ entry ถัดไปว่าผิดแทนตัวจริง (คนแก้จะไล่หาผิดบรรทัด)
    starts = [m.start() for m in re.finditer(r"\[/", block)]
    out = []
    for i, start in enumerate(starts):
        stop = starts[i + 1] if i + 1 < len(starts) else len(block)
        if _TR_ENTRY.match(block[start:stop]):
            continue
        end = block.find("\n", start)
        text = block[start:end if end > 0 else len(block)].strip()
        # หาเครื่องหมายคำพูดที่ปิดสตริงกลางคัน: <คำคั่น>'...'<ตัวอักษร>
        quote = re.search(r",\s*(['\"])(?:[^'\"\\]|\\.)*\1\s*[A-Za-z]", text)
        reason = (f"คำแปลมีเครื่องหมาย {quote.group(1)} อยู่ข้างในทั้งที่คร่อมด้วย "
                  f"{quote.group(1)} เอง — ให้สลับไปคร่อมด้วยอีกแบบ หรือเลี่ยงคำนั้น"
                  if quote else "รูปแบบ entry ต่างจากปกติ")
        out.append((block.count("\n", 0, start) + 1, text, reason))
    return out


def lint(block, pairs):
    """เทียบจำนวน entry ที่แยกได้กับจำนวน '[/' จริง และหา regex ที่ python ใช้ไม่ได้"""
    starts = len(re.findall(r"\[/", block))
    print(f"TR entries: {len(pairs)} / '[/' ที่พบ: {starts}")
    broken_entries = unparsed_entries(block)
    if broken_entries:
        print(f"  !! entry ที่แยกไม่ออก {len(broken_entries)} รายการ — JS จะพังทั้งบล็อก")
        for line_no, text, reason in broken_entries[:5]:
            print(f"     บรรทัดที่ {line_no} ของบล็อก TR: {text[:90]}")
            print(f"        เหตุ: {reason}")
    print(f"entry ที่แยกไม่ออก: {len(broken_entries)}")

    # CATMAP เป็นสตริง JS เดี่ยวชุดเดียวกัน จึงพังได้ด้วยเหตุเดียวกัน
    src = REPORT_HTML.read_text(encoding="utf-8")
    cat_block = src[src.index("var CATMAP = {"):]
    cat_block = cat_block[:cat_block.index("};") + 2]
    cat_pairs = len(re.findall(r"'((?:[^'\\]|\\.)*)'\s*:\s*'((?:[^'\\]|\\.)*)'", cat_block))
    cat_colons = len(re.findall(r"'\s*:\s*'", cat_block))
    if cat_pairs != cat_colons:
        print(f"  !! CATMAP แยกได้ {cat_pairs} คู่ แต่มีตัวคั่น {cat_colons} — "
              f"น่าจะมีเครื่องหมายคำพูดปิดสตริงกลางคัน JS จะพังทั้งบล็อก")
    print(f"CATMAP: {cat_pairs} คู่ / {cat_colons} ตัวคั่น")
    if block.count("[") != block.count("]"):
        print(f"  !! วงเล็บไม่สมดุล: [ = {block.count('[')}, ] = {block.count(']')}")
    broken = 0
    for pat, _flags, _rep in pairs:
        try:
            re.compile(pat.replace("\n", "").replace(r"\/", "/"))
        except re.error as exc:
            broken += 1
            print(f"  !! python ใช้ regex นี้ไม่ได้ (JS อาจใช้ได้): {pat[:50]} — {exc}")
    print(f"regex ที่ python ใช้ไม่ได้: {broken}")

    # ด่านสำคัญ: JS อ่าน [/.../] เป็น regex literal ถ้ามี \\/ จะแปลว่า "backslash
    # แล้วปิด regex ตรงนั้น" ที่เหลือกลายเป็นขยะ ทั้ง <script> พังเงียบ ๆ ไม่มี error
    # ในหน้าเว็บให้เห็น รู้อีกทีคือกล่องสรุปในรายงานว่างเปล่า
    # python คอมไพล์ผ่านปกติ ด่านข้างบนจึงจับไม่ได้ ต้องเช็คตรง ๆ แบบนี้
    doubled = [pat for pat, _f, _r in pairs if "\\\\/" in pat]
    if doubled:
        print(f"  !! escape เครื่องหมาย / ซ้ำ {len(doubled)} รายการ — JS จะพังทั้งบล็อก")
        for pat in doubled[:5]:
            print(f"     {pat[:60]}")
    print(f"escape / ซ้ำ: {len(doubled)}")


def _py(pat):
    return pat.replace("\n", "").replace(r"\/", "/")


def _rep_text(rep):
    return rep.replace("\\'", "'").replace('\\"', '"')


_COMPILED = {}


def _compiled(pairs):
    """คอมไพล์ทุก pattern ครั้งเดียว — จำเป็นจริง ๆ ไม่ใช่แค่ให้เร็วขึ้นเฉย ๆ

    แคชภายในของ re เก็บได้ 512 รูปแบบ ตอนนี้ TR มีมากกว่านั้น พอเรียกวนทุกข้อความ
    แคชจึงล้นแล้วคอมไพล์ใหม่ทุกครั้ง ด่าน --corpus ช้าจนรันก่อน commit ไม่ไหว
    """
    key = id(pairs)
    if key not in _COMPILED:
        out = []
        for pat, flags, rep in pairs:
            f = re.I if "i" in flags else 0
            try:
                whole = re.compile("(?:%s)$" % _py(pat), f)
            except re.error:
                whole = None
            try:
                part = re.compile(_py(pat), f)
            except re.error:
                part = None
            out.append((whole, part, rep))
        _COMPILED[key] = out
    return _COMPILED[key]


_SUMMARY_PREFIX = re.compile(r"^\s*(?:\d+\.\s*)?")


def summary_line_body(line):
    """เนื้อบรรทัดของข้อความสรุป หลังถอดเลขข้อและย่อหน้าออก

    ต้องตรงกับ trSummary() ใน report.html ที่แปลสรุป "ทีละบรรทัด" — ถ้าเครื่องมือนี้
    ตรวจทั้งบรรทัดพร้อมเลขข้อ จะไม่มีกฎเต็มประโยคไหนแมตช์ แล้วรายงานผิดว่าไม่ได้แปล
    """
    return _SUMMARY_PREFIX.sub("", line or "")


def whole_rule(text, pairs):
    """กฎที่กิน text ทั้งสตริง (ชั้น 1 ของ trEN) — คืน (ดัชนี, match) หรือ None"""
    stripped = text.strip()
    for i, (whole, _part, _rep) in enumerate(_compiled(pairs)):
        if whole is None:
            continue
        m = whole.match(stripped)
        if m:
            return i, m
    return None


def tr_whole(text, pairs):
    """ชั้น 1 อย่างเดียว — ไม่มีกฎไหนกินทั้งสตริงคืน None (ต้องตรงกับ trWhole ใน JS)"""
    text = (text or "").strip()
    if not text:
        return text
    hit = whole_rule(text, pairs)
    if not hit:
        return None

    def sub(g):
        raw = hit[1].group(int(g.group(1))) or ""
        # ค่าที่คร่อมด้วยเครื่องหมายคำพูดทั้งก้อน = ค่าที่อ่านได้จากเล่ม คงเป็นไทยเสมอ
        if re.fullmatch(r'"[^"]*"', raw.strip()):
            return raw
        return tr_en(raw, pairs)

    return re.sub(r"\$(\d)", sub, _rep_text(pairs[hit[0]][2]))


def tr_en(text, pairs):
    """จำลอง trEN() ของหน้ารายงาน — ต้องเหมือนกันเป๊ะ ไม่งั้นด่านนี้ตรวจคนละอย่างกับของจริง

    ชั้น 1 กฎเต็มประโยค (เจอแล้วหยุด) · ชั้น 2 เศษคำแบบสะสม (ใช้เมื่อชั้น 1 ไม่แมตช์)
    """
    text = (text or "").strip()
    if not text:
        return text
    whole = tr_whole(text, pairs)
    if whole is not None:
        return whole
    for _whole, part, rep in _compiled(pairs):
        if part is None:
            continue
        py_rep = re.sub(r"\$(\d)", r"\\\1", _rep_text(rep))
        try:
            text = part.sub(py_rep, text)
        except re.error:
            pass
    return text


def report_coverage(messages, pairs):
    """บอกว่าข้อความจริงกี่ข้อความแปลด้วย "กฎเต็มประโยค" กี่ข้อความยังพึ่ง "เศษคำ"

    ตัวเลข "พึ่งเศษคำ" คือหนี้ทางเทคนิคที่วัดได้ — ควรลดลงเรื่อย ๆ ห้ามเพิ่ม
    เพราะข้อความกลุ่มนี้คือกลุ่มเดียวที่กฎใหม่ที่เพิ่มทีหลังยังฉีกได้อยู่
    """
    thai = [m for m in messages if re.search(r"[ก-๙]", m)]
    whole = [m for m in thai if whole_rule(m, pairs)]
    print(f"  แปลด้วยกฎเต็มประโยค: {len(whole)}/{len(thai)} "
          f"· ยังพึ่งเศษคำ: {len(thai) - len(whole)}")


def collect_corpus(folder):
    """รันตรวจ PDF ทุกไฟล์ในโฟลเดอร์ แล้วคืนข้อความรายงานทุกบรรทัดที่ไม่ซ้ำ

    ชื่อคนไทยจาก eThesis ไม่นับว่าต้องแปล (เป็นข้อมูลของเล่ม ไม่ใช่ข้อความ UI)
    จึงตัดส่วนหลัง "บฑ. คือ" ออกก่อนตรวจ
    """
    sys.path.insert(0, str(CODE))
    import checker                                    # noqa: E402

    # ต้องรันทั้ง 2 แบบ: ไม่มีข้อมูลอ้างอิง กับมีครบ เพราะกฎคนละชุดกันทำงาน
    # (เคยตรวจแค่แบบแรก ข้อความของกฎที่ต้องใช้ข้อมูลอนุมัติจึงไม่เคยถูกตรวจคำแปล)
    REFERENCE = {
        "doc_type": "THESIS", "format": "1", "program_language": "international",
        "title_en": "A TITLE USED ONLY FOR CHECKING TRANSLATIONS",
        "title_th": "ชื่อเรื่องสำหรับตรวจคำแปล",
        "student_name": "TEST STUDENT", "student_name_th": "นักศึกษาทดสอบ",
        "student_id": "6236350 NSNS/D",
        "degree_cover_en": "DOCTOR OF PHILOSOPHY (NURSING)",
        "degree_cover_th": "ปรัชญาดุษฎีบัณฑิต (การพยาบาล)",
        "degree_sig_en": "Doctor of Philosophy (Nursing)",
        "degree_sig_th": "ปรัชญาดุษฎีบัณฑิต (การพยาบาล)",
        "degree_abbr_en": "Ph.D. (NURSING)", "degree_abbr_th": "ปร.ด. (การพยาบาล)",
        "exam_date": "1 June 2026", "year": "2026",
        "faculty": "คณะพยาบาลศาสตร์", "program": "ปรัชญาดุษฎีบัณฑิต สาขาวิชาการพยาบาล",
        "committees": {"advisory": [{"name": "ยอด สุขะมงคล"}],
                       "exam": [{"name": "ทวีศักดิ์ สมานชื่น"}]},
    }
    # แบบที่ 3: รู้ว่าเป็นหลักสูตรอะไร แต่ช่องข้อมูลอ้างอิงว่าง — ปลุกข้อความ
    # "ไม่ได้กรอก<ช่อง> ระบบจึงข้ามการเทียบข้อมูลนี้" ซึ่งเดิมไม่เคยถูกตรวจคำแปลเลย
    # (เล่มทดสอบจับคู่กับ REFERENCE ที่กรอกครบเสมอ ข้อความชุดนี้จึงไม่เคยโผล่)
    PARTIAL = {"doc_type": "THESIS", "format": "1", "program_language": "thai"}
    approvals = [{"doc_type": "THESIS", "format": "1"}, PARTIAL, REFERENCE]

    messages, seen, cats = [], set(), set()
    for path in sorted(Path(folder).glob("*.pdf")):
        for approved in approvals:
            try:
                # ปิดด่าน "ไฟล์คนละคน" เพราะเครื่องมือนี้จับคู่ข้อมูลสมมติกับเล่มไหนก็ได้
                # ถ้าไม่ปิด ระบบจะข้ามการเทียบข้อมูลอนุมัติ แล้วข้อความอีกครึ่งไม่ถูกตรวจคำแปล
                report = checker.run_check(str(path), dict(approved),
                                           skip_identity_check=True)
            except Exception as exc:                   # เล่มที่อ่านไม่ได้ ข้ามไป
                print(f"  ({path.name}: ตรวจไม่ได้ {type(exc).__name__})")
                continue
            texts = []
            for zone in ("RED", "ORANGE", "YELLOW"):
                for issue in report["issues_by_zone"][zone]:
                    # "category" ไม่ได้แปลผ่าน TR แต่ใช้ CATMAP (เทียบทั้งสตริง)
                    # จึงเก็บแยก แล้วตรวจว่ามีคีย์ใน CATMAP ครบไหม
                    texts += [issue.get(f) or "" for f in
                              ("location", "found", "expected", "fix")]
                    # "category" (ป้ายบนการ์ด) และ "section" (ชื่อกลุ่มการ์ด)
                    # ไม่ได้แปลผ่าน TR แต่ใช้ CATMAP (เทียบทั้งสตริง)
                    cats.add(issue.get("category") or "")
                    cats.add(issue.get("section") or "")
            # ตาราง "ผลเทียบข้อมูลอนุมัติรายตำแหน่ง" ก็แสดงผ่าน trEN (class tr-dyn)
            # เก็บเฉพาะ topic/location ซึ่งเป็นข้อความของระบบ — ไม่เก็บ detail
            # เพราะ detail คือ "ค่าที่พบในเล่ม" (ชื่อเรื่อง ชื่อปริญญา วันที่) ต้องคงเป็นไทย
            for group in report.get("verification") or []:
                texts.append(group.get("topic") or "")
                texts += [c.get("location") or "" for c in group.get("checks") or []]
            # กล่อง "ข้อมูลประกอบ" ก็แสดงผ่าน trEN (class tr-dyn) เหมือนกัน
            # เก็บเฉพาะ topic — detail คือค่าที่อ่านได้จากเล่ม (เช่นรายชื่อกรรมการ)
            # ต้องคงเป็นไทย เคยหลุดมาแล้ว: "รายชื่อที่ระบบอ่านได้จาก..." ไม่เคยถูกตรวจ
            for item in report.get("info") or []:
                texts.append(item.get("topic") or "")
            for item in report.get("not_checked") or []:
                texts.append(item.get("topic") or "" if isinstance(item, dict)
                             else str(item))
            # ต้องตรวจ "บรรทัดในข้อความสรุป" ด้วย ไม่ใช่แค่ทีละฟิลด์ — สรุปมีบรรทัด
            # ของตัวเอง (หัวข้อกลุ่ม, "ต้องแก้เป็น ...") ที่ไม่ใช่ฟิลด์ไหนตรง ๆ
            # ถอดเลขข้อ/ย่อหน้าออกก่อนให้ตรงกับที่ trSummary ใน report.html ทำ
            texts += [summary_line_body(line)
                      for line in (checker.plain_summary(report) or "").splitlines()]
            # รายการสีม่วง (ให้เจ้าหน้าที่ตรวจเอง) ก็แสดงผลผ่าน trEN เหมือนกัน
            for hc in report.get("human_checklist") or []:
                texts += [hc.get("item") or "", hc.get("why") or ""]

            for text in texts:
                # ตัด "รายชื่อคน" ที่ต่อท้ายออก (เป็นข้อมูลจาก eThesis ไม่ใช่ข้อความ UI)
                # แต่ต้องคงวงเล็บปิดไว้ ไม่งั้นประโยคเพี้ยนจนคำแปลไม่แมตช์
                text = re.sub(r"(บฑ\.\)?) คือ.*$", r"\1 คือ", (text or "").strip())
                if text and text not in seen:
                    seen.add(text)
                    messages.append(text)
    return messages, sorted(c for c in cats if c)


def main():
    args = sys.argv[1:]
    block, pairs = load_tr()

    if "--lint" in args:
        lint(block, pairs)
        return 0

    if "--corpus" in args:
        # ไม่ใส่ path มาก็ได้ ให้ใช้โฟลเดอร์เล่มทดสอบมาตรฐาน
        nxt = args.index("--corpus") + 1
        folder = Path(args[nxt]) if nxt < len(args) and not args[nxt].startswith("-") \
            else DEFAULT_CORPUS
        if not folder.is_dir() or not any(folder.glob("*.pdf")):
            print(f"ข้ามด่านนี้ — ไม่มีเล่มทดสอบในเครื่องนี้ (มองหาที่ {folder})")
            print("  เล่มทดสอบเป็นวิทยานิพนธ์จริงของนักศึกษา จึงไม่อยู่ใน repo")
            print("  วางไฟล์ PDF ไว้ในโฟลเดอร์นั้น หรือตั้ง ETHESIS_TEST_DIR / "
                  "ส่ง path ต่อท้าย --corpus")
            print("  (--lint ยังใช้ได้ตามปกติ ไม่ต้องใช้เล่มทดสอบ)")
            return 0
        messages, cats = collect_corpus(folder)
        print(f"เก็บข้อความจากเล่มจริงได้ {len(messages)} ข้อความ "
              f"+ {len(cats)} ชื่อหมวด")
        report_coverage(messages, pairs)
        bad = 0
        catmap = load_catmap()
        for cat in cats:
            if cat not in catmap:
                bad += 1
                print(f"\n  ชื่อหมวดไม่มีใน CATMAP: {cat}")
        for th in messages:
            en = tr_en(th, pairs)
            left = [w for w in re.findall(r"[ก-๙]+", re.sub(r'"[^"]*"', "", en))
                    if w not in THAI_PAGE_LETTERS and w not in KEEP_THAI]
            if left:
                bad += 1
                print(f"\n  ยังไม่แปล: {left}\n  TH: {th}\n  EN: {en}")
        print(f"\nUNTRANSLATED: {bad}")
        return 1 if bad else 0

    if "--file" in args:
        path = Path(args[args.index("--file") + 1])
        messages = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()
                    if ln.strip()]
    else:
        messages = [a for a in args if not a.startswith("--")]

    if not messages:
        print(__doc__)
        return 2

    bad = 0
    for th in messages:
        en = tr_en(th, pairs)
        left = [w for w in re.findall(r"[ก-๙]+", re.sub(r'"[^"]*"', "", en))
                if w not in THAI_PAGE_LETTERS and w not in KEEP_THAI]
        if left:
            bad += 1
            print(f"  ยังไม่แปล: {left}")
        print(f"  TH: {th}")
        print(f"  EN: {en}\n")
    print(f"UNTRANSLATED: {bad}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
