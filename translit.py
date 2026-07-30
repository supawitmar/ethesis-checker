#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ถอดชื่อบุคคลไทยเป็นตัวสะกดอังกฤษ — ทำงานในเครื่อง ไม่ต้องใช้ API และไม่มีค่าใช้จ่าย

ใช้แทนการเรียก AI สำหรับงานเทียบชื่อกรรมการบนหน้าลงนาม/บทคัดย่อของเล่มภาษาอังกฤษ
(ไฟล์ eThesis มีแต่ชื่อไทย เล่มพิมพ์ชื่ออังกฤษ จึงต้องถอดก่อนเทียบ)

ทำไมไม่ใช้ AI เป็นหลัก
  - ต้องมี ANTHROPIC_API_KEY และมีค่าใช้จ่ายต่อการตรวจทุกครั้ง
  - ถ้าเครดิตหมด/เน็ตล่ม การตรวจจะตกไปเป็นส้มทั้งหมด เจ้าหน้าที่ต้องไล่ดูเองทุกเล่ม
  - ผลลัพธ์ไม่คงที่ ทำ unittest ล็อกพฤติกรรมไม่ได้
ถอดในเครื่องได้ผลคงที่ ทดสอบซ้ำได้ และไม่มีวันล่ม

ความแม่นที่วัดจากเล่มจริง 13 คู่ชื่อ (เทียบกับตัวสะกดที่พิมพ์บนหน้าลงนามจริง
ด้วยเกณฑ์เดียวกับ checker คือ SequenceMatcher ratio >= 0.7)
  engine "royin"         ผ่าน 11/13   ไม่มี false positive (คู่ผิดสูงสุด 0.42)
  engine "thai2rom_onnx" ผ่าน 13/13   ไม่มี false positive
royin เป็นกฎราชบัณฑิตล้วน มากับ pythainlp ไม่ต้องลงอะไรเพิ่ม
thai2rom_onnx เป็นโมเดล ต้องลง onnxruntime เพิ่ม (~50MB) ถ้ามีจะใช้ให้อัตโนมัติ

ข้อจำกัดที่ต้องรู้: คนไทยไม่ได้สะกดชื่อตัวเองตามหลักราชบัณฑิตเสมอ
(เช่น "ภู่วรวรรณ" หลักคือ Phuworawan แต่เจ้าตัวใช้ Poovorawan)
ผลที่ถอดได้จึงเป็น "ตัวช่วยเทียบเคียง" ไม่ใช่คำตอบสุดท้าย
checker จึงลงผลเป็นสีส้มเมื่อเทียบไม่ตรง ไม่ใช่สีแดง
"""
import re

# เรียงตามความแม่น ตัวแรกที่ใช้ได้จะถูกเลือก
_ENGINES = ("thai2rom_onnx", "royin")

# เกณฑ์เทียบชื่อที่เหมาะกับแต่ละ engine — วัดจากเล่มจริง 13 คู่ชื่อ ด้วยฟังก์ชันทำ key
# ตัวเดียวกับ checker (_committee_keyname) ตัวเลขคือ ratio ของ SequenceMatcher
#   thai2rom_onnx : คู่ถูก 0.78-0.93  คู่ผิดสูงสุด 0.50  -> 0.70 จับครบ 13/13 ห่าง 0.28
#   royin         : คู่ถูก 0.57-0.90  คู่ผิดสูงสุด 0.53  -> 0.60 จับได้ 12/13 ห่าง 0.07
# ทั้งสองค่าไม่มี false positive เลย ที่เหลือจับไม่ได้จะลงสีส้มให้เจ้าหน้าที่ตรวจด้วยตา
_THRESHOLD = {"thai2rom_onnx": 0.70, "royin": 0.60}

_cached_engine = None


def _pick_engine():
    """เลือก engine ที่ใช้ได้จริงในเครื่องนี้ (ลองถอดคำจริงหนึ่งคำ ไม่ใช่แค่ import)"""
    global _cached_engine
    if _cached_engine is not None:
        return _cached_engine or None
    try:
        from pythainlp.transliterate import romanize
    except Exception:
        _cached_engine = ""
        return None
    for engine in _ENGINES:
        try:
            if romanize("ทดสอบ", engine=engine).strip():
                _cached_engine = engine
                return engine
        except Exception:
            continue
    _cached_engine = ""
    return None


def enabled():
    """ถอดชื่อในเครื่องได้หรือไม่ (ติดตั้ง pythainlp แล้วหรือยัง)"""
    return _pick_engine() is not None


def engine_name():
    """ชื่อ engine ที่กำลังใช้ ('' ถ้าใช้ไม่ได้) — ไว้แสดงในรายงาน/ดีบัก"""
    return _pick_engine() or ""


def match_threshold():
    """เกณฑ์ ratio ที่ควรใช้เทียบชื่อกับ engine ตัวที่กำลังใช้อยู่ (ดู _THRESHOLD)"""
    return _THRESHOLD.get(_pick_engine(), 0.60)


def romanize_names(thai_names):
    """ถอดรายชื่อไทยเป็นอังกฤษ คืน list ความยาวเท่า input (index ตรงกัน)

    คืน [] ถ้าถอดไม่ได้แม้แต่ชื่อเดียว เพื่อให้ผู้เรียกตัดสินใจได้ว่าจะตกไปทางอื่น
    """
    engine = _pick_engine()
    names = [str(n or "").strip() for n in (thai_names or [])]
    if not engine or not any(names):
        return []
    from pythainlp.transliterate import romanize

    out = []
    for name in names:
        try:
            words = [w for w in re.split(r'\s+', name) if w]
            out.append(" ".join(romanize(w, engine=engine) for w in words).strip())
        except Exception:
            out.append("")
    return out if all(out) else []
