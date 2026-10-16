# -*- coding: utf-8 -*-
"""เช็กว่ามีอัปเดตที่ออกแล้วบน GitHub แต่เครื่องนี้ยังไม่ได้ติดตั้งหรือไม่ — แถบ "มีอัปเดตใหม่" บนหน้าแรก

เจ้าของขอ (16 ต.ค. 2569) ให้อัปเดตแบบเดียวกับระบบปรับเอกสาร: เปิดหน้าเว็บแล้วเห็นว่ามีรุ่นใหม่ + กด "อัปเดตเลย"
ตัวติดตั้งจริงยังเป็นตัวเดียวกับ อัปเดตระบบตรวจเล่ม.bat (tools/run_local.py --update) — ไฟล์นี้แค่ดู ไม่เปลี่ยนโค้ดในเครื่อง
รูปแบบแท็ก v<หลัก>.<รอง> และหัวข้อ "## อัปเดต <เลข> — <วันที่>" ต้องตรงกับ tools/run_local.py (เทสต์ล็อกไว้)
อยู่นอก tools/ เพราะ image Docker ไม่มี tools/ (.dockerignore) — ไม่มี .git ก็แค่ไม่มีแถบ
"""
import re
import subprocess
from pathlib import Path

RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)$")
CHANGELOG_HEADING = re.compile(r"(?m)^## อัปเดต (\d+)\.(\d+)\s*[—-]\s*(.+?)\s*$")
FETCH_SECONDS = 20
GIT_SECONDS = 5
MAX_NOTES = 8


def label(version):
    return f"{version[0]}.{version[1]}"


def _git(repo, *args, timeout=GIT_SECONDS):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def release_tags(text):
    found = []
    for name in text.split():
        match = RELEASE_TAG.match(name.strip())
        if match:
            found.append(((int(match.group(1)), int(match.group(2))), name.strip()))
    return sorted(found)


def changelog_notes(text, after, upto):
    """รายการที่เปลี่ยน (บรรทัด "- ...") ของรุ่นที่ใหม่กว่า after จนถึง upto — ใหม่สุดก่อน"""
    heads = list(CHANGELOG_HEADING.finditer(text))
    notes = []
    for i, head in enumerate(heads):
        version = (int(head.group(1)), int(head.group(2)))
        if not ((after is None or version > after) and version <= upto):
            continue
        end = heads[i + 1].start() if i + 1 < len(heads) else len(text)
        items = []
        for line in text[head.end():end].splitlines():
            if line.startswith("- "):
                items.append(line[2:].strip())
            elif items and line.startswith("  ") and line.strip():
                items[-1] += " " + line.strip()            # บรรทัดต่อของรายการเดิม
        notes.extend(items)
    return notes[:MAX_NOTES]


def check(repo_dir, fetch=True, git=_git):
    """{'available', 'current', 'latest', 'notes', 'error'} — current/latest เป็นข้อความ เช่น "1.1" """
    out = {"available": False, "current": "", "latest": "", "notes": [], "error": ""}
    repo = Path(repo_dir)
    if not (repo / ".git").exists():
        return out
    try:
        if fetch:
            fetched = git(repo, "fetch", "--tags", "--quiet", "origin", timeout=FETCH_SECONDS)
            if fetched.returncode != 0:
                out["error"] = "ดึงข้อมูลรุ่นจาก GitHub ไม่สำเร็จ"
        releases = release_tags(git(repo, "tag", "--list", "v*").stdout)
        if not releases:
            return out
        have = None
        for version, name in reversed(releases):
            if git(repo, "merge-base", "--is-ancestor", name, "HEAD").returncode == 0:
                have = version
                break
        newest, tag = releases[-1]
        out["current"] = label(have) if have else ""
        out["latest"] = label(newest)
        if have is not None and have >= newest:
            return out
        out["available"] = True
        shown = git(repo, "show", f"{tag}:CHANGELOG.md")
        if shown.returncode == 0:
            out["notes"] = changelog_notes(shown.stdout, have, newest)
    except (OSError, subprocess.SubprocessError):
        out["error"] = out["error"] or "เรียก git ไม่ได้"
    return out
