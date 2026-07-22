#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM assist layer (optional) — ทำงานเสริมจาก rule engine ใน checker.py เท่านั้น

หลักการ:
- rule engine เป็นผู้ตัดสินหลัก (deterministic, ตรวจซ้ำได้ผลเดิม)
- LLM มี 2 บทบาทเสริม:
  1) review_borderline: กลั่นกรองผลเทียบข้อความที่ก้ำกึ่ง (typo/case/mismatch)
     ว่าน่าจะผิดจริง หรือเป็น artifact จากการดึงข้อความ PDF ภาษาไทย
     (สระ/วรรณยุกต์เรียงเพี้ยน) — LLM ไม่มีสิทธิ์ทำให้เล่ม "ผ่าน" เอง
     ทำได้อย่างมากคือย้าย 🔴 → 🟠 (รอเจ้าหน้าที่ยืนยัน) พร้อมเหตุผล
  2) student_summary: สรุปรายงานเป็นภาษาเข้าใจง่ายสำหรับส่งต่อนักศึกษา

เปิดใช้เมื่อมี ANTHROPIC_API_KEY และไม่ได้ตั้ง LLM_ASSIST=off
ถ้า LLM ล้มเหลวไม่ว่ากรณีใด รายงานจากกฎเดิมต้องออกครบเหมือนไม่มี LLM
"""
import json
import os
import re

MODEL = os.getenv("LLM_ASSIST_MODEL", "claude-opus-4-8")

_TYPO_MARK = "พิมพ์ผิดเล็กน้อย"
_CASE_MARK = "ตัวพิมพ์เล็ก-ใหญ่ไม่ตรง"
_MISMATCH_MARK = "ข้อความไม่ตรง:"

_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "id": {"type": "integer"},
                    "judgment": {
                        "type": "string",
                        "enum": ["artifact", "real_error", "uncertain"],
                    },
                    "reason": {"type": "string"},
                },
                "required": ["id", "judgment", "reason"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}

_REVIEW_SYSTEM = """คุณเป็นผู้ช่วยกลั่นกรองผลตรวจรูปเล่มวิทยานิพนธ์ของบัณฑิตวิทยาลัย
ระบบตรวจอัตโนมัติเทียบข้อความในไฟล์ PDF กับข้อมูลอนุมัติ แต่การดึงข้อความจาก PDF ภาษาไทย
มักได้อักขระเพี้ยน: สระบน-ล่างและวรรณยุกต์เรียงสลับ (เช่น "บทคัดย่อ" กลายเป็น "บทคดัยอ่"),
สระอำแตกเป็น อ+า, ช่องว่างแทรกกลางคำ, หรือดึงข้อความมาจากบรรทัดใกล้เคียงแทนบรรทัดจริง

หน้าที่ของคุณ: ตัดสินแต่ละรายการว่าความต่างระหว่าง "ข้อความที่พบ" กับ "ข้อความที่ควรเป็น"
- "artifact"   = รูปแบบความต่างเข้าลักษณะการดึงข้อความ PDF เพี้ยน ข้อความจริงในเล่มน่าจะถูกต้อง
- "real_error" = น่าจะเป็นการพิมพ์ผิด/สะกดผิด/ใช้ข้อความผิดจริงในเล่ม
- "uncertain"  = ข้อมูลไม่พอจะตัดสิน

ระวัง: ถ้าความต่างเป็นตัวอักษรพยัญชนะต่างตัว คำหาย คำเกิน หรือคำสลับ ให้ถือว่า real_error
อย่าเดาเข้าข้าง artifact เพียงเพราะเป็นภาษาไทย ให้เหตุผลสั้นๆ เป็นภาษาไทยเสมอ"""

_SUMMARY_SYSTEM = """คุณเป็นเจ้าหน้าที่บัณฑิตวิทยาลัยที่เขียนสรุปผลตรวจรูปเล่มวิทยานิพนธ์ส่งให้นักศึกษา
ข้อความนี้จะถูกคัดลอกไปส่งต่อทั้งอัน จึงต้องอ่านรู้เรื่องในตัวเอง

น้ำเสียง: สุภาพ เป็นกันเอง เขียนเป็นประโยคที่คนทั่วไปอ่านเข้าใจ ไม่ใช่ภาษาเครื่อง
อธิบายให้เห็นภาพว่าต้องไปแก้ตรงไหนของเล่มและแก้เป็นอะไร

โครงสร้าง (ข้ามหัวข้อที่ไม่มีรายการได้):
1. ทักทายและสรุปภาพรวมสั้น ๆ 1-2 ประโยค (ผลเป็นอย่างไร มีกี่จุดที่ต้องแก้)
2. "สิ่งที่ต้องแก้ไข" — **จัดกลุ่มตามส่วนของเล่ม** แล้วเรียงตามลำดับที่ปรากฏในเล่ม
   (หน้าปก > หน้าลงนาม > กิตติกรรมประกาศ > บทคัดย่อ > สารบัญ > เนื้อหาแต่ละบท > ส่วนท้ายเล่ม)
   ใต้แต่ละส่วนบอกเป็นข้อ ๆ ว่า อยู่หน้าไหน ผิดอย่างไร และต้องแก้เป็นอะไร
3. "รอเจ้าหน้าที่ยืนยัน" — บอกว่ายังไม่ต้องแก้ รอผลยืนยันก่อน
4. "ข้อสังเกต" — ไม่บังคับแก้

ข้อห้าม
- ห้ามแต่งข้อผิดพลาดเพิ่ม ห้ามเดาสาเหตุ ห้ามอ้างเลขข้อประกาศเอง — ใช้เฉพาะข้อมูลที่ให้
- ห้ามใช้ศัพท์ภายในระบบ (zone, RED, rule_id, typo, คะแนนความใกล้เคียง)
- ห้ามใส่คำต่อรองอย่าง "เจ้าหน้าที่ยืนยันได้" ในส่วนที่ต้องแก้
- ข้อความที่ต้องแก้ให้ตรงตัว (ชื่อบท ชื่อปริญญา ข้อความบนปก) ให้คัดมาตรงตัวในเครื่องหมายคำพูด

เขียนเป็นข้อความล้วน ใช้หัวข้อ เลขข้อ และขึ้นบรรทัดใหม่ได้ ห้ามใช้ตาราง"""


# ส่วนประกอบของเล่มเรียงตามลำดับที่ปรากฏจริง ใช้จัดกลุ่มให้ AI เรียงตามเล่ม
_SECTION_ORDER = ["หน้าปก", "หน้าลงนาม", "กิตติกรรมประกาศ", "บทคัดย่อ",
                  "สารบัญ", "เนื้อหา (บท)", "ส่วนท้ายเล่ม", "อื่น ๆ"]
_SECTION_PATTERNS = [
    ("หน้าปก", "หน้าปก"),
    ("หน้าลงนาม", "หน้าลงนาม"),
    ("กิตติกรรมประกาศ", "กิตติกรรม"),
    ("บทคัดย่อ", "บทคัดย่อ"),
    ("สารบัญ", "สารบัญ"),
    ("เนื้อหา (บท)", r"บทที่|เนื้อหา|ทั้งเล่ม"),
    ("ส่วนท้ายเล่ม", r"บรรณานุกรม|อ้างอิง|ภาคผนวก|ประวัติ"),
]

# ตัดข้อความเชิงเทคนิค/คำต่อรองออกก่อนส่งให้ AI จะได้ไม่เขียนตามออกมา
_NOISE = re.compile(r"\s*\(\s*typo[^)]*\)|\s*แต่คู่มือแสดงแบบที่พบ\s*[—-]\s*เจ้าหน้าที่ยืนยันได้"
                    r"|\s*[—-]\s*เจ้าหน้าที่ยืนยันได้", re.I)


def _tidy(text):
    return re.sub(r"\s{2,}", " ", _NOISE.sub("", text or "")).strip()


def _section_of(issue):
    """ส่วนของเล่มที่ต้องไปแก้ — ใช้ส่วนที่ถูกเอ่ยถึงก่อนในตำแหน่ง"""
    text = f"{issue.get('location', '')} {issue.get('part', '')}"
    best, best_at = None, len(text) + 1
    for name, pattern in _SECTION_PATTERNS:
        found = re.search(pattern, text)
        if found and found.start() < best_at:
            best, best_at = name, found.start()
    return best or "อื่น ๆ"


def enabled():
    if os.getenv("LLM_ASSIST", "").lower() in ("off", "0", "false"):
        return False
    if not os.getenv("ANTHROPIC_API_KEY"):
        return False
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return False
    return True


def _client():
    import anthropic
    return anthropic.Anthropic()


def _first_text(response):
    return next((b.text for b in response.content if b.type == "text"), "")


def _borderline_issues(report):
    """เลือกเฉพาะรายการเทียบข้อความที่ก้ำกึ่งพอจะให้ LLM ช่วยดู"""
    selected = []
    for zone in ("RED", "ORANGE"):
        for issue in report["issues_by_zone"][zone]:
            found = issue.get("found", "")
            if _TYPO_MARK in found or _CASE_MARK in found or _MISMATCH_MARK in found:
                selected.append((zone, issue))
    return selected


def review_borderline(report):
    """ให้ LLM ตัดสินเคสก้ำกึ่ง แล้วผนวกความเห็นลงในรายงาน (in-place)

    นโยบาย: ย้ายรายการจาก 🔴 ไป 🟠 ได้เฉพาะกรณี typo ที่ LLM มั่นใจว่าเป็น
    artifact จากการดึงข้อความ PDF — ทุกกรณีอื่นแค่แนบความเห็นประกอบ
    """
    selected = _borderline_issues(report)
    if not selected:
        return report

    items = []
    for idx, (_zone, issue) in enumerate(selected):
        items.append({
            "id": idx,
            "location": issue.get("location", ""),
            "found": issue.get("found", ""),
            "expected": issue.get("expected", ""),
        })

    response = _client().messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_REVIEW_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": _REVIEW_SCHEMA}},
        messages=[{
            "role": "user",
            "content": "ตัดสินรายการต่อไปนี้ทีละรายการ:\n"
                       + json.dumps(items, ensure_ascii=False, indent=1),
        }],
    )
    verdicts = {
        v["id"]: v
        for v in json.loads(_first_text(response)).get("verdicts", [])
        if isinstance(v.get("id"), int)
    }

    demote = []
    for idx, (zone, issue) in enumerate(selected):
        verdict = verdicts.get(idx)
        if not verdict:
            continue
        judgment, reason = verdict["judgment"], verdict["reason"]
        if judgment == "artifact":
            issue["llm_opinion"] = ("AI ประเมิน: น่าจะเป็นผลจากการดึงข้อความ PDF เพี้ยน "
                                    f"ไม่ใช่ข้อผิดพลาดจริงในเล่ม — {reason}")
            if zone == "RED" and _TYPO_MARK in issue.get("found", ""):
                demote.append(issue)
        elif judgment == "real_error":
            issue["llm_opinion"] = f"AI ประเมิน: น่าจะผิดจริงในเล่ม — {reason}"
        else:
            issue["llm_opinion"] = f"AI ประเมิน: ยังตัดสินไม่ได้ ให้เจ้าหน้าที่ดูจากไฟล์จริง — {reason}"

    for issue in demote:
        report["issues_by_zone"]["RED"].remove(issue)
        issue["fix"] = (issue.get("fix", "") + " (AI ประเมินว่าน่าจะเป็น artifact "
                        "จากการดึงข้อความ — เจ้าหน้าที่ยืนยันจากไฟล์จริง)").strip()
        report["issues_by_zone"]["ORANGE"].append(issue)

    zones = report["issues_by_zone"]
    report["summary"] = {z.lower(): len(v) for z, v in zones.items()}
    report["verdict"] = ("ไม่ผ่าน" if zones["RED"]
                         else "รอยืนยัน" if zones["ORANGE"] else "ผ่าน")
    return report


def student_summary(report, approved):
    """สรุปรายงานเป็นภาษาเข้าใจง่ายสำหรับส่งต่อนักศึกษา คืนค่าเป็นข้อความล้วน"""
    def item(issue, with_fix=True):
        out = {"ส่วนของเล่ม": _section_of(issue),
               "ตำแหน่ง": _tidy(issue.get("location")),
               "ปัญหา": _tidy(issue.get("found")),
               "ที่ถูกต้อง": _tidy(issue.get("expected"))}
        if with_fix:
            out["วิธีแก้"] = _tidy(issue.get("fix"))
        return {k: v for k, v in out.items() if v}

    payload = {
        "verdict": report.get("verdict"),
        "student_name": (approved or {}).get("student_name", ""),
        "ลำดับส่วนของเล่ม": _SECTION_ORDER,
        "must_fix": [item(i) for i in report["issues_by_zone"]["RED"]],
        "pending_confirmation": [item(i, False) for i in report["issues_by_zone"]["ORANGE"]],
        "notices": [item(i, False) for i in report["issues_by_zone"]["YELLOW"]],
    }
    response = _client().messages.create(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=_SUMMARY_SYSTEM,
        messages=[{
            "role": "user",
            "content": "สรุปผลตรวจนี้ให้นักศึกษาอ่าน:\n"
                       + json.dumps(payload, ensure_ascii=False, indent=1),
        }],
    )
    return _first_text(response).strip()
