# -*- coding: utf-8 -*-
"""รุ่นของระบบที่ติดตั้งอยู่ · รุ่นที่ออกแล้วบน GitHub · ตัวติดตั้งรุ่นใหม่ทับของเดิม

เจ้าของขอ (16 ต.ค. 2569) ให้อัปเดตแบบเดียวกับระบบปรับเอกสาร ทั้งสองทาง:
  - จากเครื่อง: หน้าแรกขึ้นแถบ "มีอัปเดตใหม่" + "อัปเดตเลย" (main.py /update/*) หรือ อัปเดตระบบตรวจเล่ม.bat
  - แบบ manual: ดาวน์โหลด/รับไฟล์ ZIP ของรุ่นนั้น แตกไฟล์ แล้วดับเบิลคลิก ติดตั้งอัปเดตจากโฟลเดอร์นี้.bat
เครื่องที่ทำงานติดตั้งจากไฟล์ ZIP และลง Git ไม่ได้ (ถูกบล็อก) จึงต้องไม่พึ่ง git:
  - เครื่องที่มี .git (เครื่องพัฒนา) ใช้ git เหมือนเดิม — ดูแท็ก v<เลข> ที่ HEAD ยังไม่มี
  - เครื่องที่ไม่มี .git ถามรายการแท็กจาก GitHub API (repo สาธารณะ ไม่ต้องล็อกอิน) ดาวน์โหลด ZIP ของแท็ก
    แล้วติดตั้งทับด้วย install_tree() — ตัวเดียวกับที่ทาง manual ใช้
รุ่นที่ติดตั้งอยู่ (ไม่มี .git): .run/installed.json ที่ install_tree() เขียน ไม่มีก็อ่านหัวข้อบนสุดของ CHANGELOG.md
(release.py บังคับให้หัวข้อบนสุดตรงกับแท็กอยู่แล้ว ZIP ของแท็กจึงบอกรุ่นตัวเองได้)
ไม่ทับ: .env (รหัสผ่าน ค่าชีท) .venv/ .run/ .git/ — ไฟล์ที่รุ่นก่อนติดตั้งแต่รุ่นใหม่ไม่มีแล้วถูกลบ
รูปแบบแท็กและหัวข้อ CHANGELOG ต้องตรงกับ tools/run_local.py (เทสต์ล็อกไว้)
อยู่นอก tools/ เพราะ image Docker ไม่มี tools/ (.dockerignore) · ใช้แค่ไลบรารีมาตรฐาน (ตัว .bat รันด้วย Python ของเครื่อง)
"""
import json
import re
import shutil
import subprocess
import urllib.request
import zipfile
from pathlib import Path

RELEASE_TAG = re.compile(r"^v(\d+)\.(\d+)(?:\.(\d+))?$")
CHANGELOG_HEADING = re.compile(r"(?m)^## อัปเดต (\d+)\.(\d+)(?:\.(\d+))?\s*[—-]\s*(.+?)\s*$")
REPO = "supawitmar/ethesis-checker"
FETCH_SECONDS = 20
GIT_SECONDS = 5
DOWNLOAD_SECONDS = 120
MAX_NOTES = 8
INSTALLED_NAME = "installed.json"
# ส่วนที่เป็นของเครื่องนี้ ห้ามติดตั้งทับหรือลบ (path แบบ / ตัวแรกคือโฟลเดอร์บนสุด)
KEEP_DIRS = {".git", ".venv", "venv", ".run", "dist", "__pycache__", ".pytest_cache"}
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


def version_of(match):
    """(หลัก, รอง, ย่อย) — ไม่มีเลขย่อย = 0 (1.2 = 1.2.0) · เลขย่อย = แก้เล็กน้อยไม่เพิ่มความสามารถ"""
    return (int(match.group(1)), int(match.group(2)), int(match.group(3) or 0))


def label(version):
    major, minor, patch = (tuple(version) + (0,))[:3]
    return f"{major}.{minor}" + (f".{patch}" if patch else "")


def parse_label(text):
    match = re.match(r"^(\d+)\.(\d+)(?:\.(\d+))?$", str(text or "").strip())
    return version_of(match) if match else None


def _git(repo, *args, timeout=GIT_SECONDS):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def release_tags(text):
    found = []
    for name in text.split():
        match = RELEASE_TAG.match(name.strip())
        if match:
            found.append((version_of(match), name.strip()))
    return sorted(found)


def changelog_notes(text, after, upto):
    """รายการที่เปลี่ยน (บรรทัด "- ...") ของรุ่นที่ใหม่กว่า after จนถึง upto — ใหม่สุดก่อน"""
    heads = list(CHANGELOG_HEADING.finditer(text))
    notes = []
    for i, head in enumerate(heads):
        version = version_of(head)
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


# ---------- รุ่นที่ติดตั้งอยู่ (เครื่องที่ไม่มี .git) ----------

def changelog_release(base):
    """เลขรุ่นจากหัวข้อบนสุดของ CHANGELOG.md เช่น "1.3" — ไม่มีไฟล์/หัวข้อ = "" """
    try:
        text = (Path(base) / "CHANGELOG.md").read_text(encoding="utf-8")
    except OSError:
        return ""
    head = CHANGELOG_HEADING.search(text)
    return label(version_of(head)) if head else ""


def installed_info(base):
    try:
        data = json.loads((Path(base) / ".run" / INSTALLED_NAME).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def installed_release(base):
    found = str(installed_info(base).get("release", ""))
    return found if parse_label(found) else changelog_release(base)


def installed_commit(base):
    commit = str(installed_info(base).get("commit", "")).lower()
    return commit[:7] if _COMMIT.match(commit) else ""


# ---------- รุ่นที่ออกแล้วบน GitHub (ไม่ใช้ git) ----------

def _get(url, timeout=FETCH_SECONDS):
    request = urllib.request.Request(url, headers={"User-Agent": "ethesis-checker-updater",
                                                   "Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def remote_releases(get=_get):
    """[((หลัก, รอง), "v1.3", "sha"), ...] เรียงเก่าไปใหม่ — จาก GitHub API"""
    data = json.loads(get(f"https://api.github.com/repos/{REPO}/tags?per_page=100").decode("utf-8"))
    found = []
    for item in data if isinstance(data, list) else []:
        match = RELEASE_TAG.match(str(item.get("name", "")))
        if match:
            sha = str((item.get("commit") or {}).get("sha", "")).lower()
            found.append((version_of(match), item["name"], sha))
    return sorted(found)


def remote_changelog(tag, get=_get):
    return get(f"https://raw.githubusercontent.com/{REPO}/{tag}/CHANGELOG.md").decode("utf-8")


def download_release(tag, target, get=_get):
    Path(target).write_bytes(get(f"https://github.com/{REPO}/archive/refs/tags/{tag}.zip",
                                 timeout=DOWNLOAD_SECONDS))


# ---------- ติดตั้งทับ (ใช้ทั้งอัปเดตจากเครื่องและแบบ manual) ----------

def _kept(rel):
    parts = rel.split("/")
    if parts[0] in KEEP_DIRS or "__pycache__" in parts:
        return True
    name = parts[-1]
    return (name == ".env" or name.startswith(".env.")) and name != ".env.example" or name.endswith(".lnk")


def program_files(src):
    """ไฟล์โปรแกรมในโฟลเดอร์ src (path แบบ / เทียบกับ src) ไม่รวมส่วนที่เป็นของเครื่อง"""
    src = Path(src)
    rels = []
    for path in src.rglob("*"):
        if path.is_file():
            rel = path.relative_to(src).as_posix()
            if not _kept(rel):
                rels.append(rel)
    return sorted(rels)


def looks_like_program(folder):
    folder = Path(folder)
    return all((folder / name).is_file() for name in ("main.py", "checker.py", "CHANGELOG.md")) \
        and (folder / "tools" / "run_local.py").is_file()


def install_tree(src, dest, commit=""):
    """คัดลอกโปรแกรมจากโฟลเดอร์ src ทับโฟลเดอร์ที่ติดตั้ง dest — คืน {'release','changed','removed'}

    เขียนเฉพาะไฟล์ที่ต่างจากเดิม (ไฟล์ .bat ที่กำลังรันอยู่ไม่ถูกเขียนทับถ้าเนื้อหาเหมือนเดิม) ·
    ลบเฉพาะไฟล์ที่รุ่นก่อนติดตั้งไว้ (รายการใน .run/installed.json) แต่รุ่นใหม่ไม่มีแล้ว
    """
    src, dest = Path(src).resolve(), Path(dest).resolve()
    if not looks_like_program(src):
        raise ValueError("โฟลเดอร์รุ่นใหม่ไม่ครบ (ไม่พบ main.py / checker.py / CHANGELOG.md / tools/run_local.py)")
    if (dest / ".git").exists():
        raise ValueError(f"{dest} เป็นเครื่องพัฒนา (มีโฟลเดอร์ .git) — อัปเดตด้วย git ไม่ใช้ตัวติดตั้งนี้")
    if src == dest:
        raise ValueError("โฟลเดอร์รุ่นใหม่กับโฟลเดอร์ที่ติดตั้งเป็นที่เดียวกัน")
    rels = program_files(src)
    previous = set(installed_info(dest).get("files") or [])
    changed, removed = [], []
    for rel in rels:
        source, target = src / rel, (dest / rel).resolve()
        if dest not in target.parents:
            raise ValueError(f"ไฟล์ {rel} อยู่นอกโฟลเดอร์ระบบ")
        data = source.read_bytes()
        if target.is_file() and target.read_bytes() == data:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        changed.append(rel)
    for rel in sorted(previous - set(rels)):
        target = (dest / rel).resolve()
        if dest in target.parents and target.is_file() and not _kept(rel):
            target.unlink()
            removed.append(rel)
    release = changelog_release(src)
    commit = commit.lower() if _COMMIT.match(commit.lower()) else ""
    (dest / ".run").mkdir(exist_ok=True)
    (dest / ".run" / INSTALLED_NAME).write_text(
        json.dumps({"release": release, "commit": commit[:40], "files": rels}, ensure_ascii=False, indent=0),
        encoding="utf-8")
    return {"release": release, "changed": changed, "removed": removed}


def install_zip(zip_path, dest, work_dir, commit=""):
    """แตก ZIP ของรุ่น (โฟลเดอร์บนสุดโฟลเดอร์เดียว แบบที่ GitHub สร้าง) ลง work_dir แล้ว install_tree"""
    with zipfile.ZipFile(zip_path) as archive:
        for name in archive.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"ไฟล์ใน ZIP ไม่ปลอดภัย: {name}")
        archive.extractall(work_dir)
        note = archive.comment.decode("ascii", "ignore").strip().lower()      # git archive ใส่รหัส commit ไว้ที่นี่
    roots = [p for p in Path(work_dir).iterdir() if p.is_dir()]
    if len(roots) != 1:
        raise ValueError("ไฟล์ ZIP ไม่ใช่รุ่นของระบบตรวจเล่ม")
    return install_tree(roots[0], dest, commit or (note if _COMMIT.match(note) else ""))


# ---------- แถบ "มีอัปเดตใหม่" ----------

def _check_git(repo, out, fetch, git):
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
    return out


def _check_remote(base, out, get):
    have = parse_label(installed_release(base))
    try:
        releases = remote_releases(get)
    except (OSError, ValueError):
        out["error"] = "ดึงข้อมูลรุ่นจาก GitHub ไม่สำเร็จ"
        return out
    if not releases:
        return out
    newest, tag, _sha = releases[-1]
    out["current"] = label(have) if have else ""
    out["latest"] = label(newest)
    if have is not None and have >= newest:
        return out
    out["available"] = True
    try:
        out["notes"] = changelog_notes(remote_changelog(tag, get), have, newest)
    except (OSError, ValueError):
        pass
    return out


def check(repo_dir, fetch=True, git=_git, get=_get):
    """{'available', 'current', 'latest', 'notes', 'error'} — current/latest เป็นข้อความ เช่น "1.1" """
    out = {"available": False, "current": "", "latest": "", "notes": [], "error": ""}
    repo = Path(repo_dir)
    try:
        if (repo / ".git").exists():
            return _check_git(repo, out, fetch, git)
        if looks_like_program(repo):
            return _check_remote(repo, out, get)
    except (OSError, subprocess.SubprocessError):
        out["error"] = out["error"] or "เช็กรุ่นใหม่ไม่ได้"
    return out


def remove_tree(path):
    shutil.rmtree(path, ignore_errors=True)
