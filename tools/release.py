# -*- coding: utf-8 -*-
"""ออกรุ่นอัปเดตให้ทุกเครื่องติดตั้ง — python tools/release.py 1.1

เจ้าของขอ (9 ต.ค. 2569): ปรับกฎเมื่อไหร่ ให้ออกเป็น "อัปเดต 1.1 วันที่ ..." ที่ทุกเครื่องโหลดไปติดตั้งได้
ขั้นตอนเต็มอยู่ใน CHANGELOG.md หัวไฟล์ — ตัวนี้ทำเฉพาะส่วนที่พลาดง่าย:
  - ตรวจว่าโฟลเดอร์ไม่มีไฟล์แก้ค้าง (รุ่นที่ออกต้องเป็นโค้ดที่ commit และผ่านด่านแล้วเท่านั้น)
  - ตรวจว่าหัวข้อบนสุดของ CHANGELOG.md คือ "## อัปเดต <เลขนี้> — <วันที่>"
  - เลขใหม่ต้องมากกว่ารุ่นล่าสุดที่ออก และยังไม่เคยใช้
  - สร้างแท็ก v<เลข> (annotated) ที่ HEAD — ไม่ push เอง (เจ้าของสั่ง push เท่านั้น)
"""
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_local  # noqa: E402

VERSION = re.compile(r"^(\d+)\.(\d+)$")


def _git(code, *args):
    return subprocess.run(["git", *args], cwd=code, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")


def release(version_text, code=run_local.CODE, say=run_local.say):
    match = VERSION.match(version_text.strip())
    if not match:
        say(f"เลขรุ่นต้องเป็นแบบ 1.1 (ได้ {version_text!r})")
        return 1
    version = (int(match.group(1)), int(match.group(2)))
    tag = f"v{version[0]}.{version[1]}"
    code = Path(code)

    status = _git(code, "status", "--porcelain", "--untracked-files=no")
    if status.returncode != 0 or status.stdout.strip():
        say("ออกรุ่นไม่ได้: มีไฟล์ที่ยังไม่ได้ commit — commit และผ่านด่านตรวจก่อน")
        say(status.stdout.rstrip() or status.stderr.strip())
        return 1

    try:
        text = (code / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        say("ออกรุ่นไม่ได้: ไม่พบ CHANGELOG.md")
        return 1
    entries = run_local.changelog_entries(text)
    if not entries or entries[0][0] != version:
        top = f"อัปเดต {run_local._label(entries[0][0])}" if entries else "(ไม่มีหัวข้อ)"
        say(f"ออกรุ่นไม่ได้: หัวข้อบนสุดของ CHANGELOG.md ต้องเป็น \"## อัปเดต {version_text} — วันที่\""
            f" (ตอนนี้คือ {top})")
        return 1
    if not entries[0][2]:
        say("ออกรุ่นไม่ได้: หัวข้อนี้ยังไม่มีรายการว่าเปลี่ยนอะไร")
        return 1

    existing = run_local.release_tags(lambda *args: _git(code, *args))
    if any(name == tag for _, name in existing):
        say(f"ออกรุ่นไม่ได้: มีแท็ก {tag} อยู่แล้ว")
        return 1
    if existing and version <= existing[-1][0]:
        say(f"ออกรุ่นไม่ได้: เลขต้องมากกว่ารุ่นล่าสุด ({existing[-1][1]})")
        return 1

    date = entries[0][1]
    tagged = _git(code, "tag", "-a", tag, "-m", f"อัปเดต {version_text} — {date}")
    if tagged.returncode != 0:
        say("สร้างแท็กไม่สำเร็จ\n" + tagged.stderr.strip())
        return 1
    head = _git(code, "rev-parse", "--short=7", "HEAD").stdout.strip()
    say(f"ออกอัปเดต {version_text} — {date} ที่ {head} แล้ว (แท็ก {tag} ยังอยู่ในเครื่องนี้)")
    say(f"ส่งขึ้น GitHub: git push origin main {tag}")
    return 0


if __name__ == "__main__":
    run_local._utf8_output()
    if len(sys.argv) != 2:
        run_local.say("ใช้: python tools/release.py <เลขรุ่น เช่น 1.1>")
        sys.exit(2)
    sys.exit(release(sys.argv[1]))
